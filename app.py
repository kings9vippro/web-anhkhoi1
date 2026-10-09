# ============================================================
# WEB-QR v20.0 — Tạo QR Zalo (Bot by Anh Khôi)
# Logo ☎️ animation 3D — Giao diện premium
# ============================================================
import os
import io
import json
import time
import uuid
import asyncio
import threading

from flask import Flask, request, jsonify, render_template_string, Response

QR_TTL = 300
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
                "--window-size=600,800",
            ],
        )

    async def create_qr(self, session_id):
        try:
            await self.start()
            ctx = await self.browser.new_context(
                viewport={"width": 600, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                locale="vi-VN",
            )
            page = await ctx.new_page()
            await page.goto(
                "https://chat.zalo.me/",
                wait_until="networkidle",
                timeout=60000,
            )

            await asyncio.sleep(8)

            try:
                btn = await page.wait_for_selector(
                    "text=/QR|Quét mã/i", timeout=20000
                )
                await btn.click()
                print("[QR] Da click tab QR")
            except Exception as e:
                print("[QR] Khong thay tab QR: " + str(e))

            await asyncio.sleep(8)

            png = await page.screenshot(full_page=False)
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(png))
                w, h = img.size
                img = img.crop((
                    int(w * 0.15), int(h * 0.15),
                    int(w * 0.85), int(h * 0.85)
                ))
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                png = buf.getvalue()
            except Exception:
                pass

            png_path = os.path.join(DATA_DIR, session_id + ".png")
            with open(png_path, "wb") as f:
                f.write(png)

            sess_save(session_id, {
                "status": "waiting",
                "created_at": time.time(),
                "png_path": png_path,
                "cookies": None,
                "imei": None,
            })

            print("[QR] Session " + session_id + " ready")

            threading.Thread(
                target=lambda: asyncio.run(self._poll(page, session_id)),
                daemon=True,
            ).start()

        except Exception as e:
            print("[QR] error: " + str(e))
            sess_save(session_id, {
                "status": "error",
                "error": str(e),
                "created_at": time.time(),
            })

    async def _poll(self, page, session_id):
        start = time.time()
        while time.time() - start < QR_TTL:
            try:
                url = page.url
                if ("chat.zalo.me" in url and "login" not in url) or \
                   ("id.zalo.me" in url and "account" not in url):
                    return await self._extract(session_id, page)
            except Exception:
                pass
            await asyncio.sleep(2)

        data = sess_load(session_id) or {}
        data["status"] = "expired"
        sess_save(session_id, data)
        await self._close()

    async def _extract(self, session_id, page):
        try:
            await asyncio.sleep(3)
            cookies = await page.context.cookies()
            ck = {}
            for c in cookies:
                if "zalo" in c.get("domain", ""):
                    ck[c["name"]] = c["value"]

            imei = ""
            for k in ["imei", "zpw_imei", "device_id"]:
                try:
                    v = await page.evaluate("localStorage.getItem('" + k + "')")
                    if v:
                        imei = v
                        break
                except Exception:
                    pass

            data = sess_load(session_id) or {}
            data["status"] = "done"
            data["cookies"] = ck
            data["imei"] = imei or "000000000000000"
            sess_save(session_id, data)

            print("[QR] Session " + session_id + " DONE")
            await self._close()
        except Exception as e:
            data = sess_load(session_id) or {}
            data["status"] = "error"
            data["error"] = str(e)
            sess_save(session_id, data)

    async def _close(self):
        try:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass


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
    })


@app.route("/qr-image/<session_id>")
def qr_image(session_id):
    data = sess_load(session_id)
    if not data:
        return "Not found", 404
    png_path = data.get("png_path")
    if not png_path or not os.path.exists(png_path):
        return "Đang tạo QR...", 404
    with open(png_path, "rb") as f:
        return Response(f.read(), mimetype="image/png")


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
    return jsonify({"status": "ok", "service": "web-qr", "version": "20.0"})


