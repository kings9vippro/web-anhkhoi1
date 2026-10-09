# ============================================================
# WEB-QR v15.0 — Tạo mã QR Zalo (Bot by Anh Khôi)
# Chạy độc lập, không cần liên kết server nào khác.
# Chức năng: Tạo QR → quét → hiện IMEI + Cookie cho user copy.
# ============================================================
import os
import time
import uuid
import asyncio
import threading
from io import BytesIO

from flask import Flask, request, jsonify, render_template_string, Response

QR_TTL = 300  # 5 phút

app = Flask(__name__)
QR_SESSIONS = {}


# ============================================================
# ZALO QR LOGIN — Playwright
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

            # Chuyển sang tab QR nếu có
            try:
                btn = await page.wait_for_selector(
                    "text=/QR|Quét mã/i", timeout=5000
                )
                await btn.click()
                await asyncio.sleep(2)
            except Exception:
                pass

            await asyncio.sleep(3)

            # Chụp ảnh QR
            png = await page.screenshot(full_page=False)
            try:
                from PIL import Image
                img = Image.open(BytesIO(png))
                w, h = img.size
                img = img.crop(
                    (int(w * 0.15), int(h * 0.15),
                     int(w * 0.85), int(h * 0.85))
                )
                buf = BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                png = buf.getvalue()
            except Exception:
                pass

            QR_SESSIONS[session_id] = {
                "page": page,
                "browser": self.browser,
                "playwright": self.playwright,
                "png": png,
                "created_at": time.time(),
                "status": "waiting",
                "cookies": None,
                "imei": None,
            }

            # Poll login trong thread
            threading.Thread(
                target=lambda: asyncio.run(self._poll(page, session_id)),
                daemon=True,
            ).start()

        except Exception as e:
            QR_SESSIONS[session_id] = {
                "status": "error",
                "error": str(e),
                "created_at": time.time(),
            }

    async def _poll(self, page, session_id):
        start = time.time()
        while time.time() - start < QR_TTL:
            try:
                url = page.url
                if "chat.zalo.me" in url and "login" not in url:
                    return await self._extract(session_id, page)
                if "id.zalo.me" in url and "account" not in url:
                    return await self._extract(session_id, page)
            except Exception:
                pass
            await asyncio.sleep(2)

        if session_id in QR_SESSIONS:
            QR_SESSIONS[session_id]["status"] = "expired"
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
            for k in ["imei", "zpw_imei", "device_id"]:
                try:
                    v = await page.evaluate(f"localStorage.getItem('{k}')")
                    if v:
                        imei = v
                        break
                except Exception:
                    pass

            if session_id in QR_SESSIONS:
                QR_SESSIONS[session_id]["status"] = "done"
                QR_SESSIONS[session_id]["cookies"] = ck
                QR_SESSIONS[session_id]["imei"] = imei or "000000000000000"

            await self._close(session_id)
        except Exception as e:
            if session_id in QR_SESSIONS:
                QR_SESSIONS[session_id]["status"] = "error"
                QR_SESSIONS[session_id]["error"] = str(e)

    async def _close(self, session_id):
        if session_id not in QR_SESSIONS:
            return
        s = QR_SESSIONS[session_id]
        try:
            if s.get("browser"):
                await s["browser"].close()
            if s.get("playwright"):
                await s["playwright"].stop()
        except Exception:
            pass


# ============================================================
# API
# ============================================================
@app.route("/api/qr/create", methods=["POST"])
def api_qr_create():
    session_id = uuid.uuid4().hex[:16]

    def run():
        qr = ZaloQR()
        asyncio.run(qr.create_qr(session_id))

    threading.Thread(target=run, daemon=True).start()

    # Đợi QR render xong (tối đa 35s)
    start = time.time()
    while time.time() - start < 35:
        if session_id in QR_SESSIONS:
            s = QR_SESSIONS[session_id]
            if s["status"] in ("waiting", "error"):
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
    if session_id not in QR_SESSIONS:
        return jsonify({"error": "Session không tồn tại"}), 404
    s = QR_SESSIONS[session_id]
    return jsonify({
        "status": s["status"],
        "imei": s.get("imei"),
        "cookies": s.get("cookies"),
        "error": s.get("error"),
    })


@app.route("/qr-image/<session_id>")
def qr_image(session_id):
    if session_id not in QR_SESSIONS:
        return "Not found", 404
    s = QR_SESSIONS[session_id]
    if not s.get("png"):
        return "Đang tạo QR...", 404
    return Response(s["png"], mimetype="image/png")


