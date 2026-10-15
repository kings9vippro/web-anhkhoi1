# ============================================================
# app.py — WEB-QR v28.0 NOEL EDITION
# Gunicorn entrypoint: app:app
# ============================================================
import os
import json
import time
import uuid
import asyncio
import threading
import random
import string
import hashlib

from flask import Flask, request, jsonify, render_template_string, Response

# ===== FLASK APP — BẮT BUỘC Ở ĐẦU FILE =====
app = Flask(__name__)
# =============================================

QR_TTL = 600
DATA_DIR = "/tmp/qr_sessions_v28"
os.makedirs(DATA_DIR, exist_ok=True)

USED_FILE = os.path.join(DATA_DIR, "_used_devices.json")
ACTIVE_PAGES = {}

# ============================================================
# CẤU HÌNH
# ============================================================
PROXY_POOL = []

UA_POOL = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; 2312DRA50C) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; CPH2557) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

VIEWPORT_POOL = [
    {"width": 390, "height": 844},
    {"width": 393, "height": 852},
    {"width": 412, "height": 915},
    {"width": 360, "height": 800},
    {"width": 414, "height": 896},
    {"width": 428, "height": 926},
]

LOCALE_POOL = ["vi-VN", "en-US", "en-GB"]
TZ_POOL = ["Asia/Ho_Chi_Minh", "Asia/Bangkok", "Asia/Singapore", "Asia/Tokyo", "America/New_York", "Europe/London"]


def pick_proxy():
    return random.choice(PROXY_POOL) if PROXY_POOL else None


def pick_ua():
    return random.choice(UA_POOL)


def pick_viewport():
    return random.choice(VIEWPORT_POOL)


def pick_locale():
    return random.choice(LOCALE_POOL)


def pick_tz():
    return random.choice(TZ_POOL)


