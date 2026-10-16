import io
import os
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
# ZALO QR LOGIN
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
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                locale="vi-VN",
            )
            page = await ctx.new_page()

            print("[QR] Mo chat.zalo.me")
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
                    print("[QR] ✅ PHAT HIEN ZPW_SEK")
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
    return jsonify({"status": "ok", "version": "26.0"})


# ============================================================
# SHARED LIQUID GLASS CSS - THẾ HỆ MỚI
# ============================================================
SHARED_CSS = """
* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
    -webkit-tap-highlight-color: transparent;
}
html {
    -webkit-text-size-adjust: 100%;
    touch-action: manipulation;
}
body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    min-height: 100vh;
    min-height: 100dvh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #0a0a1a;
    padding: 16px;
    position: relative;
    overflow-x: hidden;
    color: #e2e8f0;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
}

/* ===== LIQUID GLASS BACKGROUND ORBS ===== */
body::before,
body::after {
    content: '';
    position: fixed;
    border-radius: 50%;
    filter: blur(80px);
    opacity: 0.55;
    z-index: 0;
    pointer-events: none;
    will-change: transform;
}
body::before {
    width: 520px;
    height: 520px;
    background: radial-gradient(circle at 30% 30%, #7c3aed, #4f46e5 50%, transparent 70%);
    top: -180px;
    left: -180px;
    animation: orbFloat1 22s ease-in-out infinite;
}
body::after {
    width: 620px;
    height: 620px;
    background: radial-gradient(circle at 70% 70%, #ec4899, #f43f5e 50%, transparent 70%);
    bottom: -220px;
    right: -220px;
    animation: orbFloat2 26s ease-in-out infinite;
}

@keyframes orbFloat1 {
    0%, 100% { transform: translate(0, 0) scale(1); }
    33%      { transform: translate(120px, 90px) scale(1.15); }
    66%      { transform: translate(-60px, 150px) scale(0.9); }
}
@keyframes orbFloat2 {
    0%, 100% { transform: translate(0, 0) scale(1); }
    33%      { transform: translate(-140px, -80px) scale(1.1); }
    66%      { transform: translate(80px, -120px) scale(0.92); }
}

/* ===== EXTRA GRADIENT LAYER ===== */
.glass-bg-extra {
    position: fixed;
    inset: 0;
    z-index: 1;
    pointer-events: none;
    background:
        radial-gradient(circle at 15% 85%, rgba(59, 130, 246, 0.35) 0%, transparent 45%),
        radial-gradient(circle at 85% 15%, rgba(236, 72, 153, 0.3) 0%, transparent 45%),
        radial-gradient(circle at 50% 50%, rgba(139, 92, 246, 0.15) 0%, transparent 60%);
    animation: bgPulse 10s ease-in-out infinite;
}
@keyframes bgPulse {
    0%, 100% { opacity: 0.85; }
    50%      { opacity: 1; }
}

/* ===== CARD - LIQUID GLASS ===== */
.card {
    position: relative;
    z-index: 10;
    width: 100%;
    max-width: 500px;
    padding: 40px 28px 32px;
    border-radius: 36px;
    background: linear-gradient(
        135deg,
        rgba(255, 255, 255, 0.13) 0%,
        rgba(255, 255, 255, 0.06) 50%,
        rgba(255, 255, 255, 0.10) 100%
    );
    backdrop-filter: blur(30px) saturate(180%);
    -webkit-backdrop-filter: blur(30px) saturate(180%);
    border: 1px solid rgba(255, 255, 255, 0.22);
    box-shadow:
        0 25px 60px rgba(0, 0, 0, 0.55),
        0 8px 24px rgba(0, 0, 0, 0.35),
        inset 0 1px 0 rgba(255, 255, 255, 0.28),
        inset 0 -1px 0 rgba(255, 255, 255, 0.08);
    animation: cardIn 0.9s cubic-bezier(0.16, 1, 0.3, 1);
    overflow: hidden;
    text-align: center;
}

/* Subtle shine reflection on top */
.card::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 2px;
    background: linear-gradient(
        90deg,
        transparent 0%,
        rgba(255, 255, 255, 0.6) 50%,
        transparent 100%
    );
    animation: shine 3s ease-in-out infinite;
}
@keyframes shine {
    0%, 100% { opacity: 0.4; transform: translateX(-30%); }
    50%      { opacity: 1; transform: translateX(30%); }
}

@keyframes cardIn {
    from {
        opacity: 0;
        transform: translateY(60px) scale(0.94);
        filter: blur(10px);
    }
    to {
        opacity: 1;
        transform: translateY(0) scale(1);
        filter: blur(0);
    }
}

/* ===== HEADINGS ===== */
h1 {
    font-size: 26px;
    font-weight: 800;
    letter-spacing: -0.5px;
    margin-bottom: 8px;
    background: linear-gradient(
        135deg,
        #ffffff 0%,
        #c7d2fe 40%,
        #f9a8d4 70%,
        #ffffff 100%
    );
    -webkit-background-clip: text;
    background-clip: text;
    -webkit-text-fill-color: transparent;
    text-shadow: 0 0 40px rgba(196, 181, 253, 0.4);
}
p.sub {
    color: rgba(226, 232, 240, 0.75);
    font-size: 13px;
    font-weight: 500;
    margin-bottom: 26px;
    letter-spacing: 0.2px;
}

/* ===== BUTTONS - LIQUID GLASS ===== */
.btn-start,
.confirm-btn,
.copy-btn {
    position: relative;
    width: 100%;
    padding: 16px 20px;
    border: 1px solid rgba(255, 255, 255, 0.25);
    border-radius: 18px;
    font-family: inherit;
    font-size: 14px;
    font-weight: 800;
    letter-spacing: 0.3px;
    cursor: pointer;
    overflow: hidden;
    transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.25s ease;
    -webkit-backdrop-filter: blur(20px);
    backdrop-filter: blur(20px);
    box-shadow:
        0 10px 30px rgba(0, 0, 0, 0.35),
        inset 0 1px 0 rgba(255, 255, 255, 0.35),
        inset 0 -1px 0 rgba(0, 0, 0, 0.1);
    color: #ffffff;
}
.btn-start::after,
.confirm-btn::after,
.copy-btn::after {
    content: '';
    position: absolute;
    top: 0;
    left: -100%;
    width: 100%;
    height: 100%;
    background: linear-gradient(
        90deg,
        transparent,
        rgba(255, 255, 255, 0.4),
        transparent
    );
    transition: left 0.6s ease;
}
.btn-start:hover::after,
.confirm-btn:hover::after,
.copy-btn:hover::after {
    left: 100%;
}
.btn-start:hover,
.confirm-btn:hover:not(:disabled),
.copy-btn:hover {
    transform: translateY(-3px);
    box-shadow:
        0 18px 40px rgba(0, 0, 0, 0.45),
        inset 0 1px 0 rgba(255, 255, 255, 0.45);
}
.btn-start:active,
.confirm-btn:active,
.copy-btn:active {
    transform: translateY(0) scale(0.98);
}
.btn-start:disabled,
.confirm-btn:disabled {
    opacity: 0.6;
    cursor: wait;
}

.btn-start {
    background: linear-gradient(135deg,
        rgba(236, 72, 153, 0.85),
        rgba(139, 92, 246, 0.85));
    margin-bottom: 22px;
}
.confirm-btn {
    background: linear-gradient(135deg,
        rgba(16, 185, 129, 0.85),
        rgba(5, 150, 105, 0.85));
    margin-top: 12px;
}
.confirm-btn.refresh {
    background: linear-gradient(135deg,
        rgba(245, 158, 11, 0.85),
        rgba(217, 119, 6, 0.85));
}
.copy-btn {
    background: linear-gradient(135deg,
        rgba(99, 102, 241, 0.85),
        rgba(139, 92, 246, 0.85));
    margin-top: 10px;
    font-size: 13px;
}
.copy-btn.green {
    background: linear-gradient(135deg,
        rgba(16, 185, 129, 0.85),
        rgba(5, 150, 105, 0.85));
}
.copy-btn.purple {
    background: linear-gradient(135deg,
        rgba(139, 92, 246, 0.85),
        rgba(124, 58, 237, 0.85));
}
.copy-btn.blue {
    background: linear-gradient(135deg,
        rgba(59, 130, 246, 0.85),
        rgba(37, 99, 235, 0.85));
}

/* ===== STEPS BOX ===== */
.steps {
    margin-top: 22px;
    text-align: left;
    padding: 18px 20px;
    background: linear-gradient(135deg,
        rgba(255, 255, 255, 0.08),
        rgba(255, 255, 255, 0.03));
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 20px;
    font-size: 13px;
    color: rgba(226, 232, 240, 0.85);
    line-height: 1.9;
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.15),
        0 8px 20px rgba(0, 0, 0, 0.25);
}
.steps b {
    color: #ffffff;
    font-weight: 700;
    display: block;
    margin-bottom: 6px;
    font-size: 13px;
    letter-spacing: 0.3px;
}

/* ===== QR AREA ===== */
.qr-container {
    position: relative;
    background: rgba(255, 255, 255, 0.98);
    padding: 12px;
    border-radius: 24px;
    margin: 0 auto 16px;
    max-width: 340px;
    width: 100%;
    box-shadow:
        0 20px 50px rgba(0, 0, 0, 0.55),
        0 0 0 1px rgba(255, 255, 255, 0.3),
        inset 0 1px 0 rgba(255, 255, 255, 0.9);
    overflow: hidden;
    animation: qrPulse 3s ease-in-out infinite;
}
@keyframes qrPulse {
    0%, 100% { box-shadow:
        0 20px 50px rgba(0, 0, 0, 0.55),
        0 0 0 1px rgba(255, 255, 255, 0.3),
        0 0 0 0 rgba(139, 92, 246, 0.4),
        inset 0 1px 0 rgba(255, 255, 255, 0.9);
    }
    50% { box-shadow:
        0 20px 50px rgba(0, 0, 0, 0.55),
        0 0 0 1px rgba(255, 255, 255, 0.3),
        0 0 0 16px rgba(139, 92, 246, 0),
        inset 0 1px 0 rgba(255, 255, 255, 0.9);
    }
}
.qr-container img {
    width: 100%;
    height: auto;
    display: block;
    border-radius: 16px;
}
.qr-label {
    text-align: center;
    font-size: 12px;
    color: rgba(226, 232, 240, 0.75);
    font-weight: 600;
    letter-spacing: 0.3px;
    margin-bottom: 16px;
}

/* ===== STATUS ===== */
.status {
    padding: 14px 18px;
    border-radius: 16px;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.3px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border: 1px solid rgba(255, 255, 255, 0.18);
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.2),
        0 6px 18px rgba(0, 0, 0, 0.3);
    transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1);
}
.status.waiting {
    background: linear-gradient(135deg,
        rgba(251, 191, 36, 0.25),
        rgba(245, 158, 11, 0.15));
    color: #fde68a;
    border-color: rgba(251, 191, 36, 0.35);
}
.status.error {
    background: linear-gradient(135deg,
        rgba(239, 68, 68, 0.25),
        rgba(220, 38, 38, 0.15));
    color: #fecaca;
    border-color: rgba(239, 68, 68, 0.4);
}
.status.expired {
    background: linear-gradient(135deg,
        rgba(148, 163, 184, 0.25),
        rgba(100, 116, 139, 0.15));
    color: #cbd5e1;
    border-color: rgba(148, 163, 184, 0.35);
}

/* ===== RESULT ===== */
.result-wrap {
    text-align: left;
    animation: fadeInUp 0.6s cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes fadeInUp {
    from { opacity: 0; transform: translateY(20px); }
    to   { opacity: 1; transform: translateY(0); }
}
.success-header {
    text-align: center;
    padding: 20px;
    background: linear-gradient(135deg,
        rgba(16, 185, 129, 0.22),
        rgba(5, 150, 105, 0.12));
    border: 1px solid rgba(16, 185, 129, 0.35);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border-radius: 20px;
    margin-bottom: 16px;
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.2),
        0 8px 22px rgba(16, 185, 129, 0.2);
}
.success-icon {
    font-size: 48px;
    margin-bottom: 6px;
    filter: drop-shadow(0 0 18px rgba(16, 185, 129, 0.7));
    animation: successPop 0.6s cubic-bezier(0.16, 1, 0.3, 1);
}
@keyframes successPop {
    0%   { transform: scale(0); opacity: 0; }
    60%  { transform: scale(1.2); }
    100% { transform: scale(1); opacity: 1; }
}
.success-title {
    font-size: 16px;
    font-weight: 800;
    letter-spacing: 0.3px;
    color: #d1fae5;
}
.result {
    padding: 14px 16px;
    border-radius: 16px;
    background: linear-gradient(135deg,
        rgba(16, 185, 129, 0.14),
        rgba(5, 150, 105, 0.06));
    border: 1px solid rgba(16, 185, 129, 0.28);
    backdrop-filter: blur(18px);
    -webkit-backdrop-filter: blur(18px);
    margin-bottom: 10px;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.15);
}
.result .label {
    font-size: 10px;
    color: #6ee7b7;
    text-transform: uppercase;
    font-weight: 800;
    letter-spacing: 1.5px;
    margin-bottom: 6px;
}
.result .value {
    font-family: 'JetBrains Mono', 'SF Mono', monospace;
    font-size: 12px;
    color: #ecfdf5;
    word-break: break-all;
    line-height: 1.55;
    font-weight: 500;
}
.result .value.scroll {
    max-height: 150px;
    overflow-y: auto;
    background: rgba(0, 0, 0, 0.25);
    padding: 10px 12px;
    border-radius: 10px;
    margin-top: 6px;
    border: 1px solid rgba(255, 255, 255, 0.08);
}
.result .value.scroll::-webkit-scrollbar {
    width: 4px;
}
.result .value.scroll::-webkit-scrollbar-thumb {
    background: rgba(16, 185, 129, 0.5);
    border-radius: 4px;
}

.hint {
    margin-top: 12px;
    font-size: 11px;
    color: rgba(226, 232, 240, 0.6);
    text-align: center;
    letter-spacing: 0.3px;
}

/* ===== TOAST ===== */
.toast {
    position: fixed;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%) translateY(80px);
    padding: 12px 26px;
    background: linear-gradient(135deg,
        rgba(16, 185, 129, 0.95),
        rgba(5, 150, 105, 0.95));
    color: #ffffff;
    font-weight: 800;
    font-size: 13px;
    letter-spacing: 0.3px;
    border-radius: 50px;
    z-index: 9999;
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    border: 1px solid rgba(255, 255, 255, 0.3);
    box-shadow:
        0 20px 45px rgba(16, 185, 129, 0.5),
        inset 0 1px 0 rgba(255, 255, 255, 0.4);
    animation: toastIn 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}
@keyframes toastIn {
    to { transform: translateX(-50%) translateY(0); }
}

/* ===== EXPIRED ===== */
.expired-icon {
    font-size: 68px;
    margin-bottom: 18px;
    filter: drop-shadow(0 0 25px rgba(245, 158, 11, 0.6));
    animation: expiredRotate 3s ease-in-out infinite;
}
@keyframes expiredRotate {
    0%, 100% { transform: rotate(0deg) scale(1); }
    50%      { transform: rotate(10deg) scale(1.05); }
}
.expired-title {
    color: #ffffff;
    margin-bottom: 12px;
    font-size: 22px;
    font-weight: 800;
    letter-spacing: -0.3px;
}
.expired-text {
    color: rgba(226, 232, 240, 0.7);
    font-size: 13px;
    margin-bottom: 24px;
}
.expired-btn {
    display: inline-block;
    padding: 15px 32px;
    background: linear-gradient(135deg,
        rgba(139, 92, 246, 0.9),
        rgba(99, 102, 241, 0.9));
    color: #ffffff;
    border: 1px solid rgba(255, 255, 255, 0.3);
    border-radius: 16px;
    text-decoration: none;
    font-weight: 800;
    font-size: 13px;
    letter-spacing: 0.3px;
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    box-shadow:
        0 12px 32px rgba(139, 92, 246, 0.45),
        inset 0 1px 0 rgba(255, 255, 255, 0.35);
    transition: transform 0.25s ease, box-shadow 0.25s ease;
}
.expired-btn:hover {
    transform: translateY(-3px);
    box-shadow:
        0 18px 40px rgba(139, 92, 246, 0.6),
        inset 0 1px 0 rgba(255, 255, 255, 0.45);
}

/* ===== RESPONSIVE ===== */
@media (max-width: 420px) {
    .card { padding: 32px 20px 26px; border-radius: 28px; }
    h1 { font-size: 22px; }
    .qr-container { max-width: 280px; padding: 10px; }
    .btn-start, .confirm-btn { padding: 15px 18px; font-size: 13px; }
}
"""