# ============================================================
# PAGES
# ============================================================
@app.route("/")
def home():
    return render_template_string(HTML_HOME)


@app.route("/qr/<session_id>")
def page_qr(session_id):
    if session_id not in QR_SESSIONS:
        return render_template_string(HTML_EXPIRED), 404
    return render_template_string(HTML_QR, session_id=session_id)


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "web-qr",
        "version": "15.0",
        "sessions": len(QR_SESSIONS),
    })


# ============================================================
# HTML — TRANG CHỦ
# ============================================================
HTML_HOME = """
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>ALB QR — Tạo mã QR Zalo</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
body {
    font-family:'Inter',sans-serif;
    min-height:100vh; min-height:100dvh;
    display:flex; align-items:center; justify-content:center</;
    background:linear-gradient(135deg,#f093fb 0button%,#f5576c 100%);
    padding:>
20px;
}
.card {
    background:white; padding   :50px 40px; border-radius:32px <;
    box-shadow:0 30px 80px rgbadiv(0,0,0,0.3); text-align:center;
    max-width:520px; width:100%;
    animation:slideUp 0.7s cubic-bezier(0.16,1,0.3,1);
}
@keyframes slideUp {
    from { opacity:0; transform:translateY(40px); }
    to { opacity:1; transform:translateY(0); }
}
.logo {
    width:90px; height:90px; border-radius:28px;
    background:linear-gradient(135deg,#f093fb,#f5576c);
    display:flex; align-items:center; justify-content:center;
    font-size:48px; margin:0 auto 24px;
    box-shadow:0 20px 50px rgba(245,87,108,0.5);
    animation:bounce 2s ease-in-out infinite;
}
@keyframes bounce {
    0%,100% { transform:translateY(0); }
    50% { transform:translateY(-8px); }
}
h1 { color:#1a1a2e; margin-bottom:12px; font-size:28px; font-weight:900; letter-spacing:-0.6px; }
p.sub { color:#6b7280; margin-bottom:32px; font-size:15px; font-weight:500; }
button {
    width:100%; padding:20px; border:none; border-radius:20px;
    background:linear-gradient(135deg,#f093fb,#f5576c);
    color:white; font-size:18px; font-weight:900; font-family:inherit;
    cursor:pointer; transition:all 0.3s;
    box-shadow:0 15px 40px rgba(245,87,108,0.45);
    position:relative; overflow:hidden;
}
button:hover { transform:translateY(-4px); box-shadow:0 22px 55px rgba(245,87,108,0.6); }
button::after {
    content:''; position:absolute; inset:0;
    background:linear-gradient(135deg,transparent,rgba(255,255,255,0.35),transparent);
    transform:translateX(-100%); transition:transform 0.7s;
}
button:hover::after { transform:translateX(100%); }
button:disabled { opacity:0.6; cursor:wait; }
.steps {
    margin-top:32px; text-align:left;
    background:#f9fafb; padding:20px; border-radius:16px;
    font-size:14px; color:#374151; line-height:1.9;
}
.steps b { color:#667eea; }
</style>
</head>
<body>
<div class="card">
    <div class="logo">📱</div>
    <h1>Tạo mã QR Zalo</h1>
    <p class="sub">Lấy IMEI + Cookie để spam Zalo</p>
    <button id="btn-start" onclick="startQR()">🚀 TẠO MÃ QR class="steps">
        <b>Hướng dẫn:</b><br>
        1️⃣ Bấm nút trên<br>
        2️⃣ Đợi 5-15 giây → hiện mã QR<br>
        3️⃣ Mở app Zalo → QR → Quét mã<br>
        4️⃣ Sau khi quét xong → copy IMEI + Cookie<br>
        5️⃣ Paste sang web spam của bạn
    </div>
</div>

<script>
async function startQR() {
    const btn = document.getElementById('btn-start');
    btn.disabled = true;
    btn.textContent = '⏳ Đang tạo QR...';
    
    try {
        const r = await fetch('/api/qr/create', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        });
        const d = await r.json();
        if (d.ok) {
            window.location.href = d.qr_url;
        } else {
            alert('Lỗi: ' + (d.error || 'Không rõ'));
            btn.disabled = false;
            btn.textContent = '🚀 TẠO MÃ QR';
        }
    } catch (e) {
        alert('Lỗi kết nối: ' + e.message);
        btn.disabled = false;
        btn.textContent = '🚀 TẠO MÃ QR';
    }
}
</script>
</body>
</html>
"""


