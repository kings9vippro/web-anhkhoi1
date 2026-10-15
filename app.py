# ============================================================
# WEB-QR v29.0 — Bám sát v26 + Multi-IP + UI Noel
# Bot by Anh Khôi
# ============================================================
import os
import io
import json
import time
import uuid
import asyncio
import threading
import random
import string
import sqlite3
import hashlib

from flask import (Flask, request, jsonify, render_template_string,
                   Response, redirect, url_for)

QR_TTL = 600
DATA_DIR = "/tmp/qr_sessions"
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "devices.db")
PROXY_FILE = os.path.join(DATA_DIR, "proxies.txt")


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
    """IMEI 15 số."""
    return "".join(random.choices(string.digits, k=15))


# ============================================================
# DEVICE PROFILES — bám sát v26, chỉ thêm nhiều loại
# ============================================================
DEVICE_PROFILES = [
    {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
     "platform": "Win32", "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh",
     "cores": 8, "mem": 8, "name": "Windows 11 Chrome"},
    {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
     "platform": "Win32", "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh",
     "cores": 4, "mem": 8, "name": "Windows 11 Edge"},
    {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
     "platform": "Win32", "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh",
     "cores": 4, "mem": 8, "name": "Windows 10 Firefox"},
    {"ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
     "platform": "MacIntel", "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh",
     "cores": 8, "mem": 16, "name": "macOS Chrome"},
    {"ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
     "platform": "MacIntel", "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh",
     "cores": 10, "mem": 16, "name": "macOS Safari"},
    {"ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
     "platform": "iPhone", "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh",
     "cores": 8, "mem": 12, "name": "iPhone 18 Pro Max"},
    {"ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
     "platform": "iPhone", "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh",
     "cores": 8, "mem": 8, "name": "iPhone 17 Pro Max"},
    {"ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
     "platform": "iPhone", "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh",
     "cores": 6, "mem": 8, "name": "iPhone 16 Pro"},
    {"ua": "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
     "platform": "Linux armv8l", "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh",
     "cores": 8, "mem": 12, "name": "Samsung S24 Ultra"},
    {"ua": "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
     "platform": "Linux armv8l", "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh",
     "cores": 8, "mem": 12, "name": "Pixel 8 Pro"},
    {"ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
     "platform": "Linux x86_64", "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh",
     "cores": 8, "mem": 8, "name": "Ubuntu Chrome"},
]


