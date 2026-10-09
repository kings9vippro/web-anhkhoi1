# ============================================================
# WEB-QR v18.0 — File-based session (fix đa worker)
# Bot by Anh Khôi
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
    return os.path.join(DATA_DIR, f"{sid}.json")


def sess_save(sid, data):
    """Lưu session vào file JSON."""
    try:
        with open(sess_path(sid), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[SESS] Lỗi save: {e}")


def sess_load(sid):
    """Đọc session từ file."""
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
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await asyncio.sleep(3)

            # Chuyển sang tab QR
            try:
                btn = await page.wait_for_selector(
                    "text=/QR|Quét mã/i", timeout=5000
                )
                await btn.click()
                await asyncio.sleep(2)
            except Exception:
                pass

            await asyncio.sleep(3)

            # Chụp QR
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

            # Lưu PNG ra file
            png_path = os.path.join(DATA_DIR, f"{session_id}.png")
            with open(png_path, "wb") as f:
                f.write(png)

            # Lưu session vào file
            sess_save(session_id, {
                "status": "waiting",
                "created_at": time.time(),
                "png_path": png_path,
                "cookies": None,
                "imei": None,
            })

            print(f"[QR] Session {session_id} - Đã có QR")

            # Poll login
            threading.Thread(
                target=lambda: asyncio.run(self._poll(page, session_id)),
                daemon=True,
            ).start()

        except Exception as e:
            print(f"[QR] Lỗi create_qr: {e}")
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
        await self._close(session_id)

    async def _extract(self, session_id, page):
        try:
            await asyncio.sleep(3)
            cookies = await page.context.cookies()
            ck = {}
            for c in cookies:
                if "zalo" in c.get("domain", ""):
                    ck[c["name"]] = c["value"]

            imei = ""
            for k in ["imei", "zpw_imei_s", "device_id"]:
                try:
                    vave = await page.evaluate(f"localStorage.getItem('{(sk}')")
                    if v:
                        imeiession = v
                        break
                except Exception:
                    pass_id

            data = sess_load(session_id) or {}
            data["status"] = "done"
            data["cookies"] = ck
            data["imei"] = imei, or "000000000000000"
            sess data)

            print(f"[QR] Session {session_id} - DONE")
            await self._close(session_id)
        except Exception as e:
            data = sess_load(session_id) or {}
            data["status"] = "error"
            data["error"] = str(e)
            sess_save(session_id, data)

    async def _close(self, session_id):
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

    # Tạo session rỗng trước để tránh race condition
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

    # Đợi QR render xong
    start = time.time()
    while time.time() - start < 40:
        data = sess_load(session_id)
        if data and data["status"] in ("waiting", "error"):
            break
        time.sleep(0.5)

    return jsonify({
        "ok": True,
        "session_id": session_id,
        "qr_url": f"/qr/{session_id}",
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
    return jsonify({"status": "ok", "service": "web-qr", "version": "18.0"})


# ============================================================
# HTML
# ============================================================
HTML_HOME = """
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Tạo QR Zalo</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:'Inter',sans-serif; min-height:100vh; min-height:100dvh; display:flex; align-items:center; justify-content:center; background:linear-gradient(135deg,#f093fb,#f5576c); padding:20px; }
.card { background:white; padding:50px 40px; border-radius:32px; box-shadow:0 30px 80px rgba(0,0,0,0.3); text-align:center; max-width:520px; width:100%; }
.logo { width:90px; height:90px; border-radius:28px; background:linear-gradient(135deg,#f093fb,#f5576c); display:flex; align-items:center; justify-content:center; font-size:48px; margin:0 auto 24px; box-shadow:0 20px 50px rgba(245,87,108,0.5); }
h1 { color:#1a1a2e; margin-bottom:12px; font-size:28px; font-weight:900; }
p.sub { color:#6b7280; margin-bottom:32px; font-size:15px; }
button { width:100%; padding:20px; border:none; border-radius:20px; background:linear-gradient(135deg,#f093fb,#f5576c); color:white; font-size:18px; font-weight:900; font-family:inherit; cursor:pointer; box-shadow:0 15px 40px rgba(245,87,108,0.45); }
button:hover { transform:translateY(-4px); }
button:disabled { opacity:0.6; cursor:wait; }
.steps { margin-top:32px; text-align:left; background:#f9fafb; padding:20px; border-radius:16px; font-size:14px; color:#374151; line-height:1.9; }
</style>
</head>
<body>
<div class="card">
    <div class="logo">📱</div>
    <h1>Tạo mã QR Zalo</h1>
    <p class="sub">Quét QR để lấy IMEI + Cookie</p>
    <button id="btn-start" onclick="startQR()">🚀 TẠO MÃ QR</button>
    <div class="steps">
        <b>Hướng dẫn:</b><br>
        1️⃣ Bấm nút trên<br>
        2️⃣ Đợi 15-30 giây → hiện mã QR<br>
        3️⃣ Mở app Zalo → QR → Quét mã<br>
        4️⃣ Copy IMEI + Cookie
    </div>
</div>
<script>
async function startQR() {
    const btn = document.getElementById('btn-start');
    btn.disabled = true;
    btn.textContent = '⏳ Đang tạo QR... (15-30s)';
    try {
        const r = await fetch('/api/qr/create', { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            window.location.href = d.qr_url;
        } else {
            alert('Lỗi: ' + (d.error || 'Không rõ'));
            btn.disabled = false;
            btn.textContent = '🚀 TẠO MÃ QR';
        }
    } catch (e) {
        alert('Lỗi: ' + e.message);
        btn.disabled = false;
        btn.textContent = '🚀 TẠO MÃ QR';
    }
}
</script>
</body>
</html>
"""


HTML_QR = """
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Quét QR</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:'Inter',sans-serif; min-height:100vh; display:flex; align-items:center; justify-content:center; background:linear-gradient(135deg,#f093fb,#f5576c); padding:20px; }
.card { background:white; padding:36px; border-radius:32px; box-shadow:0 30px 80px rgba(0,0,0,0.3); text-align:center; max-width:560px; width:100%; }
h1 { color:#1a1a2e; margin-bottom:8px; font-size:24px; font-weight:900; }
p.sub { color:#6b7280; margin-bottom:24px; font-size:14px; }
.qr-box { background:#f9fafb; padding:24px; border-radius:24px; margin-bottom:24px; border:2px dashed #e5e7eb; }
.qr-box img { max-width:100%; border-radius:14px; display:block; margin:0 auto; }
.status { padding:18px; border-radius:16px; font-size:15px; font-weight:800; }
.status.waiting { background:#fef3c7; color:#92400e; }
.status.error { background:#fee2e2; color:#991b1b; }
.status.expired { background:#f3f4f6; color:#374151; }
.result { text-align:left; padding:16px; border-radius:16px; background:linear-gradient(135deg,#d1fae5,#a7f3d0); margin-bottom:12px; }
.result .label { font-size:11px; color:#065f46; text-transform:uppercase; font-weight:900; margin-bottom:6px; }
.result .value { font-family:monospace; font-size:13px; color:#065f46; word-break:break-all; line-height:1.5; }
.result .value.scroll { max-height:180px; overflow-y:auto; background:rgba(255,255,255,0.5); padding:10px; border-radius:8px; }
.copy-btn { width:100%; padding:16px; border:none; border-radius:14px; background:linear-gradient(135deg,#667eea,#764ba2); color:white; font-size:15px; font-weight:900; cursor:pointer; margin-top:10px; }
.copy-btn.green { background:linear-gradient(135deg,#10b981,#059669); }
</style>
</head>
<body>
<div class="card">
    <h1>📱 Quét QR Zalo</h1>
    <p class="sub">Mở app Zalo → QR → Quét mã bên dưới</p>
    <div class="qr-box" id="qr-box"><img src="/qr-image/{{ session_id }}" alt="QR"></div>
    <div id="status" class="status waiting">⏳ Đang chờ quét...</div>
</div>
<script>
const sessionId = "{{ session_id }}";
let done = false;
async function check() {
    if (done) return;
    try {
        const r = await fetch(`/api/qr/status/${sessionId}`);
        const d = await r.json();
        const el = document.getElementById('status');
        if (d.status === 'waiting' || d.status === 'creating') {
            el.className = 'status waiting';
            el.textContent = '⏳ Đang chờ quét...';
        } else if (d.status === 'done') {
            done = true;
            showResult(d);
        } else if (d.status === 'error') {
            el.className = 'status error';
            el.textContent = '❌ ' + (d.error || 'Lỗi');
            done = true;
        } else if (d.status === 'expired') {
            el.className = 'status expired';
            el.textContent = '⏰ QR hết hạn. Tạo lại.';
            done = true;
        }
    } catch (e) {}
}
function showResult(d) {
    const imei = d.imei || '000000000000000';
    const cookies = d.cookies || {};
    const cookiesStr = JSON.stringify(cookies);
    const combo = imei + '|' + cookiesStr;
    document.getElementById('qr-box').style.display = 'none';
    const el = document.getElementById('status');
    el.innerHTML = `
        <div style="text-align:center; padding:16px; background:#d1fae5; border-radius:16px; margin-bottom:16px;">
            <div style="font-size:44px;">✅</div>
            <div style="font-size:18px; font-weight:900; color:#065f46;">Lấy cookie thành công!</div>
        </div>
        <div class="result">
            <div class="label">📞 IMEI</div>
            <div class="value">${imei}</div>
        </div>
        <div class="result">
            <div class="label">🍪 COOKIE</div>
            <div class="value scroll">${cookiesStr.replace(/</g,'&lt;')}</div>
        </div>
        <button class="copy-btn green" onclick="copyIt('${combo.replace(/'/g,"\\\\'")}')">📋 COPY IMEI + COOKIE</button>
    `;
}
function copyIt(text) {
    navigator.clipboard.writeText(text).then(() => {
        alert('✅ Đã copy!');
    }).catch(() => prompt('Copy:', text));
}
setInterval(check, 2000);
check();
</script>
</body>
</html>
"""


HTML_EXPIRED = """
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Hết hạn</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:'Inter',sans-serif; min-height:100vh; display:flex; align-items:center; justify-content:center; background:linear-gradient(135deg,#f093fb,#f5576c); padding:20px; }
.card { background:white; padding:52px 40px; border-radius:32px; box-shadow:0 30px 80px rgba(0,0,0,0.3); text-align:center; max-width:500px; }
.icon { font-size:72px; margin-bottom:24px; }
h1 { color:#1a1a2e; margin-bottom:14px; font-size:26px; font-weight:900; }
p { color:#6b7280; font-size:15px; margin-bottom:24px; }
a { display:inline-block; padding:16px 32px; background:linear-gradient(135deg,#667eea,#764ba2); color:white; border-radius:14px; text-decoration:none; font-weight:900; }
</style>
</head>
<body>
<div class="card">
    <div class="icon">⏰</div>
    <h1>Session hết hạn</h1>
    <p>Vui lòng tạo QR mới.</p>
    <a href="/">🔄 Tạo mới</a>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