# ============================================================
# HTML — TRANG QR
# ============================================================
HTML_QR = """
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<title>Quét QR Zalo</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
body {
    font-family:'Inter',sans-serif;
    min-height:100vh; min-height:100dvh;
    display:flex; align-items:center; justify-content:center;
    background:linear-gradient(135deg,#f093fb 0%,#f5576c 100%);
    padding:20px;
}
.card {
    background:white; padding:36px; border-radius:32px;
    box-shadow:0 30px 80px rgba(0,0,0,0.3); text-align:center;
    max-width:560px; width:100%;
    animation:slideUp 0.7s cubic-bezier(0.16,1,0.3,1);
}
@keyframes slideUp {
    from { opacity:0; transform:translateY(40px); }
    to { opacity:1; transform:translateY(0); }
}
h1 { color:#1a1a2e; margin-bottom:8px; font-size:24px; font-weight:900; }
p.sub { color:#6b7280; margin-bottom:24px; font-size:14px; font-weight:600; }
.qr-box {
    background:#f9fafb; padding:24px; border-radius:24px;
    margin-bottom:24px; border:2px dashed #e5e7eb;
}
.qr-box img { max-width:100%; border-radius:14px; display:block; margin:0 auto; }
.status {
    padding:18px; border-radius:16px; font-size:15px; font-weight:800;
    display:flex; align-items:center; justify-content:center; gap:10px;
}
.status.waiting { background:#fef3c7; color:#92400e; }
.status.done { background:#d1fae5; color:#065f46; padding:0; background:transparent; }
.status.error { background:#fee2e2; color:#991b1b; }
.status.expired { background:#f3f4f6; color:#374151; }
.pulse { animation:pulse 2s ease-in-out infinite; }
@keyframes pulse {
    0%,100% { opacity:1; }
    50% { opacity:0.7; }
}
.result {
    text-align:left; padding:16px; border-radius:16px;
    background:linear-gradient(135deg,#d1fae5,#a7f3d0);
    margin-bottom:12px;
}
.result .label {
    font-size:11px; color:#065f46; text-transform:uppercase;
    font-weight:900; letter-spacing:0.5px; margin-bottom:6px;
}
.result .value {
    font-family:'SF Mono',Monaco,monospace;
    font-size:13px; color:#065f46; word-break:break-all;
    font-weight:600; line-height:1.5;
}
.result .value.scroll {
    max-height:180px; overflow-y:auto;
    background:rgba(255,255,255,0.5); padding:10px;
    border-radius:8px; margin-top:6px;
}
.copy-btn {
    width:100%; padding:16px; border:none; border-radius:14px;
    background:linear-gradient(135deg,#667eea,#764ba2);
    color:white; font-size:15px; font-weight:900; font-family:inherit;
    cursor:pointer; box-shadow:0 10px 30px rgba(102,126,234,0.4);
    margin-top:10px; transition:all 0.3s;
}
.copy-btn:hover { transform:translateY(-2px); box-shadow:0 14px 35px rgba(102,126,234,0.55); }
.copy-btn.secondary {
    background:linear-gradient(135deg,#10b981,#059669);
    box-shadow:0 10px 30px rgba(16,185,129,0.4);
}
.hint {
    margin-top:14px; font-size:12px; color:#6b7280;
    text-align:center; font-weight:500;
}
</style>
</head>
<body>
<div class="card">
    <h1>📱 Quét QR Zalo</h1>
    <p class="sub">Mở app Zalo → QR → Quét mã bên dưới</p>
    <div class="qr-box" id="qr-box">
        <img src="/qr-image/{{ session_id }}" alt="QR">
    </div>
    <div id="status" class="status waiting pulse">
        <span>⏳</span> <span>Đang chờ quét...</span>
    </div>
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

        if (d.status === 'waiting') {
            el.className = 'status waiting pulse';
            el.innerHTML = '<span>⏳</span> <span>Đang chờ quét...</span>';
        } else if (d.status === 'done') {
            done = true;
            showResult(d);
        } else if (d.status === 'error') {
            el.className = 'status error';
            el.innerHTML = '<span>❌</span> <span>Lỗi: ' + (d.error || 'Không rõ') + '</span>';
            done = true;
        } else if (d.status === 'expired') {
            el.className = 'status expired';
            el.innerHTML = '<span>⏰</span> <span>QR hết hạn. Tải lại trang để tạo mới.</span>';
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
    el.className = 'status done';
    el.innerHTML = `
        <div style="width:100%;">
            <div style="text-align:center; padding:16px; background:#d1fae5; border-radius:16px; margin-bottom:16px;">
                <div style="font-size:44px; margin-bottom:8px;">✅</div>
                <div style="font-size:18px; font-weight:900; color:#065f46;">Đã lấy cookie thành công!</div>
            </div>
            
            <div class="result">
                <div class="label">📞 IMEI</div>
                <div class="value" id="imei-val">${imei}</div>
            </div>
            
            <div class="result">
                <div class="label">🍪 COOKIE (JSON)</div>
                <div class="value scroll" id="ck-val">${cookiesStr.replace(/</g, '&lt;')}</div>
            </div>
            
            <button class="copy-btn" onclick="copyText('${combo.replace(/'/g, "\\\\'")}')">
                📋 COPY CẢ IMEI + COOKIE
            </button>
            
            <button class="copy-btn secondary" onclick="copyText('${imei}')">
                📞 CHỈ COPY IMEI
            </button>
            
            <button class="copy-btn secondary" onclick="copyText(\`${cookiesStr}\`)">
                🍪 CHỈ COPY COOKIE
            </button>
            
            <div class="hint">
                Sau khi copy → quay lại web chính → paste vào tab Tài khoản
            </div>
        </div>
    `;
}

function copyText(text) {
    navigator.clipboard.writeText(text).then(() => {
        showToast('✅ Đã copy!');
    }).catch(() => {
        // Fallback: dùng textarea
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        try {
            document.execCommand('copy');
            showToast('✅ Đã copy!');
        } catch (e) {
            prompt('Copy thủ công:', text);
        }
        document.body.removeChild(ta);
    });
}

function showToast(msg) {
    const t = document.createElement('div');
    t.textContent = msg;
    t.style.cssText = `
        position:fixed; bottom:30px; left:50%; transform:translateX(-50%);
        background:linear-gradient(135deg,#10b981,#059669); color:white;
        padding:14px 28px; border-radius:30px; font-weight:800;
        box-shadow:0 15px 40px rgba(16,185,129,0.5); z-index:9999;
        animation:slideUpToast 0.4s cubic-bezier(0.16,1,0.3,1);
        font-size:14px;
    `;
    document.body.appendChild(t);
    setTimeout(() => {
        t.style.transition = 'all 0.3s';
        t.style.opacity = '0';
        t.style.transform = 'translateX(-50%) translateY(20px)';
        setTimeout(() => t.remove(), 300);
    }, 2000);
}

// Add animation
const style = document.createElement('style');
style.textContent = '@keyframes slideUpToast { from { opacity:0; transform:translateX(-50%) translateY(30px); } to { opacity:1; transform:translateX(-50%) translateY(0); } }';
document.head.appendChild(style);

setInterval(check, 2000);
check();
</script>
</body>
</html>
"""


