# ============================================================
# WEB-QR ZALO NOEL EDITION (LIQUID GLASS & MULTI-DEVICE)
# Fix: Crash QR Loop, Fix Auto-Zoom, SVG Santa Design
# ============================================================
import os
import json
import time
import uuid
import random
import threading
from flask import Flask, jsonify, render_template_string, Response
from playwright.sync_api import sync_playwright

app = Flask(__name__)
DATA_DIR = "/tmp/zalo_noel_sessions"
os.makedirs(DATA_DIR, exist_ok=True)
QR_TTL = 300  # 5 phút hết hạn phiên

# ------------------------------------------------------------
# 1. KHO DANH TÍNH MÁY TÍNH (MULTI-PC POOL)
# ------------------------------------------------------------
PC_PROFILES = [
    {
        "name": "Apple MacBook Pro M3 (macOS Sonoma)",
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "platform": "MacIntel",
        "vendor": "Apple Computer, Inc."
    },
    {
        "name": "Dell XPS 15 (Windows 11 64-bit)",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "platform": "Win32",
        "vendor": "Google Inc. (NVIDIA)"
    },
    {
        "name": "Lenovo ThinkPad X1 Carbon (Ubuntu Linux)",
        "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "platform": "Linux x86_64",
        "vendor": "Intel Open Source"
    },
    {
        "name": "ASUS ROG Zephyrus G16 (Windows 11)",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
        "platform": "Win32",
        "vendor": "Google Inc. (AMD)"
    },
    {
        "name": "Apple iMac 24 Retina (macOS Sequoia)",
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        "platform": "MacIntel",
        "vendor": "Apple Computer, Inc."
    }
]

def get_random_pc():
    return random.choice(PC_PROFILES)

def get_sess_file(sid):
    return os.path.join(DATA_DIR, f"{sid}.json")

def save_session(sid, data):
    try:
        with open(get_sess_file(sid), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] Save session: {e}")

def load_session(sid):
    try:
        with open(get_sess_file(sid), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

# ------------------------------------------------------------
# 2. TIẾN TRÌNH PLAYWRIGHT SYNC (CHỐNG DEADLOCK & CHỐNG DETECT)
# ------------------------------------------------------------
STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['vi-VN', 'vi', 'en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""

def worker_zalo_qr(sid, pc_info):
    img_path = os.path.join(DATA_DIR, f"{sid}.png")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--lang=vi-VN"
            ]
        )
        
        # Khung nhìn cố định chống lệch tâm/zoom lệch chỗ
        context = browser.new_context(
            user_agent=pc_info["ua"],
            viewport={"width": 520, "height": 720},
            device_scale_factor=1,
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh"
        )
        
        page = context.new_page()
        page.add_init_script(STEALTH_SCRIPT)

        try:
            print(f"[{sid}] Đang mở chat.zalo.me với PC: {pc_info['name']}")
            page.goto("https://chat.zalo.me/", timeout=45000, wait_until="domcontentloaded")
            time.sleep(3)

            # Chọn tab Mã QR nếu đang ở tab khác
            try:
                qr_btn = page.locator("text=/QR|Quét mã/i").first
                if qr_btn.is_visible(timeout=5000):
                    qr_btn.click()
            except Exception:
                pass

            time.sleep(2)

            # Chụp đúng tâm thẻ mã QR
            qr_bytes = None
            selectors = ["canvas", ".qrcode-img", "img[alt*='QR']", ".login-box", ".content-box"]
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=3000):
                        qr_bytes = el.screenshot()
                        break
                except Exception:
                    continue

            # Fallback nếu không bóc tách được thẻ con
            if not qr_bytes:
                qr_bytes = page.screenshot(full_page=False)

            with open(img_path, "wb") as f:
                f.write(qr_bytes)

            save_session(sid, {
                "status": "waiting",
                "created_at": time.time(),
                "pc_name": pc_info["name"],
                "ua": pc_info["ua"],
                "qr_version": int(time.time()),
                "cookies": None,
                "imei": None
            })
            print(f"[{sid}] QR code đã sẵn sàng!")

            # Lắng nghe trạng thái đăng nhập
            start_poll = time.time()
            while time.time() - start_poll < QR_TTL:
                cookies = context.cookies()
                ck_dict = {c["name"]: c["value"] for c in cookies if "zalo" in c.get("domain", "")}

                # Đã đăng nhập khi có zpw_sek
                if "zpw_sek" in ck_dict:
                    time.sleep(1.5)
                    imei = str(uuid.uuid4())
                    try:
                        local_imei = page.evaluate("localStorage.getItem('zpw_imei') || localStorage.getItem('imei')")
                        if local_imei:
                            imei = local_imei
                    except Exception:
                        pass

                    save_session(sid, {
                        "status": "done",
                        "pc_name": pc_info["name"],
                        "ua": pc_info["ua"],
                        "cookies": ck_dict,
                        "imei": imei,
                        "completed_at": time.time()
                    })
                    print(f"[{sid}] Quét thành công! IMEI: {imei}")
                    break

                time.sleep(1.5)

        except Exception as e:
            print(f"[{sid}] Lỗi tiến trình: {e}")
            save_session(sid, {"status": "error", "error": str(e)})
        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass

# ------------------------------------------------------------
# 3. API ROUTES
# ------------------------------------------------------------
@app.route("/api/qr/create", methods=["POST"])
def api_create():
    sid = uuid.uuid4().hex[:12]
    pc = get_random_pc()
    
    save_session(sid, {"status": "creating", "pc_name": pc["name"]})

    # Chạy worker trong thread nền độc lập
    t = threading.Thread(target=worker_zalo_qr, args=(sid, pc), daemon=True)
    t.start()

    # Chờ tối đa 15s để sinh xong ảnh QR ban đầu
    start = time.time()
    while time.time() - start < 15:
        data = load_session(sid)
        if data and data.get("status") in ["waiting", "error"]:
            break
        time.sleep(0.4)

    return jsonify({"ok": True, "session_id": sid, "pc": pc["name"]})

@app.route("/api/qr/status/<sid>")
def api_status(sid):
    data = load_session(sid)
    if not data:
        return jsonify({"status": "expired"}), 404
    return jsonify(data)

@app.route("/qr-img/<sid>")
def qr_image(sid):
    path = os.path.join(DATA_DIR, f"{sid}.png")
    if not os.path.exists(path):
        return "Image not ready", 404
    with open(path, "rb") as f:
        return Response(
            f.read(),
            mimetype="image/png",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0"
            }
        )

@app.route("/")
def home():
    return render_template_string(HTML_CODE)

