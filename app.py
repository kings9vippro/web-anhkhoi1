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
# ZALO QR CORE ENGINE — DESKTOP FAST CAPTURE & IPHONE 18 PRO MAX
# ============================================================
class ZaloQREngine:
    async def extract_qr_fast(self, page):
        # 1. Tự động chuyển tab QR nếu Zalo Web đang hiển thị tab số điện thoại
        try:
            tab_qr = await page.wait_for_selector("text=/QR|Quét mã/i", timeout=2000)
            if tab_qr:
                await tab_qr.click()
        except Exception:
            pass

        # 2. Định vị trực tiếp thẻ Canvas vẽ mã QR (Bỏ qua hoàn toàn ảnh logo Zalo)
        js_extract = """
        () => {
            // Tìm thẻ canvas của mã QR (Zalo Web dùng canvas để render mã QR)
            const canvas = document.querySelector('.qrcode canvas') || document.querySelector('.qr-container canvas') || document.querySelector('canvas');
            if (canvas && canvas.width >= 100 && canvas.height >= 100) {
                return canvas.toDataURL('image/png');
            }
            // Tìm thẻ ảnh bên trong đúng khung chứa mã QR
            const qrImg = document.querySelector('.qrcode img') || document.querySelector('.qr-wrapper img');
            if (qrImg && qrImg.src && qrImg.src.startsWith('data:image') && qrImg.width >= 100) {
                return qrImg.src;
            }
            return null;
        }
        """

        for _ in range(12):
            try:
                b64 = await page.evaluate(js_extract)
                if b64:
                    return b64
            except Exception:
                pass
            await asyncio.sleep(0.25)

        # 3. Fallback chụp phần tử chứa mã QR nếu không trích xuất được trực tiếp Base64
        for sel in [".qrcode canvas", ".qrcode", ".qr-wrapper", "canvas"]:
            try:
                el = await page.query_selector(sel)
                if el:
                    box = await el.bounding_box()
                    if box and box["width"] >= 100 and box["height"] >= 100:
                        png_bytes = await el.screenshot()
                        return "data:image/png;base64," + base64.b64encode(png_bytes).decode("utf-8")
            except Exception:
                continue

        return None

    async def create_qr(self, session_id):
        async with BROWSER_LOCK:
            browser = await get_shared_browser()
            for old_ctx in browser.contexts:
                try:
                    await old_ctx.close()
                except Exception:
                    pass

            # Sử dụng Desktop Viewport & Mac User-Agent để Zalo nhả mã QR tức thì trong 2s
            ctx = await browser.new_context(
                viewport={"width": 1280, "height": 850},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
            )
            page = await ctx.new_page()

        try:
            # Chặn tài nguyên nặng để tăng tốc độ tải
            await page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ["media", "font"]
                or any(t in route.request.url for t in ["google-analytics", "doubleclick", "stats"])
                else route.continue_(),
            )

            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
            """)

            try:
                await page.goto("https://chat.zalo.me/", wait_until="domcontentloaded", timeout=18000)
            except Exception as e:
                if "ERR_ABORTED" not in str(e):
                    raise e

            qr_b64 = await self.extract_qr_fast(page)
            if not qr_b64:
                raise Exception("Không thể lấy ảnh mã QR")

            sess_save(session_id, {
                "status": "waiting",
                "created_at": time.time(),
                "qr_b64": qr_b64,
                "cookies": None,
                "compact_cookie": None,
                "imei": None,
                "device_name": "iPhone 18 Pro Max",
                "qr_version": int(time.time()),
            })

            ACTIVE_PAGES[session_id] = {"page": page, "context": ctx}

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
        # Vòng lặp quét nhanh mỗi 0.5s để phát hiện người dùng bấm Đồng ý tức thì
        while time.time() - start < QR_TTL:
            try:
                cookies = await page.context.cookies()
                has_zpw = any(c["name"] == "zpw_sek" and c.get("value") for c in cookies if "zalo" in c.get("domain", ""))

                if has_zpw:
                    return await self._extract(session_id, page)

                url = page.url
                if "chat.zalo.me" in url and "/login" not in url and "qr" not in url.lower():
                    return await self._extract(session_id, page)
            except Exception:
                pass

            await asyncio.sleep(0.5)

        data = sess_load(session_id) or {}
        data["status"] = "expired"
        sess_save(session_id, data)
        await self._cleanup(session_id)

    async def _extract(self, session_id, page):
        try:
            await asyncio.sleep(0.8)
            cookies = await page.context.cookies()
            ck_dict = {c["name"]: c["value"] for c in cookies if "zalo" in c.get("domain", "")}

            # Tự động nén và thu gọn cookie thành chuỗi chuẩn name=value;
            compact_cookie = "; ".join([f"{k}={v}" for k, v in ck_dict.items()])

            imei = ""
            for k in ["imei", "zpw_imei", "device_id", "deviceId"]:
                try:
                    v = await page.evaluate(f"localStorage.getItem('{k}')")
                    if v:
                        imei = v
                        break
                except Exception:
                    pass

            if not imei and ck_dict.get("zpw_imei"):
                imei = ck_dict["zpw_imei"]
            if not imei:
                imei = gen_imei()

            data = sess_load(session_id) or {}
            data["status"] = "done"
            data["cookies"] = ck_dict
            data["compact_cookie"] = compact_cookie
            data["imei"] = imei
            data["device_name"] = "iPhone 18 Pro Max"
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
        "compact_cookie": None,
        "imei": None,
        "device_name": "iPhone 18 Pro Max",
        "qr_version": 0,
    })

    def run():
        engine = ZaloQREngine()
        asyncio.run(engine.create_qr(session_id))

    threading.Thread(target=run, daemon=True).start()

    # Chờ mã QR tạo xong trong 2-3s để trả về ngay cho giao diện
    start = time.time()
    qr_b64 = None
    while time.time() - start < 15:
        data = sess_load(session_id)
        if data and data.get("qr_b64"):
            qr_b64 = data["qr_b64"]
            break
        if data and data.get("status") == "error":
            break
        time.sleep(0.2)

    return jsonify({
        "ok": True,
        "session_id": session_id,
        "qr_b64": qr_b64,
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
        "compact_cookie": data.get("compact_cookie"),
        "device_name": data.get("device_name", "iPhone 18 Pro Max"),
        "error": data.get("error"),
        "qr_version": data.get("qr_version", 0),
    })


@app.route("/api/qr/refresh/<session_id>", methods=["POST"])
def api_qr_refresh(session_id):
    info = ACTIVE_PAGES.get(session_id)
    if not info:
        return jsonify({"error": "Phiên đã hoàn tất hoặc đóng"}), 404

    try:
        engine = ZaloQREngine()

        async def _refresh():
            page = info["page"]
            return await engine.extract_qr_fast(page)

        result = {}
        t = threading.Thread(target=lambda: result.update({"b64": asyncio.run(_refresh())}))
        t.start()
        t.join(timeout=5)

        b64 = result.get("b64")
        if not b64:
            return jsonify({"error": "Không thể làm mới mã QR"}), 500

        data = sess_load(session_id) or {}
        data["qr_b64"] = b64
        data["qr_version"] = int(time.time())
        sess_save(session_id, data)

        return jsonify({"ok": True, "qr_b64": b64, "qr_version": data["qr_version"]})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/")
def home():
    return render_template_string(HTML_HOME)


@app.route("/qr/<session_id>")
def page_qr(session_id):
    if not sess_exists(session_id):
        return render_template_string(HTML_EXPIRED), 404
    return render_template_string(HTML_QR, session_id=session_id)


# ============================================================
# GIAO DIỆN KÍNH LỎNG (LIQUID GLASS UI)
# ============================================================
SHARED_CSS = """
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html { -webkit-text-size-adjust:100%; touch-action:manipulation; }
body {
    font-family:-apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Segoe UI", Roboto, sans-serif;
    min-height:100vh; min-height:100dvh;
    display:flex; align-items:center; justify-content:center;
    background:
        radial-gradient(circle at 15% 15%, rgba(0, 255, 213, 0.28), transparent 45%),
        radial-gradient(circle at 85% 85%, rgba(0, 180, 255, 0.28), transparent 45%),
        radial-gradient(circle at 50% 50%, rgba(20, 25, 35, 0.8), #07090e);
    background-size:180% 180%;
    animation:liquidShift 14s ease-in-out infinite alternate;
    padding:16px;
    color:#ffffff;
}
@keyframes liquidShift {
    0% { background-position:0% 0%; }
    100% { background-position:100% 100%; }
}

.liquid-card {
    position:relative;
    background:rgba(255, 255, 255, 0.08);
    backdrop-filter:blur(36px) saturate(210%);
    -webkit-backdrop-filter:blur(36px) saturate(210%);
    padding:36px 26px 32px;
    border-radius:36px;
    border:1.5px solid rgba(255, 255, 255, 0.22);
    box-shadow:
        0 35px 90px rgba(0, 0, 0, 0.6),
        inset 0 1px 1px rgba(255, 255, 255, 0.6),
        inset 0 -1px 2px rgba(0, 255, 213, 0.2);
    text-align:center; max-width:440px; width:100%;
    animation:cardAppear 0.4s cubic-bezier(0.16,1,0.3,1);
    overflow:hidden;
}
.liquid-card::before {
    content:'';
    position:absolute; top:-50%; left:-50%; width:200%; height:200%;
    background:linear-gradient(120deg, transparent 35%, rgba(255,255,255,0.12) 50%, transparent 65%);
    animation:liquidGleam 7s linear infinite;
    pointer-events:none;
}
@keyframes liquidGleam {
    0% { transform:translateX(-100%) rotate(20deg); }
    100% { transform:translateX(100%) rotate(20deg); }
}
@keyframes cardAppear {
    from { opacity:0; transform:translateY(20px) scale(0.97); }
    to { opacity:1; transform:translateY(0) scale(1); }
}

.icon-badge {
    display:inline-flex; align-items:center; justify-content:center;
    width:62px; height:62px; border-radius:22px;
    background:linear-gradient(135deg, rgba(255,255,255,0.22), rgba(255,255,255,0.06));
    border:1px solid rgba(255,255,255,0.35);
    margin-bottom:14px;
    box-shadow:0 10px 25px rgba(0, 255, 213, 0.2), inset 0 1px 1px #ffffff;
}
.icon-badge svg { width:30px; height:30px; stroke:#ffffff; fill:none; stroke-width:2.2; }

h1 {
    color:#ffffff; margin-bottom:6px;
    font-size:22px; font-weight:900; letter-spacing:-0.4px;
    text-shadow:0 2px 14px rgba(0, 255, 213, 0.45);
}
p.sub {
    color:rgba(255, 255, 255, 0.8);
    margin-bottom:24px; font-size:13px; font-weight:500;
}

/* NÚT BẤM VIÊN THUỐC MÀU TRẮNG TUYẾT */
.btn-pill {
    position:relative; width:100%; padding:16px 24px; border-radius:9999px;
    background:linear-gradient(180deg, #ffffff 0%, #f1f5f9 100%);
    color:#05232d; font-size:14px; font-weight:800;
    font-family:inherit; cursor:pointer; letter-spacing:0.3px;
    border:1.5px solid #ffffff;
    box-shadow:
        0 10px 25px rgba(0, 0, 0, 0.25),
        inset 0 2px 2px #ffffff,
        inset 0 -2px 4px rgba(0, 200, 255, 0.2);
    display:inline-flex; align-items:center; justify-content:center; gap:10px;
    text-decoration:none;
    transition:all 0.25s cubic-bezier(0.16,1,0.3,1);
}
.btn-pill:hover {
    background:#ffffff; transform:translateY(-2px);
    box-shadow:0 15px 35px rgba(0, 255, 213, 0.35), inset 0 2px 2px #ffffff;
}
.btn-pill:active { transform:translateY(0); }
.btn-pill:disabled { opacity:0.65; cursor:wait; }
.btn-pill svg { width:18px; height:18px; stroke:#05232d; stroke-width:2.3; fill:none; }

.btn-secondary {
    margin-top:10px;
    background:rgba(255, 255, 255, 0.12);
    color:#ffffff;
    border:1px solid rgba(255, 255, 255, 0.3);
    box-shadow:0 6px 18px rgba(0,0,0,0.2), inset 0 1px 1px rgba(255,255,255,0.4);
}
.btn-secondary:hover {
    background:rgba(255, 255, 255, 0.22);
    box-shadow:0 10px 25px rgba(0, 255, 213, 0.25);
}
.btn-secondary svg { stroke:#ffffff; }

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
    background:rgba(255, 255, 255, 0.06);
    backdrop-filter:blur(20px);
    border:1px solid rgba(255, 255, 255, 0.16);
    padding:18px 20px; border-radius:24px;
    box-shadow:inset 0 1px 1px rgba(255,255,255,0.2);
}
.guide-title {
    display:flex; align-items:center; gap:8px;
    color:#a7ffeb; font-size:11px; font-weight:900;
    letter-spacing:1px; margin-bottom:12px; text-transform:uppercase;
}
.guide-title svg { width:14px; height:14px; stroke:#a7ffeb; fill:none; stroke-width:2.5; }
.guide-item {
    display:flex; align-items:center; gap:10px;
    color:rgba(255,255,255,0.9); font-size:13px; font-weight:600;
    margin-bottom:8px; line-height:1.4;
}
.guide-badge {
    flex-shrink:0; width:20px; height:20px; border-radius:50%;
    background:rgba(255, 255, 255, 0.25); color:#ffffff; font-size:11px; font-weight:900;
    display:inline-flex; align-items:center; justify-content:center;
    border:1px solid rgba(255, 255, 255, 0.5);
}
</style>
</head>
<body>
<div class="liquid-card">
    <div class="icon-badge">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <rect x="5" y="2" width="14" height="20" rx="3" ry="3"></rect>
            <line x1="12" y1="18" x2="12.01" y2="18"></line>
        </svg>
    </div>

    <h1>MÃ ĐĂNG NHẬP ZALO</h1>
    <p class="sub">Tạo mã QR siêu tốc & Nhận diện thiết bị iPhone 18 Pro Max</p>

    <button id="btn-start" class="btn-pill" onclick="startQR()">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
        </svg>
        <span>KHỞI TẠO MÃ QR</span>
    </button>

    <div class="guide-box">
        <div class="guide-title">
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
            Cơ chế hoạt động
        </div>
        <div class="guide-item"><span class="guide-badge">1</span> Nhấn Khởi tạo mã QR</div>
        <div class="guide-item"><span class="guide-badge">2</span> Mã QR tải ngay lập tức trong 2 giây</div>
        <div class="guide-item"><span class="guide-badge">3</span> Điện thoại quét mã & xác nhận đăng nhập</div>
        <div class="guide-item"><span class="guide-badge">4</span> Web tự động chuyển sang trang lấy Cookie & IMEI</div>
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
            btn.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg><span>HOÀN TẤT</span>';
            setTimeout(() => { window.location.href = d.qr_url; }, 150);
        } else {
            alert('Lỗi: ' + (d.error || 'Không rõ nguyên nhân'));
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
<title>Xác Thực QR — Liquid Glass</title>
<style>
""" + SHARED_CSS + """
.qr-frame {
    position:relative;
    background:#ffffff;
    padding:14px; border-radius:26px;
    margin:0 auto 14px; max-width:280px; width:100%; min-height:280px;
    border:2px solid rgba(0, 255, 213, 0.6);
    box-shadow:0 15px 45px rgba(0, 0, 0, 0.4), 0 0 35px rgba(0, 255, 213, 0.25);
    display:flex; align-items:center; justify-content:center;
}
.qr-frame img {
    width:100%; height:auto; display:none; border-radius:14px;
    object-fit:contain;
}
.qr-loading {
    display:flex; flex-direction:column; align-items:center; justify-content:center; gap:8px;
    color:#07202b; font-size:12px; font-weight:800;
}
.qr-loading svg { width:26px; height:26px; stroke:#00b4d8; stroke-width:2.5; fill:none; }

.device-badge {
    display:inline-flex; align-items:center; justify-content:center; gap:6px;
    font-size:12px; color:#a7ffeb; font-weight:700; margin-bottom:14px;
    text-shadow:0 1px 8px rgba(0, 255, 213, 0.5);
}
.device-badge svg { width:14px; height:14px; stroke:#a7ffeb; fill:none; stroke-width:2.2; }

.status-badge {
    padding:10px 16px; border-radius:9999px;
    font-size:12px; font-weight:800;
    display:inline-flex; align-items:center; justify-content:center; gap:8px;
    background:rgba(255, 255, 255, 0.14);
    backdrop-filter:blur(15px);
    border:1px solid rgba(255, 255, 255, 0.28);
    color:#ffffff; margin-bottom:14px; width:100%;
}
.status-badge svg { width:15px; height:15px; stroke:currentColor; fill:none; stroke-width:2.2; }

.result-card { text-align:left; animation:cardAppear 0.35s ease; }
.success-banner {
    text-align:center; padding:18px;
    background:rgba(0, 255, 213, 0.15);
    backdrop-filter:blur(24px);
    border-radius:24px; margin-bottom:14px;
    border:1.5px solid rgba(0, 255, 213, 0.45);
}
.success-badge-icon {
    width:44px; height:44px; border-radius:50%; margin:0 auto 8px;
    background:#ffffff; display:flex; align-items:center; justify-content:center;
    box-shadow:0 6px 20px rgba(0,255,213,0.5);
}
.success-badge-icon svg { width:22px; height:22px; stroke:#032930; stroke-width:2.6; fill:none; }
.success-title { font-size:16px; font-weight:900; color:#ffffff; text-shadow:0 2px 10px rgba(0,255,213,0.6); }

.data-box {
    padding:12px 16px; border-radius:18px;
    background:rgba(255, 255, 255, 0.08);
    backdrop-filter:blur(16px);
    margin-bottom:10px;
    border:1px solid rgba(255, 255, 255, 0.18);
}
.data-label {
    display:flex; align-items:center; justify-content:space-between;
    font-size:10px; color:#a7ffeb; text-transform:uppercase;
    font-weight:900; letter-spacing:1px; margin-bottom:6px;
}
.data-label-left { display:flex; align-items:center; gap:6px; }
.data-label-left svg { width:13px; height:13px; stroke:#a7ffeb; fill:none; stroke-width:2; }
.btn-toggle {
    background:transparent; border:none; color:#ffffff; font-size:10px;
    cursor:pointer; text-decoration:underline; font-weight:700;
}
.data-val {
    font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:11px; color:#ffffff;
    word-break:break-all; font-weight:600; line-height:1.45;
}
.data-val.compact {
    display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;
}

.toast {
    position:fixed; bottom:24px; left:50%; transform:translateX(-50%);
    background:#ffffff; color:#05232d;
    padding:12px 24px; border-radius:9999px;
    font-weight:900; font-size:13px; z-index:9999;
    border:1.5px solid #ffffff;
    box-shadow:0 15px 40px rgba(0,0,0,0.4), 0 0 25px rgba(0,255,213,0.6);
    display:flex; align-items:center; gap:8px;
}
.toast svg { width:16px; height:16px; stroke:#05232d; stroke-width:2.6; fill:none; }
</style>
</head>
<body>
<div class="liquid-card">
    <div class="icon-badge">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <rect x="5" y="2" width="14" height="20" rx="3" ry="3"></rect>
            <line x1="12" y1="18" x2="12.01" y2="18"></line>
        </svg>
    </div>

    <h1>MÃ ĐĂNG NHẬP ZALO</h1>
    <p class="sub">Mở Zalo trên điện thoại để quét mã bên dưới</p>

    <div id="qr-block">
        <div class="qr-frame">
            <div id="qr-loading" class="qr-loading">
                <svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg>
                <span>Đang tải mã QR...</span>
            </div>
            <img id="qr-img" alt="Mã QR Zalo">
        </div>
        <div class="device-badge">
            <svg viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
            <span>Thiết bị đăng nhập mục tiêu: iPhone 18 Pro Max</span>
        </div>
    </div>

    <div id="status" class="status-badge">
        <svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.2"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg>
        <span>Đang chờ quét và xác nhận...</span>
    </div>

    <button id="btn-refresh" class="btn-pill btn-secondary" onclick="refreshQR()">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="23 4 23 10 17 10"></polyline>
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
        </svg>
        <span>LÀM MỚI MÃ QR</span>
    </button>
</div>

<script>
const sessionId = "{{ session_id }}";
let done = false;
let isCookieExpanded = false;

function renderQR(src) {
    const img = document.getElementById('qr-img');
    const loading = document.getElementById('qr-loading');
    img.onload = () => {
        img.style.display = 'block';
        if (loading) loading.style.display = 'none';
    };
    img.src = src;
}

// Tự động kiểm tra trạng thái mỗi 600ms để chuyển trang tức thì khi bạn bấm Đồng ý
async function check() {
    if (done) return;
    try {
        const r = await fetch('/api/qr/status/' + sessionId);
        const d = await r.json();

        if (d.qr_b64) {
            renderQR(d.qr_b64);
        }

        if (d.status === 'done') {
            done = true;
            showResult(d);
        } else if (d.status === 'error') {
            document.getElementById('status').innerHTML = '<span>❌ ' + (d.error || 'Lỗi') + '</span>';
            done = true;
        } else if (d.status === 'expired') {
            document.getElementById('status').innerHTML = '<span>⏰ Phiên đã hết hạn</span>';
            done = true;
        }
    } catch (e) {}
}

async function refreshQR() {
    const btn = document.getElementById('btn-refresh');
    btn.disabled = true;
    btn.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>ĐANG TẢI...</span>';
    try {
        const r = await fetch('/api/qr/refresh/' + sessionId, { method: 'POST' });
        const d = await r.json();
        if (d.ok && d.qr_b64) {
            renderQR(d.qr_b64);
            showToast('Đã làm mới mã QR');
        }
    } catch (e) {}
    btn.disabled = false;
    btn.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg><span>LÀM MỚI MÃ QR</span>';
}

function showResult(d) {
    const imei = d.imei || '000000000000000';
    const compactCookie = d.compact_cookie || '';
    const combo = imei + '|' + compactCookie;

    document.getElementById('qr-block').style.display = 'none';
    document.getElementById('btn-refresh').style.display = 'none';
    document.getElementById('status').style.display = 'none';

    const card = document.querySelector('.liquid-card');
    const resWrap = document.createElement('div');
    resWrap.className = 'result-card';
    resWrap.innerHTML =
        '<div class="success-banner">' +
            '<div class="success-badge-icon">' +
                '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg>' +
            '</div>' +
            '<div class="success-title">ĐĂNG NHẬP THÀNH CÔNG!</div>' +
        '</div>' +
        '<div class="data-box">' +
            '<div class="data-label">' +
                '<div class="data-label-left">' +
                    '<svg viewBox="0 0 24 24"><rect x="5" y="2" width="14" height="20" rx="3"></rect><line x1="12" y1="18" x2="12.01" y2="18"></line></svg>' +
                    'Định Danh IMEI (iPhone 18 Pro Max)' +
                '</div>' +
            '</div>' +
            '<div class="data-val">' + imei + '</div>' +
        '</div>' +
        '<div class="data-box">' +
            '<div class="data-label">' +
                '<div class="data-label-left">' +
                    '<svg viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="11" rx="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>' +
                    'Cookie (Thu Gọn)' +
                '</div>' +
                '<button class="btn-toggle" id="toggle-ck" onclick="toggleCookie()">Mở rộng</button>' +
            '</div>' +
            '<div class="data-val compact" id="cookie-val">' + compactCookie + '</div>' +
        '</div>' +
        '<button class="btn-pill" id="copy-all" style="margin-top:14px;">' +
            '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>' +
            '<span>SAO CHÉP IMEI + COOKIE</span>' +
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
    document.getElementById('copy-ck').onclick = () => copyText(compactCookie);
}

function toggleCookie() {
    const el = document.getElementById('cookie-val');
    const btn = document.getElementById('toggle-ck');
    isCookieExpanded = !isCookieExpanded;
    if (isCookieExpanded) {
        el.className = 'data-val';
        btn.innerText = 'Thu gọn';
    } else {
        el.className = 'data-val compact';
        btn.innerText = 'Mở rộng';
    }
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

setInterval(check, 600);
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
.icon-badge { margin:0 auto 16px; }
</style>
</head>
<body>
<div class="liquid-card">
    <div class="icon-badge">
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