# ============================================================
# HTML HOME - LIQUID GLASS
# ============================================================
HTML_HOME = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta name="theme-color" content="#0a0a1a">
<title>Tạo QR — ALB · Liquid Glass</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
</style>
</head>
<body>
<div class="glass-bg-extra"></div>
<div class="card">
    <h1>☎️ Tạo mã QR Zalo</h1>
    <p class="sub">✦ Liquid Glass Edition · v26.0 ✦</p>
    <button id="btn-start" class="btn-start" onclick="startQR()">🚀 TẠO MÃ QR</button>
    <div class="steps">
        <b>📋 Hướng dẫn:</b>
        1️⃣ Bấm nút phía trên<br>
        2️⃣ Đợi 15–30s → hiện mã QR<br>
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


# ============================================================
# HTML QR - LIQUID GLASS
# ============================================================
HTML_QR = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta name="theme-color" content="#0a0a1a">
<title>Quét QR — ALB · Liquid Glass</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
</style>
</head>
<body>
<div class="glass-bg-extra"></div>
<div class="card">
    <h1>☎️ Quét QR Zalo</h1>
    <p class="sub">✦ Liquid Glass Edition ✦</p>

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


# ============================================================
# HTML EXPIRED - LIQUID GLASS
# ============================================================
HTML_EXPIRED = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#0a0a1a">
<title>Hết hạn — ALB</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """
</style>
</head>
<body>
<div class="glass-bg-extra"></div>
<div class="card">
    <div class="expired-icon">⏰</div>
    <div class="expired-title">Session hết hạn</div>
    <div class="expired-text">Vui lòng tạo mã QR mới</div>
    <a href="/" class="expired-btn">🔄 Tạo QR mới</a>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