# ------------------------------------------------------------
# 4. GIAO DIỆN NOEL LIQUID GLASS (CHỐNG ZOOM MOBILE & VECTOR SANTA)
# ------------------------------------------------------------
HTML_CODE = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<!-- Chống triệt để zoom màn hình/lệch layout trên mobile -->
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<title>Zalo QR — Noel Liquid Glass</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
:root {
    --glass-bg: rgba(255, 255, 255, 0.12);
    --glass-border: rgba(255, 255, 255, 0.28);
    --glass-glow: rgba(255, 255, 255, 0.45);
    --gold: #ffd166;
    --red: #ff3366;
    --green: #06d6a0;
}
* {
    margin: 0; padding: 0;
    box-sizing: border-box;
    -webkit-tap-highlight-color: transparent;
}
html, body {
    width: 100%;
    min-height: 100vh;
    min-height: 100dvh;
    touch-action: manipulation;
    -webkit-text-size-adjust: 100%;
    overflow-x: hidden;
}
body {
    font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
    display: flex;
    align-items: center;
    justify-content: center;
    background: radial-gradient(circle at 50% 15%, #1d2671, #c33764, #111e38);
    background-size: 250% 250%;
    animation: bgAurora 18s ease infinite;
    padding: 16px;
    color: #fff;
}
@keyframes bgAurora {
    0% { background-position: 0% 40%; }
    50% { background-position: 100% 60%; }
    100% { background-position: 0% 40%; }
}
#snow-canvas {
    position: fixed;
    top: 0; left: 0;
    width: 100vw; height: 100vh;
    pointer-events: none;
    z-index: 1;
}
/* Dây đèn noel lấp lánh */
.lights-bar {
    position: fixed;
    top: 0; left: 0; width: 100%;
    display: flex; justify-content: space-evenly;
    padding: 4px 0; z-index: 10;
    pointer-events: none;
}
.bulb {
    width: 10px; height: 10px;
    border-radius: 50%;
    animation: bulbPulse 1.4s infinite alternate;
}
.bulb:nth-child(4n+1) { background: #ff4757; box-shadow: 0 0 10px #ff4757; }
.bulb:nth-child(4n+2) { background: #2ed573; box-shadow: 0 0 10px #2ed573; animation-delay: 0.3s; }
.bulb:nth-child(4n+3) { background: #ffa502; box-shadow: 0 0 10px #ffa502; animation-delay: 0.7s; }
.bulb:nth-child(4n+4) { background: #70a1ff; box-shadow: 0 0 10px #70a1ff; animation-delay: 1.0s; }
@keyframes bulbPulse {
    from { opacity: 0.4; transform: scale(0.9); }
    to { opacity: 1; transform: scale(1.2); }
}

/* Thẻ Kính Lỏng (Liquid Glassmorphism) */
.liquid-card {
    position: relative;
    z-index: 5;
    background: var(--glass-bg);
    backdrop-filter: blur(28px) saturate(210%);
    -webkit-backdrop-filter: blur(28px) saturate(210%);
    border: 1.5px solid var(--glass-border);
    border-radius: 28px;
    padding: 34px 22px 24px;
    width: 100%;
    max-width: 440px;
    box-shadow: 0 25px 60px rgba(0,0,0,0.4), inset 0 1px 1px var(--glass-glow);
    text-align: center;
}

/* Artwork Ông Già Noel Vector */
.santa-wrapper {
    position: relative;
    width: 90px; height: 90px;
    margin: -70px auto 10px;
    background: radial-gradient(circle, rgba(255,255,255,0.25), rgba(255,255,255,0.05));
    backdrop-filter: blur(12px);
    border-radius: 50%;
    border: 2px solid rgba(255,255,255,0.4);
    box-shadow: 0 10px 25px rgba(0,0,0,0.3);
    display: flex;
    align-items: center;
    justify-content: center;
    animation: floatSanta 3.5s ease-in-out infinite;
}
@keyframes floatSanta {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-7px); }
}
.santa-svg {
    width: 72px; height: 72px;
}

h1 {
    font-size: 20px;
    font-weight: 800;
    letter-spacing: -0.3px;
    text-shadow: 0 2px 10px rgba(0,0,0,0.3);
}
.tag-pc {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(0, 0, 0, 0.3);
    border: 1px solid rgba(255, 255, 255, 0.18);
    padding: 6px 14px;
    border-radius: 12px;
    font-size: 11px;
    color: #f1f5f9;
    margin: 10px 0 18px;
}
.tag-pc strong { color: var(--gold); }

/* Khung chứa QR chống méo / chống lệch góc */
.qr-container {
    background: #ffffff;
    width: 250px;
    height: 250px;
    margin: 0 auto 14px;
    padding: 10px;
    border-radius: 20px;
    box-shadow: 0 15px 35px rgba(0,0,0,0.35);
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    position: relative;
}
.qr-container img {
    width: 100%;
    height: 100%;
    object-fit: contain; /* Đảm bảo QR không bao giờ bị cắt cúp phóng to */
    display: block;
    border-radius: 12px;
}
.qr-loader {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    color: #475569;
    font-size: 12px;
    font-weight: 600;
}
.spinner {
    width: 32px; height: 32px;
    border: 3.5px solid #e2e8f0;
    border-top-color: var(--red);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
}
@keyframes spin {
    to { transform: rotate(360deg); }
}

/* Các nút bấm chống zoom */
.btn {
    width: 100%;
    padding: 15px;
    border: none;
    border-radius: 18px;
    font-family: inherit;
    font-size: 16px; /* Đạt chuẩn chống zoom safari mobile */
    font-weight: 700;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    color: #fff;
    transition: all 0.2s ease;
    margin-top: 10px;
    touch-action: manipulation;
}
.btn-change {
    background: linear-gradient(135deg, #ff416c, #ff4b2b);
    box-shadow: 0 8px 25px rgba(255, 65, 108, 0.4);
}
.btn-change:active { transform: scale(0.97); }
.btn-copy {
    background: linear-gradient(135deg, #11998e, #38ef7d);
    box-shadow: 0 8px 25px rgba(56, 239, 125, 0.35);
}

/* Bảng kết quả */
.result-box {
    display: none;
    text-align: left;
    margin-bottom: 12px;
}
.data-card {
    background: rgba(0, 0, 0, 0.38);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 14px;
    padding: 10px 12px;
    margin-top: 8px;
}
.data-card label {
    font-size: 10px;
    font-weight: 800;
    color: var(--gold);
    display: block;
    margin-bottom: 3px;
    letter-spacing: 0.5px;
}
.data-card pre {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: #a7f3d0;
    word-break: break-all;
    white-space: pre-wrap;
    max-height: 80px;
    overflow-y: auto;
}
.toast {
    position: fixed;
    bottom: 24px; left: 50%;
    transform: translateX(-50%);
    background: rgba(15, 23, 42, 0.95);
    border: 1px solid rgba(255,255,255,0.2);
    color: #fff;
    padding: 10px 20px;
    border-radius: 30px;
    font-size: 13px;
    font-weight: 700;
    z-index: 100;
    display: none;
}
</style>
</head>
<body>

<canvas id="snow-canvas"></canvas>

<div class="lights-bar">
    <div class="bulb"></div><div class="bulb"></div><div class="bulb"></div>
    <div class="bulb"></div><div class="bulb"></div><div class="bulb"></div>
    <div class="bulb"></div><div class="bulb"></div>
</div>

<div class="liquid-card">
    <!-- Artwork Ông Già Noel Vector Cao Cấp -->
    <div class="santa-wrapper">
        <svg class="santa-svg" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
            <!-- Mũ Noel -->
            <path d="M22 42C25 22 45 10 74 16C78 28 72 38 70 42H22Z" fill="#E63946"/>
            <path d="M72 16C78 17 84 22 84 28C84 35 77 39 71 36L72 16Z" fill="#D90429"/>
            <circle cx="82" cy="33" r="7" fill="#FFFFFF" filter="drop-shadow(0 2px 4px rgba(0,0,0,0.15))"/>
            <!-- Vành nón tuyết -->
            <rect x="18" y="38" width="56" height="11" rx="5.5" fill="#FFFFFF"/>
            <!-- Khuôn mặt -->
            <path d="M25 46C25 46 26 62 50 62C74 62 75 46 75 46H25Z" fill="#FBD1A2"/>
            <!-- Đôi mắt -->
            <circle cx="40" cy="51" r="2.5" fill="#1D3557"/>
            <circle cx="60" cy="51" r="2.5" fill="#1D3557"/>
            <!-- Má hồng -->
            <circle cx="33" cy="54" r="3.5" fill="#FF8FA3" opacity="0.6"/>
            <circle cx="67" cy="54" r="3.5" fill="#FF8FA3" opacity="0.6"/>
            <!-- Mũi đỏ ngộ nghĩnh -->
            <ellipse cx="50" cy="54" rx="4.5" ry="3.5" fill="#E63946"/>
            <!-- Bộ râu tuyết Noel -->
            <path d="M20 50C20 68 30 88 50 88C70 88 80 68 80 50C73 57 65 58 50 58C35 58 27 57 20 50Z" fill="#F8F9FA"/>
            <!-- Ria mép bồng bềnh -->
            <path d="M50 56C42 52 32 57 32 63C42 63 48 58 50 56Z" fill="#FFFFFF"/>
            <path d="M50 56C58 52 68 57 68 63C58 63 52 58 50 56Z" fill="#FFFFFF"/>
        </svg>
    </div>

    <h1>ZALO QR — NOEL EDITION</h1>
    
    <div class="tag-pc">
        <span>💻 PC:</span> <strong id="pc-name">Đang cấp phát máy tính...</strong>
    </div>

    <!-- Khu vực hiển thị mã QR -->
    <div id="qr-section">
        <div class="qr-container">
            <div id="qr-loading" class="qr-loader">
                <div class="spinner"></div>
                <span>Đang kết nối Zalo...</span>
            </div>
            <img id="qr-img" src="" alt="Mã QR" style="display:none;">
        </div>
        <p style="font-size: 12px; opacity: 0.85;">Dùng app Zalo quét mã & chọn <b>Đồng ý</b></p>
    </div>

    <!-- Khu vực kết quả sau khi đăng nhập thành công -->
    <div id="result-section" class="result-box">
        <div style="text-align:center; margin-bottom:8px;">
            <span style="font-size:32px;">🎁</span>
            <h3 style="color:#6ee7b7; font-size:15px; margin-top:2px;">Đăng Nhập Thành Công!</h3>
            <p style="font-size:11px; opacity:0.75;">Đã bảo vệ phiên chống die cookie và imei</p>
        </div>

        <div class="data-card">
            <label>IMEI Định Danh:</label>
            <pre id="res-imei"></pre>
        </div>
        <div class="data-card">
            <label>Cookie JSON:</label>
            <pre id="res-cookie"></pre>
        </div>

        <button class="btn btn-copy" onclick="copyFullCombo()">📋 COPY COMBO (IMEI + COOKIE)</button>
    </div>

    <!-- Nút đổi máy tính & cấp QR mới -->
    <button id="btn-refresh" class="btn btn-change" onclick="startNewSession()">
        🔄 ĐỔI MÁY TÍNH & LẤY QR MỚI
    </button>
</div>

<div id="toast" class="toast">Đã copy vào bộ nhớ tạm!</div>

<script>
let currentSid = null;
let pollTimer = null;
let fullData = null;

async function startNewSession() {
    clearInterval(pollTimer);
    document.getElementById('result-section').style.display = 'none';
    document.getElementById('qr-section').style.display = 'block';
    document.getElementById('qr-img').style.display = 'none';
    document.getElementById('qr-loading').style.display = 'flex';
    document.getElementById('pc-name').innerText = "Đang khởi tạo máy mới...";
    document.getElementById('btn-refresh').disabled = true;

    try {
        const res = await fetch('/api/qr/create', { method: 'POST' });
        const d = await res.json();
        
        if (d.ok) {
            currentSid = d.session_id;
            document.getElementById('pc-name').innerText = d.pc;
            
            // Tải ảnh QR
            const img = document.getElementById('qr-img');
            img.src = '/qr-img/' + currentSid + '?t=' + Date.now();
            img.onload = () => {
                document.getElementById('qr-loading').style.display = 'none';
                img.style.display = 'block';
            };

            pollTimer = setInterval(checkStatus, 2000);
        } else {
            alert("Lỗi cấp phát QR, thử lại!");
        }
    } catch (e) {
        alert("Lỗi kết nối máy chủ: " + e.message);
    } finally {
        document.getElementById('btn-refresh').disabled = false;
    }
}

async function checkStatus() {
    if (!currentSid) return;
    try {
        const res = await fetch('/api/qr/status/' + currentSid);
        const d = await res.json();

        if (d.status === 'done') {
            clearInterval(pollTimer);
            fullData = d;
            document.getElementById('qr-section').style.display = 'none';
            document.getElementById('result-section').style.display = 'block';
            document.getElementById('res-imei').innerText = d.imei;
            document.getElementById('res-cookie').innerText = JSON.stringify(d.cookies);
            showToast("✨ Đăng nhập thành công!");
        }
    } catch (e) {}
}

function copyFullCombo() {
    if (!fullData) return;
    const payload = {
        imei: fullData.imei,
        cookies: fullData.cookies,
        user_agent: fullData.ua,
        device: fullData.pc_name
    };
    navigator.clipboard.writeText(JSON.stringify(payload, null, 2)).then(() => {
        showToast("✅ Đã sao chép cấu hình & Cookie!");
    });
}

function showToast(msg) {
    const t = document.getElementById('toast');
    t.innerText = msg;
    t.style.display = 'block';
    setTimeout(() => { t.style.display = 'none'; }, 2200);
}

// HIỆU ỨNG TUYẾT RƠI (CANVAS SIÊU NHẸ)
const canvas = document.getElementById('snow-canvas');
const ctx = canvas.getContext('2d');
let flakes = [];

function resizeCanvas() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
}
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

for (let i = 0; i < 65; i++) {
    flakes.push({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        r: Math.random() * 2.5 + 1,
        d: Math.random() * 1.2 + 0.5
    });
}

function drawSnow() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "rgba(255, 255, 255, 0.85)";
    ctx.beginPath();
    for (let f of flakes) {
        ctx.moveTo(f.x, f.y);
        ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2, true);
    }
    ctx.fill();

    for (let f of flakes) {
        f.y += f.d;
        f.x += Math.sin(f.y * 0.02) * 0.5;
        if (f.y > canvas.height) {
            f.y = -5;
            f.x = Math.random() * canvas.width;
        }
    }
    requestAnimationFrame(drawSnow);
}
drawSnow();

// Tự động kích hoạt phiên đầu tiên
startNewSession();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"🎄 [Noel Zalo QR Server] Sẵn sàng tại cổng {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