# ============================================================
# CSS CHUNG — LOGO ☎️ ANIMATION
# ============================================================
SHARED_CSS = """
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html { -webkit-text-size-adjust:100%; touch-action:manipulation; }
body {
    font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;
    min-height:100vh; min-height:100dvh;
    display:flex; align-items:center; justify-content:center;
    background:linear-gradient(-45deg,#667eea,#764ba2,#f093fb,#f5576c,#667eea);
    background-size:500% 500%;
    animation:gradientShift 18s ease infinite;
    padding:20px; overflow-x:hidden;
    position:relative;
}
@keyframes gradientShift {
    0% { background-position:0% 50%; }
    50% { background-position:100% 50%; }
    100% { background-position:0% 50%; }
}

/* Floating orbs */
.orb {
    position:fixed; border-radius:50%;
    background:radial-gradient(circle,rgba(255,255,255,0.18),transparent 70%);
    pointer-events:none; z-index:0;
    will-change:transform;
}
.orb1 { width:400px; height:400px; top:-100px; left:-100px; animation:float1 20s ease-in-out infinite; }
.orb2 { width:300px; height:300px; bottom:-80px; right:-80px; animation:float2 15s ease-in-out infinite; }
.orb3 { width:220px; height:220px; top:55%; left:75%; animation:float3 18s ease-in-out infinite; }
@keyframes float1 { 0%,100% { transform:translate(0,0) scale(1); } 50% { transform:translate(70px,50px) scale(1.1); } }
@keyframes float2 { 0%,100% { transform:translate(0,0) scale(1); } 50% { transform:translate(-60px,-70px) scale(1.15); } }
@keyframes float3 { 0%,100% { transform:translate(0,0) scale(1); } 50% { transform:translate(-40px,90px) scale(0.9); } }

/* ============================================================
   LOGO ☎️ — ANIMATION 3D SIÊU ĐẸP
   ============================================================ */
.logo-wrap {
    position:relative;
    width:130px; height:130px;
    margin:0 auto 32px;
    perspective:1000px;
}

/* Halo phát sáng phía sau */
.logo-halo {
    position:absolute; inset:-30px;
    border-radius:50%;
    background:radial-gradient(circle,
        rgba(245,87,108,0.7) 0%,
        rgba(240,147,251,0.5) 30%,
        rgba(102,126,234,0.3) 60%,
        transparent 80%);
    filter:blur(20px);
    animation:haloPulse 3s ease-in-out infinite;
    z-index:0;
}
@keyframes haloPulse {
    0%,100% { transform:scale(1); opacity:0.7; }
    50% { transform:scale(1.25); opacity:1; }
}

/* Vòng xoay bên ngoài */
.logo-ring {
    position:absolute; inset:-15px;
    border:2px solid transparent;
    border-top-color:rgba(255,255,255,0.8);
    border-right-color:rgba(255,255,255,0.3);
    border-radius:50%;
    animation:ringRotate 4s linear infinite;
    z-index:1;
}
.logo-ring::before {
    content:'';
    position:absolute; inset:8px;
    border:2px solid transparent;
    border-bottom-color:rgba(255,255,255,0.6);
    border-left-color:rgba(255,255,255,0.2);
    border-radius:50%;
    animation:ringRotate 3s linear infinite reverse;
}
@keyframes ringRotate {
    to { transform:rotate(360deg); }
}

/* Thân logo ☎️ */
.logo-icon {
    position:absolute; inset:0;
    border-radius:36px;
    background:linear-gradient(135deg,#f093fb 0%,#f5576c 50%,#ee5a6f 100%);
    display:flex; align-items:center; justify-content:center;
    font-size:72px;
    box-shadow:
        0 30px 70px rgba(245,87,108,0.6),
        0 15px 35px rgba(240,147,251,0.4),
        inset 0 -6px 25px rgba(0,0,0,0.2),
        inset 0 6px 25px rgba(255,255,255,0.5);
    animation:iconFloat 3.5s ease-in-out infinite;
    z-index:2;
    will-change:transform;
    transform-style:preserve-3d;
}
@keyframes iconFloat {
    0%,100% {
        transform:translateY(0) rotateY(0deg) rotateZ(0deg) scale(1);
    }
    25% {
        transform:translateY(-10px) rotateY(15deg) rotateZ(-5deg) scale(1.03);
    }
    50% {
        transform:translateY(-16px) rotateY(0deg) rotateZ(0deg) scale(1.05);
    }
    75% {
        transform:translateY(-10px) rotateY(-15deg) rotateZ(5deg) scale(1.03);
    }
}

/* Ánh sáng lấp lánh trên logo */
.logo-shine {
    position:absolute; inset:0;
    border-radius:36px;
    background:linear-gradient(120deg,
        transparent 30%,
        rgba(255,255,255,0.6) 50%,
        transparent 70%);
    transform:translateX(-100%);
    animation:shineMove 3s ease-in-out infinite;
    z-index:3;
    pointer-events:none;
}
@keyframes shineMove {
    0% { transform:translateX(-100%) skewX(-20deg); }
    60%,100% { transform:translateX(200%) skewX(-20deg); }
}

/* Tia sáng tỏa ra */
.logo-spark {
    position:absolute;
    width:4px; height:4px;
    background:white;
    border-radius:50%;
    box-shadow:0 0 12px rgba(255,255,255,0.9);
    z-index:4;
    animation:sparkOrbit 4s linear infinite;
}
.logo-spark:nth-child(2) {
    animation-delay:-1s;
}
.logo-spark:nth-child(3) {
    animation-delay:-2s;
}
.logo-spark:nth-child(4) {
    animation-delay:-3s;
}
@keyframes sparkOrbit {
    0% {
        transform:translate(65px,0) scale(1);
        opacity:1;
    }
    25% {
        transform:translate(0,65px) scale(0.8);
        opacity:0.8;
    }
    50% {
        transform:translate(-65px,0) scale(1);
        opacity:1;
    }
    75% {
        transform:translate(0,-65px) scale(0.8);
        opacity:0.8;
    100% {
        transform:translate(65px,0) scale(1);
        opacity:1;
    }
}

/* Card chung */
.card {
    background:rgba(255,255,255,0.98);
    backdrop-filter:blur(30px) saturate(180%);
    -webkit-backdrop-filter:blur(30px) saturate(180%);
    padding:50px 40px; border-radius:38px;
    box-shadow:
        0 40px 100px rgba(0,0,0,0.35),
        0 0 0 1px rgba(255,255,255,0.6),
        inset 0 1px 0 rgba(255,255,255,0.9);
    text-align:center; max-width:560px; width:100%;
    position:relative; z-index:5;
    animation:cardIn 0.9s cubic-bezier(0.16,1,0.3,1);
}
@keyframes cardIn {
    from { opacity:0; transform:translateY(60px) scale(0.94); }
    to { opacity:1; transform:translateY(0) scale(1); }
}

h1 {
    color:#1a1a2e; margin-bottom:12px;
    font-size:30px; font-weight:900;
    letter-spacing:-0.8px;
    background:linear-gradient(135deg,#667eea,#764ba2,#f5576c);
    -webkit-background-clip:text;
    -webkit-text-fill-color:transparent;
    background-clip:text;
}
p.sub { color:#6b7280; margin-bottom:32px; font-size:15px; font-weight:500; }

@media (max-width:640px) {
    .logo-wrap { width:110px; height:110px; }
    .logo-icon { font-size:60px; border-radius:30px; }
    .logo-shine { border-radius:30px; }
    .logo-spark { width:3px; height:3px; }
    h1 { font-size:24px; }
    .card { padding:36px 24px; border-radius:30px; }
}
"""


