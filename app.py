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
    """Tự động kiểm tra và tải trình duyệt Chromium nếu môi trường chưa có."""
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
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


app = Flask(__name__)


# ============================================================
# ZALO QR CORE ENGINE — FIX ERR_ABORTED & RENDER COMPATIBILITY
# ============================================================
class ZaloQR:
    def __init__(self):
        self.browser = None
        self.playwright = None

    async def _launch_browser(self):
        # Đã loại bỏ --single-process và --no-zygote để tránh sập Chromium trên Linux
        return await self.playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
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
                print("[QR] Đang kích hoạt tải Chromium khẩn cấp...")
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, ensure_playwright_browsers)
                self.browser = await self._launch_browser()
            else:
                raise e

    async def _capture_qr(self, page):
        try:
            # Ưu tiên tìm vùng chứa mã QR thật để chụp rõ nét nhất
            for selector in [".qrcode-img", "#app .qrcode", "canvas", "img[alt*='QR']", ".login-box"]:
                el = await page.query_selector(selector)
                if el:
                    box = await el.bounding_box()
                    if box and box["width"] > 80 and box["height"] > 80:
                        return await el.screenshot()

            return await page.screenshot(full_page=False)
        except Exception as e:
            print("[QR] Lỗi chụp màn hình: " + str(e))
            return None

    async def create_qr(self, session_id):
        try:
            await self.start()
            ctx = await self.browser.new_context(
                viewport={"width": 393, "height": 852},
                device_scale_factor=2,
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

            print("[QR] Đang truy cập chat.zalo.me...")
            # Sửa wait_until sang domcontentloaded và bọc lỗi ERR_ABORTED do chuyển trang
            try:
                await page.goto(
                    "https://chat.zalo.me/",
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
            except Exception as e:
                if "ERR_ABORTED" in str(e):
                    print("[QR] Bỏ qua chuyển hướng ERR_ABORTED")
                else:
                    raise e

            await asyncio.sleep(4)

            # Tìm và kích hoạt tab Quét mã QR nếu chưa hiển thị
            try:
                btn = await page.wait_for_selector("text=/QR|Quét mã/i", timeout=8000)
                if btn:
                    await btn.click()
                    print("[QR] Đã chọn tab QR")
            except Exception:
                pass

            await asyncio.sleep(4)

            png = await self._capture_qr(page)
            if not png:
                raise Exception("Không thể xuất ảnh mã QR")

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

            print("[QR] Phiên " + session_id + " đã sẵn sàng")

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
            print("[QR] Lỗi khởi tạo QR: " + str(e))
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
                    print("[QR] Đã xác thực thành công qua ZPW_SEK")
                    await asyncio.sleep(2)
                    return await self._extract(session_id, page)

                url = page.url
                if ("chat.zalo.me" in url and "/login" not in url and "qr" not in url.lower()):
                    await asyncio.sleep(2)
                    return await self._extract(session_id, page)

            except Exception:
                pass

            await asyncio.sleep(1.5)

        data = sess_load(session_id) or {}
        data["status"] = "expired"
        sess_save(session_id, data)
        await self._cleanup(session_id)

    async def _extract(self, session_id, page):
        try:
            await asyncio.sleep(4)
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
                if info.get("browser"):
                    await info["browser"].close()
                if info.get("playwright"):
                    await info["playwright"].stop()
            except Exception:
                pass


ACTIVE_PAGES = {}


# ============================================================
# API CONTROLLER
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
            "note": "Phiên đã đóng",
        })

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

        def run():
            result["data"] = asyncio.run(_read())

        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=10)

        if "data" not in result:
            return jsonify({"status": "waiting", "note": "Hết thời gian chờ"})

        ck, imei = result["data"]
        if ck.get("zpw_sek"):
            if not imei:
                imei = gen_imei()
            data["status"] = "done"
            data["cookies"] = ck
            data["imei"] = imei
            sess_save(session_id, data)
            return jsonify({"status": "done", "imei": imei, "cookies": ck})

        return jsonify({"status": "waiting", "note": "Chưa phát hiện token", "cookies_count": len(ck)})

    except Exception as e:
        return jsonify({"status": "waiting", "error": str(e)})