# ============================================================
# PROXY
# ============================================================
def load_proxies():
    proxies = []
    if not os.path.exists(PROXY_FILE):
        return proxies
    try:
        with open(PROXY_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                proxies.append(line)
    except Exception as e:
        print("[PROXY] error: " + str(e))
    return proxies


def parse_proxy(p):
    """Chuyển string → Playwright proxy format."""
    if not p:
        return None
    if p.startswith("http://") or p.startswith("https://") or p.startswith("socks5://"):
        return {"server": p}
    parts = p.split(":")
    if len(parts) == 2:
        return {"server": "http://" + parts[0] + ":" + parts[1]}
    elif len(parts) == 4:
        return {"server": "http://" + parts[0] + ":" + parts[1],
                "username": parts[2], "password": parts[3]}
    return None


def pick_proxy():
    ps = load_proxies()
    if not ps:
        return None
    return random.choice(ps)


# ============================================================
# DATABASE
# ============================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS devices (
        device_id TEXT PRIMARY KEY,
        profile_idx INTEGER,
        imei TEXT,
        proxy TEXT,
        in_use INTEGER DEFAULT 0,
        status TEXT DEFAULT 'available',
        use_count INTEGER DEFAULT 0,
        last_used REAL,
        created_at REAL
    )""")
    conn.commit()
    conn.close()


init_db()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def acquire_device():
    conn = db()
    row = conn.execute("""SELECT * FROM devices
        WHERE in_use = 0 AND status = 'available'
        ORDER BY use_count ASC, last_used ASC LIMIT 1""").fetchone()
    if row:
        did = row["device_id"]
        conn.execute("""UPDATE devices SET in_use = 1,
            last_used = ?, use_count = use_count + 1
            WHERE device_id = ?""", (time.time(), did))
        conn.commit()
        result = {"device_id": did, "profile_idx": row["profile_idx"],
                  "imei": row["imei"], "proxy": row["proxy"]}
        conn.close()
        return result

    pi = random.randint(0, len(DEVICE_PROFILES) - 1)
    imei = gen_imei()
    proxy = pick_proxy()
    did = "dev_" + uuid.uuid4().hex[:12]
    conn.execute("""INSERT INTO devices
        (device_id, profile_idx, imei, proxy, in_use, last_used, created_at, use_count, status)
        VALUES (?, ?, ?, ?, 1, ?, ?, 1, 'available')""",
        (did, pi, imei, proxy, time.time(), time.time()))
    conn.commit()
    conn.close()
    return {"device_id": did, "profile_idx": pi, "imei": imei, "proxy": proxy}


def release_device(did):
    try:
        conn = db()
        conn.execute("UPDATE devices SET in_use = 0 WHERE device_id = ?", (did,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def mark_device_used(did):
    try:
        conn = db()
        conn.execute("UPDATE devices SET status = 'used', in_use = 0 WHERE device_id = ?", (did,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def device_stats():
    conn = db()
    total = conn.execute("SELECT COUNT(*) as c FROM devices").fetchone()["c"]
    avail = conn.execute("SELECT COUNT(*) as c FROM devices WHERE in_use=0 AND status='available'").fetchone()["c"]
    inuse = conn.execute("SELECT COUNT(*) as c FROM devices WHERE in_use=1").fetchone()["c"]
    used = conn.execute("SELECT COUNT(*) as c FROM devices WHERE status='used'").fetchone()["c"]
    conn.close()
    return {"total": total, "available": avail, "in_use": inuse,
            "used": used, "proxies": len(load_proxies())}


app = Flask(__name__)
ACTIVE_PAGES = {}


# ============================================================
# ZALO QR — BÁM SÁT v26
# ============================================================
class ZaloQR:
    def __init__(self):
        self.browser = None
        self.playwright = None
        self.device = None

    async def start(self, device):
        from playwright.async_api import async_playwright
        self.device = device
        profile = DEVICE_PROFILES[device["profile_idx"]]

        proxy_cfg = None
        if device.get("proxy"):
            proxy_cfg = parse_proxy(device["proxy"])
            if proxy_cfg:
                print("[PROXY] " + proxy_cfg.get("server", "?"))

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--window-size=600,900",
            ],
            proxy=proxy_cfg,
        )

    async def _capture_qr(self, page):
        try:
            png = await page.screenshot(full_page=False)
            print("[QR] Chụp - " + str(len(png)) + " bytes")
            return png
        except Exception as e:
            print("[QR] Lỗi chụp: " + str(e))
            return None

    async def create_qr(self, session_id):
        try:
            device = acquire_device()
            profile = DEVICE_PROFILES[device["profile_idx"]]
            print("[DEVICE] " + session_id + " → " + device["device_id"] +
                  " (" + profile["name"] + ")")

            await self.start(device)

            ctx = await self.browser.new_context(
                viewport={"width": 600, "height": 900},
                user_agent=profile["ua"],
                locale=profile["locale"],
                timezone_id=profile["tz"],
                extra_http_headers={
                    "Accept-Language": profile["locale"] + ",en;q=0.9",
                },
            )

            # Anti-detect nhẹ
            await ctx.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'platform', { get: () => '""" + profile["platform"] + """' });
                Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => """ + str(profile["cores"]) + """ });
                Object.defineProperty(navigator, 'deviceMemory', { get: () => """ + str(profile["mem"]) + """ });
                window.chrome = { runtime: {} };
            """)

            page = await ctx.new_page()

            print("[QR] Mở chat.zalo.me")
            await page.goto(
                "https://chat.zalo.me/",
                wait_until="networkidle",
                timeout=60000,
            )

            # BÁM SÁT v26: chờ 8s
            await asyncio.sleep(8)

            # BÁM SÁT v26: click nút QR
            try:
                btn = await page.wait_for_selector(
                    "text=/QR|Quét mã/i", timeout=15000
                )
                await btn.click()
                print("[QR] Đã click tab QR")
            except Exception as e:
                print("[QR] Không thấy nút QR: " + str(e))

            # BÁM SÁT v26: chờ 6s
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
                "device_id": device["device_id"],
                "device_name": profile["name"],
                "proxy": device["proxy"],
                "qr_version": int(time.time()),
            })

            print("[QR] " + session_id + " ready")

            ACTIVE_PAGES[session_id] = {
                "page": page,
                "browser": self.browser,
                "playwright": self.playwright,
                "device_id": device["device_id"],
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
            if self.device:
                release_device(self.device["device_id"])

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
                          " | ck=" + str(len(ck_dict)) +
                          " | zpw_sek=" + str("zpw_sek" in ck_dict))

                if ck_dict.get("zpw_sek"):
                    print("[QR] ✅ CÓ ZPW_SEK")
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

        print("[QR] Hết hạn: " + session_id)
        data = sess_load(session_id) or {}
        data["status"] = "expired"
        sess_save(session_id, data)
        await self._cleanup(session_id)

    def _short_cookies(self, raw):
        """Cookie ngắn gọn — chỉ giữ essential."""
        keep = ["zpw_sek", "zpw_ver", "zpw_type", "zpsid",
                "zalo_id", "app_id", "domain"]
        short = {}
        for k in keep:
            if k in raw:
                short[k] = raw[k]
        # Nếu không có cái nào trong list → giữ tất cả
        if not short:
            return raw
        return short

    async def _extract(self, session_id, page):
        try:
            print("[QR] Extract - chờ 5s")
            await asyncio.sleep(5)

            cookies = await page.context.cookies()
            ck_raw = {}
            for c in cookies:
                if "zalo" in c.get("domain", ""):
                    ck_raw[c["name"]] = c["value"]

            # Cookie ngắn gọn
            ck = self._short_cookies(ck_raw)
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

            if not imei and ck_raw.get("zpw_imei"):
                imei = ck_raw["zpw_imei"]

            if not imei:
                imei = gen_imei()

            data = sess_load(session_id) or {}
            data["status"] = "done"
            data["cookies"] = ck
            data["imei"] = imei
            sess_save(session_id, data)

            did = data.get("device_id")
            if did:
                mark_device_used(did)

            print("[QR] DONE - IMEI: " + imei)
            await self._cleanup(session_id)
        except Exception as e:
            print("[QR] Extract error: " + str(e))
            data = sess_load(session_id) or {}
            data["status"] = "error"
            data["error"] = str(e)
            sess_save(session_id, data)
            did = data.get("device_id")
            if did:
                release_device(did)

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
            did = info.get("device_id")
            if did:
                release_device(did)


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
        "device_name": data.get("device_name", ""),
        "proxy": data.get("proxy", ""),
        "qr_version": data.get("qr_version", 0),
    })