# ============================================================
# HTML — TRANG CHỦ
# ============================================================
HTML_HOME = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<title>Tạo QR Zalo — ALB Forge</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """

.btn-start {
    width:100%; padding:22px; border:none; border-radius:22px;
    background:linear-gradient(135deg,#f093fb 0%,#f5576c 100%);
    color:white; font-size:19px; font-weight:900;
    font-family:inherit; cursor:pointer;
    box-shadow:
        0 20px 50px rgba(245,87,108,0.5),
        inset 0 -3px 15px rgba(0,0,0,0.15),
        inset 0 3px 15px rgba(255,255,255,0.25);
    transition:all 0.35s cubic-bezier(0.16,1,0.3,1);
    position:relative; overflow:hidden;
    letter-spacing:0.3px;
}
.btn-start::before {
    content:'';
    position:absolute; inset:0;
    background:linear-gradient(120deg,transparent 30%,rgba(255,255,255,0.5),transparent 70%);
    transform:translateX(-100%);
    transition:transform 0.8s;
}
.btn-start:hover::before { transform:translateX(100%); }
.btn-start:hover {
    transform:translateY(-5px) scale(1.02);
    box-shadow:0 28px 65px rgba(245,87,108,0.65);
}
.btn-start:active { transform:translateY(-2px) scale(0.99); }
.btn-start:disabled { opacity:0.7; cursor:wait; }

.steps {
    margin-top:36px; text-align:left;
    background:linear-gradient(135deg,#f9fafb,#f3f4f6);
    padding:24px; border-radius:20px;
    font-size:14px; color:#374151;
    line-height:2; border:1px solid #e5e7eb;
}
.steps-title {
    font-weight:900; color:#667eea;
    font-size:13px; text-transform:uppercase;
    letter-spacing:1px; margin-bottom:14px;
}
.step-row { display:flex; align-items:center; gap:12px; margin-bottom:10px; }
.step-num {
    width:26px; height:26px; border-radius:50%;
    background:linear-gradient(135deg,#667eea,#764ba2);
    color:white; font-weight:900; font-size:12px;
    display:flex; align-items:center; justify-content:center;
    flex-shrink:0;
    box-shadow:0 4px 10px rgba(102,126,234,0.4);
}
</style>
</head>
<body>
<div class="orb orb1"></div>
<div class="orb orb2"></div>
<div class="orb orb3"></div>

<div class="card">

    <!-- LOGO ☎️ ANIMATION -->
    <div class="logo-wrap">
        <div class="logo-halo"></div>
        <div class="logo-ring"></div>
        <div class="logo-icon">☎️</div>
        <div class="logo-shine"></div>
        <div class="logo-spark" style="top:50%; left:50%; margin:-2px 0 0 -2px;"></div>
        <div class="logo-spark" style="top:50%; left:50%; margin:-2px 0 0 -2px;"></div>
        <div class="logo-spark" style="top:50%; left:50%; margin:-2px 0 0 -2px;"></div>
        <div class="logo-spark" style="top:50%; left:50%; margin:-2px 0 0 -2px;"></div>
    </div>

    <h1>Tạo mã QR Zalo</h1>
    <p class="sub">Quét QR để lấy IMEI + Cookie</p>

    <button id="btn-start" class="btn-start" onclick="startQR()">
        🚀 TẠO MÃ QR
    </button>

    <div class="steps">
        <div class="steps-title">Hướng dẫn nhanh</div>
        <div class="step-row"><div class="step-num">1</div><div>Bấm nút "Tạo mã QR" ở trên</div></div>
        <div class="step-row"><div class="step-num">2</div><div>Đợi 15-30 giây → hiện mã QR</div></div>
        <div class="step-row"><div class="step-num">3</div><div>Mở app Zalo → QR → Quét mã</div></div>
        <div class="step-row"><div class="step-num">4</div><div>Copy IMEI + Cookie sang web spam</div></div>
    </div>

</div>

<script>
async function startQR() {
    const btn = document.getElementById('btn-start');
    btn.disabled = true;
    btn.innerHTML = '⏳ ĐANG TẠO QR... (15-30s)';
    try {
        const r = await fetch('/api/qr/create', { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            btn.innerHTML = '✅ XONG! ĐANG CHUYỂN TRANG...';
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
# HTML — TRANG QR
# ============================================================
HTML_QR = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<title>Quét QR — ALB Forge</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """

.qr-box {
    background:linear-gradient(135deg,#f9fafb,#f3f4f6);
    padding:28px; border-radius:28px;
    margin-bottom:26px;
    border:2px dashed #cbd5e1;
    position:relative;
    box-shadow:inset 0 4px 20px rgba(0,0,0,0.03);
    transition:all 0.4s;
}
.qr-box::before {
    content:'';
    position:absolute; inset:-2px;
    border-radius:28px;
    background:linear-gradient(135deg,#f093fb,#f5576c);
    z-index:-1;
    opacity:0.4;
    filter:blur(15px);
    animation:qrGlow 3s ease-in-out infinite;
}
@keyframes qrGlow {
    0%,100% { opacity:0.4; }
    50% { opacity:0.75; }
}
.qr-box img {
    max-width:100%; border-radius:16px;
    display:block; margin:0 auto;
    box-shadow:0 10px 30px rgba(0,0,0,0.1);
    background:white;
}

.status {
    padding:20px; border-radius:18px;
    font-size:15px; font-weight:800;
    display:flex; align-items:center; justify-content:center; gap:12px;
    transition:all 0.4s cubic-bezier(0.16,1,0.3,1);
}
.status.waiting {
    background:linear-gradient(135deg,#fef3c7,#fde68a);
    color:#92400e;
    animation:waitingPulse 2s ease-in-out infinite;
}
@keyframes waitingPulse {
    0%,100% { transform:scale(1); box-shadow:0 0 0 0 rgba(251,191,36,0.5); }
    50% { transform:scale(1.01); box-shadow:0 0 0 12px rgba(251,191,36,0); }
}
.status.error { background:linear-gradient(135deg,#fee2e2,#fecaca); color:#991b1b; }
.status.expired { background:linear-gradient(135deg,#f3f4f6,#e5e7eb); color:#374151; }

.result-wrap {
    text-align:left;
    animation:resultIn 0.6s cubic-bezier(0.16,1,0.3,1);
}
@keyframes resultIn {
    from { opacity:0; transform:translateY(20px); }
    to { opacity:1; transform:translateY(0); }
}

.success-header {
    text-align:center; padding:22px;
    background:linear-gradient(135deg,#d1fae5,#a7f3d0);
    border-radius:22px; margin-bottom:20px;
    position:relative; overflow:hidden;
}
.success-header::before {
    content:'';
    position:absolute; inset:0;
    background:linear-gradient(120deg,transparent,rgba(255,255,255,0.6),transparent);
    transform:translateX(-100%);
    animation:shimmer 2.5s ease-in-out infinite;
}
@keyframes shimmer {
    0% { transform:translateX(-100%); }
    50%,100% { transform:translateX(100%); }
}
.success-icon {
    font-size:56px; line-height:1;
    margin-bottom:8px;
    animation:successBounce 0.8s cubic-bezier(0.16,1,0.3,1);
}
@keyframes successBounce {
    0% { transform:scale(0); }
    60% { transform:scale(1.2); }
    100% { transform:scale(1); }
}
.success-title {
    font-size:20px; font-weight:900;
    color:#065f46; letter-spacing:-0.3px;
}

.result {
    padding:18px; border-radius:18px;
    background:linear-gradient(135deg,#ecfdf5,#d1fae5);
    margin-bottom:14px;
    border:1px solid #a7f3d0;
}
.result .label {
    font-size:11px; color:#065f46;
    text-transform:uppercase; font-weight:900;
    letter-spacing:1px; margin-bottom:8px;
    display:flex; align-items:center; gap:6px;
}
.result .value {
    font-family:'SF Mono',Monaco,Consolas,monospace;
    font-size:13px; color:#065f46;
    word-break:break-all; line-height:1.6;
    font-weight:600;
}
.result .value.scroll {
    max-height:200px; overflow-y:auto;
    background:rgba(255,255,255,0.6);
    padding:12px; border-radius:10px;
    margin-top:8px;
}
.result .value.scroll::-webkit-scrollbar { width:6px; }
.result .value.scroll::-webkit-scrollbar-thumb {
    background:#a7f3d0; border-radius:3px;
}

.copy-btn {
    width:100%; padding:18px; border:none;
    border-radius:16px;
    background:linear-gradient(135deg,#667eea,#764ba2);
    color:white; font-size:16px; font-weight:900;
    font-family:inherit; cursor:pointer;
    margin-top:10px;
    box-shadow:
        0 12px 30px rgba(102,126,234,0.4),
        inset 0 -3px 12px rgba(0,0,0,0.1),
        inset 0 3px 12px rgba(255,255,255,0.2);
    transition:all 0.3s cubic-bezier(0.16,1,0.3,1);
    position:relative; overflow:hidden;
}
.copy-btn::before {
    content:'';
    position:absolute; inset:0;
    background:linear-gradient(120deg,transparent,rgba(255,255,255,0.4),transparent);
    transform:translateX(-100%);
    transition:transform 0.6s;
}
.copy-btn:hover::before { transform:translateX(100%); }
.copy-btn:hover { transform:translateY(-3px); box-shadow:0 18px 40px rgba(102,126,234,0.55); }
.copy-btn:active { transform:translateY(-1px); }

.copy-btn.green {
    background:linear-gradient(135deg,#10b981,#059669);
    box-shadow:0 12px 30px rgba(16,185,129,0.4);
}
.copy-btn.green:hover { box-shadow:0 18px 40px rgba(16,185,129,0.55); }
.copy-btn.purple {
    background:linear-gradient(135deg,#8b5cf6,#7c3aed);
    box-shadow:0 12px 30px rgba(139,92,246,0.4);
}
.copy-btn.blue {
    background:linear-gradient(135deg,#3b82f6,#2563eb);
    box-shadow:0 12px 30px rgba(59,130,246,0.4);
}

.hint {
    margin-top:14px; font-size:12px;
    color:#6b7280; text-align:center;
    font-weight:600; line-height:1.6;
}

.toast {
    position:fixed; bottom:30px; left:50%;
    transform:translateX(-50%);
    background:linear-gradient(135deg,#10b981,#059669);
    color:white; padding:16px 32px;
    border-radius:50px; font-weight:900;
    font-size:15px;
    box-shadow:0 20px 50px rgba(16,185,129,0.6);
    z-index:9999;
    animation:toastIn 0.4s cubic-bezier(0.16,1,0.3,1);
    display:flex; align-items:center; gap:10px;
}
@keyframes toastIn {
    from { opacity:0; transform:translateX(-50%) translateY(40px) scale(0.8); }
    to { opacity:1; transform:translateX(-50%) translateY(0) scale(1); }
}
.toast.out { animation:toastOut 0.3s ease forwards; }
@keyframes toastOut {
    to { opacity:0; transform:translateX(-50%) translateY(30px) scale(0.9); }
}

@media (max-width:640px) {
    .qr-box { padding:20px; }
    .success-icon { font-size:48px; }
    .success-title { font-size:18px; }
    .result .value { font-size:12px; }
}
</style>
</head>
<body>
<div class="orb orb1"></div>
<div class="orb orb2"></div>
<div class="orb orb3"></div>

<div class="card">

    <!-- LOGO ☎️ -->
    <div class="logo-wrap">
        <div class="logo-halo"></div>
        <div class="logo-ring"></div>
        <div class="logo-icon">☎️</div>
        <div class="logo-shine"></div>
        <div class="logo-spark" style="top:50%; left:50%; margin:-2px 0 0 -2px;"></div>
        <div class="logo-spark" style="top:50%; left:50%; margin:-2px 0 0 -2px;"></div>
        <div class="logo-spark" style="top:50%; left:50%; margin:-2px 0 0 -2px;"></div>
        <div class="logo-spark" style="top:50%; left:50%; margin:-2px 0 0 -2px;"></div>
    </div>

    <h1>📱 Quét QR Zalo</h1>
    <p class="sub">Mở app Zalo → QR → Quét mã bên dưới</p>

    <div class="qr-box" id="qr-box">
        <img src="/qr-image/{{ session_id }}" alt="QR">
    </div>

    <div id="status" class="status waiting">
        <span style="font-size:20px;">⏳</span>
        <span>Đang chờ quét...</span>
    </div>

</div>

<script>
const sessionId = "{{ session_id }}";
let done = false;

async function check() {
    if (done) return;
    try {
        const r = await fetch('/api/qr/status/' + sessionId);
        const d = await r.json();
        const el = document.getElementById('status');

        if (d.status === 'waiting' || d.status === 'creating') {
            el.className = 'status waiting';
            el.innerHTML = '<span style="font-size:20px;">⏳</span><span>Đang chờ quét...</span>';
        } else if (d.status === 'done') {
            done = true;
            showResult(d);
        } else if (d.status === 'error') {
            el.className = 'status error';
            el.innerHTML = '<span style="font-size:20px;">❌</span><span>' + (d.error || 'Lỗi') + '</span>';
            done = true;
        } else if (d.status === 'expired') {
            el.className = 'status expired';
            el.innerHTML = '<span style="font-size:20px;">⏰</span><span>QR hết hạn. Tạo lại.</span>';
            done = true;
        }
    } catch (e) {}
}

function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function showResult(d) {
    const imei = d.imei || '000000000000000';
    const cookies = d.cookies || {};
    const cookiesStr = JSON.stringify(cookies);
    const combo = imei + '|' + cookiesStr;

    document.getElementById('qr-box').style.display = 'none';

    const el = document.getElementById('status');
    el.className = 'status';
    el.style.padding = '0';
    el.style.background = 'transparent';
    el.innerHTML =
        '<div class="result-wrap">' +
            '<div class="success-header">' +
                '<div class="success-icon">✅</div>' +
                '<div class="success-title">Lấy cookie thành công!</div>' +
            '</div>' +
            '<div class="result">' +
                '<div class="label">📞 IMEI</div>' +
                '<div class="value" id="imei-val">' + escapeHtml(imei) + '</div>' +
            '</div>' +
            '<div class="result">' +
                '<div class="label">🍪 COOKIE (JSON)</div>' +
                '<div class="value scroll" id="ck-val">' + escapeHtml(cookiesStr) + '</div>' +
            '</div>' +
            '<button class="copy-btn green" id="copy-all">📋 COPY CẢ IMEI + COOKIE</button>' +
            '<button class="copy-btn blue" id="copy-imei">📞 CHỈ COPY IMEI</button>' +
            '<button class="copy-btn purple" id="copy-ck">🍪 CHỈ COPY COOKIE</button>' +
            '<div class="hint">Sau khi copy → quay lại web spam → paste vào tab Tài khoản</div>' +
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
    try {
        document.execCommand('copy');
        showToast('✅ Đã copy!');
    } catch (e) {
        prompt('Copy thủ công:', text);
    }
    document.body.removeChild(ta);
}

function showToast(msg) {
    const old = document.querySelector('.toast');
    if (old) old.remove();
    const t = document.createElement('div');
    t.className = 'toast';
    t.innerHTML = msg;
    document.body.appendChild(t);
    setTimeout(function() {
        t.classList.add('out');
        setTimeout(function() { t.remove(); }, 300);
    }, 2000);
}

setInterval(check, 2000);
check();
</script>
</body>
</html>
"""


# ============================================================
# HTML — SESSION EXPIRED
# ============================================================
HTML_EXPIRED = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Hết hạn — ALB Forge</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
""" + SHARED_CSS + """

.expired-icon { font-size:80px; margin-bottom:24px; animation:iconWobble 2s ease-in-out infinite; }
@keyframes iconWobble {
    0%,100% { transform:rotate(0deg); }
    25% { transform:rotate(-8deg); }
    75% { transform:rotate(8deg); }
}
h1 { color:#1a1a2e; margin-bottom:16px; font-size:28px; font-weight:900; }
p { color:#6b7280; font-size:15px; margin-bottom:28px; line-height:1.7; }
a {
    display:inline-block; padding:18px 40px;
    background:linear-gradient(135deg,#667eea,#764ba2);
    color:white; border-radius:18px;
    text-decoration:none; font-weight:900; font-size:16px;
    box-shadow:0 15px 40px rgba(102,126,234,0.5);
    transition:all 0.3s cubic-bezier(0.16,1,0.3,1);
}
a:hover { transform:translateY(-3px); box-shadow:0 22px 55px rgba(102,126,234,0.65); }
</style>
</head>
<body>
<div class="orb orb1"></div>
<div class="orb orb2"></div>

<div class="card">
    <div class="expired-icon">⏰</div>
    <h1>Session hết hạn</h1>
    <p>Session QR này đã hết hạn.<br>Vui lòng tạo mã QR mới.</p>
    <a href="/">🔄 Tạo QR mới</a>
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
