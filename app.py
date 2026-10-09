# ============================================================
# WEB-QR — Tạo QR Zalo (Bot by Anh Khôi)
# ============================================================
import os
import time
import uuid
import asyncio
import threading
from io import BytesIO

from flask import Flask, request, jsonify, render_template_string, Response
import requests

QR_TTL = 180
LENH_KEY = os.environ.get("LENH_KEY", "qr-lenh-shared-key-2026")
LENH_URL = os.environ.get("LENH_URL", "")

app = Flask(__name__)
QR_SESSIONS = {}


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

            try:
                btn = await page.wait_for_selector(
                    "text=/QR|Quét mã/i", timeout=5000
                )
                await btn.click()
                await asyncio.sleep(2)
            except Exception:
                pass

            await asyncio.sleep(3)

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

            if LENH_URL and LENH_KEY:
                try:
                    requests.post(
                        f"{LENH_URL}/api/receive-cookie",
                        headers={
                            "X-QR-Key": LENH_KEY,
                            "Content-Type": "application/json",
                        },
                        json={
                            "imei": imei or "000000000000000",
                            "cookies": ck,
                            "label": f"qr_{session_id[:8]}",
                        },
                        timeout=15,
                    )
                    print(f"[QR] Đã gửi cookie về {LENH_URL}")
                except Exception as e:
                    print(f"[QR] Không gửi được cookie: {e}")

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


@app.route("/api/qr/create", methods=["POST"])
def api_qr_create():
    data = request.json or {}
    session_id = uuid.uuid4().hex[:16]

    def run():
        qr = ZaloQR()
        asyncio.run(qr.create_qr(session_id))

    threading.Thread(target=run, daemon=True).start()

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


@app.route("/qr/<session_id>")
def page_qr(session_id):
    if session_id not in QR_SESSIONS:
        return render_template_string(HTML_EXPIRED), 404
    return render_template_string(HTML_QR, session_id=session_id)


@app.route("/qr-image/<session_id>")
def qr_image(session_id):
    if session_id not in QR_SESSIONS:
        return "Not found", 404
    s = QR_SESSIONS[session_id]
    if not s.get("png"):
        return "Đang tạo QR...", 404
    return Response(s["png"], mimetype="image/png")


@app.route("/")
def home():
    return render_template_string(HTML_HOME)


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "web-qr",
        "sessions": len(QR_SESSIONS),
        "lenh_configured": bool(LENH_URL),
    })


HTML_HOME = """
<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"><title>ALB QR Service</title><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet"><style>* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; } body { font-family:'Inter',sans-serif; min-height:100vh; min-height:100dvh; display:flex; align-items:center; justify-content:center; background:linear-gradient(135deg,#f093fb 0%,#f5576c 100%); padding:20px; } .card { background:white; padding:52px 44px; border-radius:32px; box-shadow:0 30px 80px rgba(0,0,0,0.3); text-align:center; max-width:520px; width:100%; } .logo { width:80px; height:80px; border-radius:24px; background:linear-gradient(135deg,#f093fb,#f5576c); display:flex; align-items:center; justify-content:center; font-size:40px; margin:0 auto 24px; } h1 { color:#1a1a2e; margin-bottom:14px; font-size:28px; font-weight:900; } p { color:#6b7280; margin-bottom:12px; font-size:15px; font-weight:500; } a { display:inline-block; padding:16px 32px; margin-top:20px; background:linear-gradient(135deg,#667eea,#764ba2); color:white; border-radius:16px; text-decoration:none; font-weight:800; font-size:15px; }</style></head><body><div class="card"><div class="logo">📱</div><h1>ALB QR Service</h1><p>Web tạo mã QR Zalo</p><p style="font-size:13px;opacity:0.7;">Vui lòng truy cập qua web chính để tạo QR</p><a href="/health">🔍 Kiểm tra trạng thái</a></div></body></html>
"""

HTML_QR = """
<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"><title>Quét QR Zalo</title><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet"><style>* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; } body { font-family:'Inter',sans-serif; min-height:100vh; min-height:100dvh; display:flex; align-items:center; justify-content:center; background:linear-gradient(135deg,#f093fb 0%,#f5576c 100%); padding:20px; } .card { background:white; padding:40px; border-radius:32px; box-shadow:0 30px 80px rgba(0,0,0,0.3); text-align:center; max-width:520px; width:100%; } h1 { color:#1a1a2e; margin-bottom:10px; font-size:26px; font-weight:900; } p.sub { color:#6b7280; margin-bottom:28px; font-size:15px; font-weight:600; } .qr-box { background:#f9fafb; padding:28px; border-radius:24px; margin-bottom:28px; border:2px dashed #e5e7eb; } .qr-box img { max-width:100%; border-radius:16px; display:block; margin:0 auto; } .status { padding:18px; border-radius:16px; font-size:15px; font-weight:800; display:flex; align-items:center; justify-content:center; gap:10px; } .status.waiting { background:#fef3c7; color:#92400e; } .status.done { background:#d1fae5; color:#065f46; } .status.error { background:#fee2e2; color:#991b1b; } .status.expired { background:#f3f4f6; color:#374151; } .pulse { animation:pulse 2s ease-in-out infinite; } @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.75; } }</style></head><body><div class="card"><h1>📱 Quét QR Zalo</h1><p class="sub">Mở app Zalo → QR → Quét mã</p><div class="qr-box"><img src="/qr-image/{{ session_id }}" alt="QR"></div><div id="status" class="status waiting pulse"><span>⏳</span> <span>Đang chờ quét...</span></div></div><script>const sessionId = "{{ session_id }}";let done = false;async function check() { if (done) return; try { const r = await fetch(`/api/qr/status/${sessionId}`); const d = await r.json(); const el = document.getElementById('status'); if (d.status === 'waiting') { el.className = 'status waiting pulse'; el.innerHTML = '<span>⏳</span> <span>Đang chờ quét...</span>'; } else if (d.status === 'done') { el.className = 'status done'; el.innerHTML = '<span>✅</span> <span>Đã lấy cookie! Có thể đóng tab.</span>'; done = true; } else if (d.status === 'error') { el.className = 'status error'; el.innerHTML = '<span>❌</span> <span>Lỗi: ' + (d.error || 'Không rõ') + '</span>'; done = true; } else if (d.status === 'expired') { el.className = 'status expired'; el.innerHTML = '<span>⏰</span> <span>QR hết hạn.</span>'; done = true; } } catch (e) {} } setInterval(check, 3000); check();</script></body></html>
"""

HTML_EXPIRED = """
<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"><title>Session hết hạn</title><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet"><style>* { margin:0; padding:0; box-sizing:border-box; } body { font-family:'Inter',sans-serif; min-height:100vh; min-height:100dvh; display:flex; align-items:center; justify-content:center; background:linear-gradient(135deg,#f093fb 0%,#f5576c 100%); padding:20px; } .card { background:white; padding:52px 44px; border-radius:32px; box-shadow:0 30px 80px rgba(0,0,0,0.3); text-align:center; } .icon { font-size:72px; margin-bottom:24px; } h1 { color:#1a1a2e; margin-bottom:14px; font-size:26px; font-weight:900; } p { color:#6b7280; font-size:15px; font-weight:500; }</style></head><body><div class="card"><div class="icon">⏰</div><h1>Session hết hạn</h1><p>Vui lòng quay lại web chính và tạo QR mới.</p></div></body></html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)