def gen_imei():
    prefix = random.choice(["35", "86", "01", "99", "45", "49"])
    body = "".join(random.choices(string.digits, k=12))
    base = prefix + body
    total = 0
    for i, ch in enumerate(reversed(base)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check = (10 - (total % 10)) % 10
    return base + str(check)


def gen_device_fingerprint():
    seed = uuid.uuid4().hex
    return {
        "canvas_hash": hashlib.md5(seed.encode()).hexdigest()[:16],
        "webgl_vendor": random.choice(["Apple Inc.", "Google Inc.", "Qualcomm", "ARM", "Intel Inc."]),
        "webgl_renderer": random.choice(["Apple GPU", "Adreno (TM) 740", "Mali-G78", "Intel Iris Xe"]),
        "hardware_concurrency": random.choice([4, 6, 8, 12]),
        "device_memory": random.choice([4, 6, 8]),
        "platform": random.choice(["iPhone", "Linux armv8l", "Linux x86_64", "MacIntel"]),
        "audio_hash": hashlib.md5((seed + "audio").encode()).hexdigest()[:16],
    }


def sess_path(sid):
    return os.path.join(DATA_DIR, sid + ".json")


def sess_save(sid, data):
    try:
        with open(sess_path(sid), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
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


def load_used():
    try:
        with open(USED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"imeis": [], "fingerprints": []}


def save_used(d):
    try:
        with open(USED_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception as e:
        print("[USED] save error: " + str(e))


def mark_used(imei, fp_hash):
    d = load_used()
    if imei and imei not in d["imeis"]:
        d["imeis"].append(imei)
    if fp_hash and fp_hash not in d["fingerprints"]:
        d["fingerprints"].append(fp_hash)
    d["imeis"] = d["imeis"][-5000:]
    d["fingerprints"] = d["fingerprints"][-5000:]
    save_used(d)


def is_used_imei(imei):
    return imei in load_used()["imeis"]


def gen_unique_imei():
    for _ in range(50):
        im = gen_imei()
        if not is_used_imei(im):
            return im
    return gen_imei()


# ============================================================
# ZALO QR ENGINE
# ============================================================
class ZaloQR:
    def __init__(self, session_id):
        self.session_id = session_id
        self.browser = None
        self.playwright = None
        self.proxy = pick_proxy()
        self.ua = pick_ua()
        self.viewport = pick_viewport()
        self.locale = pick_locale()
        self.tz = pick_tz()
        self.fingerprint = gen_device_fingerprint()
        self.imei = gen_unique_imei()

    async def start(self):
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        launch_args = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--window-size=" + str(self.viewport["width"]) + "," + str(self.viewport["height"]),
                "--lang=" + self.locale,
            ],
        }
        if self.proxy:
            launch_args["proxy"] = {"server": self.proxy}
        self.browser = await self.playwright.chromium.launch(**launch_args)

    async def _stealth(self, ctx):
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['vi-VN','vi','en-US','en'] });
            Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => %d });
            Object.defineProperty(navigator, 'deviceMemory', { get: () => %d });
            Object.defineProperty(navigator, 'platform', { get: () => '%s' });
            window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
        """ % (
            self.fingerprint["hardware_concurrency"],
            self.fingerprint["device_memory"],
            self.fingerprint["platform"],
        ))

    async def create_qr(self):
        try:
            await self.start()
            ctx = await self.browser.new_context(
                viewport=self.viewport,
                user_agent=self.ua,
                locale=self.locale,
                timezone_id=self.tz,
                device_scale_factor=random.choice([2, 3]),
                is_mobile=True,
                has_touch=True,
            )
            await self._stealth(ctx)
            page = await ctx.new_page()
            await page.goto("https://chat.zalo.me/", wait_until="networkidle", timeout=60000)
            await asyncio.sleep(8)
            try:
                btn = await page.wait_for_selector("text=/QR|Quét mã/i", timeout=15000)
                await btn.click()
            except Exception as e:
                print("[QR] nut QR: " + str(e))
            await asyncio.sleep(6)
            png = await page.screenshot(full_page=False)
            png_path = os.path.join(DATA_DIR, self.session_id + ".png")
            with open(png_path, "wb") as f:
                f.write(png)
            sess_save(self.session_id, {
                "status": "waiting",
                "created_at": time.time(),
                "png_path": png_path,
                "cookies": None,
                "imei": self.imei,
                "qr_version": int(time.time()),
                "ua": self.ua,
                "viewport": self.viewport,
                "cookies_backup": [],
            })
            ACTIVE_PAGES[self.session_id] = {
                "page": page,
                "browser": self.browser,
                "playwright": self.playwright,
                "ctx": ctx,
                "qr_obj": self,
            }
            threading.Thread(target=lambda: asyncio.run(self._poll(page)), daemon=True).start()
        except Exception as e:
            print("[QR] create_qr error: " + str(e))
            sess_save(self.session_id, {"status": "error", "error": str(e), "created_at": time.time()})

    async def _backup_cookies(self, ctx, tag):
        try:
            cookies = await ctx.cookies()
            ck = {}
            for c in cookies:
                if "zalo" in c.get("domain", ""):
                    ck[c["name"]] = c["value"]
            data = sess_load(self.session_id) or {}
            backups = data.get("cookies_backup", [])
            backups.append({"tag": tag, "ts": time.time(), "cookies": ck})
            data["cookies_backup"] = backups[-20:]
            sess_save(self.session_id, data)
            return ck
        except Exception as e:
            print("[BACKUP] " + str(e))
            return {}

    async def _poll(self, page):
        start = time.time()
        check_count = 0
        last_cookie_count = 0
        while time.time() - start < QR_TTL:
            check_count += 1
            try:
                ck_dict = await self._backup_cookies(page.context, "poll_" + str(check_count))
                if ck_dict.get("zpw_sek"):
                    await asyncio.sleep(3)
                    return await self._extract(page)
                if len(ck_dict) < last_cookie_count:
                    try:
                        await page.reload(wait_until="networkidle", timeout=30000)
                    except Exception:
                        pass
                last_cookie_count = len(ck_dict)
                url = page.url
                if ("chat.zalo.me" in url and "/login" not in url and "qr" not in url.lower()):
                    await asyncio.sleep(3)
                    return await self._extract(page)
            except Exception as e:
                print("[QR] Poll error: " + str(e))
            await asyncio.sleep(1.5)
        data = sess_load(self.session_id) or {}
        data["status"] = "expired"
        sess_save(self.session_id, data)
        await self._cleanup()

    async def _extract(self, page):
        try:
            await asyncio.sleep(5)
            ck = {}
            for attempt in range(8):
                cookies = await page.context.cookies()
                ck = {}
                for c in cookies:
                    if "zalo" in c.get("domain", ""):
                        ck[c["name"]] = c["value"]
                if ck.get("zpw_sek"):
                    break
                await asyncio.sleep(2)
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
                imei = self.imei
            if is_used_imei(imei):
                imei = gen_unique_imei()
            mark_used(imei, self.fingerprint.get("canvas_hash", ""))
            data = sess_load(self.session_id) or {}
            data["status"] = "done"
            data["cookies"] = ck
            data["imei"] = imei
            data["done_at"] = time.time()
            sess_save(self.session_id, data)
            await self._cleanup()
        except Exception as e:
            data = sess_load(self.session_id) or {}
            data["status"] = "error"
            data["error"] = str(e)
            sess_save(self.session_id, data)

    async def _cleanup(self):
        info = ACTIVE_PAGES.pop(self.session_id, None)
        if info:
            try:
                if info.get("browser"):
                    await info["browser"].close()
                if info.get("playwright"):
                    await info["playwright"].stop()
            except Exception:
                pass


# ============================================================
# API ROUTES
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
        qr = ZaloQR(session_id)
        asyncio.run(qr.create_qr())

    threading.Thread(target=run, daemon=True).start()
    start = time.time()
    while time.time() - start < 60:
        data = sess_load(session_id)
        if data and data["status"] in ("waiting", "error"):
            break
        time.sleep(0.5)
    return jsonify({"ok": True, "session_id": session_id, "qr_url": "/qr/" + session_id, "ttl": QR_TTL})


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
        return jsonify({"status": "done", "imei": data.get("imei"), "cookies": data.get("cookies")})
    info = ACTIVE_PAGES.get(session_id)
    if not info:
        return jsonify({"status": data.get("status", "waiting"), "imei": data.get("imei"), "cookies": data.get("cookies"), "note": "Session đã đóng"})
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
                    v = await page.evaluate("localStorage.getItem('" + k + "')")
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
                imei = info["qr_obj"].imei
            if is_used_imei(imei):
                imei = gen_unique_imei()
            mark_used(imei, info["qr_obj"].fingerprint.get("canvas_hash", ""))
            data["status"] = "done"
            data["cookies"] = ck
            data["imei"] = imei
            sess_save(session_id, data)
            return jsonify({"status": "done", "imei": imei, "cookies": ck})
        return jsonify({"status": "waiting", "note": "Chưa có zpw_sek", "cookies_count": len(ck)})
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
    return Response(content, mimetype="image/png", headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    })


@app.route("/api/stats")
def api_stats():
    d = load_used()
    return jsonify({
        "total_imeis_used": len(d["imeis"]),
        "total_fingerprints_used": len(d["fingerprints"]),
        "active_sessions": len(ACTIVE_PAGES),
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": "28.0-noel"})


@app.route("/")
def home():
    return render_template_string(HTML_HOME)


@app.route("/qr/<session_id>")
def page_qr(session_id):
    if not sess_exists(session_id):
        return render_template_string(HTML_EXPIRED), 404
    return render_template_string(HTML_QR, session_id=session_id)


# ============================================================
# HTML
# ============================================================
SHARED_CSS = """
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html { -webkit-text-size-adjust:100%; touch-action:manipulation; }
body {
    font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;
    min-height:100vh; min-height:100dvh;
    display:flex; align-items:center; justify-content:center;
    background:
        radial-gradient(circle at 15% 20%, rgba(255,50,80,0.35), transparent 45%),
        radial-gradient(circle at 85% 15%, rgba(0,200,120,0.30), transparent 45%),
        radial-gradient(circle at 50% 90%, rgba(255,200,0,0.25), transparent 45%),
        linear-gradient(135deg, #0a1628 0%, #1a0a2e 50%, #0d2818 100%);
    background-size:400% 400%;
    animation:noelShift 22s ease infinite;
    padding:16px; position:relative; overflow-x:hidden;
}
@keyframes noelShift {
    0% { background-position:0% 50%; }
    50% { background-position:100% 50%; }
    100% { background-position:0% 50%; }
}
.snow { position:fixed; top:-10px; color:#fff; pointer-events:none; font-size:14px; opacity:0.85; z-index:1; animation:snowFall linear infinite; text-shadow:0 0 8px rgba(255,255,255,0.9); }
@keyframes snowFall { 0% { transform:translateY(-20px) rotate(0); opacity:0; } 10% { opacity:0.9; } 90% { opacity:0.9; } 100% { transform:translateY(105vh) rotate(360deg); opacity:0; } }
.santa { position:fixed; top:12%; left:-140px; font-size:52px; z-index:2; pointer-events:none; animation:santaFly 16s linear infinite; filter:drop-shadow(0 0 20px rgba(255,80,80,0.8)); }
@keyframes santaFly { 0% { transform:translateX(0) translateY(0) rotate(-6deg); } 50% { transform:translateX(60vw) translateY(-40px) rotate(6deg); } 100% { transform:translateX(120vw) translateY(0) rotate(-6deg); } }
.star { position:fixed; color:#ffd966; pointer-events:none; font-size:20px; z-index:1; animation:twinkle 3s ease-in-out infinite; text-shadow:0 0 14px #ffd966, 0 0 24px #ff6b6b; }
@keyframes twinkle { 0%,100% { opacity:0.3; transform:scale(0.9); } 50% { opacity:1; transform:scale(1.15); } }
.card {
    position:relative; z-index:10;
    background:rgba(255,255,255,0.10);
    backdrop-filter:blur(28px) saturate(180%);
    -webkit-backdrop-filter:blur(28px) saturate(180%);
    padding:36px 24px; border-radius:32px;
    box-shadow:0 40px 100px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.35), 0 0 60px rgba(255,80,80,0.15);
    border:1.5px solid rgba(255,255,255,0.22);
    text-align:center; max-width:520px; width:100%;
    animation:cardIn 0.9s cubic-bezier(0.16,1,0.3,1);
    overflow:hidden;
}
@keyframes cardIn { from { opacity:0; transform:translateY(60px) scale(0.94); } to { opacity:1; transform:translateY(0) scale(1); } }
h1 { color:#fff; margin-bottom:8px; font-size:24px; font-weight:900; background:linear-gradient(135deg,#ff6b6b,#ffd966,#4ade80,#60a5fa); background-size:300% 300%; -webkit-background-clip:text; background-clip:text; -webkit-text-fill-color:transparent; animation:textShift 5s ease infinite; position:relative; z-index:2; }
@keyframes textShift { 0%,100% { background-position:0% 50%; } 50% { background-position:100% 50%; } }
p.sub { color:rgba(255,255,255,0.75); margin-bottom:22px; font-size:13px; font-weight:600; position:relative; z-index:2; }
"""

NOEL_EFFECTS_HTML = '<div class="santa">🎅</div>'

NOEL_EFFECTS_JS = """
(function(){
    const snowChars = ['❄','❅','❆','•','*'];
    for (let i = 0; i < 28; i++) {
        const s = document.createElement('div');
        s.className = 'snow';
        s.textContent = snowChars[Math.floor(Math.random()*snowChars.length)];
        s.style.left = Math.random()*100 + 'vw';
        s.style.fontSize = (8 + Math.random()*16) + 'px';
        s.style.animationDuration = (6 + Math.random()*10) + 's';
        s.style.animationDelay = (Math.random()*8) + 's';
        document.body.appendChild(s);
    }
    const starChars = ['✦','★','✧','✩','✪'];
    for (let i = 0; i < 14; i++) {
        const st = document.createElement('div');
        st.className = 'star';
        st.textContent = starChars[Math.floor(Math.random()*starChars.length)];
        st.style.left = Math.random()*100 + 'vw';
        st.style.top = Math.random()*100 + 'vh';
        st.style.animationDelay = (Math.random()*3) + 's';
        st.style.fontSize = (12 + Math.random()*16) + 'px';
        document.body.appendChild(st);
    }
})();
"""


HTML_HOME = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>🎄 Tạo QR Zalo — NOEL EDITION</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
.btn-start {
    width:100%; padding:18px; border:none; border-radius:20px;
    background:linear-gradient(135deg,#ef4444,#dc2626,#b91c1c);
    color:white; font-size:17px; font-weight:900;
    font-family:inherit; cursor:pointer;
    box-shadow:0 20px 50px rgba(220,38,38,0.55), inset 0 1px 0 rgba(255,255,255,0.4);
    position:relative; z-index:2; letter-spacing:0.5px;
}
.btn-start:hover { transform:translateY(-4px); }
.btn-start:disabled { opacity:0.7; cursor:wait; }
.steps {
    margin-top:24px; text-align:left;
    background:rgba(255,255,255,0.08);
    backdrop-filter:blur(10px);
    padding:18px; border-radius:18px;
    font-size:13px; color:rgba(255,255,255,0.9);
    line-height:1.9; font-weight:600;
    border:1px solid rgba(255,255,255,0.15);
    position:relative; z-index:2;
}
.steps b { color:#ffd966; }
</style>
</head>
<body>
""" + NOEL_EFFECTS_HTML + """
<div class="card">
    <h1>🎄 Tạo mã QR Zalo 🎅</h1>
    <p class="sub">✨ NOEL EDITION — Multi-Device IP ✨</p>
    <button id="btn-start" class="btn-start" onclick="startQR()">🎁 TẠO MÃ QR NOEL</button>
    <div class="steps">
        <b>🎄 Hướng dẫn:</b><br>
        1️⃣ Bấm nút trên<br>
        2️⃣ Đợi 15-30s → hiện mã QR<br>
        3️⃣ Mở app Zalo → QR → Quét<br>
        4️⃣ Đồng ý trên điện thoại<br>
        5️⃣ Web tự hiện IMEI + Cookie
    </div>
</div>
<script>
""" + NOEL_EFFECTS_JS + """
async function startQR() {
    const btn = document.getElementById('btn-start');
    btn.disabled = true;
    btn.innerHTML = '🎅 ĐANG TẠO QR...';
    try {
        const r = await fetch('/api/qr/create', { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            btn.innerHTML = '🎁 XONG!';
            setTimeout(function() { window.location.href = d.qr_url; }, 300);
        } else {
            alert('Lỗi: ' + (d.error || 'Không rõ'));
            btn.disabled = false;
            btn.innerHTML = '🎁 TẠO MÃ QR NOEL';
        }
    } catch (e) {
        alert('Lỗi: ' + e.message);
        btn.disabled = false;
        btn.innerHTML = '🎁 TẠO MÃ QR NOEL';
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
<title>🎄 Quét QR — NOEL EDITION</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
.qr-container {
    background:rgba(255,255,255,0.95);
    padding:10px; border-radius:20px;
    margin:0 auto 14px; max-width:340px; width:100%;
    box-shadow:0 20px 50px rgba(0,0,0,0.35), 0 0 0 2px rgba(255,80,80,0.35), 0 0 40px rgba(255,80,80,0.25);
    border:2px solid rgba(255,215,0,0.5);
    overflow:hidden; position:relative; z-index:2;
}
.qr-container img { width:100%; height:auto; display:block; border-radius:12px; }
.qr-label { text-align:center; font-size:12px; color:rgba(255,255,255,0.85); font-weight:700; margin-bottom:14px; position:relative; z-index:2; }
.status {
    padding:14px; border-radius:16px;
    font-size:13px; font-weight:800;
    display:flex; align-items:center; justify-content:center; gap:8px;
    position:relative; z-index:2;
    border:1px solid rgba(255,255,255,0.2);
}
.status.waiting { background:linear-gradient(135deg,rgba(255,200,0,0.28),rgba(255,150,0,0.22)); color:#fff5cc; }
.status.error { background:linear-gradient(135deg,rgba(255,80,80,0.32),rgba(200,20,20,0.22)); color:#ffd5d5; }
.status.expired { background:rgba(255,255,255,0.10); color:rgba(255,255,255,0.8); }
.confirm-btn {
    width:100%; margin-top:10px; padding:14px;
    background:linear-gradient(135deg,#10b981,#059669);
    color:white; border:none; border-radius:14px;
    font-size:13px; font-weight:900; font-family:inherit; cursor:pointer;
    box-shadow:0 10px 25px rgba(16,185,129,0.45), inset 0 1px 0 rgba(255,255,255,0.3);
    position:relative; z-index:2; letter-spacing:0.5px;
}
.confirm-btn.refresh { background:linear-gradient(135deg,#f59e0b,#d97706); box-shadow:0 10px 25px rgba(245,158,11,0.45); }
.result-wrap { text-align:left; position:relative; z-index:2; }
.success-header {
    text-align:center; padding:18px;
    background:linear-gradient(135deg,rgba(16,185,129,0.30),rgba(5,150,105,0.22));
    border-radius:18px; margin-bottom:16px;
    border:1px solid rgba(16,185,129,0.4);
}
.success-icon { font-size:46px; margin-bottom:6px; }
.success-title { font-size:16px; font-weight:900; color:#d1fae5; }
.result {
    padding:14px; border-radius:14px;
    background:linear-gradient(135deg,rgba(255,255,255,0.12),rgba(255,255,255,0.06));
    margin-bottom:10px;
    border:1px solid rgba(255,255,255,0.18);
}
.result .label { font-size:10px; color:#ffd966; text-transform:uppercase; font-weight:900; letter-spacing:1.2px; margin-bottom:6px; }
.result .value { font-family:'Courier New',monospace; font-size:11px; color:#fff; word-break:break-all; line-height:1.6; font-weight:600; }
.result .value.scroll { max-height:150px; overflow-y:auto; background:rgba(0,0,0,0.25); padding:8px; border-radius:8px; margin-top:6px; }
.copy-btn {
    width:100%; padding:14px; border:none; border-radius:12px;
    background:linear-gradient(135deg,#ef4444,#b91c1c);
    color:white; font-size:13px; font-weight:900;
    font-family:inherit; cursor:pointer; margin-top:8px;
    box-shadow:0 10px 25px rgba(239,68,68,0.4);
    letter-spacing:0.5px;
}
.copy-btn.green { background:linear-gradient(135deg,#10b981,#059669); }
.copy-btn.purple { background:linear-gradient(135deg,#8b5cf6,#7c3aed); }
.copy-btn.blue { background:linear-gradient(135deg,#3b82f6,#2563eb); }
.hint { margin-top:10px; font-size:11px; color:rgba(255,255,255,0.7); text-align:center; }
.toast {
    position:fixed; bottom:24px; left:50%; transform:translateX(-50%);
    background:linear-gradient(135deg,#10b981,#059669);
    color:white; padding:12px 24px; border-radius:50px;
    font-weight:900; font-size:13px; z-index:9999;
    box-shadow:0 20px 50px rgba(16,185,129,0.6);
}
</style>
</head>
<body>
""" + NOEL_EFFECTS_HTML + """
<div class="card">
    <h1>🎄 Quét QR Zalo 🎅</h1>
    <p class="sub">✨ Mở app Zalo → QR → Quét ✨</p>
    <div id="qr-area">
        <div class="qr-container">
            <img id="qr-img" src="/qr-image/{{ session_id }}" alt="QR">
        </div>
        <div class="qr-label">📱 Quét → Đồng ý trên điện thoại</div>
    </div>
    <div id="status" class="status waiting">
        <span>⏳</span> <span>Đang chờ quét...</span>
    </div>
    <button id="btn-refresh" class="confirm-btn refresh" onclick="refreshQR()">🔄 LÀM MỚI QR</button>
    <button id="btn-check" class="confirm-btn" onclick="forceCheck()">✅ TÔI ĐÃ ĐỒNG Ý - LẤY COOKIE</button>
</div>
<script>
""" + NOEL_EFFECTS_JS + """
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
            showToast('🎁 Đã lấy QR mới!');
            lastQrVersion = d.qr_version;
        } else alert('Lỗi: ' + (d.error || 'Không rõ'));
    } catch (e) { alert('Lỗi: ' + e.message); }
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
        if (d.status === 'done') { done = true; showResult(d); }
        else {
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
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
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
    el.innerHTML =
        '<div class="result-wrap">' +
            '<div class="success-header">' +
                '<div class="success-icon">🎄✅🎅</div>' +
                '<div class="success-title">Đăng nhập thành công!</div>' +
            '</div>' +
            '<div class="result"><div class="label">📞 IMEI</div><div class="value">' + escapeHtml(imei) + '</div></div>' +
            '<div class="result"><div class="label">🍪 COOKIE</div><div class="value scroll">' + escapeHtml(cookiesStr) + '</div></div>' +
            '<button class="copy-btn green" id="copy-all">📋 COPY CẢ IMEI + COOKIE</button>' +
            '<button class="copy-btn blue" id="copy-imei">📞 CHỈ COPY IMEI</button>' +
            '<button class="copy-btn purple" id="copy-ck">🍪 CHỈ COPY COOKIE</button>' +
            '<div class="hint">🎁 Copy → paste vào web spam</div>' +
        '</div>';
    document.getElementById('copy-all').onclick = function() { copyText(combo); };
    document.getElementById('copy-imei').onclick = function() { copyText(imei); };
    document.getElementById('copy-ck').onclick = function() { copyText(cookiesStr); };
}

function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function() { showToast('🎁 Đã copy!'); }).catch(function() { fallbackCopy(text); });
    } else fallbackCopy(text);
}

function fallbackCopy(text) {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.left = '-9999px';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); showToast('🎁 Đã copy!'); } catch (e) { prompt('Copy:', text); }
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
<title>🎄 Hết hạn</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
.expired-icon { font-size:64px; margin-bottom:18px; position:relative; z-index:2; }
.expired-title { color:#fff; margin-bottom:12px; font-size:22px; font-weight:900; position:relative; z-index:2; }
.expired-text { color:rgba(255,255,255,0.75); font-size:13px; margin-bottom:22px; position:relative; z-index:2; }
.expired-btn {
    display:inline-block; padding:14px 28px;
    background:linear-gradient(135deg,#ef4444,#b91c1c);
    color:white; border-radius:14px; text-decoration:none;
    font-weight:900; font-size:13px;
    box-shadow:0 15px 40px rgba(239,68,68,0.5);
    position:relative; z-index:2;
}
</style>
</head>
<body>
""" + NOEL_EFFECTS_HTML + """
<div class="card">
    <div class="expired-icon">⏰🎄</div>
    <div class="expired-title">Session hết hạn</div>
    <div class="expired-text">Tạo mã QR mới</div>
    <a href="/" class="expired-btn">🔄 Tạo QR mới</a>
</div>
<script>
""" + NOEL_EFFECTS_JS + """
</script>
</body>
</html>
"""


# ============================================================
# ENTRYPOINT — CHỈ chạy khi gọi python app.py trực tiếp
# Gunicorn import sẽ KHÔNG chạy khối này
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