@app.route("/api/qr/refresh/<session_id>", methods=["POST"])
def api_qr_refresh(session_id):
    info = ACTIVE_PAGES.get(session_id)
    if not info:
        return jsonify({"error": "Phiên đã hoàn tất hoặc đóng"}), 404

    try:
        async def _refresh():
            page = info["page"]
            await asyncio.sleep(1)
            for selector in [".qrcode-img", "#app .qrcode", "canvas", "img[alt*='QR']"]:
                el = await page.query_selector(selector)
                if el:
                    return await el.screenshot()
            return await page.screenshot(full_page=False)

        result = {}

        def run():
            result["png"] = asyncio.run(_refresh())

        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=15)

        if "png" not in result or not result["png"]:
            return jsonify({"error": "Không thể chụp lại QR"}), 500

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
    return jsonify({"status": "ok", "version": "29.0", "theme": "pure-snow-white"})


# ============================================================
# PURE SNOW-WHITE MINIMALIST ARCHITECTURE (B&W GLASS)
# ============================================================
SHARED_CSS = """
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html { -webkit-text-size-adjust:100%; touch-action:manipulation; }
body {
    font-family:-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    min-height:100vh; min-height:100dvh;
    display:flex; align-items:center; justify-content:center;
    background:#0a0a0c;
    background-image:
        radial-gradient(at 0% 0%, #1e1e24 0, transparent 50%),
        radial-gradient(at 100% 100%, #17171c 0, transparent 50%),
        radial-gradient(circle at 50% 50%, #141418 0%, #09090b 100%);
    padding:20px;
    color:#09090b;
}

/* KHUNG CARD CHÍNH - MÀU TRẮNG TUYẾT CAO CẤP */
.card {
    position:relative;
    background:#ffffff;
    padding:44px 30px 40px;
    border-radius:36px;
    box-shadow:
        0 30px 80px rgba(0, 0, 0, 0.45),
        0 1px 3px rgba(0, 0, 0, 0.1),
        inset 0 1px 1px #ffffff;
    text-align:center; max-width:480px; width:100%;
    animation:cardIn 0.5s cubic-bezier(0.16,1,0.3,1);
}
@keyframes cardIn {
    from { opacity:0; transform:translateY(24px) scale(0.97); }
    to { opacity:1; transform:translateY(0) scale(1); }
}

/* BIỂU TƯỢNG ĐẦU TRANG */
.brand-icon {
    display:inline-flex; align-items:center; justify-content:center;
    width:64px; height:64px; border-radius:24px;
    background:#f4f4f5;
    border:1px solid #e4e4e7;
    margin-bottom:18px;
    box-shadow:0 4px 16px rgba(0,0,0,0.04);
}
.brand-icon svg { width:32px; height:32px; stroke:#09090b; fill:none; stroke-width:2; }

h1 {
    color:#09090b;
    margin-bottom:8px;
    font-size:22px; font-weight:800;
    letter-spacing:-0.5px;
}
p.sub {
    color:#71717a;
    margin-bottom:30px; font-size:13px; font-weight:500;
    line-height:1.5;
}

/* NÚT BẤM VIÊN THUỐC TRẮNG TUYẾT (PILL-SHAPED SNOW BUTTONS) */
.btn-pill {
    position:relative;
    width:100%; padding:17px 24px; border-radius:9999px;
    background:#ffffff;
    color:#09090b; font-size:14px; font-weight:700;
    font-family:inherit; cursor:pointer; letter-spacing:0.3px;
    border:1.5px solid #e4e4e7;
    box-shadow:
        0 6px 20px rgba(0, 0, 0, 0.06),
        inset 0 1px 1px #ffffff;
    display:inline-flex; align-items:center; justify-content:center; gap:10px;
    text-decoration:none;
    transition:all 0.25s cubic-bezier(0.16,1,0.3,1);
}
.btn-pill:hover {
    background:#fcfcfc;
    border-color:#18181b;
    transform:translateY(-2px);
    box-shadow:0 12px 28px rgba(0, 0, 0, 0.1);
}
.btn-pill:active {
    transform:translateY(0);
    box-shadow:0 4px 12px rgba(0, 0, 0, 0.05);
}
.btn-pill:disabled { opacity:0.6; cursor:wait; }
.btn-pill svg { width:18px; height:18px; stroke:#09090b; stroke-width:2.2; fill:none; }

.btn-secondary {
    margin-top:12px;
    background:#f8fafc;
    border-color:#e2e8f0;
}
"""


