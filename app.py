import os
import io
import json
import time
import uuid
import asyncio
import threading
import random
import string

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
# ZALO QR LOGIN — iPhone 18 UA — Render-ready
# ============================================================
class ZaloQR:
    def __init__(self):
        self.browser = None
        self.playwright = None

    async def start(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
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
                raise Exception("Không chụp được ảnh")

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
                    print("[QR] PHAT HIEN ZPW_SEK")
                    await asyncio.sleep(3)
                    return await self._extract(session_id, page)

                url = page.url
                if ("chat.zalo.me" in url and "/login" not in url
                        and "qr" not in url.lower()):
                    print("[QR] URL doi: " + url)
                    await asyncio.sleep(3)
                    return await self._extract(session_id, page)

            except Exception as e:
                print("[QR] Poll error: " + str(e))

            await asyncio.sleep(1.5)

        print("[QR] Session het han: " + session_id)
        data = sess_load(session_id) or {}
        data["status"] = "expired"
        sess_save(session_id, data)
        await self._cleanup(session_id)

    async def _extract(self, session_id, page):
        try:
            print("[QR] Extract - Doi 5s")
            await asyncio.sleep(5)

            cookies = await page.context.cookies()
            ck = {}
            for c in cookies:
                if "zalo" in c.get("domain", ""):
                    ck[c["name"]] = c["value"]

            print("[QR] Cookies: " + str(list(ck.keys())))

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

            print("[QR] DONE - IMEI: " + imei)
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
        return jsonify({"error": "Session không tồn tại"}), 404
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
        return jsonify({"error": "Session không tồn tại"}), 404

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
            "note": "Session đã đóng",
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
            return jsonify({"status": "waiting", "note": "Timeout"})

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
            await asyncio.sleep(1)
            return await page.screenshot(full_page=False)

        result = {}

        def run():
            result["png"] = asyncio.run(_refresh())

        t = threading.Thread(target=run)
        t.start()
        t.join(timeout=15)

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
    return render_template_string(HTML_QR, session_id=session_id)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": "28.1", "ua": "iPhone-18"})


# ============================================================
# LIQUID GLASS UI v28.1 — XANH NGỌC BÍCH
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
    background:rgba(255,255,255,0.12);
    backdrop-filter:blur(40px) saturate(200%);
    -webkit-backdrop-filter:blur(40px) saturate(200%);
    padding:38px 24px 34px;
    border-radius:36px;
    border:1.5px solid rgba(255,255,255,0.35);
    box-shadow:
        0 30px 90px rgba(0,60,80,0.45),
        inset 0 1px 1px rgba(255,255,255,0.6),
        inset 0 -1px 1px rgba(0,255,200,0.15);
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
h1 {
    color:#ffffff;
    margin-bottom:8px;
    font-size:25px; font-weight:900;
    letter-spacing:-0.5px;
    text-shadow:0 4px 24px rgba(0,255,200,0.65), 0 0 40px rgba(0,200,255,0.4);
    background:linear-gradient(135deg,#ffffff,#a7ffeb,#80deea);
    -webkit-background-clip:text;
    -webkit-text-fill-color:transparent;
    background-clip:text;
}
p.sub {
    color:rgba(255,255,255,0.85);
    margin-bottom:24px; font-size:13px; font-weight:500;
    text-shadow:0 2px 10px rgba(0,0,0,0.25);
}
"""


HTML_HOME = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Tạo QR — Liquid Glass</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
.btn-start {
    position:relative;
    width:100%; padding:20px; border:none; border-radius:22px;
    background:linear-gradient(135deg,rgba(0,255,200,0.9),rgba(0,180,255,0.9));
    color:#003d4d; font-size:17px; font-weight:900;
    font-family:inherit; cursor:pointer; letter-spacing:0.5px;
    border:1.5px solid rgba(255,255,255,0.6);
    box-shadow:
        0 20px 50px rgba(0,200,180,0.55),
        inset 0 1px 1px rgba(255,255,255,0.9),
        inset 0 -2px 6px rgba(0,120,140,0.25);
    transition:all 0.35s cubic-bezier(0.16,1,0.3,1);
    overflow:hidden;
}
.btn-start::after {
    content:'';
    position:absolute; top:0; left:-100%;
    width:100%; height:100%;
    background:linear-gradient(90deg,transparent,rgba(255,255,255,0.6),transparent);
    transition:left 0.6s;
}
.btn-start:hover::after { left:100%; }
.btn-start:hover {
    transform:translateY(-5px) scale(1.02);
    box-shadow:0 28px 65px rgba(0,200,180,0.7);
}
.btn-start:active { transform:translateY(-1px) scale(0.99); }
.btn-start:disabled { opacity:0.75; cursor:wait; }
.steps {
    margin-top:26px; text-align:left;
    background:rgba(255,255,255,0.14);
    backdrop-filter:blur(20px);
    border:1px solid rgba(255,255,255,0.3);
    padding:20px; border-radius:20px;
    font-size:13px; color:#ffffff; line-height:2;
    text-shadow:0 1px 6px rgba(0,0,0,0.3);
    box-shadow:inset 0 1px 1px rgba(255,255,255,0.4);
}
.steps b { color:#a7ffeb; font-weight:900; }
</style>
</head>
<body>
<div class="card">
    <h1>☎️ TẠO MÃ QR ZALO</h1>
    <p class="sub">✨ Liquid Glass v28.1 — iPhone 18 UA — Quét QR lấy IMEI + Cookie</p>
    <button id="btn-start" class="btn-start" onclick="startQR()">🚀 TẠO MÃ QR</button>
    <div class="steps">
        <b>HƯỚNG DẪN:</b><br>
        1️⃣ Bấm nút trên<br>
        2️⃣ Đợi 15-30s → hiện mã QR<br>
        3️⃣ Mở app Zalo → QR → Quét<br>
        4️⃣ Đồng ý trên điện thoại<br>
        5️⃣ Web tự hiện IMEI + Cookie
    </div>
</div>
<script>
async function startQR() {
    const btn = document.getElementById('btn-start');
    btn.disabled = true;
    btn.innerHTML = '⏳ ĐANG TẠO QR...';
    try {
        const r = await fetch('/api/qr/create', { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            btn.innerHTML = '✅ XONG!';
            setTimeout(function() { window.location.href = d.qr_url; }, 300);
        } else {
            alert('Lỗi: ' + (d.error || 'Không rõ'));
            btn.disabled = false;
            btn.innerHTML = '🚀 TẠO MÃ QR';
        }
    } catch (e) {
        alert('Lỗi: ' + e.message);
        btn.disabled = false;
        btn.innerHTML = '🚀 TẠO MÃ QR';
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
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
.qr-container {
    position:relative;
    background:rgba(255,255,255,0.95);
    padding:10px; border-radius:22px;
    margin:0 auto 16px; max-width:340px; width:100%;
    border:2px solid rgba(0,255,200,0.6);
    box-shadow:
        0 20px 55px rgba(0,120,140,0.5),
        0 0 40px rgba(0,255,200,0.35),
        inset 0 1px 1px rgba(255,255,255,0.9);
    overflow:hidden;
}
.qr-container img {
    width:100%; height:auto; display:block; border-radius:14px;
}
.qr-label {
    text-align:center; font-size:12px;
    color:rgba(255,255,255,0.95);
    font-weight:800; margin-bottom:16px;
    text-shadow:0 2px 10px rgba(0,0,0,0.35);
    letter-spacing:0.3px;
}
.status {
    padding:15px; border-radius:16px;
    font-size:13px; font-weight:800;
    display:flex; align-items:center; justify-content:center; gap:8px;
    backdrop-filter:blur(20px);
    border:1px solid rgba(255,255,255,0.4);
    box-shadow:inset 0 1px 1px rgba(255,255,255,0.5);
}
.status.waiting {
    background:rgba(255,240,150,0.35);
    color:#fff8e1;
    text-shadow:0 1px 6px rgba(180,120,0,0.6);
}
.status.error {
    background:rgba(255,120,120,0.35);
    color:#ffe4e6;
    text-shadow:0 1px 6px rgba(150,0,0,0.5);
}
.status.expired {
    background:rgba(200,200,200,0.3);
    color:#f1f5f9;
}
.confirm-btn {
    width:100%; margin-top:12px; padding:16px;
    background:linear-gradient(135deg,rgba(0,255,170,0.92),rgba(0,200,140,0.92));
    color:#003d2b; border:1.5px solid rgba(255,255,255,0.6);
    border-radius:16px;
    font-size:13px; font-weight:900;
    font-family:inherit; cursor:pointer; letter-spacing:0.4px;
    box-shadow:
        0 14px 35px rgba(0,220,160,0.55),
        inset 0 1px 1px rgba(255,255,255,0.85);
    transition:all 0.3s cubic-bezier(0.16,1,0.3,1);
}
.confirm-btn:hover { transform:translateY(-3px) scale(1.01); }
.confirm-btn:active { transform:translateY(0) scale(0.99); }
.confirm-btn.refresh {
    background:linear-gradient(135deg,rgba(255,200,80,0.9),rgba(255,150,40,0.9));
    color:#4a2800;
    box-shadow:0 14px 35px rgba(255,160,40,0.5), inset 0 1px 1px rgba(255,255,255,0.85);
}
.result-wrap { text-align:left; }
.success-header {
    text-align:center; padding:20px;
    background:linear-gradient(135deg,rgba(0,255,180,0.35),rgba(0,220,255,0.3));
    border-radius:22px; margin-bottom:18px;
    border:1px solid rgba(255,255,255,0.5);
    backdrop-filter:blur(20px);
    box-shadow:inset 0 1px 1px rgba(255,255,255,0.6);
}
.success-icon { font-size:48px; margin-bottom:8px; filter:drop-shadow(0 0 20px rgba(0,255,200,0.8)); }
.success-title {
    font-size:17px; font-weight:900; color:#ffffff;
    text-shadow:0 2px 14px rgba(0,255,200,0.7);
    letter-spacing:0.3px;
}
.result {
    padding:15px; border-radius:16px;
    background:rgba(255,255,255,0.18);
    backdrop-filter:blur(24px);
    margin-bottom:12px;
    border:1px solid rgba(255,255,255,0.35);
    box-shadow:inset 0 1px 1px rgba(255,255,255,0.5);
}
.result .label {
    font-size:10px; color:#a7ffeb;
    text-transform:uppercase; font-weight:900;
    letter-spacing:1.5px; margin-bottom:7px;
    text-shadow:0 1px 6px rgba(0,255,200,0.5);
}
.result .value {
    font-family:'SF Mono','Fira Code',monospace;
    font-size:11px; color:#ffffff;
    word-break:break-all; line-height:1.6; font-weight:600;
    text-shadow:0 1px 4px rgba(0,0,0,0.3);
}
.result .value.scroll {
    max-height:150px; overflow-y:auto;
    background:rgba(0,0,0,0.2); padding:10px;
    border-radius:10px; margin-top:8px;
    border:1px solid rgba(255,255,255,0.15);
}
.copy-btn {
    width:100%; padding:15px; border:none; border-radius:14px;
    color:white; font-size:13px; font-weight:900;
    font-family:inherit; cursor:pointer; margin-top:10px;
    letter-spacing:0.4px;
    border:1.5px solid rgba(255,255,255,0.55);
    transition:all 0.3s cubic-bezier(0.16,1,0.3,1);
    box-shadow:inset 0 1px 1px rgba(255,255,255,0.5);
}
.copy-btn:hover { transform:translateY(-3px) scale(1.01); }
.copy-btn:active { transform:translateY(0) scale(0.99); }
.copy-btn.green {
    background:linear-gradient(135deg,rgba(0,255,170,0.9),rgba(0,200,140,0.9));
    color:#003d2b;
    box-shadow:0 12px 30px rgba(0,220,160,0.5), inset 0 1px 1px rgba(255,255,255,0.85);
}
.copy-btn.purple {
    background:linear-gradient(135deg,rgba(180,130,255,0.9),rgba(140,80,240,0.9));
    box-shadow:0 12px 30px rgba(150,80,240,0.5), inset 0 1px 1px rgba(255,255,255,0.5);
}
.copy-btn.blue {
    background:linear-gradient(135deg,rgba(80,190,255,0.9),rgba(30,140,240,0.9));
    box-shadow:0 12px 30px rgba(30,140,240,0.5), inset 0 1px 1px rgba(255,255,255,0.5);
}
.hint {
    margin-top:12px; font-size:11px;
    color:rgba(255,255,255,0.8); text-align:center;
    text-shadow:0 1px 6px rgba(0,0,0,0.3);
}
.toast {
    position:fixed; bottom:24px; left:50%; transform:translateX(-50%);
    background:linear-gradient(135deg,rgba(0,255,180,0.95),rgba(0,200,140,0.95));
    backdrop-filter:blur(20px);
    color:#003d2b; padding:14px 28px; border-radius:50px;
    font-weight:900; font-size:13px; z-index:9999;
    border:1.5px solid rgba(255,255,255,0.7);
    box-shadow:0 16px 40px rgba(0,220,160,0.6), inset 0 1px 1px rgba(255,255,255,0.9);
    animation:toastIn 0.35s cubic-bezier(0.16,1,0.3,1);
}
@keyframes toastIn {
    from { opacity:0; transform:translate(-50%,30px) scale(0.9); }
    to { opacity:1; transform:translate(-50%,0) scale(1); }
}
</style>
</head>
<body>
<div class="card">
    <h1>☎️ QUÉT QR ZALO</h1>
    <p class="sub">✨ Liquid Glass v28.1 — iPhone 18 — Mở app Zalo → QR → Quét</p>

    <div id="qr-area">
        <div class="qr-container">
            <img id="qr-img" src="/qr-image/{{ session_id }}" alt="QR">
        </div>
        <div class="qr-label">📱 Quét → Đồng ý trên điện thoại</div>
    </div>

    <div id="status" class="status waiting">
        <span>⏳</span> <span>Đang chờ quét...</span>
    </div>

    <button id="btn-refresh" class="confirm-btn refresh" onclick="refreshQR()">
        🔄 LÀM MỚI QR
    </button>

    <button id="btn-check" class="confirm-btn" onclick="forceCheck()">
        ✅ TÔI ĐÃ ĐỒNG Ý - LẤY COOKIE
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
            el.innerHTML = '<span>⏳</span> <span>Đang chờ quét...</span>';
        } else if (d.status === 'done') {
            done = true;
            showResult(d);
        } else if (d.status === 'error') {
            el.className = 'status error';
            el.innerHTML = '<span>❌</span> <span>' + (d.error || 'Lỗi') + '</span>';
            done = true;
        } else if (d.status === 'expired') {
            el.className = 'status expired';
            el.innerHTML = '<span>⏰</span> <span>QR hết hạn</span>';
            done = true;
        }
    } catch (e) {}
}

async function refreshQR() {
    const btn = document.getElementById('btn-refresh');
    btn.disabled = true;
    btn.innerHTML = '⏳ Đang lấy QR mới...';
    try {
        const r = await fetch('/api/qr/refresh/' + sessionId, { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            document.getElementById('qr-img').src = '/qr-image/' + sessionId + '?v=' + d.qr_version;
            showToast('✅ Đã lấy QR mới!');
            lastQrVersion = d.qr_version;
        } else {
            alert('Lỗi: ' + (d.error || 'Không rõ'));
        }
    } catch (e) {
        alert('Lỗi: ' + e.message);
    }
    btn.disabled = false;
    btn.innerHTML = '🔄 LÀM MỚI QR';
}

async function forceCheck() {
    const btn = document.getElementById('btn-check');
    btn.disabled = true;
    btn.innerHTML = '⏳ Đang lấy cookie...';
    try {
        const r = await fetch('/api/qr/force-extract/' + sessionId, { method: 'POST' });
        const d = await r.json();
        if (d.status === 'done') {
            done = true;
            showResult(d);
        } else {
            alert('Chưa có cookie. Đợi 5-10 giây rồi thử lại.\\n' + (d.note || ''));
            btn.disabled = false;
            btn.innerHTML = '✅ TÔI ĐÃ ĐỒNG Ý - LẤY COOKIE';
        }
    } catch (e) {
        alert('Lỗi: ' + e.message);
        btn.disabled = false;
        btn.innerHTML = '✅ TÔI ĐÃ ĐỒNG Ý - LẤY COOKIE';
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
                '<div class="success-icon">✅</div>' +
                '<div class="success-title">ĐĂNG NHẬP THÀNH CÔNG!</div>' +
            '</div>' +
            '<div class="result">' +
                '<div class="label">📞 IMEI</div>' +
                '<div class="value">' + escapeHtml(imei) + '</div>' +
            '</div>' +
            '<div class="result">' +
                '<div class="label">🍪 COOKIE</div>' +
                '<div class="value scroll">' + escapeHtml(cookiesStr) + '</div>' +
            '</div>' +
            '<button class="copy-btn green" id="copy-all">📋 COPY CẢ IMEI + COOKIE</button>' +
            '<button class="copy-btn blue" id="copy-imei">📞 CHỈ COPY IMEI</button>' +
            '<button class="copy-btn purple" id="copy-ck">🍪 CHỈ COPY COOKIE</button>' +
            '<div class="hint">Copy → paste vào web spam</div>' +
        '</div>';

    document.getElementById('copy-all').onclick = function() { copyText(combo); };
    document.getElementById('copy-imei').onclick = function() { copyText(imei); };
    document.getElementById('copy-ck').onclick = function() { copyText(cookiesStr); };
}

function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function() {
            showToast('✅ Đã copy!');
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
    try { document.execCommand('copy'); showToast('✅ Đã copy!'); }
    catch (e) { prompt('Copy:', text); }
    document.body.removeChild(ta);
}

function showToast(msg) {
    const old = document.querySelector('.toast');
    if (old) old.remove();
    const t = document.createElement('div');
    t.className = 'toast';
    t.innerHTML = msg;
    document.body.appendChild(t);
    setTimeout(function() { t.remove(); }, 2000);
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
<title>Hết hạn — Liquid Glass</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
.expired-icon { font-size:68px; margin-bottom:20px; filter:drop-shadow(0 0 30px rgba(0,255,200,0.7)); }
.expired-title {
    color:#ffffff; margin-bottom:14px; font-size:23px; font-weight:900;
    text-shadow:0 2px 14px rgba(0,255,200,0.6);
    letter-spacing:-0.3px;
}
.expired-text {
    color:rgba(255,255,255,0.85); font-size:13px; margin-bottom:24px;
    text-shadow:0 1px 8px rgba(0,0,0,0.3);
}
.expired-btn {
    display:inline-block; padding:16px 32px;
    background:linear-gradient(135deg,rgba(0,255,200,0.9),rgba(0,180,255,0.9));
    color:#003d4d; border-radius:16px; text-decoration:none;
    font-weight:900; font-size:13px; letter-spacing:0.5px;
    border:1.5px solid rgba(255,255,255,0.65);
    box-shadow:0 18px 45px rgba(0,200,180,0.55), inset 0 1px 1px rgba(255,255,255,0.9);
    transition:all 0.3s cubic-bezier(0.16,1,0.3,1);
}
.expired-btn:hover { transform:translateY(-4px) scale(1.03); }
</style>
</head>
<body>
<div class="card">
    <div class="expired-icon">⏰</div>
    <div class="expired-title">SESSION HẾT HẠN</div>
    <div class="expired-text">Vui lòng tạo mã QR mới</div>
    <a href="/" class="expired-btn">🔄 TẠO QR MỚI</a>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
