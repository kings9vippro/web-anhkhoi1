import os
import sys
import time
import json
import uuid
import random
import string
import asyncio
import threading
from flask import Flask, request, jsonify, render_template_string, Response

DATA_DIR = "/tmp/qr_sessions"
os.makedirs(DATA_DIR, exist_ok=True)
QR_TTL = 300

app = Flask(__name__)
ACTIVE_BROWSERS = {}

def sess_path(sid):
    return os.path.join(DATA_DIR, f"{sid}.json")

def sess_save(sid, data):
    try:
        with open(sess_path(sid), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] Lưu session thất bại: {e}")

def sess_load(sid):
    try:
        with open(sess_path(sid), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def gen_imei():
    return "".join(random.choices(string.digits, k=15))

# ============================================================
# SERVICE PLAYWRIGHT - GIẢ LẬP APPLE SAFARI
# ============================================================
class ZaloAuthService:
    def __init__(self, session_id):
        self.session_id = session_id
        self.browser = None
        self.playwright = None

    async def run(self):
        from playwright.async_api import async_playwright
        try:
            self.playwright = await async_playwright().start()
            
            # Cấu hình tối ưu bộ nhớ cho Render 512MB RAM
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-zygote",
                    "--single-process",
                ],
            )
            
            # Giả lập danh tính Apple Safari (macOS / iPad Desktop)
            context = await self.browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
                locale="vi-VN",
            )
            page = await context.new_page()

            ACTIVE_BROWSERS[self.session_id] = {
                "browser": self.browser,
                "playwright": self.playwright,
            }

            print(f"[{self.session_id}] Mở chat.zalo.me với danh tính thiết bị Apple...")
            await page.goto("https://chat.zalo.me/", wait_until="networkidle", timeout=60000)
            await asyncio.sleep(4)

            # Chuyển tab sang quét mã QR
            try:
                qr_tab = await page.wait_for_selector("text=/QR|Quét mã/i", timeout=10000)
                if qr_tab:
                    await qr_tab.click()
            except Exception:
                pass

            await asyncio.sleep(3)

            # Chụp ảnh QR
            png_bytes = await page.screenshot(full_page=False)
            img_path = os.path.join(DATA_DIR, f"{self.session_id}.png")
            with open(img_path, "wb") as f:
                f.write(png_bytes)

            sess_save(self.session_id, {
                "status": "ready",
                "created_at": time.time(),
                "img_path": img_path,
                "imei": None,
                "cookies": None,
            })
            print(f"[{self.session_id}] Mã QR sẵn sàng.")

            # Vòng lặp chờ xác nhận trên điện thoại
            start_time = time.time()
            while time.time() - start_time < QR_TTL:
                cookies = await page.context.cookies()
                ck_dict = {c["name"]: c["value"] for c in cookies if "zalo" in c.get("domain", "")}

                if ck_dict.get("zpw_sek") or ("chat.zalo.me" in page.url and "/login" not in page.url):
                    print(f"[{self.session_id}] ✅ Đăng nhập thành công từ thiết bị Apple!")
                    await asyncio.sleep(2)

                    cookies = await page.context.cookies()
                    ck_dict = {c["name"]: c["value"] for c in cookies if "zalo" in c.get("domain", "")}
                    imei = ck_dict.get("zpw_imei") or gen_imei()

                    sess_save(self.session_id, {
                        "status": "done",
                        "imei": imei,
                        "cookies": ck_dict,
                    })

                    # Tiêu hủy mã QR ngay sau khi quét
                    if os.path.exists(img_path):
                        os.remove(img_path)
                    break

                await asyncio.sleep(1.5)
            else:
                sess_save(self.session_id, {"status": "expired"})

        except Exception as e:
            err_msg = str(e)
            print(f"[{self.session_id}] Lỗi tiến trình: {err_msg}")
            sess_save(self.session_id, {"status": "error", "error": err_msg})
        finally:
            await self.cleanup()

    async def cleanup(self):
        ACTIVE_BROWSERS.pop(self.session_id, None)
        try:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass

# ============================================================
# API ENDPOINTS
# ============================================================
@app.route("/api/create", methods=["POST"])
def api_create():
    session_id = uuid.uuid4().hex[:12]
    sess_save(session_id, {"status": "initializing", "created_at": time.time()})

    def run_worker():
        service = ZaloAuthService(session_id)
        asyncio.run(service.run())

    threading.Thread(target=run_worker, daemon=True).start()
    return jsonify({"session_id": session_id})

@app.route("/api/status/<session_id>")
def api_status(session_id):
    data = sess_load(session_id)
    if not data:
        return jsonify({"status": "not_found"}), 404
    return jsonify(data)

@app.route("/qr/<session_id>.png")
def get_qr_image(session_id):
    path = os.path.join(DATA_DIR, f"{session_id}.png")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return Response(f.read(), mimetype="image/png")
    return "QR đã bị hủy hoặc không tồn tại", 404