@app.route("/api/qr/force-extract/<session_id>", methods=["POST"])
def api_qr_force_extract(session_id):
    data = sess_load(session_id)
    if not data:
        return jsonify({"error": "Session không tồn tại"}), 404

    if data.get("status") == "done" and data.get("cookies"):
        return jsonify({"status": "done", "imei": data.get("imei"),
                        "cookies": data.get("cookies")})

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
            ck_raw = {}
            for c in cookies:
                if "zalo" in c.get("domain", ""):
                    ck_raw[c["name"]] = c["value"]

            ck = {}
            keep = ["zpw_sek", "zpw_ver", "zpw_type", "zpsid",
                    "zalo_id", "app_id", "domain"]
            for k in keep:
                if k in ck_raw:
                    ck[k] = ck_raw[k]
            if not ck:
                ck = ck_raw

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
                imei = data.get("imei") or gen_imei()
            data["status"] = "done"
            data["cookies"] = ck
            data["imei"] = imei
            sess_save(session_id, data)
            did = data.get("device_id")
            if did:
                mark_device_used(did)
            return jsonify({"status": "done", "imei": imei, "cookies": ck})

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
        content, mimetype="image/png",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate",
                 "Pragma": "no-cache", "Expires": "0"})


@app.route("/api/stats")
def api_stats():
    return jsonify(device_stats())


@app.route("/")
def home():
    return render_template_string(HTML_HOME, stats=device_stats())


@app.route("/qr/<session_id>")
def page_qr(session_id):
    if not sess_exists(session_id):
        return render_template_string(HTML_EXPIRED), 404
    return render_template_string(HTML_QR, session_id=session_id)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": "29.0"})