# ============================================================
# HTML — SESSION EXPIRED
# ============================================================
HTML_EXPIRED = """
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Session hết hạn</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body {
    font-family:'Inter',sans-serif;
    min-height:100vh; min-height:100dvh;
    display:flex; align-items:center; justify-content:center;
    background:linear-gradient(135deg,#f093fb 0%,#f5576c 100%);
    padding:20px;
}
.card {
    background:white; padding:52px 40px; border-radius:32px;
    box-shadow:0 30px 80px rgba(0,0,0,0.3); text-align:center;
    max-width:500px; width:100%;
}
.icon { font-size:72px; margin-bottom:24px; }
h1 { color:#1a1a2e; margin-bottom:14px; font-size:26px; font-weight:900; }
p { color:#6b7280; font-size:15px; font-weight:500; margin-bottom:24px; line-height:1.6; }
a {
    display:inline-block; padding:16px 32px;
    background:linear-gradient(135deg,#667eea,#764ba2); color:white;
    border-radius:14px; text-decoration:none; font-weight:900; font-size:15px;
    box-shadow:0 12px 30px rgba(102,126,234,0.4);
    transition:all 0.3s;
}
a:hover { transform:translateY(-3px); box-shadow:0 18px 45px rgba(102,126,234,0.55); }
</style>
</head>
<body>
<div class="card">
    <div class="icon">⏰</div>
    <h1>Session hết hạn</h1>
    <p>Session QR này đã hết hạn (5 phút).<br>Vui lòng tạo mã QR mới.</p>
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
