# ============================================================
# WEB-QR v28.0 — Multi-Device + Multi-IP + Noel Edition
# Bot by Anh Khôi • Single file
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
from datetime import datetime

from flask import (Flask, request, jsonify, render_template_string,
                   Response, redirect, url_for)

QR_TTL = 600
DATA_DIR = os.environ.get("QR_DATA_DIR", "/tmp/qr_sessions")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "devices.db")
PROXY_FILE = os.path.join(DATA_DIR, "proxies.txt")


# ============================================================
# DEVICE PROFILES — nhiều loại máy (gồm iPhone 18 Pro Max)
# ============================================================
DEVICE_PROFILES = [
    # iPhone 18 Pro Max (giả lập — chưa ra mắt)
    {"ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
     "platform": "iPhone", "viewport": {"width": 430, "height": 932},
     "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh", "cores": 8, "mem": 12,
     "device": "iPhone 18 Pro Max Safari"},
    {"ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/122.0.6261.89 Mobile/15E148 Safari/604.1",
     "platform": "iPhone", "viewport": {"width": 430, "height": 932},
     "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh", "cores": 8, "mem": 12,
     "device": "iPhone 18 Pro Max Chrome"},
    # iPhone 17 Pro Max
    {"ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
     "platform": "iPhone", "viewport": {"width": 430, "height": 932},
     "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh", "cores": 8, "mem": 8,
     "device": "iPhone 17 Pro Max Safari"},
    # iPhone 16 Pro
    {"ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
     "platform": "iPhone", "viewport": {"width": 393, "height": 852},
     "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh", "cores": 6, "mem": 8,
     "device": "iPhone 16 Pro Safari"},
    # iPhone 15 Pro
    {"ua": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
     "platform": "iPhone", "viewport": {"width": 393, "height": 852},
     "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh", "cores": 6, "mem": 6,
     "device": "iPhone 15 Pro Safari"},
    # Samsung S24 Ultra
    {"ua": "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
     "platform": "Linux armv8l", "viewport": {"width": 412, "height": 915},
     "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh", "cores": 8, "mem": 12,
     "device": "Samsung S24 Ultra"},
    # Samsung S23
    {"ua": "Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
     "platform": "Linux armv8l", "viewport": {"width": 412, "height": 915},
     "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh", "cores": 8, "mem": 8,
     "device": "Samsung S23"},
    # Pixel 8 Pro
    {"ua": "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
     "platform": "Linux armv8l", "viewport": {"width": 412, "height": 915},
     "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh", "cores": 8, "mem": 12,
     "device": "Pixel 8 Pro"},
    # Xiaomi 14
    {"ua": "Mozilla/5.0 (Linux; Android 14; 23127PN0CG) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
     "platform": "Linux armv8l", "viewport": {"width": 393, "height": 873},
     "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh", "cores": 8, "mem": 12,
     "device": "Xiaomi 14"},
    # Windows 11 Chrome
    {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
     "platform": "Win32", "viewport": {"width": 1920, "height": 1080},
     "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh", "cores": 8, "mem": 8,
     "device": "Windows 11 Chrome"},
    # Windows 11 Edge
    {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
     "platform": "Win32", "viewport": {"width": 1536, "height": 864},
     "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh", "cores": 4, "mem": 8,
     "device": "Windows 11 Edge"},
    # Windows 10 Firefox
    {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
     "platform": "Win32", "viewport": {"width": 1366, "height": 768},
     "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh", "cores": 4, "mem": 8,
     "device": "Windows 10 Firefox"},
    # macOS Sonoma Chrome
    {"ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
     "platform": "MacIntel", "viewport": {"width": 1440, "height": 900},
     "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh", "cores": 8, "mem": 16,
     "device": "macOS Sonoma Chrome"},
    # macOS Safari
    {"ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
     "platform": "MacIntel", "viewport": {"width": 1680, "height": 1050},
     "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh", "cores": 10, "mem": 16,
     "device": "macOS Safari"},
    # Ubuntu Chrome
    {"ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
     "platform": "Linux x86_64", "viewport": {"width": 1920, "height": 1080},
     "locale": "vi-VN", "tz": "Asia/Ho_Chi_Minh", "cores": 8, "mem": 8,
     "device": "Ubuntu Chrome"},
]


