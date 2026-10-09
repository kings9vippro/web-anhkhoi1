# ============================================================
# WEB-QR v16.0 — Tạo QR Zalo qua Browserless.io
# Không cần Chromium trên Render — dùng server Browserless
# ============================================================
import os
import io
import time
import json
import uuid
import base64
import threading

from flask import Flask, request, jsonify, render_template_string, Response
import requests

# ============================================================
# CONFIG
# ============================================================
BROWSERLESS_KEY = os.environ.get("BROWSERLESS_KEY", "")
QR_TTL = 300  # 5 phút

app = Flask(__name__)
QR_SESSIONS = {}


# ============================================================
# BROWSERLESS API
# ============================================================
def browserless_content(url, wait_ms=5000):
    """
    Gọi Browserless để lấy HTML của trang.
    """
    api_url = f"https://chrome.browserless.io/content?token={BROWSERLESS_KEY}"
    payload = {
        "url": url,
        "gotoOptions": {
            "waitUntil": "networkidle2",
            "timeout": 30000,
        },
        "waitFor": wait_ms,
    }
    headers = {"Content-Type": "application/json"}
    r = requests.post(api_url, json=payload, headers=headers, timeout=60)
    return r


def browserless_screenshot(url, wait_ms=5000):
    """
    Gọi Browserless để chụp ảnh trang.
    """
    api_url = f"https://chrome.browserless.io/screenshot?token={BROWSERLESS_KEY}"
    payload = {
        "url": url,
        "options": {
            "fullPage": False,
            "type": "png",
        },
        "gotoOptions": {
            "waitUntil": "networkidle2",
            "timeout": 30000,
        },
        "waitFor": wait_ms,
    }
    headers = {"Content-Type": "application/json"}
    r = requests.post(api_url, json=payload, headers=headers, timeout=60)
    return r


def browserless_evaluate(url, script, wait_ms=5000):
    """
    Gọi Browserless để chạy JavaScript và lấy kết quả.
    """
    api_url = f"https://chrome.browserless.io/function?token={BROWSERLESS_KEY}"
    payload = {
        "code": f"""
        module.exports = async function({{ page }}) {{
            await page.goto("{url}", {{ waitUntil: "networkidle2", timeout: 30000 }});
            await new Promise(r => setTimeout(r, {wait_ms}));
            const result = await page.evaluate(() => {{
                {script}
            }});
            return {{ data: result, type: 'application/json' }};
        }}
        """,
    }
    headers = {"Content-Type": "application/json"}
    r = requests.post(api_url, json=payload, headers=headers, timeout=60)
    return r


# ============================================================
# QR SESSION WORKER
# ============================================================
def create_qr_session(session_id):
    """
    Tạo session QR — dùng Browserless để lấy ảnh QR và cookie.
    """
    try:
        # Bước 1: Chụp ảnh trang chat.zalo.me
        print(f"[QR] Bắt đầu tạo session {session_id}")
        r = browserless_screenshot("https://chat.zalo.me/", wait_ms=8000)

        if r.status_code != 200:
            QR_SESSIONS[session_id] = {
                "status": "error",
                "error": f"Browserless lỗi: HTTP {r.status_code} - {r.text[:200]}",
                "created_at": time.time(),
            }
            return

        png_data = r.content

        # Cắt ảnh QR (giữa màn hình)
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(png_data))
            w, h = img.size
            img = img.crop((
                int(w * 0.15), int(h * 0.15),
                int(w * 0.85), int(h * 0.85)
            ))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            png_data = buf.getvalue()
        except Exception:
            pass

        QR_SESSIONS[session_id] = {
            "png": png_data,
            "status": "waiting",
            "created_at": time.time(),
            "cookies": None,
            "imei": None,
        }

        print(f"[QR] Session {session_id} đã có ảnh QR")

        # Bước 2: Poll để lấy cookie
        threading.Thread(
            target=poll_login,
            args=(session_id,),
            daemon=True,
        ).start()

    except Exception as e:
        QR_SESSIONS[session_id] = {
            "status": "error",
            "error": str(e),
            "created_at": time.time(),
        }
        print(f"[QR] Lỗi: {e}")


def poll_login(session_id):
    """
    Poll bằng Browserless để lấy cookie sau khi user quét.
    Gọi lại mỗi 5 giây cho đến khi có cookie hoặc hết hạn.
    """
    start = time.time()
    while time.time() - start < QR_TTL:
        try:
            # Gọi Browserless để lấy cookie hiện tại
            script = """
                const ck = {};
                document.cookie.split(';').forEach(c => {
                    const p = c.trim().split('=');
                    if (p.length >= 2) ck[p[0]] = p.slice(1).join('=');
                });
                const imei = localStorage.getItem('imei')
                    || localStorage.getItem('zpw_imei')
                    || localStorage.getItem('device_id')
                    || '000000000000000';
                return { cookies: ck, imei: imei, url: window.location.href };
            """
            r = browserless_evaluate("https://chat.zalo.me/", script, wait_ms=3000)

            if r.status_code == 200:
                data = r.json()
                result = data.get("data", {})
                ck = result.get("cookies", {})
                imei = result.get("imei", "")
                url = result.get("url", "")

                # Nếu URL đổi sang chat chính hoặc có cookie zalo đủ
                if ("chat.zalo.me" in url and "login" not in url) or \
                   ("zpw_sek" in ck and "zalo_u_id" in ck):
                    QR_SESSIONS[session_id]["status"] = "done"
                    QR_SESSIONS[session_id]["cookies"] = ck
                    QR_SESSIONS[session_id]["imei"] = imei or "000000000000000"
                    print(f"[QR] Session {session_id} DONE - UID: {ck.get('zalo_u_id')}")
                    return

        except Exception as e:
            print(f"[QR] Poll lỗi: {e}")

        time.sleep(5)

    QR_SESSIONS[session_id]["status"] = "expired"