# ============================================================
# CSS NOEL SHARED
# ============================================================
SHARED_CSS = """
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html { -webkit-text-size-adjust:100%; touch-action:manipulation; }
body {
    font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;
    min-height:100vh; min-height:100dvh;
    display:flex; align-items:center; justify-content:center;
    background:linear-gradient(-45deg,#0a1929,#1e3a5f,#c41e3a,#0f5132,#0a1929);
    background-size:500% 500%;
    animation:gradientShift 20s ease infinite;
    padding:16px; position:relative; overflow-x:hidden;
}
@keyframes gradientShift {
    0% { background-position:0% 50%; }
    50% { background-position:100% 50%; }
    100% { background-position:0% 50%; }
}
.snowflake {
    position:fixed; top:-10px; color:#fff;
    user-select:none; pointer-events:none;
    font-size:20px; z-index:1;
    animation:snowfall linear infinite;
    text-shadow:0 0 8px rgba(255,255,255,0.8);
}
@keyframes snowfall {
    0% { transform:translateY(-10px) translateX(0) rotate(0deg); opacity:1; }
    100% { transform:translateY(105vh) translateX(50px) rotate(360deg); opacity:0.3; }
}
.santa {
    position:fixed; top:20px; left:-120px;
    font-size:56px; z-index:2; pointer-events:none;
    animation:santaSlide 18s linear infinite;
}
@keyframes santaSlide {
    0% { transform:translateX(0) rotate(-5deg); }
    25% { transform:translateX(30vw) rotate(5deg) translateY(-10px); }
    50% { transform:translateX(60vw) rotate(-3deg); }
    75% { transform:translateX(85vw) rotate(8deg) translateY(-15px); }
    100% { transform:translateX(110vw) rotate(-5deg); }
}
.rudolph {
    position:fixed; top:90px; left:-100px;
    font-size:36px; z-index:2; pointer-events:none;
    animation:rudolphSlide 22s linear infinite;
    animation-delay:3s;
}
@keyframes rudolphSlide {
    0% { transform:translateX(0); }
    100% { transform:translateX(110vw); }
}
.card {
    position:relative; z-index:10;
    background:rgba(255,255,255,0.12);
    backdrop-filter:blur(30px) saturate(180%);
    -webkit-backdrop-filter:blur(30px) saturate(180%);
    padding:36px 24px; border-radius:32px;
    box-shadow:
        0 40px 100px rgba(0,0,0,0.5),
        0 0 0 1px rgba(255,255,255,0.15),
        inset 0 1px 0 rgba(255,255,255,0.25);
    border:1px solid rgba(255,255,255,0.2);
    text-align:center; max-width:520px; width:100%;
    animation:cardIn 0.9s cubic-bezier(0.16,1,0.3,1);
}
@keyframes cardIn {
    from { opacity:0; transform:translateY(60px) scale(0.94); }
    to { opacity:1; transform:translateY(0) scale(1); }
}
.lights { display:flex; justify-content:center; gap:8px; margin-bottom:14px; }
.light { width:10px; height:10px; border-radius:50%; animation:lightGlow 1.5s ease-in-out infinite; box-shadow:0 0 12px currentColor; }
.light:nth-child(1) { background:#ff3b30; color:#ff3b30; animation-delay:0s; }
.light:nth-child(2) { background:#ffd700; color:#ffd700; animation-delay:0.3s; }
.light:nth-child(3) { background:#00d4ff; color:#00d4ff; animation-delay:0.6s; }
.light:nth-child(4) { background:#10b981; color:#10b981; animation-delay:0.9s; }
.light:nth-child(5) { background:#f107a3; color:#f107a3; animation-delay:1.2s; }
@keyframes lightGlow {
    0%,100% { opacity:0.4; transform:scale(0.85); }
    50% { opacity:1; transform:scale(1.25); }
}
h1 {
    color:#fff; margin-bottom:8px;
    font-size:26px; font-weight:900;
    text-shadow:0 2px 20px rgba(255,255,255,0.4);
}
h1 .santa-emoji { display:inline-block; animation:santaBounce 2s ease-in-out infinite; }
@keyframes santaBounce {
    0%,100% { transform:translateY(0) rotate(-5deg); }
    50% { transform:translateY(-8px) rotate(5deg); }
}
p.sub { color:rgba(255,255,255,0.85); margin-bottom:22px; font-size:13px; font-weight:600; }
"""