# ============================================================
# PROXY MANAGER — đọc từ proxies.txt
# ============================================================
def load_proxies():
    """
    Format proxies.txt (mỗi dòng 1 proxy):
      ip:port
      ip:port:user:pass
      http://user:pass@ip:port
      socks5://ip:port
    """
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
        print("[PROXY] load error: " + str(e))
    return proxies


def parse_proxy(p):
    """Chuyển proxy string → Playwright format."""
    if not p:
        return None
    if p.startswith("http://") or p.startswith("https://"):
        return {"server": p}
    if p.startswith("socks5://"):
        return {"server": p}
    parts = p.split(":")
    if len(parts) == 2:
        # ip:port
        return {"server": "http://" + parts[0] + ":" + parts[1]}
    elif len(parts) == 4:
        # ip:port:user:pass
        return {
            "server": "http://" + parts[0] + ":" + parts[1],
            "username": parts[2],
            "password": parts[3],
        }
    return None


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
        fp_hash TEXT,
        proxy TEXT,
        in_use INTEGER DEFAULT 0,
        last_used REAL,
        created_at REAL,
        use_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'available'
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        device_id TEXT,
        status TEXT,
        cookies TEXT,
        imei TEXT,
        proxy TEXT,
        created_at REAL,
        completed_at REAL,
        error TEXT
    )""")
    conn.commit()
    conn.close()


init_db()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# DEVICE MANAGEMENT
# ============================================================
def gen_imei():
    tac = random.choice(["35", "86", "01", "99", "49", "52"])
    serial = "".join(random.choices(string.digits, k=6))
    base = tac + serial

    def luhn_check(num_str):
        total = 0
        for i, ch in enumerate(reversed(num_str)):
            n = int(ch)
            if i % 2 == 0:
                n *= 2
                if n > 9:
                    n -= 9
            total += n
        return (10 - (total % 10)) % 10

    check = luhn_check(base + "0")
    return base + str(check)


def gen_device_fp(profile_idx):
    seed = "{}-{}-{}".format(profile_idx, time.time(), random.random())
    return hashlib.md5(seed.encode()).hexdigest()[:16]


def pick_proxy():
    """Lấy 1 proxy ngẫu nhiên từ list."""
    proxies = load_proxies()
    if not proxies:
        return None
    return random.choice(proxies)


def acquire_device():
    """
    Lấy device available → dùng.
    Hết → tạo mới với profile random + proxy random.
    """
    conn = db()
    row = conn.execute("""SELECT * FROM devices
        WHERE in_use = 0 AND status = 'available'
        ORDER BY use_count ASC, last_used ASC LIMIT 1""").fetchone()

    if row:
        dev_id = row["device_id"]
        conn.execute("""UPDATE devices SET in_use = 1,
            last_used = ?, use_count = use_count + 1
            WHERE device_id = ?""", (time.time(), dev_id))
        conn.commit()
        result = {
            "device_id": dev_id,
            "profile_idx": row["profile_idx"],
            "imei": row["imei"],
            "fp_hash": row["fp_hash"],
            "proxy": row["proxy"],
            "reused": True,
        }
        conn.close()
        return result

    # Tạo mới
    profile_idx = random.randint(0, len(DEVICE_PROFILES) - 1)
    imei = gen_imei()
    fp = gen_device_fp(profile_idx)
    proxy = pick_proxy()
    dev_id = "dev_" + uuid.uuid4().hex[:12]

    conn.execute("""INSERT INTO devices
        (device_id, profile_idx, imei, fp_hash, proxy, in_use, last_used,
         created_at, use_count, status)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?, 1, 'available')""",
        (dev_id, profile_idx, imei, fp, proxy, time.time(), time.time()))
    conn.commit()
    conn.close()

    return {
        "device_id": dev_id,
        "profile_idx": profile_idx,
        "imei": imei,
        "fp_hash": fp,
        "proxy": proxy,
        "reused": False,
    }


def release_device(device_id):
    try:
        conn = db()
        conn.execute("UPDATE devices SET in_use = 0 WHERE device_id = ?",
                     (device_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        print("[DEVICE] release error: " + str(e))


def mark_device_used(device_id):
    try:
        conn = db()
        conn.execute("""UPDATE devices SET status = 'used',
            in_use = 0 WHERE device_id = ?""", (device_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        print("[DEVICE] mark error: " + str(e))


def get_device_stats():
    conn = db()
    total = conn.execute("SELECT COUNT(*) as c FROM devices").fetchone()["c"]
    available = conn.execute(
        "SELECT COUNT(*) as c FROM devices WHERE in_use = 0 AND status = 'available'"
    ).fetchone()["c"]
    in_use = conn.execute(
        "SELECT COUNT(*) as c FROM devices WHERE in_use = 1"
    ).fetchone()["c"]
    used = conn.execute(
        "SELECT COUNT(*) as c FROM devices WHERE status = 'used'"
    ).fetchone()["c"]
    conn.close()
    proxies = len(load_proxies())
    return {"total": total, "available": available,
            "in_use": in_use, "used": used, "proxies": proxies}


# ============================================================
# SESSION FILE HELPERS
# ============================================================
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


app = Flask(__name__)


# ============================================================
# ZALO QR ENGINE
# ============================================================
ACTIVE_PAGES = {}


class ZaloQR:
    def __init__(self):
        self.browser = None
        self.playwright = None
        self.device = None

    async def start(self, device):
        from playwright.async_api import async_playwright
        self.device = device
        profile = DEVICE_PROFILES[device["profile_idx"]]

        launch_args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--window-size={},{}".format(
                profile["viewport"]["width"],
                profile["viewport"]["height"]),
        ]

        proxy_cfg = None
        if device.get("proxy"):
            proxy_cfg = parse_proxy(device["proxy"])
            if proxy_cfg:
                print("[PROXY] Dùng " + str(proxy_cfg.get("server", "?")))

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=launch_args,
            proxy=proxy_cfg,
        )

    async def _capture_qr(self, page):
        try:
            full_png = await page.screenshot(full_page=False)
            print("[QR] Chụp - " + str(len(full_png)) + " bytes")
            return full_png
        except Exception as e:
            print("[QR] Lỗi chụp: " + str(e))
            return None

    async def create_qr(self, session_id):
        try:
            device = acquire_device()
            print("[DEVICE] " + session_id + " → " + device["device_id"] +
                  " (" + DEVICE_PROFILES[device["profile_idx"]]["device"] + ")" +
                  (" | proxy=" + str(device["proxy"]) if device["proxy"] else " | no-proxy"))

            await self.start(device)
            profile = DEVICE_PROFILES[device["profile_idx"]]

            ctx = await self.browser.new_context(
                viewport=profile["viewport"],
                user_agent=profile["ua"],
                locale=profile["locale"],
                timezone_id=profile["tz"],
                device_scale_factor=1,
                is_mobile="Mobile" in profile["ua"],
                has_touch="Mobile" in profile["ua"],
                extra_http_headers={
                    "Accept-Language": profile["locale"] + ",en;q=0.9",
                    "Sec-Ch-Ua-Platform": '"' + profile["platform"] + '"',
                },
            )

            await ctx.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'platform', { get: () => '""" + profile["platform"] + """' });
                Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => """ + str(profile["cores"]) + """ });
                Object.defineProperty(navigator, 'deviceMemory', { get: () => """ + str(profile["mem"]) + """ });
                Object.defineProperty(navigator, 'languages', { get: () => ['vi-VN', 'vi', 'en-US', 'en'] });
                window.chrome = { runtime: {} };
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
            """)

            page = await ctx.new_page()

            print("[QR] Mở chat.zalo.me")
            await page.goto(
                "https://chat.zalo.me/",
                wait_until="domcontentloaded",
                timeout=60000,
            )

            if "Mobile" in profile["ua"]:
                try:
                    await page.set_viewport_size({"width": 1200, "height": 900})
                    await asyncio.sleep(1)
                except Exception:
                    pass

            await asyncio.sleep(6)

            try:
                btn = await page.wait_for_selector(
                    "text=/QR|Quét mã|Đăng nhập bằng QR/i", timeout=15000
                )
                await btn.click()
                print("[QR] Đã click tab QR")
            except Exception as e:
                print("[QR] Không thấy nút QR: " + str(e))

            await asyncio.sleep(5)

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
                "imei": device["imei"],
                "device_id": device["device_id"],
                "profile_idx": device["profile_idx"],
                "device_name": profile["device"],
                "proxy": device["proxy"],
                "qr_version": int(time.time()),
            })

            conn = db()
            conn.execute("""INSERT OR REPLACE INTO sessions
                (session_id, device_id, status, imei, proxy, created_at)
                VALUES (?, ?, 'waiting', ?, ?, ?)""",
                (session_id, device["device_id"], device["imei"],
                 device.get("proxy"), time.time()))
            conn.commit()
            conn.close()

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

    async def _warm_up(self, page):
        try:
            print("[WARMUP] Bắt đầu")
            await asyncio.sleep(2)
            await page.evaluate("window.scrollTo(0, 200)")
            await asyncio.sleep(random.uniform(0.8, 1.5))
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(random.uniform(0.5, 1.2))
            for _ in range(3):
                x = random.randint(100, 800)
                y = random.randint(100, 600)
                await page.mouse.move(x, y)
                await asyncio.sleep(random.uniform(0.2, 0.5))
            print("[WARMUP] Xong")
        except Exception as e:
            print("[WARMUP] Error: " + str(e))

    async def _extract(self, session_id, page):
        try:
            print("[QR] Extract - warm-up")
            await self._warm_up(page)
            await asyncio.sleep(random.uniform(2, 4))

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

            data_prev = sess_load(session_id) or {}
            if not imei:
                imei = data_prev.get("imei") or gen_imei()

            data = sess_load(session_id) or {}
            data["status"] = "done"
            data["cookies"] = ck
            data["imei"] = imei
            data["completed_at"] = time.time()
            sess_save(session_id, data)

            try:
                conn = db()
                conn.execute("""UPDATE sessions SET status = 'done',
                    cookies = ?, completed_at = ? WHERE session_id = ?""",
                    (json.dumps(ck), time.time(), session_id))
                conn.commit()
                conn.close()
            except Exception:
                pass

            device_id = data.get("device_id")
            if device_id:
                mark_device_used(device_id)
                print("[DEVICE] " + device_id + " used")

            print("[QR] DONE - IMEI: " + imei)
            await self._cleanup(session_id)
        except Exception as e:
            print("[QR] Extract error: " + str(e))
            data = sess_load(session_id) or {}
            data["status"] = "error"
            data["error"] = str(e)
            sess_save(session_id, data)
            device_id = data.get("device_id")
            if device_id:
                release_device(device_id)

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
            device_id = info.get("device_id")
            if device_id:
                release_device(device_id)


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
                imei = data.get("imei") or gen_imei()
            data["status"] = "done"
            data["cookies"] = ck
            data["imei"] = imei
            sess_save(session_id, data)
            device_id = data.get("device_id")
            if device_id:
                mark_device_used(device_id)
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


