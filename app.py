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

# ============================================================
# POOL USER-AGENT iPHONE / iPAD / MAC — CHỈ THIẾT BỊ IP
# ============================================================
UA_POOL = [
    # ===== iPHONE 18 PRO MAX — iOS 19 =====
    "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 19_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.1 Mobile/15E148 Safari/604.1",

    # ===== iPHONE 18 / 18 PRO — iOS 19 =====
    "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Mobile/15E148 Safari/604.1",

    # ===== iPHONE 17 PRO MAX — iOS 18.7 =====
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.7 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7_8 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.7 Mobile/15E148 Safari/604.1",

    # ===== iPHONE 17 / 17 PRO — iOS 18.5 =====
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",

    # ===== iPHONE 16 PRO MAX — iOS 18.3 =====
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.1",

    # ===== iPHONE 16 PRO — iOS 18.2 =====
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1",

    # ===== iPHONE 16 / 16 PLUS — iOS 18.1 =====
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",

    # ===== iPHONE 16E — iOS 18.0 =====
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",

    # ===== iPHONE 15 PRO MAX — iOS 17.6 =====
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",

    # ===== iPHONE 15 PRO — iOS 17.5 =====
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",

    # ===== iPHONE 15 / 15 PLUS — iOS 17.4 =====
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",

    # ===== iPHONE 14 PRO MAX — iOS 17.3 =====
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Mobile/15E148 Safari/604.1",

    # ===== iPHONE 14 / 14 PLUS — iOS 17.2 =====
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",

    # ===== iPAD PRO M4 — iPadOS 18 =====
    "Mozilla/5.0 (iPad; CPU OS 18_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 18_6_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1",

    # ===== iPAD AIR M3 — iPadOS 17 =====
    "Mozilla/5.0 (iPad; CPU OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",

    # ===== MACBOOK PRO M4 — macOS 15 =====
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_2) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_8_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15",

    # ===== MACBOOK AIR M3 — macOS 14 =====
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",

    # ===== iMAC M3 — macOS 14 =====
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]


