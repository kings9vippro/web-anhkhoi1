import os
import io
import json
import time
import uuid
import queue
import threading
import random
import string
import subprocess
import sys

from flask import Flask, request, jsonify, render_template_string, Response

QR_TTL = 600
DATA_DIR = "/tmp/qr_sessions"
os.makedirs(DATA_DIR, exist_ok=True)

# Quản lý luồng trình duyệt độc lập, chống crash bộ nhớ
ACTIVE_WORKERS = {}


def ensure_playwright_browsers():
    """Tự động kiểm tra và tải trình duyệt Chromium nếu môi trường Render chưa có."""
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print("[SETUP] playwright install note: " + str(e))


threading.Thread(target=ensure_playwright_browsers, daemon=True).start()


def sess_path(sid):
    return os.path.join(DATA_DIR, sid + ".json")


def sess_save(sid, data):
    try:
        with open(sess_path(sid), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        print("[SESS] Save error: " + str(e))


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
# WORKER TRÌNH DUYỆT ĐỘC LẬP (ĐỒNG BỘ - CHỐNG OOM & CRASH)
# ============================================================
def run_zalo_worker(session_id, cmd_q):
    from playwright.sync_api import sync_playwright

    png_path = os.path.join(DATA_DIR, f"{session_id}.png")

    try:
        with sync_playwright() as p:
            # Tối ưu cờ khởi chạy tiết kiệm RAM tối đa cho Render Free (512MB)
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--renderer-process-limit=1",
                    "--js-flags=--max-old-space-size=128",
                    "--window-size=1280,800",
                ],
            )

            # BẮT BUỘC DÙNG DESKTOP USER-AGENT ĐỂ ZALO HIỆN MÃ QR
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
            )
            page = context.new_page()

            # Chặn tải các tài nguyên nặng (video, font không cần thiết)
            def intercept_route(route):
                if route.request.resource_type in ["media", "font"]:
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", intercept_route)

            print(f"[WORKER-{session_id}] Mở chat.zalo.me...")
            try:
                page.goto("https://chat.zalo.me/", timeout=35000, wait_until="domcontentloaded")
            except Exception as e:
                print(f"[WORKER-{session_id}] Điều hướng note: {e}")

            time.sleep(3)

            # Tự động chọn tab "VỚI MÃ QR" nếu Zalo hiển thị tab chọn
            try:
                qr_tab = page.query_selector("text=/VỚI MÃ QR|QR/i")
                if qr_tab:
                    qr_tab.click()
                    time.sleep(1)
            except Exception:
                pass

            # Hàm chụp riêng vùng chứa mã QR rõ nét
            def capture_qr_image():
                for sel in [".qrcode", ".qrcode-img", "div[class*='qrcode']", ".login-box", "canvas"]:
                    el = page.query_selector(sel)
                    if el:
                        try:
                            box = el.bounding_box()
                            if box and box["width"] > 60 and box["height"] > 60:
                                el.screenshot(path=png_path)
                                return True
                        except Exception:
                            pass
                page.screenshot(path=png_path)
                return True

            capture_qr_image()
            print(f"[WORKER-{session_id}] Đã xuất ảnh QR thành công")

            sess_save(session_id, {
                "status": "waiting",
                "created_at": time.time(),
                "png_path": png_path,
                "cookies": None,
                "imei": None,
                "qr_version": int(time.time()),
            })

            start_time = time.time()
            while time.time() - start_time < QR_TTL:
                # Kiểm tra hàng đợi lệnh từ người dùng (Làm mới QR)
                try:
                    cmd = cmd_q.get_nowait()
                    if cmd == "refresh":
                        print(f"[WORKER-{session_id}] Thực hiện làm mới mã QR...")
                        try:
                            reload_btn = page.query_selector("text=/Lấy mã mới|Quét mã|Tải lại/i")
                            if reload_btn:
                                reload_btn.click()
                                time.sleep(1.5)
                        except Exception:
                            pass
                        capture_qr_image()
                        data = sess_load(session_id) or {}
                        data["qr_version"] = int(time.time())
                        sess_save(session_id, data)
                    elif cmd == "stop":
                        break
                except queue.Empty:
                    pass

                # Kiểm tra kết quả quét mã thành công
                try:
                    cookies = context.cookies()
                    ck = {c["name"]: c["value"] for c in cookies if "zalo" in c.get("domain", "")}

                    # Nếu xuất hiện zpw_sek hoặc URL chuyển vào hòm thư
                    if ck.get("zpw_sek") or ("chat.zalo.me" in page.url and "/login" not in page.url and "account" not in page.url):
                        print(f"[WORKER-{session_id}] Xác thực Zalo thành công!")
                        time.sleep(3)

                        cookies = context.cookies()
                        ck = {c["name"]: c["value"] for c in cookies if "zalo" in c.get("domain", "")}

                        imei = ""
                        for k in ["imei", "zpw_imei", "device_id", "deviceId"]:
                            try:
                                v = page.evaluate(f"localStorage.getItem('{k}')")
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
                        break
                except Exception as e:
                    print(f"[WORKER-{session_id}] Lỗi kiểm tra cookie: {e}")

                time.sleep(1.5)

            browser.close()
    except Exception as e:
        print(f"[WORKER-{session_id}] Lỗi ngoại lệ: {e}")
        sess_save(session_id, {
            "status": "error",
            "error": str(e),
            "created_at": time.time(),
        })
    finally:
        ACTIVE_WORKERS.pop(session_id, None)


