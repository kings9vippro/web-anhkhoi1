import os
import io
import json
import time
import uuid
import asyncio
import threading
import random
import string
import subprocess
import sys
import base64

from flask import Flask, request, jsonify, render_template_string, Response

QR_TTL = 600
DATA_DIR = "/tmp/qr_sessions"
os.makedirs(DATA_DIR, exist_ok=True)

app = Flask(__name__)

# ============================================================
# QUẢN LÝ TIẾN TRÌNH TRÌNH DUYỆT ĐƠN (TIẾT KIỆM RAM)
# ============================================================
GLOBAL_BROWSER = None
GLOBAL_PLAYWRIGHT = None
BROWSER_LOCK = asyncio.Lock()
ACTIVE_PAGES = {}


async def get_shared_browser():
    global GLOBAL_BROWSER, GLOBAL_PLAYWRIGHT
    if GLOBAL_BROWSER and GLOBAL_BROWSER.is_connected():
        return GLOBAL_BROWSER

    from playwright.async_api import async_playwright
    GLOBAL_PLAYWRIGHT = await async_playwright().start()

    launch_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--renderer-process-limit=1",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1280,850",
    ]

    try:
        GLOBAL_BROWSER = await GLOBAL_PLAYWRIGHT.chromium.launch(
            headless=True,
            args=launch_args,
        )
    except Exception:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
        GLOBAL_BROWSER = await GLOBAL_PLAYWRIGHT.chromium.launch(
            headless=True,
            args=launch_args,
        )

    return GLOBAL_BROWSER


def sess_path(sid):
    return os.path.join(DATA_DIR, sid + ".json")


