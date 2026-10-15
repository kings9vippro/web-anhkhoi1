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


def ensure_playwright_browsers():
    """Tự động kiểm tra và cài đặt Chromium nếu thiếu."""
    try:
        print("[SETUP] Kiểm tra và tự động cài Chromium...")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=False,
        )
        print("[SETUP] Cài đặt / kiểm tra Chromium hoàn tất.")
    except Exception as e:
        print("[SETUP] Lỗi khi chạy playwright install: " + str(e))


threading.Thread(target=ensure_playwright_browsers, daemon=True).start()


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
# ZALO QR LOGIN ENGINE — iPhone 18 UA
# ============================================================
class ZaloQR:
    def __init__(self):
        self.browser = None
        self.playwright = None

    async def _launch_browser(self):
        return await self.playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--no-zygote",
                "--disable-blink-features=AutomationControlled",
                "--window-size=600,900",
            ],
        )

    async def start(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()

        try:
            self.browser = await self._launch_browser()
        except Exception as e:
            err_str = str(e).lower()
            if "executable doesn't exist" in err_str or "playwright install" in err_str:
                print("[QR] Phát hiện thiếu Chromium, đang kích hoạt tải khẩn cấp...")
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, ensure_playwright_browsers)
                self.browser = await self._launch_browser()
            else:
                raise e

    async def _capture_qr(self, page):
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
            ctx = await self.browser.new_context(
                viewport={"width": 393, "height": 852},
                device_scale_factor=3,
                is_mobile=True,
                has_touch=True,
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                    "Version/18.0 Mobile/15E148 Safari/604.1"
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

            print("[QR] Mo chat.zalo.me (iPhone 18 UA)")
            await page.goto(
                "https://chat.zalo.me/",
                wait_until="networkidle",
                timeout=60000,
            )

            await asyncio.sleep(8)

            try:
                btn = await page.wait_for_selector(
                    "text=/QR|Quét mã/i", timeout=15000
                )
                await btn.click()
                print("[QR] Da click tab QR")
            except Exception as e:
                print("[QR] Khong tim thay nut QR: " + str(e))

            await asyncio.sleep(6)

            png = await self._capture_qr(page)
            if not png:
                raise Exception("Không chụp được ảnh mã QR")

            png_path = os.path.join(DATA_DIR, session_id + ".png")
            with open(png_path, "wb") as f:
                f.write(png)

            sess_save(session_id, {
                "status": "waiting",
                "created_at": time.time(),
                "png_path": png_path,
                "cookies": None,
                "imei": None,
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
        check_count = 0

        while time.time() - start < QR_TTL:
            check_count += 1
            try:
                cookies = await page.context.cookies()
                ck_dict = {}
                for c in cookies:
                    if "zalo" in c.get("domain", ""):
                        ck_dict[c["name"]] = c["value"]

                if check_count % 5 == 0:
                    print("[QR] Poll #" + str(check_count) +
                          " | cookies: " + str(len(ck_dict)) +
                          " | has_zpw_sek: " + str("zpw_sek" in ck_dict))

                if ck_dict.get("zpw_sek"):
                    print("[QR] PHÁT HIỆN ZPW_SEK")
                    await asyncio.sleep(3)
                    return await self._extract(session_id, page)

                url = page.url
                if ("chat.zalo.me" in url and "/login" not in url
                        and "qr" not in url.lower()):
                    print("[QR] URL đổi: " + url)
                    await asyncio.sleep(3)
                    return await self._extract(session_id, page)

            except Exception as e:
                print("[QR] Poll error: " + str(e))

            await asyncio.sleep(1.5)

        print("[QR] Session hết hạn: " + session_id)
        data = sess_load(session_id) or {}
        data["status"] = "expired"
        sess_save(session_id, data)
        await self._cleanup(session_id)

    async def _extract(self, session_id, page):
        try:
            print("[QR] Trích xuất dữ liệu sau 5s")
            await asyncio.sleep(5)

            cookies = await page.context.cookies()
            ck = {}
            for c in cookies:
                if "zalo" in c.get("domain", ""):
                    ck[c["name"]] = c["value"]

            imei = ""
            for k in ["imei", "zpw_imei", "device_id", "deviceId"]:
                try:
                    v = await page.evaluate(
                        "localStorage.getItem('" + k + "')"
                    )
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

            print("[QR] THÀNH CÔNG - IMEI: " + imei)
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
# API ENDPOINTS
# ============================================================
@app.route("/api/qr/create", methods=["POST"])
def api_qr_create():
    session_id = uuid.uuid4().hex[:16]

    sess_save(session_id, {
        "status": "creating",
        "created_at": time.time(),
        "png_path": None,
        "cookies": None,
        "imei": None,
        "qr_version": 0,
    })

    def run():
        qr = ZaloQR()
        asyncio.run(qr.create_qr(session_id))

    threading.Thread(target=run, daemon=True).start()

    start = time.time()
    while time.time() - start < 60:
        data = sess_load(session_id)
        if data and data["status"] in ("waiting", "error"):
            break
        time.sleep(0.5)

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
        return jsonify({"error": "Phiên làm việc không tồn tại"}), 404
    return jsonify({
        "status": data.get("status", "waiting"),
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
        return jsonify({
            "status": "done",
            "imei": data.get("imei"),
            "cookies": data.get("cookies"),
        })

    info = ACTIVE_PAGES.get(session_id)
    if not info:
        return jsonify({
            "status": data.get("status", "waiting"),
            "imei": data.get("imei"),
            "cookies": data.get("cookies"),
            "note": "Phiên đã hoàn tất hoặc đóng",
        })

    try:
        async def _read():
            page = info["page"]
            cookies = await page.context.cookies()
            ck = {}
            for c in cookies:
                if "zalo" in c.get("domain", ""):
                    ck[c["name"]] = c["value"]

            imei = ""
            for k in ["imei", "zpw_imei", "device_id", "deviceId"]:
                try:
                    v = await page.evaluate(
                        "localStorage.getItem('" + k + "')"
                    )
                    if v:
                        imei = v
                        break
                except Exception:
                    pass
            return ck, imei

        result = {}

        def run():
            result["data"] = asyncio.run(_read())

        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=10)

        if "data" not in result:
            return jsonify({"status": "waiting", "note": "Hết thời gian phản hồi"})

        ck, imei = result["data"]

        if ck.get("zpw_sek"):
            if not imei:
                imei = gen_imei()
            data["status"] = "done"
            data["cookies"] = ck
            data["imei"] = imei
            sess_save(session_id, data)
            return jsonify({
                "status": "done",
                "imei": imei,
                "cookies": ck,
            })

        return jsonify({
            "status": "waiting",
            "note": "Chưa phát hiện zpw_sek từ Zalo",
            "cookies_count": len(ck),
        })

    except Exception as e:
        return jsonify({"status": "waiting", "error": str(e)})


@app.route("/api/qr/refresh/<session_id>", methods=["POST"])
def api_qr_refresh(session_id):
    info = ACTIVE_PAGES.get(session_id)
    if not info:
        return jsonify({"error": "Phiên đã đóng"}), 404

    try:
        async def _refresh():
            page = info["page"]
            await asyncio.sleep(1)
            return await page.screenshot(full_page=False)

        result = {}

        def run():
            result["png"] = asyncio.run(_refresh())

        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=15)

        if "png" not in result or not result["png"]:
            return jsonify({"error": "Không thể chụp lại ảnh QR"}), 500

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
        return "Đang tạo mã QR...", 404
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
    return render_template_string(HTML_QR, session_id=session_id)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": "28.2", "ua": "iPhone-18"})


# ============================================================
# GIAO DIỆN LIQUID GLASS — NÚT MÀU TRẮNG TUYẾT & VECTOR SVG DESIGN
# ============================================================
SHARED_CSS = """
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html { -webkit-text-size-adjust:100%; touch-action:manipulation; }
body {
    font-family:'Inter','SF Pro Display',-apple-system,BlinkMacSystemFont,sans-serif;
    min-height:100vh; min-height:100dvh;
    display:flex; align-items:center; justify-content:center;
    background:
        radial-gradient(circle at 15% 15%, rgba(0,255,200,0.55), transparent 45%),
        radial-gradient(circle at 85% 20%, rgba(0,180,255,0.55), transparent 45%),
        radial-gradient(circle at 25% 85%, rgba(0,255,150,0.45), transparent 45%),
        radial-gradient(circle at 80% 80%, rgba(0,220,255,0.5), transparent 45%),
        linear-gradient(135deg, #00c9a7 0%, #00b4d8 35%, #0077b6 70%, #023e8a 100%);
    background-size:200% 200%, 200% 200%, 200% 200%, 200% 200%, 100% 100%;
    animation:liquidShift 22s ease-in-out infinite;
    padding:16px;
    overflow-x:hidden;
}
@keyframes liquidShift {
    0%,100% { background-position:0% 0%, 100% 0%, 0% 100%, 100% 100%, 0% 50%; }
    50% { background-position:100% 100%, 0% 100%, 100% 0%, 0% 0%, 100% 50%; }
}
.card {
    position:relative;
    background:rgba(255,255,255,0.14);
    backdrop-filter:blur(40px) saturate(200%);
    -webkit-backdrop-filter:blur(40px) saturate(200%);
    padding:38px 24px 34px;
    border-radius:36px;
    border:1.5px solid rgba(255,255,255,0.45);
    box-shadow:
        0 30px 90px rgba(0,60,80,0.45),
        inset 0 1px 1px rgba(255,255,255,0.8),
        inset 0 -1px 1px rgba(0,255,200,0.2);
    text-align:center; max-width:520px; width:100%;
    animation:cardIn 0.9s cubic-bezier(0.16,1,0.3,1);
    overflow:hidden;
}
.card::before {
    content:'';
    position:absolute; top:-50%; left:-50%;
    width:200%; height:200%;
    background:linear-gradient(120deg, transparent 30%, rgba(255,255,255,0.25) 50%, transparent 70%);
    animation:shine 6s linear infinite;
    pointer-events:none;
}
@keyframes shine {
    0% { transform:translateX(-100%) rotate(15deg); }
    100% { transform:translateX(100%) rotate(15deg); }
}
@keyframes cardIn {
    from { opacity:0; transform:translateY(70px) scale(0.92); }
    to { opacity:1; transform:translateY(0) scale(1); }
}

.brand-badge {
    display:inline-flex; align-items:center; justify-content:center;
    width:64px; height:64px; border-radius:22px;
    background:linear-gradient(135deg, rgba(255,255,255,0.3), rgba(255,255,255,0.1));
    border:1.5px solid rgba(255,255,255,0.7);
    box-shadow:0 12px 30px rgba(0,255,200,0.35), inset 0 1px 1px #ffffff;
    margin-bottom:14px;
}
.brand-badge svg { width:34px; height:34px; color:#ffffff; }

h1 {
    color:#ffffff;
    margin-bottom:8px;
    font-size:24px; font-weight:900;
    letter-spacing:-0.5px;
    text-shadow:0 4px 24px rgba(0,255,200,0.65), 0 0 40px rgba(0,200,255,0.4);
    background:linear-gradient(135deg,#ffffff,#cffff6,#a5f3fc);
    -webkit-background-clip:text;
    -webkit-text-fill-color:transparent;
    background-clip:text;
}
p.sub {
    color:rgba(255,255,255,0.9);
    margin-bottom:24px; font-size:13px; font-weight:500;
    text-shadow:0 2px 10px rgba(0,0,0,0.25);
}

/* NÚT MÀU TRẮNG TUYẾT (SNOW WHITE BUTTONS) */
.btn-snow {
    position:relative;
    width:100%; padding:18px 24px; border:none; border-radius:20px;
    background:linear-gradient(180deg, #ffffff 0%, #f4f8fb 100%);
    color:#052a36; font-size:15px; font-weight:800;
    font-family:inherit; cursor:pointer; letter-spacing:0.4px;
    border:1.5px solid #ffffff;
    box-shadow:
        0 14px 35px rgba(0, 30, 45, 0.25),
        inset 0 2px 2px #ffffff,
        inset 0 -2px 5px rgba(190, 220, 235, 0.45),
        0 0 25px rgba(255, 255, 255, 0.4);
    display:inline-flex; align-items:center; justify-content:center; gap:10px;
    text-decoration:none;
    transition:all 0.35s cubic-bezier(0.16,1,0.3,1);
    overflow:hidden;
}
.btn-snow::after {
    content:'';
    position:absolute; top:0; left:-120%;
    width:100%; height:100%;
    background:linear-gradient(90deg, transparent, rgba(255,255,255,0.9), transparent);
    transition:left 0.65s;
}
.btn-snow:hover::after { left:120%; }
.btn-snow:hover {
    transform:translateY(-3px) scale(1.015);
    background:#ffffff;
    box-shadow:
        0 20px 45px rgba(0, 40, 60, 0.3),
        0 0 35px rgba(255, 255, 255, 0.7),
        inset 0 2px 2px #ffffff;
}
.btn-snow:active { transform:translateY(0) scale(0.99); }
.btn-snow:disabled { opacity:0.75; cursor:wait; }
.btn-snow svg { width:20px; height:20px; stroke:#052a36; stroke-width:2.2; fill:none; }

.btn-secondary {
    margin-top:12px;
}
"""


HTML_HOME = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Hệ Thống QR Zalo — Liquid Glass</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
.steps {
    margin-top:26px; text-align:left;
    background:rgba(255,255,255,0.15);
    backdrop-filter:blur(24px);
    border:1px solid rgba(255,255,255,0.4);
    padding:22px; border-radius:22px;
    box-shadow:inset 0 1px 1px rgba(255,255,255,0.5);
}
.steps-title {
    display:flex; align-items:center; gap:8px;
    color:#cffff6; font-size:12px; font-weight:900;
    letter-spacing:1px; margin-bottom:14px; text-transform:uppercase;
}
.steps-title svg { width:16px; height:16px; stroke:#cffff6; fill:none; stroke-width:2.5; }
.step-item {
    display:flex; align-items:center; gap:12px;
    color:#ffffff; font-size:13px; font-weight:600;
    margin-bottom:10px; line-height:1.4;
    text-shadow:0 1px 4px rgba(0,0,0,0.25);
}
.step-badge {
    flex-shrink:0; width:24px; height:24px; border-radius:50%;
    background:#ffffff; color:#052a36; font-size:11px; font-weight:900;
    display:inline-flex; align-items:center; justify-content:center;
    box-shadow:0 3px 10px rgba(0,0,0,0.15), 0 0 10px rgba(255,255,255,0.6);
}
</style>
</head>
<body>
<div class="card">
    <div class="brand-badge">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="7" height="7" rx="1.5"></rect>
            <rect x="14" y="3" width="7" height="7" rx="1.5"></rect>
            <rect x="14" y="14" width="7" height="7" rx="1.5"></rect>
            <rect x="3" y="14" width="7" height="7" rx="1.5"></rect>
            <path d="M7 17v.01"></path><path d="M17 7v.01"></path><path d="M7 7v.01"></path>
        </svg>
    </div>

    <h1>TRÌNH TẠO MÃ QR ZALO</h1>
    <p class="sub">Tự động hóa định danh — iPhone 18 UA — Trích xuất IMEI & Cookie</p>

    <button id="btn-start" class="btn-snow" onclick="startQR()">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
        </svg>
        <span>KHỞI TẠO MÃ QR</span>
    </button>

    <div class="steps">
        <div class="steps-title">
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
            Quy trình thực hiện
        </div>
        <div class="step-item"><span class="step-badge">1</span> Nhấn nút khởi tạo mã QR phía trên</div>
        <div class="step-item"><span class="step-badge">2</span> Hệ thống sinh phiên duyệt web giả lập sau 15-30s</div>
        <div class="step-item"><span class="step-badge">3</span> Mở ứng dụng Zalo trên điện thoại và quét mã</div>
        <div class="step-item"><span class="step-badge">4</span> Nhấn xác nhận đồng ý đăng nhập trên điện thoại</div>
        <div class="step-item"><span class="step-badge">5</span> Dữ liệu IMEI và Cookie tự động đồng bộ lên màn hình</div>
    </div>
</div>

<script>
async function startQR() {
    const btn = document.getElementById('btn-start');
    btn.disabled = true;
    btn.innerHTML = '<svg viewBox="0 0 24 24" style="animation:spin 1s linear infinite;"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>ĐANG KHỞI TẠO...</span>';
    try {
        const r = await fetch('/api/qr/create', { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            btn.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg><span>HOÀN TẤT!</span>';
            setTimeout(function() { window.location.href = d.qr_url; }, 300);
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
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
.qr-container {
    position:relative;
    background:rgba(255,255,255,0.98);
    padding:10px; border-radius:24px;
    margin:0 auto 16px; max-width:340px; width:100%;
    border:2px solid rgba(255,255,255,0.9);
    box-shadow:
        0 20px 55px rgba(0,80,100,0.4),
        0 0 40px rgba(0,255,200,0.35),
        inset 0 1px 1px #ffffff;
    overflow:hidden;
}
.qr-container img {
    width:100%; height:auto; display:block; border-radius:16px;
}
.qr-label {
    display:flex; align-items:center; justify-content:center; gap:8px;
    text-align:center; font-size:12px;
    color:rgba(255,255,255,0.95);
    font-weight:700; margin-bottom:16px;
    text-shadow:0 2px 10px rgba(0,0,0,0.35);
}
.qr-label svg { width:16px; height:16px; stroke:currentColor; fill:none; stroke-width:2; }

.status {
    padding:14px; border-radius:18px;
    font-size:13px; font-weight:800;
    display:flex; align-items:center; justify-content:center; gap:10px;
    backdrop-filter:blur(20px);
    border:1px solid rgba(255,255,255,0.4);
    box-shadow:inset 0 1px 1px rgba(255,255,255,0.5);
    margin-bottom:14px;
}
.status.waiting {
    background:rgba(255,255,255,0.22);
    color:#ffffff;
}
.status.error {
    background:rgba(255,100,100,0.3);
    color:#ffe4e6;
}
.status.expired {
    background:rgba(200,200,200,0.25);
    color:#f1f5f9;
}
.status svg { width:18px; height:18px; stroke:currentColor; fill:none; stroke-width:2.2; }

@keyframes spin {
    0% { transform:rotate(0deg); }
    100% { transform:rotate(360deg); }
}
.spin { animation:spin 1.2s linear infinite; }

.result-wrap { text-align:left; }
.success-header {
    text-align:center; padding:22px;
    background:linear-gradient(135deg,rgba(255,255,255,0.25),rgba(255,255,255,0.1));
    border-radius:24px; margin-bottom:18px;
    border:1px solid rgba(255,255,255,0.6);
    backdrop-filter:blur(24px);
    box-shadow:inset 0 1px 1px rgba(255,255,255,0.8);
}
.success-badge {
    width:56px; height:56px; border-radius:50%; margin:0 auto 10px;
    background:#ffffff; display:flex; align-items:center; justify-content:center;
    box-shadow:0 10px 30px rgba(0,255,200,0.5), 0 0 20px rgba(255,255,255,0.8);
}
.success-badge svg { width:30px; height:30px; stroke:#052a36; stroke-width:2.6; fill:none; }
.success-title {
    font-size:17px; font-weight:900; color:#ffffff;
    text-shadow:0 2px 14px rgba(0,255,200,0.7);
    letter-spacing:0.3px;
}

.result {
    padding:16px; border-radius:18px;
    background:rgba(255,255,255,0.18);
    backdrop-filter:blur(24px);
    margin-bottom:12px;
    border:1px solid rgba(255,255,255,0.4);
    box-shadow:inset 0 1px 1px rgba(255,255,255,0.5);
}
.result-header {
    display:flex; align-items:center; gap:8px;
    font-size:11px; color:#cffff6;
    text-transform:uppercase; font-weight:900;
    letter-spacing:1px; margin-bottom:8px;
}
.result-header svg { width:15px; height:15px; stroke:currentColor; fill:none; stroke-width:2.2; }
.result .value {
    font-family:'SF Mono','Fira Code',monospace;
    font-size:11px; color:#ffffff;
    word-break:break-all; line-height:1.6; font-weight:600;
}
.result .value.scroll {
    max-height:140px; overflow-y:auto;
    background:rgba(0,0,0,0.22); padding:10px;
    border-radius:12px; margin-top:8px;
    border:1px solid rgba(255,255,255,0.15);
}

.hint {
    margin-top:14px; font-size:11px;
    color:rgba(255,255,255,0.85); text-align:center;
    font-weight:500;
}

.toast {
    position:fixed; bottom:24px; left:50%; transform:translateX(-50%);
    background:#ffffff; color:#052a36;
    padding:12px 24px; border-radius:50px;
    font-weight:800; font-size:13px; z-index:9999;
    border:1.5px solid #ffffff;
    box-shadow:0 16px 45px rgba(0,0,0,0.3), 0 0 25px rgba(255,255,255,0.8);
    display:flex; align-items:center; gap:8px;
}
.toast svg { width:18px; height:18px; stroke:#052a36; stroke-width:2.5; fill:none; }
</style>
</head>
<body>
<div class="card">
    <div class="brand-badge">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="7" height="7" rx="1.5"></rect>
            <rect x="14" y="3" width="7" height="7" rx="1.5"></rect>
            <rect x="14" y="14" width="7" height="7" rx="1.5"></rect>
            <rect x="3" y="14" width="7" height="7" rx="1.5"></rect>
        </svg>
    </div>

    <h1>XÁC THỰC MÃ QR</h1>
    <p class="sub">Mở ứng dụng Zalo trên điện thoại để quét mã bên dưới</p>

    <div id="qr-area">
        <div class="qr-container">
            <img id="qr-img" src="/qr-image/{{ session_id }}" alt="Mã QR">
        </div>
        <div class="qr-label">
            <svg viewBox="0 0 24 24"><rect x="5" y="2" width="14" height="20" rx="2" ry="2"></rect><line x1="12" y1="18" x2="12.01" y2="18"></line></svg>
            <span>Quét mã và xác nhận đồng ý trên điện thoại</span>
        </div>
    </div>

    <div id="status" class="status waiting">
        <svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg>
        <span>Đang chờ thao tác quét...</span>
    </div>

    <button id="btn-refresh" class="btn-snow" onclick="refreshQR()">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="23 4 23 10 17 10"></polyline>
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
        </svg>
        <span>LÀM MỚI MÃ QR</span>
    </button>

    <button id="btn-check" class="btn-snow btn-secondary" onclick="forceCheck()">
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
let lastQrVersion = 0;

async function check() {
    if (done) return;
    try {
        const r = await fetch('/api/qr/status/' + sessionId);
        const d = await r.json();
        const el = document.getElementById('status');

        if (d.qr_version && d.qr_version !== lastQrVersion) {
            lastQrVersion = d.qr_version;
            document.getElementById('qr-img').src = '/qr-image/' + sessionId + '?v=' + d.qr_version;
        }

        if (d.status === 'waiting' || d.status === 'creating') {
            el.className = 'status waiting';
            el.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>Đang chờ thao tác quét...</span>';
        } else if (d.status === 'done') {
            done = true;
            showResult(d);
        } else if (d.status === 'error') {
            el.className = 'status error';
            el.innerHTML = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg><span>' + (d.error || 'Lỗi xử lý') + '</span>';
            done = true;
        } else if (d.status === 'expired') {
            el.className = 'status expired';
            el.innerHTML = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg><span>Phiên làm việc đã hết hạn</span>';
            done = true;
        }
    } catch (e) {}
}

async function refreshQR() {
    const btn = document.getElementById('btn-refresh');
    btn.disabled = true;
    btn.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>ĐANG LÀM MỚI...</span>';
    try {
        const r = await fetch('/api/qr/refresh/' + sessionId, { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            document.getElementById('qr-img').src = '/qr-image/' + sessionId + '?v=' + d.qr_version;
            showToast('Đã làm mới mã QR thành công');
            lastQrVersion = d.qr_version;
        } else {
            alert('Lỗi: ' + (d.error || 'Không thể làm mới'));
        }
    } catch (e) {
        alert('Lỗi: ' + e.message);
    }
    btn.disabled = false;
    btn.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg><span>LÀM MỚI MÃ QR</span>';
}

async function forceCheck() {
    const btn = document.getElementById('btn-check');
    btn.disabled = true;
    btn.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.25"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>ĐANG TRÍCH XUẤT...</span>';
    try {
        const r = await fetch('/api/qr/force-extract/' + sessionId, { method: 'POST' });
        const d = await r.json();
        if (d.status === 'done') {
            done = true;
            showResult(d);
        } else {
            alert('Chưa có thông tin xác nhận. Vui lòng đợi 5-10 giây rồi thử lại.\\n' + (d.note || ''));
            btn.disabled = false;
            btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg><span>TÔI ĐÃ ĐỒNG Ý - LẤY COOKIE</span>';
        }
    } catch (e) {
        alert('Lỗi: ' + e.message);
        btn.disabled = false;
        btn.innerHTML = '<svg viewBox="0 0 24 24"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg><span>TÔI ĐÃ ĐỒNG Ý - LẤY COOKIE</span>';
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

    document.getElementById('qr-area').style.display = 'none';
    document.getElementById('btn-check').style.display = 'none';
    document.getElementById('btn-refresh').style.display = 'none';

    const el = document.getElementById('status');
    el.className = 'status';
    el.style.padding = '0';
    el.style.background = 'transparent';
    el.style.border = 'none';
    el.style.boxShadow = 'none';
    el.innerHTML =
        '<div class="result-wrap">' +
            '<div class="success-header">' +
                '<div class="success-badge">' +
                    '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg>' +
                '</div>' +
                '<div class="success-title">ĐĂNG NHẬP THÀNH CÔNG</div>' +
            '</div>' +
            '<div class="result">' +
                '<div class="result-header">' +
                    '<svg viewBox="0 0 24 24"><rect x="5" y="2" width="14" height="20" rx="2" ry="2"></rect><line x1="12" y1="18" x2="12.01" y2="18"></line></svg>' +
                    'Định Danh IMEI' +
                '</div>' +
                '<div class="value">' + escapeHtml(imei) + '</div>' +
            '</div>' +
            '<div class="result">' +
                '<div class="result-header">' +
                    '<svg viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>' +
                    'Dữ Liệu Cookie' +
                '</div>' +
                '<div class="value scroll">' + escapeHtml(cookiesStr) + '</div>' +
            '</div>' +
            '<button class="btn-snow" id="copy-all">' +
                '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>' +
                '<span>SAO CHÉP IMEI + COOKIE</span>' +
            '</button>' +
            '<button class="btn-snow btn-secondary" id="copy-imei">' +
                '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>' +
                '<span>CHỈ SAO CHÉP IMEI</span>' +
            '</button>' +
            '<button class="btn-snow btn-secondary" id="copy-ck">' +
                '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>' +
                '<span>CHỈ SAO CHÉP COOKIE</span>' +
            '</button>' +
            '<div class="hint">Đã sẵn sàng để sử dụng cho phiên tiếp theo</div>' +
        '</div>';

    document.getElementById('copy-all').onclick = function() { copyText(combo); };
    document.getElementById('copy-imei').onclick = function() { copyText(imei); };
    document.getElementById('copy-ck').onclick = function() { copyText(cookiesStr); };
}

function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function() {
            showToast('Đã sao chép vào bộ nhớ tạm');
        }).catch(function() { fallbackCopy(text); });
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
    try { document.execCommand('copy'); showToast('Đã sao chép vào bộ nhớ tạm'); }
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
    setTimeout(function() { t.remove(); }, 2200);
}

setInterval(check, 2000);
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
<title>Hết Hạn Phiên — Liquid Glass</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
.expired-badge {
    width:72px; height:72px; border-radius:50%; margin:0 auto 18px;
    background:#ffffff; display:flex; align-items:center; justify-content:center;
    box-shadow:0 12px 35px rgba(0,0,0,0.2), 0 0 25px rgba(255,255,255,0.8);
}
.expired-badge svg { width:36px; height:36px; stroke:#052a36; stroke-width:2.2; fill:none; }
.expired-title {
    color:#ffffff; margin-bottom:12px; font-size:22px; font-weight:900;
    text-shadow:0 2px 14px rgba(0,255,200,0.6);
    letter-spacing:-0.3px;
}
.expired-text {
    color:rgba(255,255,255,0.85); font-size:13px; margin-bottom:24px;
    text-shadow:0 1px 8px rgba(0,0,0,0.3);
}
</style>
</head>
<body>
<div class="card">
    <div class="expired-badge">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
    </div>
    <div class="expired-title">PHIÊN LÀM VIỆC ĐÃ HẾT HẠN</div>
    <div class="expired-text">Vui lòng khởi tạo phiên đăng nhập mới để tiếp tục</div>
    <a href="/" class="btn-snow">
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