# ============================================================
# API
# ============================================================
@app.route("/api/qr/create", methods=["POST"])
def api_qr_create():
    if not BROWSERLESS_KEY:
        return jsonify({
            "error": "Chưa cấu hình BROWSERLESS_KEY. Vào Render → Environment → thêm env này."
        }), 500

    session_id = uuid.uuid4().hex[:16]

    threading.Thread(
        target=create_qr_session,
        args=(session_id,),
        daemon=True,
    ).start()

    # Đợi QR render xong (tối đa 40s)
    start = time.time()
    while time.time() - start < 40:
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
        "version": "16.0-browserless",
        "sessions": len(QR_SESSIONS),
        "browserless_configured": bool(BROWSERLESS_KEY),
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
<title>Tạo QR Zalo</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
body { font-family:'Inter',sans-serif; min-height:100vh; min-height:100dvh; display:flex; align-items:center; justify-content:center; background:linear-gradient(135deg,#f093fb,#f5576c); padding:20px; }
.card { background:white; padding:50px 40px; border-radius:32px; box-shadow:0 30px 80px rgba(0,0,0,0.3); text-align:center; max-width:520px; width:100%; animation:slideUp 0.7s cubic-bezier(0.16,1,0.3,1); }
@keyframes slideUp { from { opacity:0; transform:translateY(40px); } to { opacity:1; transform:translateY(0); } }
.logo { width:90px; height:90px; border-radius:28px; background:linear-gradient(135deg,#f093fb,#f5576c); display:flex; align-items:center; justify-content:center; font-size:48px; margin:0 auto 24px; box-shadow:0 20px 50px rgba(245,87,108,0.5); }
h1 { color:#1a1a2e; margin-bottom:12px; font-size:28px; font-weight:900; }
p.sub { color:#6b7280; margin-bottom:32px; font-size:15px; }
button { width:100%; padding:20px; border:none; border-radius:20px; background:linear-gradient(135deg,#f093fb,#f5576c); color:white; font-size:18px; font-weight:900; font-family:inherit; cursor:pointer; box-shadow:0 15px 40px rgba(245,87,108,0.45); transition:all 0.3s; }
button:hover { transform:translateY(-4px); box-shadow:0 22px 55px rgba(245,87,108,0.6); }
button:disabled { opacity:0.6; cursor:wait; }
.steps { margin-top:32px; text-align:left; background:#f9fafb; padding:20px; border-radius:16px; font-size:14px; color:#374151; line-height:1.9; }
.steps b { color:#667eea; }
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
        2️⃣ Đợi 5-15 giây → hiện mã QR<br>
        3️⃣ Mở app Zalo → QR → Quét mã<br>
        4️⃣ Copy IMEI + Cookie
    </div>
</div>

<script>
async function startQR() {
    const btn = document.getElementById('btn-start');
    btn.disabled = true;
    btn.textContent = '⏳ Đang tạo QR...';
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
<title>Quét QR Zalo</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:'Inter',sans-serif; min-height:100vh; min-height:100dvh; display:flex; align-items:center; justify-content:center; background:linear-gradient(135deg,#f093fb,#f5576c); padding:20px; }
.card { background:white; padding:36px; border-radius:32px; box-shadow:0 30px 80px rgba(0,0,0,0.3); text-align:center; max-width:560px; width:100%; }
h1 { color:#1a1a2e; margin-bottom:8px; font-size:24px; font-weight:900; }
p.sub { color:#6b7280; margin-bottom:24px; font-size:14px; }
.qr-box { background:#f9fafb; padding:24px; border-radius:24px; margin-bottom:24px; border:2px dashed #e5e7eb; }
.qr-box img { max-width:100%; border-radius:14px; display:block; margin:0 auto; }
.status { padding:18px; border-radius:16px; font-size:15px; font-weight:800; }
.status.waiting { background:#fef3c7; color:#92400e; }
.status.done { background:transparent; }
.status.error { background:#fee2e2; color:#991b1b; }
.status.expired { background:#f3f4f6; color:#374151; }
.result { text-align:left; padding:16px; border-radius:16px; background:linear-gradient(135deg,#d1fae5,#a7f3d0); margin-bottom:12px; }
.result .label { font-size:11px; color:#065f46; text-transform:uppercase; font-weight:900; margin-bottom:6px; }
.result .value { font-family:monospace; font-size:13px; color:#065f46; word-break:break-all; line-height:1.5; }
.result .value.scroll { max-height:180px; overflow-y:auto; background:rgba(255,255,255,0.5); padding:10px; border-radius:8px; }
.copy-btn { width:100%; padding:16px; border:none; border-radius:14px; background:linear-gradient(135deg,#667eea,#764ba2); color:white; font-size:15px; font-weight:900; font-family:inherit; cursor:pointer; margin-top:10px; }
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
        if (d.status === 'waiting') {
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
            el.textContent = '⏰ QR hết hạn. Tải lại trang.';
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
        const t = document.createElement('div');
        t.textContent = '✅ Đã copy!';
        t.style.cssText = 'position:fixed;bottom:30px;left:50%;transform:translateX(-50%);background:#10b981;color:white;padding:14px 28px;border-radius:30px;font-weight:800;z-index:9999;';
        document.body.appendChild(t);
        setTimeout(() => t.remove(), 2000);
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


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