@app.route("/api/stats")
def api_stats():
    return jsonify(get_device_stats())


@app.route("/")
def home():
    return render_template_string(HTML_HOME, stats=get_device_stats())


@app.route("/qr/<session_id>")
def page_qr(session_id):
    if not sess_exists(session_id):
        return render_template_string(HTML_EXPIRED), 404
    return render_template_string(HTML_QR, session_id=session_id)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": "28.0"})


# ============================================================
# SHARED CSS — NOEL
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
    position:fixed; top:-10px;
    color:#fff; user-select:none; pointer-events:none;
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
.star {
    position:fixed; color:#ffd700;
    font-size:14px; z-index:1; pointer-events:none;
    animation:twinkle 2s ease-in-out infinite;
    text-shadow:0 0 10px rgba(255,215,0,0.9);
}
@keyframes twinkle {
    0%,100% { opacity:0.3; transform:scale(0.8); }
    50% { opacity:1; transform:scale(1.3); }
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
.lights {
    display:flex; justify-content:center; gap:8px;
    margin-bottom:14px; padding-top:6px;
}
.light {
    width:10px; height:10px; border-radius:50%;
    animation:lightGlow 1.5s ease-in-out infinite;
    box-shadow:0 0 12px currentColor;
}
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
    letter-spacing:-0.5px;
}
h1 .santa-emoji {
    display:inline-block;
    animation:santaBounce 2s ease-in-out infinite;
}
@keyframes santaBounce {
    0%,100% { transform:translateY(0) rotate(-5deg); }
    50% { transform:translateY(-8px) rotate(5deg); }
}
p.sub { color:rgba(255,255,255,0.85); margin-bottom:22px; font-size:13px; font-weight:600; letter-spacing:0.3px; }
"""


# ============================================================
# HOME PAGE
# ============================================================
HTML_HOME = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>🎄 Tạo QR Zalo — ALB Noel v28</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
.btn-start {
    width:100%; padding:18px; border:none; border-radius:20px;
    background:linear-gradient(135deg,#c41e3a,#8b0000,#c41e3a);
    background-size:200% 200%;
    color:white; font-size:17px; font-weight:900;
    font-family:inherit; cursor:pointer;
    box-shadow:0 20px 50px rgba(196,30,58,0.6), inset 0 1px 0 rgba(255,255,255,0.3);
    transition:all 0.3s cubic-bezier(0.16,1,0.3,1);
    position:relative; overflow:hidden;
}
.btn-start::before {
    content:''; position:absolute; inset:0;
    background:linear-gradient(120deg,transparent 30%,rgba(255,255,255,0.4) 50%,transparent 70%);
    transform:translateX(-100%); transition:transform 0.8s;
}
.btn-start:hover::before { transform:translateX(100%); }
.btn-start:hover { transform:translateY(-4px); box-shadow:0 25px 60px rgba(196,30,58,0.7); }
.btn-start:disabled { opacity:0.7; cursor:wait; }
.stats-bar {
    display:grid; grid-template-columns:repeat(5,1fr); gap:6px;
    margin:18px 0;
}
.stat-item {
    background:rgba(255,255,255,0.08);
    backdrop-filter:blur(10px);
    padding:10px 4px; border-radius:12px;
    border:1px solid rgba(255,255,255,0.15);
}
.stat-item .num {
    font-size:16px; font-weight:900; color:#fff;
    text-shadow:0 0 12px rgba(255,255,255,0.5);
}
.stat-item .lbl {
    font-size:8px; color:rgba(255,255,255,0.7);
    text-transform:uppercase; letter-spacing:0.5px;
    font-weight:800; margin-top:3px;
}
.steps {
    margin-top:24px; text-align:left;
    background:rgba(255,255,255,0.08);
    backdrop-filter:blur(15px);
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
    <h1><span class="santa-emoji">🎅</span> Tạo mã QR Zalo</h1>
    <p class="sub">❄️ Multi-Device • Multi-IP • Noel v28 ❄️</p>

    <div class="stats-bar">
        <div class="stat-item">
            <div class="num" id="st-total">{{ stats.total }}</div>
            <div class="lbl">Tổng</div>
        </div>
        <div class="stat-item">
            <div class="num" id="st-avail">{{ stats.available }}</div>
            <div class="lbl">Trống</div>
        </div>
        <div class="stat-item">
            <div class="num" id="st-inuse">{{ stats.in_use }}</div>
            <div class="lbl">Đang dùng</div>
        </div>
        <div class="stat-item">
            <div class="num" id="st-used">{{ stats.used }}</div>
            <div class="lbl">Đã dùng</div>
        </div>
        <div class="stat-item">
            <div class="num" id="st-proxies">{{ stats.proxies }}</div>
            <div class="lbl">Proxy</div>
        </div>
    </div>

    <button id="btn-start" class="btn-start" onclick="startQR()">
        🎁 TẠO MÃ QR MỚI
    </button>

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
for (let i = 0; i < 15; i++) {
    const st = document.createElement('div');
    st.className = 'star';
    st.innerHTML = ['✦', '✧', '⭐', '✨'][Math.floor(Math.random()*4)];
    st.style.left = Math.random() * 100 + 'vw';
    st.style.top = Math.random() * 100 + 'vh';
    st.style.animationDelay = (Math.random() * 2) + 's';
    document.body.appendChild(st);
}

async function startQR() {
    const btn = document.getElementById('btn-start');
    btn.disabled = true;
    btn.innerHTML = '⏳ ĐANG TẠO QR...';
    try {
        const r = await fetch('/api/qr/create', { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            btn.innerHTML = '🎉 XONG!';
            setTimeout(function() { window.location.href = d.qr_url; }, 300);
        } else {
            alert('Lỗi: ' + (d.error || 'Không rõ'));
            btn.disabled = false;
            btn.innerHTML = '🎁 TẠO MÃ QR MỚI';
        }
    } catch (e) {
        alert('Lỗi: ' + e.message);
        btn.disabled = false;
        btn.innerHTML = '🎁 TẠO MÃ QR MỚI';
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
<title>🎄 Quét QR — ALB Noel v28</title>
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
    text-shadow:0 0 8px rgba(255,215,0,0.4);
}
.proxy-badge {
    display:inline-flex; align-items:center; gap:6px;
    padding:5px 12px; border-radius:20px;
    background:rgba(0,212,255,0.15);
    border:1px solid rgba(0,212,255,0.4);
    color:#00d4ff; font-size:9.5px; font-weight:800;
    text-transform:uppercase; letter-spacing:0.5px;
    margin-bottom:14px;
    max-width:100%; overflow:hidden; text-overflow:ellipsis;
}
.qr-container {
    background:rgba(255,255,255,0.95);
    padding:10px; border-radius:18px;
    margin:0 auto 14px; max-width:340px; width:100%;
    box-shadow:0 20px 50px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.9);
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
    backdrop-filter:blur(10px);
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
    box-shadow:0 10px 25px rgba(16,185,129,0.5), inset 0 1px 0 rgba(255,255,255,0.3);
    transition:all 0.3s;
}
.confirm-btn:hover { transform:translateY(-2px); }
.confirm-btn.refresh {
    background:linear-gradient(135deg,#f59e0b,#d97706);
    box-shadow:0 10px 25px rgba(245,158,11,0.5), inset 0 1px 0 rgba(255,255,255,0.3);
}
.result-wrap { text-align:left; }
.success-header {
    text-align:center; padding:18px;
    background:rgba(16,185,129,0.15);
    border:1px solid rgba(16,185,129,0.4);
    border-radius:18px; margin-bottom:16px;
    backdrop-filter:blur(10px);
}
.success-icon { font-size:46px; margin-bottom:6px; animation:santaBounce 2s ease-in-out infinite; }
.success-title { font-size:16px; font-weight:900; color:#34d399; }
.result {
    padding:14px; border-radius:14px;
    background:rgba(255,255,255,0.1);
    border:1px solid rgba(255,255,255,0.2);
    margin-bottom:10px;
    backdrop-filter:blur(10px);
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
    transition:all 0.3s;
    box-shadow:inset 0 1px 0 rgba(255,255,255,0.3);
}
.copy-btn:hover { transform:translateY(-2px); }
.copy-btn.green { background:linear-gradient(135deg,#10b981,#059669); }
.copy-btn.purple { background:linear-gradient(135deg,#8b5cf6,#7c3aed); }
.copy-btn.blue { background:linear-gradient(135deg,#3b82f6,#2563eb); }
.hint { margin-top:10px; font-size:11px; color:rgba(255,255,255,0.7); text-align:center; }
.toast {
    position:fixed; bottom:24px; left:50%; transform:translateX(-50%);
    background:linear-gradient(135deg,#10b981,#059669);
    color:white; padding:12px 24px; border-radius:50px;
    font-weight:900; font-size:13px; z-index:9999;
    box-shadow:0 10px 30px rgba(16,185,129,0.5);
    animation:toastIn 0.3s ease;
}
@keyframes toastIn {
    from { opacity:0; transform:translateX(-50%) translateY(20px); }
    to { opacity:1; transform:translateX(-50%) translateY(0); }
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
.expired-title { color:#fff; margin-bottom:12px; font-size:22px; font-weight:900; text-shadow:0 2px 20px rgba(255,255,255,0.4); }
.expired-text { color:rgba(255,255,255,0.8); font-size:13px; margin-bottom:22px; }
.expired-btn {
    display:inline-block; padding:14px 28px;
    background:linear-gradient(135deg,#c41e3a,#8b0000);
    color:white; border-radius:14px; text-decoration:none;
    font-weight:900; font-size:13px;
    box-shadow:0 10px 30px rgba(196,30,58,0.5);
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


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
