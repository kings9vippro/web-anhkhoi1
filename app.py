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

# Thư mục lưu trữ tạm thời phiên đăng nhập
DATA_DIR = "/tmp/qr_sessions"
os.makedirs(DATA_DIR, exist_ok=True)
QR_TTL = 300  # Thời gian hết hạn QR: 5 phút

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
# TIẾN TRÌNH PLAYWRIGHT LẤY QR VÀ COOKIE ZALO
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
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = await self.browser.new_context(
                viewport={"width": 550, "height": 700},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                locale="vi-VN",
            )
            page = await context.new_page()

            # Lưu browser để dọn dẹp khi cần
            ACTIVE_BROWSERS[self.session_id] = {
                "browser": self.browser,
                "playwright": self.playwright,
            }

            print(f"[{self.session_id}] Đang mở chat.zalo.me...")
            await page.goto("https://chat.zalo.me/", wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(4)

            # Đảm bảo đang mở tab Quét mã QR
            try:
                qr_tab = await page.wait_for_selector("text=/QR|Quét mã/i", timeout=6000)
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
            print(f"[{self.session_id}] Mã QR đã sẵn sàng.")

            # Vòng lặp chờ xác nhận trên điện thoại
            start_time = time.time()
            while time.time() - start_time < QR_TTL:
                cookies = await page.context.cookies()
                ck_dict = {c["name"]: c["value"] for c in cookies if "zalo" in c.get("domain", "")}

                # Đăng nhập thành công khi có zpw_sek hoặc URL chuyển tiếp
                if ck_dict.get("zpw_sek") or ("chat.zalo.me" in page.url and "/login" not in page.url):
                    print(f"[{self.session_id}] ✅ Đăng nhập thành công!")
                    await asyncio.sleep(2)

                    # Đọc lại cookie đầy đủ
                    cookies = await page.context.cookies()
                    ck_dict = {c["name"]: c["value"] for c in cookies if "zalo" in c.get("domain", "")}
                    imei = ck_dict.get("zpw_imei") or gen_imei()

                    sess_save(self.session_id, {
                        "status": "done",
                        "imei": imei,
                        "cookies": ck_dict,
                    })

                    # Hủy ngay file QR sau khi quét xong (dùng 1 lần)
                    if os.path.exists(img_path):
                        os.remove(img_path)
                    break

                await asyncio.sleep(1.5)
            else:
                sess_save(self.session_id, {"status": "expired"})

        except Exception as e:
            print(f"[{self.session_id}] Lỗi: {e}")
            sess_save(self.session_id, {"status": "error", "error": str(e)})
        finally:
            await self.cleanup()

    async def cleanup(self):
        info = ACTIVE_BROWSERS.pop(self.session_id, None)
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
    return "QR không khả dụng hoặc đã bị hủy", 404


# ============================================================
# GIAO DIỆN TẬP TRUNG MÃ QR
# ============================================================
@app.route("/")
def index():
    return render_template_string("""
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Đăng nhập Zalo QR</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
            background-color: #f0f2f5;
            display: flex; justify-content: center; align-items: center;
            min-height: 100vh; padding: 16px;
        }
        .container {
            background: #ffffff;
            width: 100%; max-width: 380px;
            padding: 32px 24px;
            border-radius: 12px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.08);
            text-align: center;
        }
        h2 { font-size: 20px; color: #0068ff; margin-bottom: 6px; }
        p.desc { font-size: 13px; color: #666; margin-bottom: 20px; }

        .qr-wrapper {
            width: 250px; height: 250px;
            margin: 0 auto 16px;
            border: 1px solid #e1e4ea;
            border-radius: 10px;
            display: flex; align-items: center; justify-content: center;
            background: #fafafa;
            overflow: hidden;
            position: relative;
        }
        .qr-wrapper img { width: 100%; height: 100%; object-fit: cover; }
        
        .spinner {
            border: 3px solid #f3f3f3;
            border-top: 3px solid #0068ff;
            border-radius: 50%;
            width: 32px; height: 32px;
            animation: spin 0.8s linear infinite;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }

        .status-text {
            font-size: 14px; font-weight: 500;
            color: #444; margin-bottom: 16px; min-height: 20px;
        }
        .btn {
            display: inline-block; width: 100%;
            padding: 12px; background: #0068ff;
            color: #fff; font-weight: 600; font-size: 14px;
            border: none; border-radius: 8px; cursor: pointer;
        }
        .btn:hover { background: #0056d6; }

        .result-box {
            display: none; text-align: left;
            background: #f7f9fa; border: 1px solid #dce1e7;
            padding: 12px; border-radius: 8px; margin-top: 15px;
            font-size: 12px; word-break: break-all;
        }
        .result-box textarea {
            width: 100%; height: 90px; margin-top: 6px;
            font-family: monospace; font-size: 11px;
            padding: 8px; border: 1px solid #ccc; border-radius: 6px;
            resize: none;
        }
    </style>
</head>
<body>

<div class="container">
    <h2>Zalo QR Login</h2>
    <p class="desc">Mở ứng dụng Zalo trên điện thoại để quét mã</p>

    <div class="qr-wrapper" id="qrContainer">
        <div class="spinner" id="spinner"></div>
        <img id="qrImg" style="display: none;" alt="QR Code">
    </div>

    <div class="status-text" id="statusText">Đang khởi tạo mã QR...</div>

    <button class="btn" id="refreshBtn" onclick="initNewQR()" style="display:none;">Tạo mã mới</button>

    <div class="result-box" id="resultBox">
        <b>IMEI:</b> <span id="resImei"></span>
        <div style="margin-top: 8px;"><b>Cookies JSON:</b></div>
        <textarea id="resCookies" readonly></textarea>
        <button class="btn" style="margin-top: 8px;" onclick="copyData()">Sao chép Cookies</button>
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
        document.getElementById('spinner').style.display = 'block';
        document.getElementById('statusText').innerText = 'Đang kết nối máy chủ tạo QR...';
        document.getElementById('statusText').style.color = '#444';

        try {
            const res = await fetch('/api/create', { method: 'POST' });
            const data = await res.json();
            currentSid = data.session_id;

            // Bắt đầu theo dõi trạng thái
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
                    document.getElementById('statusText').innerText = 'Vui lòng quét mã trên Zalo điện thoại';
                }
            } else if (data.status === 'done') {
                clearInterval(pollTimer);
                document.getElementById('qrContainer').style.display = 'none';
                document.getElementById('statusText').innerText = '🎉 Đăng nhập thành công!';
                document.getElementById('statusText').style.color = '#28a745';

                document.getElementById('resImei').innerText = data.imei;
                document.getElementById('resCookies').value = JSON.stringify(data.cookies, null, 2);
                document.getElementById('resultBox').style.display = 'block';
                document.getElementById('refreshBtn').style.display = 'block';
            } else if (data.status === 'expired' || data.status === 'error') {
                clearInterval(pollTimer);
                document.getElementById('spinner').style.display = 'none';
                document.getElementById('statusText').innerText = data.status === 'expired' ? 'Mã QR đã hết hạn' : 'Đã có lỗi xảy ra';
                document.getElementById('refreshBtn').style.display = 'block';
            }
        } catch (e) {}
    }

    function copyData() {
        const text = document.getElementById('resCookies');
        text.select();
        document.execCommand('copy');
        alert('Đã sao chép Cookies vào bộ nhớ tạm!');
    }

    // Tự động tạo và hiện QR ngay khi load trang
    window.onload = initNewQR;
</script>

</body>
</html>
    """)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