HTML_HOME = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Đăng Nhập Zalo — Pure Minimalist</title>
<style>
""" + SHARED_CSS + """
.guide-box {
    margin-top:28px; text-align:left;
    background:#fafafa;
    border:1px solid #f4f4f5;
    padding:22px; border-radius:24px;
}
.guide-title {
    display:flex; align-items:center; gap:8px;
    color:#09090b; font-size:11px; font-weight:800;
    letter-spacing:1px; margin-bottom:14px; text-transform:uppercase;
}
.guide-title svg { width:15px; height:15px; stroke:#09090b; fill:none; stroke-width:2.5; }
.guide-item {
    display:flex; align-items:center; gap:12px;
    color:#52525b; font-size:13px; font-weight:500;
    margin-bottom:11px; line-height:1.4;
}
.guide-badge {
    flex-shrink:0; width:22px; height:22px; border-radius:50%;
    background:#ffffff; color:#09090b; font-size:11px; font-weight:800;
    display:inline-flex; align-items:center; justify-content:center;
    border:1px solid #e4e4e7;
    box-shadow:0 2px 4px rgba(0,0,0,0.03);
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

    <h1>XÁC THỰC MÃ QR</h1>
    <p class="sub">Khởi tạo phiên duyệt web tự động và trích xuất dữ liệu đăng nhập</p>

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
        <div class="guide-item"><span class="guide-badge">1</span> Nhấn nút khởi tạo mã QR phía trên</div>
        <div class="guide-item"><span class="guide-badge">2</span> Đợi hệ thống tải trang đăng nhập trong giây lát</div>
        <div class="guide-item"><span class="guide-badge">3</span> Sử dụng app Zalo trên điện thoại để quét mã</div>
        <div class="guide-item"><span class="guide-badge">4</span> Xác nhận Cho phép đăng nhập trên màn hình máy</div>
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
            btn.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg><span>HOÀN TẤT</span>';
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
<title>Quét QR — Pure Minimalist</title>
<style>
""" + SHARED_CSS + """
.qr-frame {
    position:relative;
    background:#ffffff;
    padding:16px; border-radius:28px;
    margin:0 auto 16px; max-width:320px; width:100%;
    border:1.5px solid #e4e4e7;
    box-shadow:0 12px 30px rgba(0, 0, 0, 0.04);
}
.qr-frame img {
    width:100%; height:auto; display:block; border-radius:18px;
    background:#fafafa; min-height:220px; object-fit:contain;
}
.qr-desc {
    display:flex; align-items:center; justify-content:center; gap:8px;
    font-size:12px; color:#71717a; font-weight:600; margin-bottom:20px;
}
.qr-desc svg { width:15px; height:15px; stroke:#71717a; fill:none; stroke-width:2; }

.status-badge {
    padding:12px 18px; border-radius:9999px;
    font-size:13px; font-weight:600;
    display:inline-flex; align-items:center; justify-content:center; gap:8px;
    background:#f4f4f5; color:#09090b;
    margin-bottom:18px; width:100%;
    border:1px solid #e4e4e7;
}
.status-badge.error {
    background:#fef2f2; color:#991b1b; border-color:#fecaca;
}
.status-badge svg { width:16px; height:16px; stroke:currentColor; fill:none; stroke-width:2.2; }

@keyframes spin {
    0% { transform:rotate(0deg); }
    100% { transform:rotate(360deg); }
}
.spin { animation:spin 1s linear infinite; }

.result-card { text-align:left; }
.success-banner {
    text-align:center; padding:18px;
    background:#f4f4f5; border-radius:24px; margin-bottom:18px;
    border:1px solid #e4e4e7;
}
.success-badge-icon {
    width:48px; height:48px; border-radius:50%; margin:0 auto 10px;
    background:#ffffff; display:flex; align-items:center; justify-content:center;
    box-shadow:0 4px 14px rgba(0, 0, 0, 0.06);
    border:1px solid #e4e4e7;
}
.success-badge-icon svg { width:24px; height:24px; stroke:#09090b; stroke-width:2.5; fill:none; }
.success-banner-title {
    font-size:16px; font-weight:800; color:#09090b;
}

.data-box {
    padding:14px 18px; border-radius:20px;
    background:#fafafa; margin-bottom:12px;
    border:1px solid #e4e4e7;
}
.data-label {
    display:flex; align-items:center; gap:7px;
    font-size:11px; color:#71717a; text-transform:uppercase;
    font-weight:800; letter-spacing:0.8px; margin-bottom:6px;
}
.data-label svg { width:14px; height:14px; stroke:#71717a; fill:none; stroke-width:2; }
.data-val {
    font-family:-apple-system, BlinkMacSystemFont, "SF Mono", monospace;
    font-size:12px; color:#09090b; word-break:break-all;
    font-weight:600; line-height:1.5;
}
.data-val.scrollable {
    max-height:110px; overflow-y:auto;
    background:#ffffff; padding:10px; border-radius:12px;
    border:1px solid #f4f4f5; margin-top:6px;
}

.toast {
    position:fixed; bottom:24px; left:50%; transform:translateX(-50%);
    background:#09090b; color:#ffffff;
    padding:12px 24px; border-radius:9999px;
    font-weight:600; font-size:13px; z-index:9999;
    box-shadow:0 12px 30px rgba(0,0,0,0.3);
    display:flex; align-items:center; gap:8px;
}
.toast svg { width:16px; height:16px; stroke:#ffffff; stroke-width:2.5; fill:none; }
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
    <p class="sub">Mở ứng dụng Zalo trên điện thoại và quét mã bên dưới</p>

    <div id="qr-block">
        <div class="qr-frame">
            <img id="qr-img" src="/qr-image/{{ session_id }}" alt="Mã QR">
        </div>
        <div class="qr-desc">
            <svg viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path></svg>
            <span>Quét mã và xác nhận an toàn trên thiết bị</span>
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
            el.className = 'status-badge';
            el.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.2"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>Đang chờ thao tác quét...</span>';
        } else if (d.status === 'done') {
            done = true;
            showResult(d);
        } else if (d.status === 'error') {
            el.className = 'status-badge error';
            el.innerHTML = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg><span>' + (d.error || 'Lỗi thao tác') + '</span>';
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
    btn.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.2"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>ĐANG LÀM MỚI...</span>';
    try {
        const r = await fetch('/api/qr/refresh/' + sessionId, { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            document.getElementById('qr-img').src = '/qr-image/' + sessionId + '?v=' + d.qr_version;
            showToast('Đã làm mới mã QR');
            lastQrVersion = d.qr_version;
        } else {
            alert('Lỗi: ' + (d.error || 'Không rõ'));
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
    btn.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.2"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>ĐANG KIỂM TRA...</span>';
    try {
        const r = await fetch('/api/qr/force-extract/' + sessionId, { method: 'POST' });
        const d = await r.json();
        if (d.status === 'done') {
            done = true;
            showResult(d);
        } else {
            alert('Chưa có xác thực thành công. Vui lòng thử lại sau vài giây.\\n' + (d.note || ''));
            btn.disabled = false;
            btn.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg><span>TÔI ĐÃ ĐỒNG Ý - LẤY COOKIE</span>';
        }
    } catch (e) {
        alert('Lỗi: ' + e.message);
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
<title>Hết Hạn Phiên</title>
<style>
""" + SHARED_CSS + """
.expired-icon-wrap {
    width:64px; height:64px; border-radius:50%; margin:0 auto 18px;
    background:#fafafa; display:flex; align-items:center; justify-content:center;
    border:1px solid #e4e4e7;
}
.expired-icon-wrap svg { width:32px; height:32px; stroke:#71717a; stroke-width:2; fill:none; }
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