# ============================================================
# API CONTROLLER
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

    cmd_q = queue.Queue()
    ACTIVE_WORKERS[session_id] = cmd_q

    worker_thread = threading.Thread(
        target=run_zalo_worker,
        args=(session_id, cmd_q),
        daemon=True,
    )
    worker_thread.start()

    # Chờ tối đa 15s để tạo ảnh QR ban đầu
    start = time.time()
    while time.time() - start < 15:
        data = sess_load(session_id)
        if data and data.get("status") in ("waiting", "error"):
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
        return jsonify({"error": "Phiên làm việc không tồn tại"}), 404
    return jsonify({
        "status": data.get("status", "waiting"),
        "imei": data.get("imei"),
        "cookies": data.get("cookies"),
        "error": data.get("error"),
        "qr_version": data.get("qr_version", 0),
    })


@app.route("/api/qr/refresh/<session_id>", methods=["POST"])
def api_qr_refresh(session_id):
    cmd_q = ACTIVE_WORKERS.get(session_id)
    if not cmd_q:
        return jsonify({"error": "Phiên duyệt web đã đóng hoặc hết hạn"}), 404

    cmd_q.put("refresh")
    time.sleep(2)

    data = sess_load(session_id) or {}
    return jsonify({"ok": True, "qr_version": data.get("qr_version", int(time.time()))})


@app.route("/api/qr/force-extract/<session_id>", methods=["POST"])
def api_qr_force_extract(session_id):
    data = sess_load(session_id)
    if not data:
        return jsonify({"error": "Phiên không tồn tại"}), 404

    if data.get("status") == "done" and data.get("cookies"):
        return jsonify({
            "status": "done",
            "imei": data.get("imei"),
            "cookies": data.get("cookies"),
        })

    return jsonify({
        "status": "waiting",
        "note": "Vui lòng xác nhận đăng nhập trên Zalo điện thoại trước",
    })