# ============================================================
# HOME
# ============================================================
HTML_HOME = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>🎄 Tạo QR Zalo — ALB</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
.btn-start {
    width:100%; padding:18px; border:none; border-radius:20px;
    background:linear-gradient(135deg,#c41e3a,#8b0000,#c41e3a);
    color:white; font-size:17px; font-weight:900;
    font-family:inherit; cursor:pointer;
    box-shadow:0 20px 50px rgba(196,30,58,0.6), inset 0 1px 0 rgba(255,255,255,0.3);
    transition:all 0.3s;
}
.btn-start:hover { transform:translateY(-4px); }
.btn-start:disabled { opacity:0.7; cursor:wait; }
.stats-bar { display:grid; grid-template-columns:repeat(5,1fr); gap:6px; margin:18px 0; }
.stat-item {
    background:rgba(255,255,255,0.08); backdrop-filter:blur(10px);
    padding:10px 4px; border-radius:12px;
    border:1px solid rgba(255,255,255,0.15);
}
.stat-item .num { font-size:16px; font-weight:900; color:#fff; }
.stat-item .lbl {
    font-size:8px; color:rgba(255,255,255,0.7);
    text-transform:uppercase; letter-spacing:0.5px;
    font-weight:800; margin-top:3px;
}
.steps {
    margin-top:24px; text-align:left;
    background:rgba(255,255,255,0.08); backdrop-filter:blur(15px);
    padding:18px; border-radius:18px;
    border:1px solid rgba(255,255,255,0.15);
    font-size:13px; color:rgba(255,255,255,0.9); line-height:1.9;
}
.steps b { color:#ffd700; }
</style>
</head>
<body>
<div class="santa">🎅</div>
<div class="rudolph">🦌</div>
<div class="card">
    <div class="lights">
        <div class="light"></div><div class="light"></div>
        <div class="light"></div><div class="light"></div>
        <div class="light"></div>
    </div>
    <h1><span class="santa-emoji">🎅</span> Tạo QR Zalo</h1>
    <p class="sub">❄️ Multi-Device • Multi-IP ❄️</p>

    <div class="stats-bar">
        <div class="stat-item"><div class="num" id="st-total">{{ stats.total }}</div><div class="lbl">Tổng</div></div>
        <div class="stat-item"><div class="num" id="st-avail">{{ stats.available }}</div><div class="lbl">Trống</div></div>
        <div class="stat-item"><div class="num" id="st-inuse">{{ stats.in_use }}</div><div class="lbl">Đang</div></div>
        <div class="stat-item"><div class="num" id="st-used">{{ stats.used }}</div><div class="lbl">Đã dùng</div></div>
        <div class="stat-item"><div class="num" id="st-proxies">{{ stats.proxies }}</div><div class="lbl">Proxy</div></div>
    </div>

    <button id="btn-start" class="btn-start" onclick="startQR()">🎁 TẠO MÃ QR</button>

    <div class="steps">
        <b>🎄 Hướng dẫn:</b><br>
        1️⃣ Bấm nút trên<br>
        2️⃣ Đợi 15-30s → hiện QR<br>
        3️⃣ Mở Zalo → QR → Quét<br>
        4️⃣ Đồng ý trên điện thoại<br>
        5️⃣ Web tự hiện IMEI + Cookie
    </div>
</div>

<script>
for (let i = 0; i < 30; i++) {
    const s = document.createElement('div');
    s.className = 'snowflake';
    s.innerHTML = ['❄', '❅', '❆', '✻', '✼'][Math.floor(Math.random()*5)];
    s.style.left = Math.random() * 100 + 'vw';
    s.style.animationDuration = (5 + Math.random() * 8) + 's';
    s.style.animationDelay = (Math.random() * 5) + 's';
    s.style.fontSize = (10 + Math.random() * 20) + 'px';
    s.style.opacity = 0.4 + Math.random() * 0.5;
    document.body.appendChild(s);
}

async function startQR() {
    const btn = document.getElementById('btn-start');
    btn.disabled = true;
    btn.innerHTML = '⏳ ĐANG TẠO...';
    try {
        const r = await fetch('/api/qr/create', { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            btn.innerHTML = '🎉 XONG!';
            setTimeout(function() { window.location.href = d.qr_url; }, 300);
        } else {
            alert('Lỗi: ' + (d.error || 'Không rõ'));
            btn.disabled = false;
            btn.innerHTML = '🎁 TẠO MÃ QR';
        }
    } catch (e) {
        alert('Lỗi: ' + e.message);
        btn.disabled = false;
        btn.innerHTML = '🎁 TẠO MÃ QR';
    }
}

setInterval(async function() {
    try {
        const r = await fetch('/api/stats');
        const d = await r.json();
        document.getElementById('st-total').textContent = d.total;
        document.getElementById('st-avail').textContent = d.available;
        document.getElementById('st-inuse').textContent = d.in_use;
        document.getElementById('st-used').textContent = d.used;
        document.getElementById('st-proxies').textContent = d.proxies;
    } catch (e) {}
}, 5000);
</script>
</body>
</html>
"""


# ============================================================
# QR PAGE
# ============================================================
HTML_QR = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>🎄 Quét QR — ALB</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
.device-badge {
    display:inline-flex; align-items:center; gap:6px;
    padding:6px 14px; border-radius:20px;
    background:rgba(255,215,0,0.15);
    border:1px solid rgba(255,215,0,0.4);
    color:#ffd700; font-size:10.5px; font-weight:800;
    text-transform:uppercase; letter-spacing:0.6px;
    margin-bottom:8px;
}
.proxy-badge {
    display:inline-flex; align-items:center; gap:6px;
    padding:5px 12px; border-radius:20px;
    background:rgba(0,212,255,0.15);
    border:1px solid rgba(0,212,255,0.4);
    color:#00d4ff; font-size:9.5px; font-weight:800;
    margin-bottom:14px;
}
.qr-container {
    background:rgba(255,255,255,0.95);
    padding:10px; border-radius:18px;
    margin:0 auto 14px; max-width:340px; width:100%;
    box-shadow:0 20px 50px rgba(0,0,0,0.4);
    border:2px solid rgba(255,255,255,0.3);
    overflow:hidden; position:relative;
}
.qr-container::before {
    content:'🎄'; position:absolute;
    top:6px; right:8px; font-size:20px;
    opacity:0.6; z-index:1;
}
.qr-container img { width:100%; height:auto; display:block; border-radius:12px; }
.qr-label {
    text-align:center; font-size:12px; color:rgba(255,255,255,0.85);
    font-weight:600; margin-bottom:14px;
}
.status {
    padding:14px; border-radius:14px;
    font-size:13px; font-weight:800;
    display:flex; align-items:center; justify-content:center; gap:8px;
}
.status.waiting {
    background:rgba(255,215,0,0.15);
    border:1px solid rgba(255,215,0,0.4);
    color:#ffd700;
}
.status.error {
    background:rgba(239,68,68,0.15);
    border:1px solid rgba(239,68,68,0.4);
    color:#fca5a5;
}
.status.expired {
    background:rgba(148,163,184,0.15);
    border:1px solid rgba(148,163,184,0.4);
    color:#cbd5e1;
}
.confirm-btn {
    width:100%; margin-top:10px; padding:14px;
    background:linear-gradient(135deg,#10b981,#059669);
    color:white; border:none; border-radius:14px;
    font-size:13px; font-weight:900; font-family:inherit; cursor:pointer;
    box-shadow:0 10px 25px rgba(16,185,129,0.5);
}
.confirm-btn.refresh {
    background:linear-gradient(135deg,#f59e0b,#d97706);
}
.result-wrap { text-align:left; }
.success-header {
    text-align:center; padding:18px;
    background:rgba(16,185,129,0.15);
    border:1px solid rgba(16,185,129,0.4);
    border-radius:18px; margin-bottom:16px;
}
.success-icon { font-size:46px; margin-bottom:6px; animation:santaBounce 2s ease-in-out infinite; }
.success-title { font-size:16px; font-weight:900; color:#34d399; }
.result {
    padding:14px; border-radius:14px;
    background:rgba(255,255,255,0.1);
    border:1px solid rgba(255,255,255,0.2);
    margin-bottom:10px;
}
.result .label {
    font-size:10px; color:#ffd700;
    text-transform:uppercase; font-weight:900;
    letter-spacing:1px; margin-bottom:6px;
}
.result .value {
    font-family:monospace; font-size:11px; color:#fff;
    word-break:break-all; line-height:1.5; font-weight:600;
}
.result .value.scroll {
    max-height:150px; overflow-y:auto;
    background:rgba(0,0,0,0.3); padding:8px;
    border-radius:8px; margin-top:6px;
}
.copy-btn {
    width:100%; padding:14px; border:none; border-radius:12px;
    color:white; font-size:13px; font-weight:900;
    font-family:inherit; cursor:pointer; margin-top:8px;
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
}
</style>
</head>
<body>
<div class="santa">🎅</div>
<div class="rudolph">🦌</div>
<div class="card">
    <div class="lights">
        <div class="light"></div><div class="light"></div>
        <div class="light"></div><div class="light"></div>
        <div class="light"></div>
    </div>
    <h1><span class="santa-emoji">🎅</span> Quét QR Zalo</h1>
    <p class="sub">❄️ Mở Zalo → QR → Quét ❄️</p>

    <div class="device-badge" id="device-badge">
        💻 <span id="device-name">Đang tải...</span>
    </div>
    <div class="proxy-badge" id="proxy-badge" style="display:none;">
        🌐 <span id="proxy-name">—</span>
    </div>

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
for (let i = 0; i < 25; i++) {
    const s = document.createElement('div');
    s.className = 'snowflake';
    s.innerHTML = ['❄', '❅', '❆', '✻', '✼'][Math.floor(Math.random()*5)];
    s.style.left = Math.random() * 100 + 'vw';
    s.style.animationDuration = (5 + Math.random() * 8) + 's';
    s.style.animationDelay = (Math.random() * 5) + 's';
    s.style.fontSize = (10 + Math.random() * 20) + 'px';
    s.style.opacity = 0.4 + Math.random() * 0.5;
    document.body.appendChild(s);
}

const sessionId = "{{ session_id }}";
let done = false;
let lastQrVersion = 0;
let firstLoad = true;

async function check() {
    if (done) return;
    try {
        const r = await fetch('/api/qr/status/' + sessionId);
        const d = await r.json();
        const el = document.getElementById('status');

        if (firstLoad && d.device_name) {
            document.getElementById('device-name').textContent = d.device_name;
            if (d.proxy) {
                document.getElementById('proxy-name').textContent = d.proxy;
                document.getElementById('proxy-badge').style.display = 'inline-flex';
            }
            firstLoad = false;
        }

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
            showToast('🎄 Đã lấy QR mới!');
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
            alert('Chưa có cookie. Đợi 5-10 giây rồi thử lại.\n' + (d.note || ''));
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
    document.getElementById('device-badge').style.display = 'none';
    document.getElementById('proxy-badge').style.display = 'none';

    const el = document.getElementById('status');
    el.className = 'status';
    el.style.padding = '0';
    el.style.background = 'transparent';
    el.style.border = 'none';
    el.innerHTML =
        '<div class="result-wrap">' +
            '<div class="success-header">' +
                '<div class="success-icon">🎁</div>' +
                '<div class="success-title">Đăng nhập thành công!</div>' +
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
            '<div class="hint">🎄 Copy → paste vào web spam</div>' +
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
<title>Hết hạn</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
.expired-icon { font-size:64px; margin-bottom:18px; animation:santaBounce 2s ease-in-out infinite; }
.expired-title { color:#fff; margin-bottom:12px; font-size:22px; font-weight:900; }
.expired-text { color:rgba(255,255,255,0.8); font-size:13px; margin-bottom:22px; }
.expired-btn {
    display:inline-block; padding:14px 28px;
    background:linear-gradient(135deg,#c41e3a,#8b0000);
    color:white; border-radius:14px; text-decoration:none;
    font-weight:900; font-size:13px;
}
</style>
</head>
<body>
<div class="santa">🎅</div>
<div class="card">
    <div class="lights">
        <div class="light"></div><div class="light"></div>
        <div class="light"></div><div class="light"></div>
        <div class="light"></div>
    </div>
    <div class="expired-icon">⏰</div>
    <div class="expired-title">Session hết hạn</div>
    <div class="expired-text">Tạo mã QR mới</div>
    <a href="/" class="expired-btn">🎁 Tạo QR mới</a>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