def sess_save(sid, data):
    try:
        with open(sess_path(sid), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        print("[SESS] Save error: " + str(e))


def sess_load(sid):
    try:
        with open(sess_path(sid), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def sess_exists(sid):
    return os.path.exists(sess_path(sid))


def gen_imei():
    return "".join(random.choices(string.digits, k=15))


# ============================================================
# ZALO QR CORE ENGINE — SIÊU TỐC & BẮT CHUẨN QR
# ============================================================
class ZaloQREngine:
    async def extract_qr_base64(self, page):
        # Tự động click vào tab QR nếu Zalo mặc định hiển thị tab số điện thoại
        try:
            tab_qr = await page.query_selector("text=/QR|Quét mã/i")
            if tab_qr:
                await tab_qr.click()
        except Exception:
            pass

        js_code = """
        () => {
            // Tìm thẻ chứa mã QR chuẩn xác trên Zalo Web
            const qrImg = document.querySelector('.qrcode img') || document.querySelector('canvas') || document.querySelector('img[alt*="QR"]') || document.querySelector('.qr-wrapper img');
            if (qrImg) {
                if (qrImg.tagName === 'CANVAS') {
                    return qrImg.toDataURL('image/png');
                }
                if (qrImg.src && qrImg.src.length > 50) {
                    return qrImg.src;
                }
            }
            // Fallback quét toàn bộ ảnh base64 có sẵn
            const imgs = Array.from(document.querySelectorAll('img'));
            for (let img of imgs) {
                if (img.src && img.src.startsWith('data:image') && img.width > 50) {
                    return img.src;
                }
            }
            return null;
        }
        """
        for _ in range(15):
            try:
                b64 = await page.evaluate(js_code)
                if b64:
                    return b64
            except Exception:
                pass
            await asyncio.sleep(0.4)

        # Fallback chụp vùng màn hình chứa mã QR
        try:
            el = await page.query_selector(".qrcode, .qr-wrapper, canvas")
            if el:
                png_bytes = await el.screenshot()
                return "data:image/png;base64," + base64.b64encode(png_bytes).decode("utf-8")
        except Exception:
            pass

        return None

    async def create_qr(self, session_id):
        async with BROWSER_LOCK:
            browser = await get_shared_browser()
            for old_ctx in browser.contexts:
                try:
                    await old_ctx.close()
                except Exception:
                    pass

            ctx = await browser.new_context(
                viewport={"width": 1280, "height": 850},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
            )
            page = await ctx.new_page()

        try:
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
            """)

            print("[QR] Truy cập chat.zalo.me...")
            try:
                await page.goto("https://chat.zalo.me/", wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                if "ERR_ABORTED" not in str(e):
                    raise e

            await asyncio.sleep(1.5)
            qr_b64 = await self.extract_qr_base64(page)
            if not qr_b64:
                raise Exception("Không tìm thấy mã QR trên trang")

            sess_save(session_id, {
                "status": "waiting",
                "created_at": time.time(),
                "qr_b64": qr_b64,
                "cookies": None,
                "imei": None,
                "qr_version": int(time.time()),
            })

            ACTIVE_PAGES[session_id] = {"page": page, "context": ctx}
            print(f"[QR] Phiên {session_id} đã sẵn sàng")

            threading.Thread(
                target=lambda: asyncio.run(self._poll(page, session_id)),
                daemon=True,
            ).start()

        except Exception as e:
            print("[QR] Lỗi tạo phiên: " + str(e))
            sess_save(session_id, {
                "status": "error",
                "error": str(e),
                "created_at": time.time(),
            })

    async def _poll(self, page, session_id):
        start = time.time()
        while time.time() - start < QR_TTL:
            try:
                cookies = await page.context.cookies()
                ck_dict = {c["name"]: c["value"] for c in cookies if "zalo" in c.get("domain", "")}

                if ck_dict.get("zpw_sek"):
                    await asyncio.sleep(1)
                    return await self._extract(session_id, page)

                url = page.url
                if ("chat.zalo.me" in url and "/login" not in url and "qr" not in url.lower()):
                    await asyncio.sleep(1)
                    return await self._extract(session_id, page)
            except Exception:
                pass

            await asyncio.sleep(1.2)

        data = sess_load(session_id) or {}
        data["status"] = "expired"
        sess_save(session_id, data)
        await self._cleanup(session_id)

    async def _extract(self, session_id, page):
        try:
            await asyncio.sleep(1.5)
            cookies = await page.context.cookies()
            ck = {c["name"]: c["value"] for c in cookies if "zalo" in c.get("domain", "")}

            imei = ""
            for k in ["imei", "zpw_imei", "device_id", "deviceId"]:
                try:
                    v = await page.evaluate(f"localStorage.getItem('{k}')")
                    if v:
                        imei = v
                        break
                except Exception:
                    pass

            if not imei and ck.get("zpw_imei"):
                imei = ck["zpw_imei"]
            if not imei:
                imei = gen_imei()

            data = sess_load(session_id) or {}
            data["status"] = "done"
            data["cookies"] = ck
            data["imei"] = imei
            sess_save(session_id, data)
            await self._cleanup(session_id)
        except Exception as e:
            data = sess_load(session_id) or {}
            data["status"] = "error"
            data["error"] = str(e)
            sess_save(session_id, data)

    async def _cleanup(self, session_id):
        info = ACTIVE_PAGES.pop(session_id, None)
        if info:
            try:
                await info["context"].close()
            except Exception:
                pass


# ============================================================
# API CONTROLLER
# ============================================================
@app.route("/api/qr/create", methods=["POST"])
def api_qr_create():
    session_id = uuid.uuid4().hex[:16]

    sess_save(session_id, {
        "status": "creating",
        "created_at": time.time(),
        "qr_b64": None,
        "cookies": None,
        "imei": None,
        "qr_version": 0,
    })

    def run():
        engine = ZaloQREngine()
        asyncio.run(engine.create_qr(session_id))

    threading.Thread(target=run, daemon=True).start()

    start = time.time()
    while time.time() - start < 20:
        data = sess_load(session_id)
        if data and data["status"] in ("waiting", "error"):
            break
        time.sleep(0.25)

    return jsonify({
        "ok": True,
        "session_id": session_id,
        "qr_url": "/qr/" + session_id,
        "ttl": QR_TTL,
    })


@app.route("/api/qr/status/<session_id>", methods=["GET"])
def api_qr_status(session_id):
    data = sess_load(session_id)
    if not data:
        return jsonify({"error": "Phiên không tồn tại"}), 404
    return jsonify({
        "status": data.get("status", "waiting"),
        "qr_b64": data.get("qr_b64"),
        "imei": data.get("imei"),
        "cookies": data.get("cookies"),
        "error": data.get("error"),
        "qr_version": data.get("qr_version", 0),
    })


@app.route("/api/qr/force-extract/<session_id>", methods=["POST"])
def api_qr_force_extract(session_id):
    data = sess_load(session_id)
    if not data:
        return jsonify({"error": "Phiên không tồn tại"}), 404

    if data.get("status") == "done" and data.get("cookies"):
        return jsonify({"status": "done", "imei": data.get("imei"), "cookies": data.get("cookies")})

    info = ACTIVE_PAGES.get(session_id)
    if not info:
        return jsonify({"status": data.get("status", "waiting"), "note": "Phiên đã đóng"})

    try:
        async def _read():
            page = info["page"]
            cookies = await page.context.cookies()
            ck = {c["name"]: c["value"] for c in cookies if "zalo" in c.get("domain", "")}
            imei = ""
            for k in ["imei", "zpw_imei", "device_id", "deviceId"]:
                try:
                    v = await page.evaluate(f"localStorage.getItem('{k}')")
                    if v:
                        imei = v
                        break
                except Exception:
                    pass
            return ck, imei

        result = {}
        t = threading.Thread(target=lambda: result.update({"data": asyncio.run(_read())}))
        t.start()
        t.join(timeout=5)

        if "data" not in result:
            return jsonify({"status": "waiting", "note": "Đang kiểm tra"})

        ck, imei = result["data"]
        if ck.get("zpw_sek"):
            if not imei:
                imei = gen_imei()
            data["status"] = "done"
            data["cookies"] = ck
            data["imei"] = imei
            sess_save(session_id, data)
            return jsonify({"status": "done", "imei": imei, "cookies": ck})

        return jsonify({"status": "waiting", "note": "Chưa xác nhận"})

    except Exception as e:
        return jsonify({"status": "waiting", "error": str(e)})


@app.route("/api/qr/refresh/<session_id>", methods=["POST"])
def api_qr_refresh(session_id):
    info = ACTIVE_PAGES.get(session_id)
    if not info:
        return jsonify({"error": "Phiên đã đóng hoặc hết hạn"}), 404

    try:
        engine = ZaloQREngine()

        async def _refresh():
            page = info["page"]
            return await engine.extract_qr_base64(page)

        result = {}
        t = threading.Thread(target=lambda: result.update({"b64": asyncio.run(_refresh())}))
        t.start()
        t.join(timeout=6)

        b64 = result.get("b64")
        if not b64:
            return jsonify({"error": "Không thể tải lại ảnh QR"}), 500

        data = sess_load(session_id) or {}
        data["qr_b64"] = b64
        data["qr_version"] = int(time.time())
        sess_save(session_id, data)

        return jsonify({"ok": True, "qr_b64": b64, "qr_version": data["qr_version"]})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/qr-image/<session_id>")
def qr_image(session_id):
    data = sess_load(session_id)
    if not data:
        return "Not found", 404
    qr_b64 = data.get("qr_b64")
    if qr_b64 and "," in qr_b64:
        content = base64.b64decode(qr_b64.split(",", 1)[1])
        return Response(content, mimetype="image/png")
    return "Đang tạo mã...", 404


@app.route("/")
def home():
    return render_template_string(HTML_HOME)


@app.route("/qr/<session_id>")
def page_qr(session_id):
    if not sess_exists(session_id):
        return render_template_string(HTML_EXPIRED), 404
    return render_template_string(HTML_QR, session_id=session_id)


# ============================================================
# GIAO DIỆN HIỆU ỨNG KÍNH LỎNG (LIQUID GLASS UI)
# ============================================================
SHARED_CSS = """
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html { -webkit-text-size-adjust:100%; touch-action:manipulation; }
body {
    font-family:-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
    min-height:100vh; min-height:100dvh;
    display:flex; align-items:center; justify-content:center;
    background:
        radial-gradient(circle at 20% 20%, rgba(0, 255, 200, 0.25), transparent 45%),
        radial-gradient(circle at 80% 80%, rgba(0, 150, 255, 0.25), transparent 45%),
        linear-gradient(135deg, #050508 0%, #121218 50%, #08080a 100%);
    background-size:200% 200%;
    animation:liquidMove 15s ease infinite alternate;
    padding:20px;
    color:#ffffff;
}
@keyframes liquidMove {
    0% { background-position: 0% 0%; }
    100% { background-position: 100% 100%; }
}

/* HIỆU ỨNG KÍNH LỎNG CAO CẤP (LIQUID GLASS) */
.card {
    position:relative;
    background: rgba(255, 255, 255, 0.07);
    backdrop-filter: blur(30px) saturate(180%);
    -webkit-backdrop-filter: blur(30px) saturate(180%);
    padding:40px 28px 36px;
    border-radius:36px;
    border: 1.5px solid rgba(255, 255, 255, 0.18);
    box-shadow:
        0 30px 90px rgba(0, 0, 0, 0.6),
        inset 0 1px 1px rgba(255, 255, 255, 0.4),
        inset 0 -1px 1px rgba(0, 255, 200, 0.2);
    text-align:center; max-width:460px; width:100%;
    animation:cardIn 0.5s cubic-bezier(0.16,1,0.3,1);
    overflow:hidden;
}
.card::before {
    content:'';
    position:absolute; top:-50%; left:-50%;
    width:200%; height:200%;
    background: linear-gradient(120deg, transparent 30%, rgba(255,255,255,0.08) 50%, transparent 70%);
    animation:shine 6s linear infinite;
    pointer-events:none;
}
@keyframes shine {
    0% { transform: translateX(-100%) rotate(15deg); }
    100% { transform: translateX(100%) rotate(15deg); }
}
@keyframes cardIn {
    from { opacity:0; transform:translateY(24px) scale(0.96); }
    to { opacity:1; transform:translateY(0) scale(1); }
}

.brand-icon {
    display:inline-flex; align-items:center; justify-content:center;
    width:64px; height:64px; border-radius:22px;
    background: rgba(255, 255, 255, 0.1);
    border: 1px solid rgba(255, 255, 255, 0.25);
    margin-bottom:16px;
    box-shadow: 0 8px 25px rgba(0, 255, 200, 0.15), inset 0 1px 1px rgba(255,255,255,0.5);
}
.brand-icon svg { width:30px; height:30px; stroke:#ffffff; fill:none; stroke-width:2; }

h1 {
    color:#ffffff;
    margin-bottom:6px;
    font-size:22px; font-weight:900;
    letter-spacing:-0.4px;
    text-shadow: 0 2px 12px rgba(0, 255, 200, 0.4);
}
p.sub {
    color: rgba(255, 255, 255, 0.75);
    margin-bottom:26px; font-size:13px; font-weight:500;
    line-height:1.4;
}

.btn-pill {
    position:relative;
    width:100%; padding:16px 24px; border-radius:9999px;
    background: rgba(255, 255, 255, 0.15);
    backdrop-filter: blur(15px);
    -webkit-backdrop-filter: blur(15px);
    color:#ffffff; font-size:14px; font-weight:800;
    font-family:inherit; cursor:pointer; letter-spacing:0.3px;
    border: 1.5px solid rgba(255, 255, 255, 0.3);
    box-shadow:
        0 10px 30px rgba(0, 0, 0, 0.3),
        inset 0 1px 1px rgba(255, 255, 255, 0.6);
    display:inline-flex; align-items:center; justify-content:center; gap:10px;
    text-decoration:none;
    transition:all 0.25s cubic-bezier(0.16,1,0.3,1);
}
.btn-pill:hover {
    background: rgba(255, 255, 255, 0.25);
    border-color: rgba(0, 255, 200, 0.6);
    transform:translateY(-2px);
    box-shadow:0 15px 35px rgba(0, 255, 200, 0.25), inset 0 1px 1px rgba(255,255,255,0.8);
}
.btn-pill:active { transform:translateY(0); }
.btn-pill:disabled { opacity:0.6; cursor:wait; }
.btn-pill svg { width:18px; height:18px; stroke:#ffffff; stroke-width:2.2; fill:none; }

.btn-secondary {
    margin-top:12px;
    background: rgba(255, 255, 255, 0.08);
    border-color: rgba(255, 255, 255, 0.15);
}

@keyframes spin {
    0% { transform:rotate(0deg); }
    100% { transform:rotate(360deg); }
}
.spin { animation:spin 1s linear infinite; }
"""


HTML_HOME = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Đăng Nhập Zalo — Liquid Glass</title>
<style>
""" + SHARED_CSS + """
.guide-box {
    margin-top:24px; text-align:left;
    background: rgba(255, 255, 255, 0.05);
    backdrop-filter: blur(20px);
    border: 1px solid rgba(255, 255, 255, 0.12);
    padding:20px; border-radius:24px;
    box-shadow: inset 0 1px 1px rgba(255,255,255,0.2);
}
.guide-title {
    display:flex; align-items:center; gap:8px;
    color:#a7ffeb; font-size:11px; font-weight:900;
    letter-spacing:1px; margin-bottom:12px; text-transform:uppercase;
}
.guide-title svg { width:14px; height:14px; stroke:#a7ffeb; fill:none; stroke-width:2.5; }
.guide-item {
    display:flex; align-items:center; gap:10px;
    color: rgba(255,255,255,0.85); font-size:13px; font-weight:600;
    margin-bottom:10px; line-height:1.4;
}
.guide-badge {
    flex-shrink:0; width:22px; height:22px; border-radius:50%;
    background: rgba(255, 255, 255, 0.2); color:#ffffff; font-size:11px; font-weight:900;
    display:inline-flex; align-items:center; justify-content:center;
    border: 1px solid rgba(255, 255, 255, 0.4);
}
</style>
</head>
<body>
<div class="card">
    <div class="brand-icon">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="7" height="7" rx="2"></rect>
            <rect x="14" y="3" width="7" height="7" rx="2"></rect>
            <rect x="14" y="14" width="7" height="7" rx="2"></rect>
            <rect x="3" y="14" width="7" height="7" rx="2"></rect>
        </svg>
    </div>

    <h1>MÃ ĐĂNG NHẬP ZALO</h1>
    <p class="sub">Hệ thống trích xuất QR siêu tốc & hiệu ứng kính lỏng</p>

    <button id="btn-start" class="btn-pill" onclick="startQR()">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
        </svg>
        <span>KHỞI TẠO MÃ QR</span>
    </button>

    <div class="guide-box">
        <div class="guide-title">
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
            Hướng dẫn thao tác
        </div>
        <div class="guide-item"><span class="guide-badge">1</span> Nhấn Khởi tạo mã QR</div>
        <div class="guide-item"><span class="guide-badge">2</span> Đợi 2-4 giây để mã QR hiện lên</div>
        <div class="guide-item"><span class="guide-badge">3</span> Dùng ứng dụng Zalo quét mã</div>
        <div class="guide-item"><span class="guide-badge">4</span> Nhận ngay IMEI & Cookie tự động</div>
    </div>
</div>

<script>
async function startQR() {
    const btn = document.getElementById('btn-start');
    btn.disabled = true;
    btn.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>ĐANG KHỞI TẠO...</span>';
    try {
        const r = await fetch('/api/qr/create', { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            btn.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg><span>THÀNH CÔNG</span>';
            setTimeout(() => { window.location.href = d.qr_url; }, 200);
        } else {
            alert('Lỗi: ' + (d.error || 'Không rõ'));
            btn.disabled = false;
            btn.innerHTML = '<svg viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg><span>KHỞI TẠO MÃ QR</span>';
        }
    } catch (e) {
        alert('Lỗi kết nối: ' + e.message);
        btn.disabled = false;
        btn.innerHTML = '<svg viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg><span>KHỞI TẠO MÃ QR</span>';
    }
}
</script>
</body>
</html>
"""


HTML_QR = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Quét QR — Liquid Glass</title>
<style>
""" + SHARED_CSS + """
.qr-frame {
    position:relative;
    background: rgba(255, 255, 255, 0.95);
    padding:14px; border-radius:24px;
    margin:0 auto 14px; max-width:280px; width:100%; min-height:280px;
    border: 2px solid rgba(0, 255, 200, 0.5);
    box-shadow: 0 15px 40px rgba(0, 0, 0, 0.4), 0 0 30px rgba(0, 255, 200, 0.2);
    display:flex; align-items:center; justify-content:center;
}
.qr-frame img {
    width:100%; height:auto; display:none; border-radius:14px;
    object-fit:contain;
}
.qr-loading {
    display:flex; flex-direction:column; align-items:center; justify-content:center; gap:10px;
    color:#09090b; font-size:12px; font-weight:800;
}
.qr-loading svg { width:26px; height:26px; stroke:#00b4d8; stroke-width:2.5; fill:none; }

.qr-desc {
    display:flex; align-items:center; justify-content:center; gap:6px;
    font-size:12px; color: rgba(255,255,255,0.85); font-weight:600; margin-bottom:16px;
}
.qr-desc svg { width:14px; height:14px; stroke:#a7ffeb; fill:none; stroke-width:2; }

.status-badge {
    padding:11px 16px; border-radius:9999px;
    font-size:12px; font-weight:800;
    display:inline-flex; align-items:center; justify-content:center; gap:8px;
    background: rgba(255, 255, 255, 0.12);
    backdrop-filter: blur(10px);
    border: 1px solid rgba(255, 255, 255, 0.25);
    color:#ffffff; margin-bottom:16px; width:100%;
}
.status-badge.error {
    background: rgba(255, 100, 100, 0.25);
    color:#ffe4e6; border-color: rgba(255, 100, 100, 0.4);
}
.status-badge svg { width:15px; height:15px; stroke:currentColor; fill:none; stroke-width:2.2; }

.result-card { text-align:left; }
.success-banner {
    text-align:center; padding:18px;
    background: rgba(0, 255, 200, 0.15);
    backdrop-filter: blur(20px);
    border-radius:22px; margin-bottom:14px;
    border: 1px solid rgba(0, 255, 200, 0.4);
}
.success-badge-icon {
    width:44px; height:44px; border-radius:50%; margin:0 auto 8px;
    background:#ffffff; display:flex; align-items:center; justify-content:center;
    box-shadow:0 5px 20px rgba(0,255,200,0.5);
}
.success-badge-icon svg { width:22px; height:22px; stroke:#003d33; stroke-width:2.6; fill:none; }
.success-banner-title { font-size:16px; font-weight:900; color:#ffffff; text-shadow: 0 2px 10px rgba(0,255,200,0.6); }

.data-box {
    padding:12px 16px; border-radius:18px;
    background: rgba(255, 255, 255, 0.08);
    backdrop-filter: blur(15px);
    margin-bottom:10px;
    border: 1px solid rgba(255, 255, 255, 0.2);
}
.data-label {
    display:flex; align-items:center; gap:6px;
    font-size:10px; color:#a7ffeb; text-transform:uppercase;
    font-weight:900; letter-spacing:1px; margin-bottom:4px;
}
.data-label svg { width:13px; height:13px; stroke:#a7ffeb; fill:none; stroke-width:2; }
.data-val {
    font-family:monospace; font-size:11px; color:#ffffff;
    word-break:break-all; font-weight:600; line-height:1.4;
}
.data-val.scrollable {
    max-height:100px; overflow-y:auto;
    background: rgba(0, 0, 0, 0.25); padding:8px; border-radius:12px;
    border: 1px solid rgba(255, 255, 255, 0.1); margin-top:4px;
}

.toast {
    position:fixed; bottom:24px; left:50%; transform:translateX(-50%);
    background: rgba(255, 255, 255, 0.9);
    backdrop-filter: blur(20px);
    color:#052a36; padding:12px 24px; border-radius:9999px;
    font-weight:900; font-size:13px; z-index:9999;
    border: 1.5px solid #ffffff;
    box-shadow: 0 15px 40px rgba(0,0,0,0.4), 0 0 25px rgba(0,255,200,0.6);
    display:flex; align-items:center; gap:8px;
}
.toast svg { width:16px; height:16px; stroke:#052a36; stroke-width:2.5; fill:none; }
</style>
</head>
<body>
<div class="card">
    <div class="brand-icon">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <rect x="5" y="2" width="14" height="20" rx="3" ry="3"></rect>
            <line x1="12" y1="18" x2="12.01" y2="18"></line>
        </svg>
    </div>

    <h1>MÃ ĐĂNG NHẬP ZALO</h1>
    <p class="sub">Mở ứng dụng Zalo trên điện thoại để quét mã</p>

    <div id="qr-block">
        <div class="qr-frame">
            <div id="qr-loading" class="qr-loading">
                <svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg>
                <span>Đang tải mã QR...</span>
            </div>
            <img id="qr-img" alt="Mã QR">
        </div>
        <div class="qr-desc">
            <svg viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
            <span>Thiết bị đăng nhập mục tiêu: iPhone 18</span>
        </div>
    </div>

    <div id="status" class="status-badge">
        <svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.2"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg>
        <span>Đang chờ quét...</span>
    </div>

    <button id="btn-refresh" class="btn-pill" onclick="refreshQR()">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="23 4 23 10 17 10"></polyline>
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
        </svg>
        <span>LÀM MỚI MÃ QR</span>
    </button>

    <button id="btn-check" class="btn-pill btn-secondary" onclick="forceCheck()">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"></polyline>
        </svg>
        <span>TÔI ĐÃ ĐỒNG Ý - LẤY COOKIE</span>
    </button>
</div>

<script>
const sessionId = "{{ session_id }}";
let done = false;

function renderQRImage(src) {
    const img = document.getElementById('qr-img');
    const loading = document.getElementById('qr-loading');
    img.onload = () => {
        img.style.display = 'block';
        if (loading) loading.style.display = 'none';
    };
    img.src = src;
}

async function check() {
    if (done) return;
    try {
        const r = await fetch('/api/qr/status/' + sessionId);
        const d = await r.json();
        const el = document.getElementById('status');

        if (d.qr_b64) {
            renderQRImage(d.qr_b64);
        }

        if (d.status === 'waiting' || d.status === 'creating') {
            el.className = 'status-badge';
            el.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.2"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>Đang chờ thao tác quét...</span>';
        } else if (d.status === 'done') {
            done = true;
            showResult(d);
        } else if (d.status === 'error') {
            el.className = 'status-badge error';
            el.innerHTML = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg><span>' + (d.error || 'Lỗi') + '</span>';
            done = true;
        } else if (d.status === 'expired') {
            el.className = 'status-badge';
            el.innerHTML = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg><span>Mã QR đã hết hạn</span>';
            done = true;
        }
    } catch (e) {}
}

async function refreshQR() {
    const btn = document.getElementById('btn-refresh');
    btn.disabled = true;
    btn.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.2"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>ĐANG TẢI...</span>';
    try {
        const r = await fetch('/api/qr/refresh/' + sessionId, { method: 'POST' });
        const d = await r.json();
        if (d.ok && d.qr_b64) {
            renderQRImage(d.qr_b64);
            showToast('Đã làm mới mã QR');
        } else {
            alert('Lỗi: ' + (d.error || 'Không rõ'));
        }
    } catch (e) {
        alert('Lỗi kết nối: ' + e.message);
    }
    btn.disabled = false;
    btn.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg><span>LÀM MỚI MÃ QR</span>';
}

async function forceCheck() {
    const btn = document.getElementById('btn-check');
    btn.disabled = true;
    btn.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.2"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>ĐANG KIỂM TRA...</span>';
    try {
        const r = await fetch('/api/qr/force-extract/' + sessionId, { method: 'POST' });
        const d = await r.json();
        if (d.status === 'done') {
            done = true;
            showResult(d);
        } else {
            alert('Chưa xác nhận trên điện thoại. Vui lòng quét mã và thử lại.\\n' + (d.note || ''));
            btn.disabled = false;
            btn.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg><span>TÔI ĐÃ ĐỒNG Ý - LẤY COOKIE</span>';
        }
    } catch (e) {
        alert('Lỗi kết nối: ' + e.message);
        btn.disabled = false;
        btn.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg><span>TÔI ĐÃ ĐỒNG Ý - LẤY COOKIE</span>';
    }
}

function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function showResult(d) {
    const imei = d.imei || '000000000000000';
    const cookies = d.cookies || {};
    const cookiesStr = JSON.stringify(cookies);
    const combo = imei + '|' + cookiesStr;

    document.getElementById('qr-block').style.display = 'none';
    document.getElementById('btn-check').style.display = 'none';
    document.getElementById('btn-refresh').style.display = 'none';
    document.getElementById('status').style.display = 'none';

    const card = document.querySelector('.card');
    const resWrap = document.createElement('div');
    resWrap.className = 'result-card';
    resWrap.innerHTML =
        '<div class="success-banner">' +
            '<div class="success-badge-icon">' +
                '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg>' +
            '</div>' +
            '<div class="success-banner-title">ĐĂNG NHẬP THÀNH CÔNG</div>' +
        '</div>' +
        '<div class="data-box">' +
            '<div class="data-label">' +
                '<svg viewBox="0 0 24 24"><rect x="5" y="2" width="14" height="20" rx="3"></rect><line x1="12" y1="18" x2="12.01" y2="18"></line></svg>' +
                'Mã Định Danh IMEI' +
            '</div>' +
            '<div class="data-val">' + escapeHtml(imei) + '</div>' +
        '</div>' +
        '<div class="data-box">' +
            '<div class="data-label">' +
                '<svg viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="11" rx="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>' +
                'Dữ Liệu Cookie' +
            '</div>' +
            '<div class="data-val scrollable">' + escapeHtml(cookiesStr) + '</div>' +
        '</div>' +
        '<button class="btn-pill" id="copy-all">' +
            '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>' +
            '<span>SAO CHÉP TẤT CẢ (IMEI + COOKIE)</span>' +
        '</button>' +
        '<button class="btn-pill btn-secondary" id="copy-imei">' +
            '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>' +
            '<span>CHỈ SAO CHÉP IMEI</span>' +
        '</button>' +
        '<button class="btn-pill btn-secondary" id="copy-ck">' +
            '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>' +
            '<span>CHỈ SAO CHÉP COOKIE</span>' +
        '</button>';

    card.appendChild(resWrap);

    document.getElementById('copy-all').onclick = () => copyText(combo);
    document.getElementById('copy-imei').onclick = () => copyText(imei);
    document.getElementById('copy-ck').onclick = () => copyText(cookiesStr);
}

function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => showToast('Đã sao chép vào bộ nhớ tạm')).catch(() => fallbackCopy(text));
    } else {
        fallbackCopy(text);
    }
}

function fallbackCopy(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); showToast('Đã sao chép'); }
    catch (e) { prompt('Sao chép:', text); }
    document.body.removeChild(ta);
}

function showToast(msg) {
    const old = document.querySelector('.toast');
    if (old) old.remove();
    const t = document.createElement('div');
    t.className = 'toast';
    t.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg><span>' + msg + '</span>';
    document.body.appendChild(t);
    setTimeout(() => { t.remove(); }, 2000);
}

setInterval(check, 1500);
check();
</script>
</body>
</html>
"""


HTML_EXPIRED = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hết Hạn Phiên</title>
<style>
""" + SHARED_CSS + """
.expired-icon-wrap {
    width:56px; height:56px; border-radius:50%; margin:0 auto 16px;
    background: rgba(255, 255, 255, 0.1); display:flex; align-items:center; justify-content:center;
    border: 1px solid rgba(255, 255, 255, 0.2);
}
.expired-icon-wrap svg { width:28px; height:28px; stroke:#ffffff; stroke-width:2; fill:none; }
</style>
</head>
<body>
<div class="card">
    <div class="expired-icon-wrap">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
    </div>
    <h1>PHIÊN HẾT HẠN</h1>
    <p class="sub">Mã QR đã hết thời gian chờ bảo mật. Vui lòng tạo phiên mới.</p>
    <a href="/" class="btn-pill">
        <svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>
        <span>TẠO MÃ QR MỚI</span>
    </a>
</div>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