# ============================================================
# GIAO DIỆN
# ============================================================
@app.route("/")
def index():
    return render_template_string("""
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Zalo QR Login (Apple Device)</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background-color: #f0f2f5;
            display: flex; justify-content: center; align-items: center;
            min-height: 100vh; padding: 16px;
        }
        .container {
            background: #ffffff; width: 100%; max-width: 360px;
            padding: 30px 20px; border-radius: 16px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.06); text-align: center;
        }
        h2 { font-size: 20px; color: #0068ff; margin-bottom: 6px; }
        p.desc { font-size: 13px; color: #666; margin-bottom: 20px; }
        .device-badge {
            display: inline-block; font-size: 11px; background: #eef5ff;
            color: #0068ff; padding: 4px 10px; border-radius: 20px;
            margin-bottom: 15px; font-weight: 600;
        }
        .qr-wrapper {
            width: 250px; height: 250px; margin: 0 auto 16px;
            border: 1px solid #e1e4ea; border-radius: 12px;
            display: flex; align-items: center; justify-content: center;
            background: #fafafa; position: relative; overflow: hidden;
        }
        .qr-wrapper img { width: 100%; height: 100%; object-fit: cover; }
        .spinner {
            border: 3px solid #f3f3f3; border-top: 3px solid #0068ff;
            border-radius: 50%; width: 36px; height: 36px;
            animation: spin 0.8s linear infinite;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .status-text {
            font-size: 14px; font-weight: 500; color: #444;
            margin-bottom: 16px; min-height: 20px;
        }
        .error-detail {
            font-size: 11px; color: #dc3545; background: #fff5f5;
            padding: 8px; border-radius: 6px; margin-bottom: 12px;
            text-align: left; word-break: break-all; display: none;
        }
        .btn {
            width: 100%; padding: 13px; background: #0068ff;
            color: #fff; font-weight: 600; font-size: 15px;
            border: none; border-radius: 10px; cursor: pointer;
        }
        .btn:active { background: #0056d6; }
        .result-box {
            display: none; text-align: left; background: #f8f9fa;
            border: 1px solid #e9ecef; padding: 14px; border-radius: 10px;
            margin-top: 15px; font-size: 12px;
        }
        .result-box textarea {
            width: 100%; height: 85px; margin-top: 6px;
            font-family: monospace; font-size: 11px; padding: 8px;
            border: 1px solid #ced4da; border-radius: 6px; resize: none;
        }
    </style>
</head>
<body>

<div class="container">
    <h2>Zalo QR Login</h2>
    <div class="device-badge">Thiết bị: Apple Safari (macOS / iPad)</div>
    <p class="desc">Mở Zalo trên điện thoại để quét mã</p>

    <div class="qr-wrapper" id="qrContainer">
        <div class="spinner" id="spinner"></div>
        <img id="qrImg" style="display: none;" alt="QR Code">
    </div>

    <div class="status-text" id="statusText">Đang khởi tạo mã QR...</div>
    <div class="error-detail" id="errorDetail"></div>

    <button class="btn" id="refreshBtn" onclick="initNewQR()" style="display:none;">Tạo mã mới</button>

    <div class="result-box" id="resultBox">
        <b>IMEI:</b> <span id="resImei"></span>
        <div style="margin-top: 8px;"><b>Cookies JSON:</b></div>
        <textarea id="resCookies" readonly></textarea>
        <button class="btn" style="margin-top: 10px;" onclick="copyData()">Sao chép Cookies</button>
    </div>
</div>

<script>
    let pollTimer = null;
    let currentSid = null;

    async function initNewQR() {
        clearInterval(pollTimer);
        document.getElementById('refreshBtn').style.display = 'none';
        document.getElementById('resultBox').style.display = 'none';
        document.getElementById('qrImg').style.display = 'none';
        document.getElementById('errorDetail').style.display = 'none';
        document.getElementById('qrContainer').style.display = 'flex';
        document.getElementById('spinner').style.display = 'block';
        document.getElementById('statusText').innerText = 'Đang kết nối máy chủ...';
        document.getElementById('statusText').style.color = '#444';

        try {
            const res = await fetch('/api/create', { method: 'POST' });
            const data = await res.json();
            currentSid = data.session_id;
            pollTimer = setInterval(checkStatus, 1500);
        } catch (e) {
            document.getElementById('statusText').innerText = 'Lỗi kết nối máy chủ!';
            document.getElementById('spinner').style.display = 'none';
            document.getElementById('refreshBtn').style.display = 'block';
        }
    }

    async function checkStatus() {
        if (!currentSid) return;
        try {
            const res = await fetch('/api/status/' + currentSid);
            const data = await res.json();

            if (data.status === 'ready') {
                const img = document.getElementById('qrImg');
                if (img.style.display === 'none') {
                    document.getElementById('spinner').style.display = 'none';
                    img.src = '/qr/' + currentSid + '.png?t=' + Date.now();
                    img.style.display = 'block';
                    document.getElementById('statusText').innerText = 'Mở Zalo quét mã và bấm xác nhận';
                }
            } else if (data.status === 'done') {
                clearInterval(pollTimer);
                document.getElementById('qrContainer').style.display = 'none';
                document.getElementById('statusText').innerText = '🎉 Đăng nhập Apple Device thành công!';
                document.getElementById('statusText').style.color = '#28a745';
                document.getElementById('resImei').innerText = data.imei;
                document.getElementById('resCookies').value = JSON.stringify(data.cookies, null, 2);
                document.getElementById('resultBox').style.display = 'block';
                document.getElementById('refreshBtn').style.display = 'block';
            } else if (data.status === 'expired' || data.status === 'error') {
                clearInterval(pollTimer);
                document.getElementById('spinner').style.display = 'none';
                document.getElementById('statusText').innerText = data.status === 'expired' ? 'Mã QR đã hết hạn' : 'Lỗi khởi tạo QR';
                if (data.error) {
                    const errBox = document.getElementById('errorDetail');
                    errBox.innerText = data.error;
                    errBox.style.display = 'block';
                }
                document.getElementById('refreshBtn').style.display = 'block';
            }
        } catch (e) {}
    }

    function copyData() {
        const text = document.getElementById('resCookies');
        text.select();
        document.execCommand('copy');
        alert('Đã sao chép Cookies!');
    }

    window.onload = initNewQR;
</script>

</body>
</html>
    """)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