def pick_ua():
    """Chọn ngẫu nhiên 1 UA iPhone/iPad/Mac."""
    return random.choice(UA_POOL)


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
# ZALO QR LOGIN — MỖI SESSION 1 UA iPHONE/iPAD/MAC RIÊNG
# ============================================================
class ZaloQR:
    def __init__(self):
        self.browser = None
        self.playwright = None
        self.ua = pick_ua()          # UA riêng cho từng session
        self.device_name = self._device_from_ua(self.ua)

    @staticmethod
    def _device_from_ua(ua):
        if "iPhone" in ua:
            return "iPhone"
        if "iPad" in ua:
            return "iPad"
        if "Macintosh" in ua:
            return "Mac"
        return "Unknown"

    async def start(self):
        from playwright.async_api import async_playwright
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
                viewport={"width": 600, "height": 900},
                user_agent=self.ua,               # ← UA iPhone/iPad/Mac
                locale="vi-VN",
                is_mobile=("iPhone" in self.ua or "iPad" in self.ua),
                has_touch=("iPhone" in self.ua or "iPad" in self.ua),
                device_scale_factor=3,
            )
            page = await ctx.new_page()

            print("[QR] Mo chat.zalo.me | Device: " + self.device_name)
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
                "device": self.device_name,       # lưu tên thiết bị
                "user_agent": self.ua,            # lưu UA
            })

            print("[QR] Session " + session_id + " ready | " + self.device_name)

            ACTIVE_PAGES[session_id] = {
                "page": page,
                "browser": self.browser,
                "playwright": self.playwright,
                "device": self.device_name,
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
                "device": self.device_name,
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

            print("[QR] DONE - IMEI: " + imei + " | " + self.device_name)
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
        "device": data.get("device", "Unknown"),
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
            "device": data.get("device", "Unknown"),
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
                "device": data.get("device", "Unknown"),
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
    return jsonify({"status": "ok", "version": "27.0"})


# ============================================================
# CSS — KÍNH LỎNG (LIQUID GLASS) + MỘT MÀU NỔI BẬT
# ============================================================
SHARED_CSS = """
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html { -webkit-text-size-adjust:100%; touch-action:manipulation; }
body {
    font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;
    min-height:100vh; min-height:100dvh;
    display:flex; align-items:center; justify-content:center;
    background:
        radial-gradient(circle at 15% 20%, rgba(0,229,255,0.35), transparent 45%),
        radial-gradient(circle at 85% 30%, rgba(124,58,237,0.35), transparent 50%),
        radial-gradient(circle at 50% 85%, rgba(0,255,170,0.30), transparent 50%),
        linear-gradient(135deg,#0b0f1a 0%,#131a2b 50%,#0b0f1a 100%);
    background-attachment:fixed;
    padding:16px;
    color:#fff;
    overflow-x:hidden;
}
body::before {
    content:"";
    position:fixed; inset:-20%;
    background:
        radial-gradient(circle at 30% 40%, rgba(0,229,255,0.18), transparent 55%),
        radial-gradient(circle at 70% 60%, rgba(168,85,247,0.18), transparent 55%);
    filter:blur(40px);
    animation:floatBg 14s ease-in-out infinite alternate;
    pointer-events:none;
    z-index:-1;
}
@keyframes floatBg {
    0% { transform:translate(0,0) scale(1); }
    100% { transform:translate(30px,-30px) scale(1.1); }
}
.card {
    position:relative;
    background:rgba(255,255,255,0.06);
    backdrop-filter:blur(28px) saturate(200%);
    -webkit-backdrop-filter:blur(28px) saturate(200%);
    padding:34px 22px; border-radius:30px;
    border:1px solid rgba(255,255,255,0.18);
    box-shadow:
        0 8px 32px rgba(0,0,0,0.5),
        inset 0 1px 0 rgba(255,255,255,0.25),
        inset 0 -1px 0 rgba(255,255,255,0.05);
    text-align:center; max-width:520px; width:100%;
    animation:cardIn 0.9s cubic-bezier(0.16,1,0.3,1);
    overflow:hidden;
}
.card::after {
    content:"";
    position:absolute; top:-50%; left:-50%;
    width:200%; height:200%;
    background:linear-gradient(120deg, transparent 30%, rgba(255,255,255,0.10) 50%, transparent 70%);
    transform:rotate(25deg);
    animation:shine 6s linear infinite;
    pointer-events:none;
}
@keyframes shine {
    0% { transform:translateX(-100%) rotate(25deg); }
    100% { transform:translateX(100%) rotate(25deg); }
}
@keyframes cardIn {
    from { opacity:0; transform:translateY(60px) scale(0.94); }
    to { opacity:1; transform:translateY(0) scale(1); }
}
h1 {
    color:#ffffff;
    margin-bottom:8px;
    font-size:24px; font-weight:900;
    letter-spacing:-0.5px;
    text-shadow:0 2px 20px rgba(0,229,255,0.5);
}
p.sub { color:rgba(255,255,255,0.65); margin-bottom:22px; font-size:13px; font-weight:500; }
"""


HTML_HOME = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Tạo QR — ALB</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
.btn-start {
    width:100%; padding:18px; border:none; border-radius:20px;
    background:rgba(0,229,255,0.14);
    border:1px solid rgba(0,229,255,0.55);
    color:#00e5ff;
    font-size:17px; font-weight:900;
    font-family:inherit; cursor:pointer;
    box-shadow:0 0 30px rgba(0,229,255,0.35), inset 0 0 20px rgba(0,229,255,0.10);
    transition:all 0.3s ease;
    backdrop-filter:blur(10px);
}
.btn-start:hover {
    background:rgba(0,229,255,0.22);
    box-shadow:0 0 45px rgba(0,229,255,0.55), inset 0 0 25px rgba(0,229,255,0.15);
    transform:translateY(-3px);
}
.btn-start:disabled { opacity:0.6; cursor:wait; transform:none; }
.steps {
    margin-top:24px; text-align:left;
    background:rgba(255,255,255,0.05);
    border:1px solid rgba(255,255,255,0.12);
    padding:18px; border-radius:18px;
    font-size:13px; color:rgba(255,255,255,0.85); line-height:1.9;
    backdrop-filter:blur(10px);
}
.steps b { color:#00e5ff; }
.device-tag {
    display:inline-block; margin-top:10px;
    font-size:11px; color:#00e5ff;
    background:rgba(0,229,255,0.10);
    border:1px solid rgba(0,229,255,0.4);
    padding:4px 12px; border-radius:20px;
    font-weight:700;
}
</style>
</head>
<body>
<div class="card">
    <h1>📱 TẠO MÃ QR ZALO</h1>
    <p class="sub">Quét QR → Nhận IMEI + Cookie</p>
    <button id="btn-start" class="btn-start" onclick="startQR()">▶ TẠO MÃ QR</button>
    <div class="device-tag">Device: iPhone / iPad / Mac</div>
    <div class="steps">
        <b>HƯỚNG DẪN</b><br>
        1 › Bấm nút trên<br>
        2 › Đợi 15-30 giây → QR hiện<br>
        3 › Mở Zalo → QR → Quét<br>
        4 › Đồng ý trên điện thoại<br>
        5 › Web tự hiện IMEI + Cookie
    </div>
</div>
<script>
async function startQR() {
    const btn = document.getElementById('btn-start');
    btn.disabled = true;
    btn.innerHTML = 'ĐANG TẠO QR...';
    try {
        const r = await fetch('/api/qr/create', { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            btn.innerHTML = 'XONG!';
            setTimeout(function() { window.location.href = d.qr_url; }, 300);
        } else {
            alert('Lỗi: ' + (d.error || 'Không rõ'));
            btn.disabled = false;
            btn.innerHTML = '▶ TẠO MÃ QR';
        }
    } catch (e) {
        alert('Lỗi: ' + e.message);
        btn.disabled = false;
        btn.innerHTML = '▶ TẠO MÃ QR';
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
<title>Quét QR — ALB</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
.qr-container {
    background:rgba(255,255,255,0.95);
    padding:10px; border-radius:20px;
    margin:0 auto 14px; max-width:320px; width:100%;
    box-shadow:
        0 20px 50px rgba(0,0,0,0.5),
        0 0 30px rgba(0,229,255,0.30);
    border:1px solid rgba(0,229,255,0.5);
    overflow:hidden;
    animation:pulseQR 3s ease-in-out infinite;
}
@keyframes pulseQR {
    0%,100% { box-shadow:0 20px 50px rgba(0,0,0,0.5), 0 0 30px rgba(0,229,255,0.30); }
    50% { box-shadow:0 20px 50px rgba(0,0,0,0.5), 0 0 55px rgba(0,229,255,0.65); }
}
.qr-container img {
    width:100%; height:auto; display:block; border-radius:14px;
}
.qr-label {
    text-align:center; font-size:12px; color:rgba(255,255,255,0.75);
    font-weight:600; margin-bottom:14px;
    letter-spacing:0.3px;
}
.device-badge {
    display:inline-block; margin-bottom:12px;
    font-size:11px; color:#00e5ff;
    background:rgba(0,229,255,0.10);
    border:1px solid rgba(0,229,255,0.45);
    padding:4px 12px; border-radius:20px;
    font-weight:800; letter-spacing:0.5px;
}
.status {
    padding:14px; border-radius:16px;
    font-size:13px; font-weight:800;
    display:flex; align-items:center; justify-content:center; gap:8px;
    backdrop-filter:blur(10px);
    border:1px solid rgba(255,255,255,0.15);
}
.status.waiting {
    background:rgba(0,229,255,0.10);
    color:#00e5ff;
    border-color:rgba(0,229,255,0.4);
    box-shadow:inset 0 0 20px rgba(0,229,255,0.08);
}
.status.error {
    background:rgba(255,60,60,0.12);
    color:#ff6b6b;
    border-color:rgba(255,60,60,0.4);
}
.status.expired {
    background:rgba(255,255,255,0.06);
    color:rgba(255,255,255,0.7);
}
.confirm-btn {
    width:100%; margin-top:10px; padding:14px;
    background:rgba(0,229,255,0.14);
    border:1px solid rgba(0,229,255,0.55);
    color:#00e5ff;
    border-radius:16px;
    font-size:13px; font-weight:900; font-family:inherit; cursor:pointer;
    box-shadow:0 0 25px rgba(0,229,255,0.30), inset 0 0 15px rgba(0,229,255,0.08);
    transition:all 0.3s ease;
    letter-spacing:0.5px;
}
.confirm-btn:hover {
    background:rgba(0,229,255,0.24);
    box-shadow:0 0 40px rgba(0,229,255,0.55), inset 0 0 20px rgba(0,229,255,0.12);
}
.confirm-btn.refresh {
    background:rgba(255,200,0,0.12);
    border-color:rgba(255,200,0,0.5);
    color:#ffc800;
    box-shadow:0 0 25px rgba(255,200,0,0.25), inset 0 0 15px rgba(255,200,0,0.08);
}
.confirm-btn.refresh:hover {
    background:rgba(255,200,0,0.22);
    box-shadow:0 0 40px rgba(255,200,0,0.5), inset 0 0 20px rgba(255,200,0,0.12);
}
.result-wrap { text-align:left; }
.success-header {
    text-align:center; padding:18px;
    background:rgba(0,229,255,0.10);
    border:1px solid rgba(0,229,255,0.4);
    border-radius:18px; margin-bottom:16px;
    box-shadow:inset 0 0 25px rgba(0,229,255,0.10);
}
.success-icon { font-size:46px; margin-bottom:6px; }
.success-title { font-size:16px; font-weight:900; color:#00e5ff; letter-spacing:0.5px; }
.result {
    padding:14px; border-radius:16px;
    background:rgba(255,255,255,0.05);
    border:1px solid rgba(255,255,255,0.15);
    margin-bottom:10px;
    backdrop-filter:blur(10px);
}
.result .label {
    font-size:10px; color:#00e5ff;
    text-transform:uppercase; font-weight:900;
    letter-spacing:1.5px; margin-bottom:6px;
}
.result .value {
    font-family:'SF Mono',Menlo,monospace; font-size:11px;
    color:#ffffff; word-break:break-all; line-height:1.6; font-weight:600;
}
.result .value.scroll {
    max-height:150px; overflow-y:auto;
    background:rgba(0,0,0,0.25); padding:10px;
    border-radius:10px; margin-top:6px;
    border:1px solid rgba(255,255,255,0.08);
}
.copy-btn {
    width:100%; padding:14px; border-radius:14px;
    background:rgba(0,229,255,0.14);
    border:1px solid rgba(0,229,255,0.55);
    color:#00e5ff;
    font-size:13px; font-weight:900;
    font-family:inherit; cursor:pointer; margin-top:8px;
    box-shadow:0 0 22px rgba(0,229,255,0.28), inset 0 0 12px rgba(0,229,255,0.08);
    transition:all 0.3s ease;
    letter-spacing:0.5px;
}
.copy-btn:hover {
    background:rgba(0,229,255,0.24);
    box-shadow:0 0 38px rgba(0,229,255,0.55), inset 0 0 18px rgba(0,229,255,0.12);
}
.copy-btn.green { color:#00e5ff; border-color:rgba(0,229,255,0.6); background:rgba(0,229,255,0.16); }
.copy-btn.purple { color:#a855f7; border-color:rgba(168,85,247,0.55); background:rgba(168,85,247,0.12); box-shadow:0 0 22px rgba(168,85,247,0.28), inset 0 0 12px rgba(168,85,247,0.08); }
.copy-btn.purple:hover { background:rgba(168,85,247,0.22); box-shadow:0 0 38px rgba(168,85,247,0.55); }
.copy-btn.blue { color:#3b82f6; border-color:rgba(59,130,246,0.55); background:rgba(59,130,246,0.12); box-shadow:0 0 22px rgba(59,130,246,0.28), inset 0 0 12px rgba(59,130,246,0.08); }
.copy-btn.blue:hover { background:rgba(59,130,246,0.22); box-shadow:0 0 38px rgba(59,130,246,0.55); }
.hint { margin-top:12px; font-size:11px; color:rgba(255,255,255,0.5); text-align:center; letter-spacing:0.3px; }
.toast {
    position:fixed; bottom:24px; left:50%; transform:translateX(-50%);
    background:rgba(0,229,255,0.18);
    border:1px solid rgba(0,229,255,0.6);
    color:#00e5ff;
    backdrop-filter:blur(20px);
    padding:12px 24px; border-radius:50px;
    font-weight:900; font-size:13px; z-index:9999;
    box-shadow:0 0 30px rgba(0,229,255,0.5);
    animation:toastIn 0.3s ease;
}
@keyframes toastIn {
    from { opacity:0; transform:translate(-50%,20px); }
    to { opacity:1; transform:translate(-50%,0); }
}
</style>
</head>
<body>
<div class="card">
    <h1>📱 QUÉT QR ZALO</h1>
    <p class="sub">Mở Zalo → QR → Quét</p>
    <div class="device-badge" id="device-badge">DEVICE: —</div>

    <div id="qr-area">
        <div class="qr-container">
            <img id="qr-img" src="/qr-image/{{ session_id }}" alt="QR">
        </div>
        <div class="qr-label">QUÉT → ĐỒNG Ý TRÊN ĐIỆN THOẠI</div>
    </div>

    <div id="status" class="status waiting">
        <span>⏳</span> <span>Đang chờ quét...</span>
    </div>

    <button id="btn-refresh" class="confirm-btn refresh" onclick="refreshQR()">
        ↻ LÀM MỚI QR
    </button>

    <button id="btn-check" class="confirm-btn" onclick="forceCheck()">
        ✓ TÔI ĐÃ ĐỒNG Ý - LẤY COOKIE
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

        if (d.device && d.device !== 'Unknown') {
            document.getElementById('device-badge').textContent = 'DEVICE: ' + d.device;
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
            el.innerHTML = '<span>✕</span> <span>' + (d.error || 'Lỗi') + '</span>';
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
    btn.innerHTML = 'ĐANG LẤY QR MỚI...';
    try {
        const r = await fetch('/api/qr/refresh/' + sessionId, { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            document.getElementById('qr-img').src = '/qr-image/' + sessionId + '?v=' + d.qr_version;
            showToast('ĐÃ LẤY QR MỚI');
            lastQrVersion = d.qr_version;
        } else {
            alert('Lỗi: ' + (d.error || 'Không rõ'));
        }
    } catch (e) {
        alert('Lỗi: ' + e.message);
    }
    btn.disabled = false;
    btn.innerHTML = '↻ LÀM MỚI QR';
}

async function forceCheck() {
    const btn = document.getElementById('btn-check');
    btn.disabled = true;
    btn.innerHTML = 'ĐANG LẤY COOKIE...';
    try {
        const r = await fetch('/api/qr/force-extract/' + sessionId, { method: 'POST' });
        const d = await r.json();
        if (d.status === 'done') {
            done = true;
            showResult(d);
        } else {
            alert('Chưa có cookie. Đợi 5-10 giây rồi thử lại.\n' + (d.note || ''));
            btn.disabled = false;
            btn.innerHTML = '✓ TÔI ĐÃ ĐỒNG Ý - LẤY COOKIE';
        }
    } catch (e) {
        alert('Lỗi: ' + e.message);
        btn.disabled = false;
        btn.innerHTML = '✓ TÔI ĐÃ ĐỒNG Ý - LẤY COOKIE';
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
    const device = d.device || 'Unknown';

    document.getElementById('qr-area').style.display = 'none';
    document.getElementById('btn-check').style.display = 'none';
    document.getElementById('btn-refresh').style.display = 'none';
    document.getElementById('device-badge').textContent = 'DEVICE: ' + device;

    const el = document.getElementById('status');
    el.className = 'status';
    el.style.padding = '0';
    el.style.background = 'transparent';
    el.style.border = 'none';
    el.innerHTML =
        '<div class="result-wrap">' +
            '<div class="success-header">' +
                '<div class="success-icon">✓</div>' +
                '<div class="success-title">ĐĂNG NHẬP THÀNH CÔNG</div>' +
            '</div>' +
            '<div class="result">' +
                '<div class="label">DEVICE</div>' +
                '<div class="value">' + escapeHtml(device) + '</div>' +
            '</div>' +
            '<div class="result">' +
                '<div class="label">IMEI</div>' +
                '<div class="value">' + escapeHtml(imei) + '</div>' +
            '</div>' +
            '<div class="result">' +
                '<div class="label">COOKIE</div>' +
                '<div class="value scroll">' + escapeHtml(cookiesStr) + '</div>' +
            '</div>' +
            '<button class="copy-btn green" id="copy-all">COPY CẢ IMEI + COOKIE</button>' +
            '<button class="copy-btn blue" id="copy-imei">CHỈ COPY IMEI</button>' +
            '<button class="copy-btn purple" id="copy-ck">CHỈ COPY COOKIE</button>' +
            '<div class="hint">Copy → paste vào web</div>' +
        '</div>';

    document.getElementById('copy-all').onclick = function() { copyText(combo); };
    document.getElementById('copy-imei').onclick = function() { copyText(imei); };
    document.getElementById('copy-ck').onclick = function() { copyText(cookiesStr); };
}

function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function() {
            showToast('ĐÃ COPY');
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
    try { document.execCommand('copy'); showToast('ĐÃ COPY'); }
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
.expired-icon { font-size:64px; margin-bottom:18px; }
.expired-title { color:#ffffff; margin-bottom:12px; font-size:22px; font-weight:900; letter-spacing:-0.5px; }
.expired-text { color:rgba(255,255,255,0.6); font-size:13px; margin-bottom:22px; }
.expired-btn {
    display:inline-block; padding:14px 28px;
    background:rgba(0,229,255,0.14);
    border:1px solid rgba(0,229,255,0.55);
    color:#00e5ff;
    border-radius:16px; text-decoration:none;
    font-weight:900; font-size:13px;
    box-shadow:0 0 25px rgba(0,229,255,0.30), inset 0 0 15px rgba(0,229,255,0.08);
    transition:all 0.3s ease;
    letter-spacing:0.5px;
}
.expired-btn:hover {
    background:rgba(0,229,255,0.24);
    box-shadow:0 0 40px rgba(0,229,255,0.55), inset 0 0 20px rgba(0,229,255,0.12);
}
</style>
</head>
<body>
<div class="card">
    <div class="expired-icon">⏰</div>
    <div class="expired-title">SESSION HẾT HẠN</div>
    <div class="expired-text">Tạo mã QR mới để tiếp tục</div>
    <a href="/" class="expired-btn">↻ TẠO QR MỚI</a>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