@app.route("/qr-image/<session_id>")
def qr_image(session_id):
    data = sess_load(session_id)
    if not data:
        return "Not found", 404
    png_path = data.get("png_path")
    if not png_path or not os.path.exists(png_path):
        return "Đang tạo mã QR...", 404
    with open(png_path, "rb") as f:
        content = f.read()
    return Response(
        content,
        mimetype="image/png",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.route("/")
def home():
    return render_template_string(HTML_HOME)


@app.route("/qr/<session_id>")
def page_qr(session_id):
    if not sess_exists(session_id):
        return render_template_string(HTML_EXPIRED), 404
    return render_template_string(HTML_QR, session_id=session_id)


# ============================================================
# THIẾT KẾ TRẮNG TUYẾT & ĐEN OBSIDIAN (SNOW-WHITE LUXURY)
# ============================================================
SHARED_CSS = """
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html { -webkit-text-size-adjust:100%; touch-action:manipulation; }
body {
    font-family:-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
    min-height:100vh; min-height:100dvh;
    display:flex; align-items:center; justify-content:center;
    background:#f4f5f7;
    background-image:
        radial-gradient(circle at 50% 0%, #ffffff 0%, #edf0f4 100%);
    padding:20px;
    color:#0f172a;
}

/* KHUNG CARD CHÍNH - TRẮNG TUYẾT HOÀN HẢO */
.card {
    position:relative;
    background:#ffffff;
    padding:44px 28px 40px;
    border-radius:36px;
    border:1px solid rgba(0, 0, 0, 0.08);
    box-shadow:
        0 20px 60px -15px rgba(15, 23, 42, 0.08),
        0 0 1px 1px rgba(0, 0, 0, 0.02);
    text-align:center; max-width:460px; width:100%;
    animation:cardIn 0.45s cubic-bezier(0.16,1,0.3,1);
}
@keyframes cardIn {
    from { opacity:0; transform:translateY(20px) scale(0.98); }
    to { opacity:1; transform:translateY(0) scale(1); }
}

.brand-badge {
    display:inline-flex; align-items:center; justify-content:center;
    width:62px; height:62px; border-radius:22px;
    background:#fafafa;
    border:1.5px solid #e5e7eb;
    margin-bottom:18px;
    box-shadow:0 4px 14px rgba(0,0,0,0.03);
}
.brand-badge svg { width:30px; height:30px; stroke:#0f172a; fill:none; stroke-width:2; }

h1 {
    color:#0f172a;
    margin-bottom:8px;
    font-size:22px; font-weight:800;
    letter-spacing:-0.4px;
}
p.sub {
    color:#64748b;
    margin-bottom:28px; font-size:13px; font-weight:500;
    line-height:1.5;
}

/* NÚT BẤM VIÊN THUỐC TRẮNG TUYẾT (SNOW PILL BUTTONS) */
.btn-pill {
    position:relative;
    width:100%; padding:17px 24px; border-radius:9999px;
    background:linear-gradient(180deg, #ffffff 0%, #f9fafb 100%);
    color:#0f172a; font-size:14px; font-weight:700;
    font-family:inherit; cursor:pointer; letter-spacing:0.3px;
    border:1.5px solid #e2e8f0;
    box-shadow:
        0 4px 14px rgba(0, 0, 0, 0.04),
        inset 0 1px 1px #ffffff;
    display:inline-flex; align-items:center; justify-content:center; gap:10px;
    text-decoration:none;
    transition:all 0.25s cubic-bezier(0.16,1,0.3,1);
}
.btn-pill:hover {
    background:#ffffff;
    border-color:#0f172a;
    transform:translateY(-2px);
    box-shadow:0 8px 24px rgba(15, 23, 42, 0.08);
}
.btn-pill:active { transform:translateY(0); }
.btn-pill:disabled { opacity:0.6; cursor:wait; }
.btn-pill svg { width:18px; height:18px; stroke:#0f172a; stroke-width:2.2; fill:none; }

.btn-secondary {
    margin-top:12px;
    background:#ffffff;
    border-color:#e2e8f0;
}

@keyframes spin {
    0% { transform:rotate(0deg); }
    100% { transform:rotate(360deg); }
}
.spin { animation:spin 1s linear infinite; }
"""


HTML_HOME = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Đăng Nhập Zalo — Snow White</title>
<style>
""" + SHARED_CSS + """
.guide-box {
    margin-top:28px; text-align:left;
    background:#fafafa;
    border:1px solid #f1f5f9;
    padding:22px; border-radius:24px;
}
.guide-title {
    display:flex; align-items:center; gap:8px;
    color:#0f172a; font-size:11px; font-weight:800;
    letter-spacing:1px; margin-bottom:14px; text-transform:uppercase;
}
.guide-title svg { width:15px; height:15px; stroke:#0f172a; fill:none; stroke-width:2.5; }
.guide-item {
    display:flex; align-items:center; gap:12px;
    color:#475569; font-size:13px; font-weight:500;
    margin-bottom:11px; line-height:1.4;
}
.guide-badge {
    flex-shrink:0; width:22px; height:22px; border-radius:50%;
    background:#ffffff; color:#0f172a; font-size:11px; font-weight:800;
    display:inline-flex; align-items:center; justify-content:center;
    border:1px solid #e2e8f0;
    box-shadow:0 2px 4px rgba(0,0,0,0.03);
}
</style>
</head>
<body>
<div class="card">
    <div class="brand-badge">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="7" height="7" rx="2"></rect>
            <rect x="14" y="3" width="7" height="7" rx="2"></rect>
            <rect x="14" y="14" width="7" height="7" rx="2"></rect>
            <rect x="3" y="14" width="7" height="7" rx="2"></rect>
        </svg>
    </div>

    <h1>XÁC THỰC MÃ QR</h1>
    <p class="sub">Tạo phiên duyệt web tự động và quét mã để trích xuất cookie</p>

    <button id="btn-start" class="btn-pill" onclick="startQR()">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon>
        </svg>
        <span>KHỞI TẠO MÃ QR</span>
    </button>

    <div class="guide-box">
        <div class="guide-title">
            <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>
            Quy trình xác thực
        </div>
        <div class="guide-item"><span class="guide-badge">1</span> Nhấn Khởi tạo mã QR để nạp trang Zalo</div>
        <div class="guide-item"><span class="guide-badge">2</span> Mở app Zalo trên điện thoại và quét mã hiển thị</div>
        <div class="guide-item"><span class="guide-badge">3</span> Xác nhận đồng ý đăng nhập trên điện thoại</div>
        <div class="guide-item"><span class="guide-badge">4</span> Web tự nhận diện và xuất ngay IMEI & Cookie</div>
    </div>
</div>

<script>
async function startQR() {
    const btn = document.getElementById('btn-start');
    btn.disabled = true;
    btn.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.2"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>ĐANG KHỞI TẠO...</span>';
    try {
        const r = await fetch('/api/qr/create', { method: 'POST' });
        const d = await r.json();
        if (d.ok) {
            btn.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg><span>HOÀN TẤT</span>';
            setTimeout(function() { window.location.href = d.qr_url; }, 250);
        } else {
            alert('Lỗi: ' + (d.error || 'Không rõ nguyên nhân'));
            btn.disabled = false;
            btn.innerHTML = '<svg viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg><span>KHỞI TẠO MÃ QR</span>';
        }
    } catch (e) {
        alert('Lỗi kết nối máy chủ');
        btn.disabled = false;
        btn.innerHTML = '<svg viewBox="0 0 24 24"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"></polygon></svg><span>KHỞI TẠO MÃ QR</span>';
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
<title>Quét QR — Snow White</title>
<style>
""" + SHARED_CSS + """
.qr-frame {
    position:relative;
    background:#ffffff;
    padding:16px; border-radius:26px;
    margin:0 auto 16px; max-width:300px; width:100%;
    border:1.5px solid #e2e8f0;
    box-shadow:0 8px 24px rgba(0, 0, 0, 0.04);
    min-height:260px;
    display:flex; align-items:center; justify-content:center;
}
.qr-frame img {
    width:100%; height:auto; display:block; border-radius:16px;
    object-fit:contain;
}
.qr-loader {
    display:flex; flex-direction:column; align-items:center; gap:10px;
    color:#64748b; font-size:12px; font-weight:600;
}
.qr-loader svg { width:28px; height:28px; stroke:#0f172a; stroke-width:2; fill:none; }

.status-badge {
    padding:12px 18px; border-radius:9999px;
    font-size:13px; font-weight:600;
    display:inline-flex; align-items:center; justify-content:center; gap:8px;
    background:#f8fafc; color:#0f172a;
    margin-bottom:18px; width:100%;
    border:1px solid #e2e8f0;
}
.status-badge svg { width:16px; height:16px; stroke:currentColor; fill:none; stroke-width:2.2; }

.result-card { text-align:left; }
.success-banner {
    text-align:center; padding:18px;
    background:#f8fafc; border-radius:22px; margin-bottom:16px;
    border:1px solid #e2e8f0;
}
.success-badge-icon {
    width:46px; height:46px; border-radius:50%; margin:0 auto 10px;
    background:#ffffff; display:flex; align-items:center; justify-content:center;
    box-shadow:0 4px 12px rgba(0, 0, 0, 0.05);
    border:1px solid #e2e8f0;
}
.success-badge-icon svg { width:22px; height:22px; stroke:#0f172a; stroke-width:2.5; fill:none; }
.success-banner-title {
    font-size:16px; font-weight:800; color:#0f172a;
}

.data-box {
    padding:14px 18px; border-radius:20px;
    background:#fafafa; margin-bottom:12px;
    border:1px solid #e2e8f0;
}
.data-label {
    display:flex; align-items:center; gap:7px;
    font-size:11px; color:#64748b; text-transform:uppercase;
    font-weight:800; letter-spacing:0.8px; margin-bottom:6px;
}
.data-label svg { width:14px; height:14px; stroke:#64748b; fill:none; stroke-width:2; }
.data-val {
    font-family:-apple-system, BlinkMacSystemFont, "SF Mono", monospace;
    font-size:12px; color:#0f172a; word-break:break-all;
    font-weight:600; line-height:1.5;
}
.data-val.scrollable {
    max-height:110px; overflow-y:auto;
    background:#ffffff; padding:10px; border-radius:12px;
    border:1px solid #f1f5f9; margin-top:6px;
}

.toast {
    position:fixed; bottom:24px; left:50%; transform:translateX(-50%);
    background:#0f172a; color:#ffffff;
    padding:12px 24px; border-radius:9999px;
    font-weight:600; font-size:13px; z-index:9999;
    box-shadow:0 12px 30px rgba(0,0,0,0.25);
    display:flex; align-items:center; gap:8px;
}
.toast svg { width:16px; height:16px; stroke:#ffffff; stroke-width:2.5; fill:none; }
</style>
</head>
<body>
<div class="card">
    <div class="brand-badge">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <rect x="5" y="2" width="14" height="20" rx="3" ry="3"></rect>
            <line x1="12" y1="18" x2="12.01" y2="18"></line>
        </svg>
    </div>

    <h1>MÃ ĐĂNG NHẬP ZALO</h1>
    <p class="sub">Mở ứng dụng Zalo trên điện thoại và quét mã bên dưới</p>

    <div id="qr-block">
        <div class="qr-frame">
            <div id="qr-loading-indicator" class="qr-loader">
                <svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.2"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg>
                <span>Đang tải mã QR...</span>
            </div>
            <img id="qr-img" src="/qr-image/{{ session_id }}" alt="Mã QR" style="display:none;" onload="onQrImageLoaded()" onerror="onQrImageError()">
        </div>
    </div>

    <div id="status" class="status-badge">
        <svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.2"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg>
        <span>Đang chờ thao tác quét...</span>
    </div>

    <button id="btn-refresh" class="btn-pill" onclick="refreshQR()">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="23 4 23 10 17 10"></polyline>
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
        </svg>
        <span>LÀM MỚI MÃ QR</span>
    </button>

    <button id="btn-check" class="btn-pill btn-secondary" onclick="forceCheck()">
        <svg viewBox="0 0 24 24" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"></polyline>
        </svg>
        <span>TÔI ĐÃ ĐỒNG Ý TRÊN ĐIỆN THOẠI</span>
    </button>
</div>

<script>
const sessionId = "{{ session_id }}";
let done = false;
let lastQrVersion = 0;

function onQrImageLoaded() {
    document.getElementById('qr-loading-indicator').style.display = 'none';
    document.getElementById('qr-img').style.display = 'block';
}

function onQrImageError() {
    setTimeout(function() {
        document.getElementById('qr-img').src = '/qr-image/' + sessionId + '?t=' + Date.now();
    }, 2000);
}

async function check() {
    if (done) return;
    try {
        const r = await fetch('/api/qr/status/' + sessionId);
        if (!r.ok) return;
        const d = await r.json();
        const el = document.getElementById('status');

        if (d.qr_version && d.qr_version !== lastQrVersion) {
            lastQrVersion = d.qr_version;
            document.getElementById('qr-img').src = '/qr-image/' + sessionId + '?v=' + d.qr_version;
        }

        if (d.status === 'waiting' || d.status === 'creating') {
            el.className = 'status-badge';
            el.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.2"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>Đang chờ quét...</span>';
        } else if (d.status === 'done') {
            done = true;
            showResult(d);
        } else if (d.status === 'error') {
            el.className = 'status-badge';
            el.innerHTML = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg><span>' + (d.error || 'Lỗi thao tác') + '</span>';
            done = true;
        }
    } catch (e) {}
}

async function refreshQR() {
    const btn = document.getElementById('btn-refresh');
    btn.disabled = true;
    btn.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.2"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>ĐANG LÀM MỚI...</span>';
    try {
        const r = await fetch('/api/qr/refresh/' + sessionId, { method: 'POST' });
        if (r.ok) {
            const d = await r.json();
            document.getElementById('qr-img').src = '/qr-image/' + sessionId + '?v=' + d.qr_version;
            showToast('Đã làm mới mã QR');
            lastQrVersion = d.qr_version;
        } else {
            showToast('Phiên làm việc đã đóng, vui lòng tạo mới');
        }
    } catch (e) {
        showToast('Không thể kết nối máy chủ');
    }
    btn.disabled = false;
    btn.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg><span>LÀM MỚI MÃ QR</span>';
}

async function forceCheck() {
    const btn = document.getElementById('btn-check');
    btn.disabled = true;
    btn.innerHTML = '<svg class="spin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-opacity="0.2"></circle><path d="M12 2a10 10 0 0 1 10 10"></path></svg><span>ĐANG KIỂM TRA...</span>';
    try {
        const r = await fetch('/api/qr/force-extract/' + sessionId, { method: 'POST' });
        const d = await r.json();
        if (d.status === 'done') {
            done = true;
            showResult(d);
        } else {
            showToast(d.note || 'Chưa phát hiện đăng nhập');
            btn.disabled = false;
            btn.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg><span>TÔI ĐÃ ĐỒNG Ý TRÊN ĐIỆN THOẠI</span>';
        }
    } catch (e) {
        showToast('Lỗi kiểm tra dữ liệu');
        btn.disabled = false;
        btn.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg><span>TÔI ĐÃ ĐỒNG Ý TRÊN ĐIỆN THOẠI</span>';
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

    document.getElementById('qr-block').style.display = 'none';
    document.getElementById('btn-check').style.display = 'none';
    document.getElementById('btn-refresh').style.display = 'none';
    document.getElementById('status').style.display = 'none';

    const card = document.querySelector('.card');
    const resWrap = document.createElement('div');
    resWrap.className = 'result-card';
    resWrap.innerHTML =
        '<div class="success-banner">' +
            '<div class="success-badge-icon">' +
                '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg>' +
            '</div>' +
            '<div class="success-banner-title">ĐĂNG NHẬP THÀNH CÔNG</div>' +
        '</div>' +
        '<div class="data-box">' +
            '<div class="data-label">' +
                '<svg viewBox="0 0 24 24"><rect x="5" y="2" width="14" height="20" rx="3"></rect><line x1="12" y1="18" x2="12.01" y2="18"></line></svg>' +
                'Mã Định Danh IMEI' +
            '</div>' +
            '<div class="data-val">' + escapeHtml(imei) + '</div>' +
        '</div>' +
        '<div class="data-box">' +
            '<div class="data-label">' +
                '<svg viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="11" rx="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4"></path></svg>' +
                'Dữ Liệu Cookie' +
            '</div>' +
            '<div class="data-val scrollable">' + escapeHtml(cookiesStr) + '</div>' +
        '</div>' +
        '<button class="btn-pill" id="copy-all">' +
            '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>' +
            '<span>SAO CHÉP TẤT CẢ (IMEI + COOKIE)</span>' +
        '</button>' +
        '<button class="btn-pill btn-secondary" id="copy-imei">' +
            '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>' +
            '<span>CHỈ SAO CHÉP IMEI</span>' +
        '</button>' +
        '<button class="btn-pill btn-secondary" id="copy-ck">' +
            '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>' +
            '<span>CHỈ SAO CHÉP COOKIE</span>' +
        '</button>';

    card.appendChild(resWrap);

    document.getElementById('copy-all').onclick = function() { copyText(combo); };
    document.getElementById('copy-imei').onclick = function() { copyText(imei); };
    document.getElementById('copy-ck').onclick = function() { copyText(cookiesStr); };
}

function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function() {
            showToast('Đã sao chép vào bộ nhớ tạm');
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
    try { document.execCommand('copy'); showToast('Đã sao chép'); }
    catch (e) { prompt('Sao chép:', text); }
    document.body.removeChild(ta);
}

function showToast(msg) {
    const old = document.querySelector('.toast');
    if (old) old.remove();
    const t = document.createElement('div');
    t.className = 'toast';
    t.innerHTML = '<svg viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg><span>' + msg + '</span>';
    document.body.appendChild(t);
    setTimeout(function() { t.remove(); }, 2200);
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
<title>Hết Hạn Phiên</title>
<style>
""" + SHARED_CSS + """
.expired-icon-wrap {
    width:64px; height:64px; border-radius:50%; margin:0 auto 18px;
    background:#fafafa; display:flex; align-items:center; justify-content:center;
    border:1px solid #e2e8f0;
}
.expired-icon-wrap svg { width:32px; height:32px; stroke:#64748b; stroke-width:2; fill:none; }
</style>
</head>
<body>
<div class="card">
    <div class="expired-icon-wrap">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
    </div>
    <h1>PHIÊN HẾT HẠN</h1>
    <p class="sub">Mã QR đã hết thời gian chờ bảo mật. Vui lòng tạo phiên mới.</p>
    <a href="/" class="btn-pill">
        <svg viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>
        <span>TẠO MÃ QR MỚI</span>
    </a>
</div>
</body>
</html>
"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
