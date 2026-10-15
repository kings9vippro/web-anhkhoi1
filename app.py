# ============================================================
# ZALO QR NOEL ULTIMATE EDITION (LIQUID GLASS & ULTRA-FAST)
# Fix: OOM Render, Race Condition 404, Auto-Zoom iOS, Multi-PC
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
DATA_DIR = "/tmp/zalo_qr_noel"
os.makedirs(DATA_DIR, exist_ok=True)
QR_TTL = 300  # Phiên sống trong 5 phút

# ------------------------------------------------------------
# 1. KHO DANH TÍNH MÁY TÍNH THỰC TẾ (MULTI-PC ENGINE)
# ------------------------------------------------------------
PC_PROFILES = [
    {
        "name": "Apple MacBook Pro 16 M3 Max (macOS Sonoma)",
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 800},
        "platform": "MacIntel"
    },
    {
        "name": "Dell XPS 15 9530 (Windows 11 64-bit)",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 720},
        "platform": "Win32"
    },
    {
        "name": "Lenovo ThinkPad X1 Carbon Gen 11 (Ubuntu Linux)",
        "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "viewport": {"width": 1280, "height": 800},
        "platform": "Linux x86_64"
    },
    {
        "name": "ASUS ROG Zephyrus G16 (Windows 11 Home)",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
        "viewport": {"width": 1366, "height": 768},
        "platform": "Win32"
    },
    {
        "name": "Apple iMac 24 4.5K Retina (macOS Sequoia)",
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
        "viewport": {"width": 1280, "height": 720},
        "platform": "MacIntel"
    }
]

def get_next_pc():
    return random.choice(PC_PROFILES)

def get_session_file(sid):
    return os.path.join(DATA_DIR, f"{sid}.json")

def save_sess(sid, data):
    try:
        with open(get_session_file(sid), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] Save session: {e}")

def load_sess(sid):
    try:
        with open(get_session_file(sid), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

# ------------------------------------------------------------
# 2. WORKER PLAYWRIGHT TỐI ƯU CỰC HẠN CHO RENDER (CHỐNG OOM)
# ------------------------------------------------------------
STEALTH_INJECT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['vi-VN', 'vi', 'en-US', 'en'] });
"""

def worker_generate_qr(sid, pc):
    img_path = os.path.join(DATA_DIR, f"{sid}.png")
    
    # Khởi chạy Playwright với cấu hình ép xung bộ nhớ cực thấp
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-zygote",
                    "--single-process",  # Sống còn cho Render 512MB RAM
                    "--disable-blink-features=AutomationControlled",
                    "--disable-extensions",
                    "--disable-component-update"
                ]
            )

            context = browser.new_context(
                user_agent=pc["ua"],
                viewport=pc["viewport"],
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh"
            )

            page = context.new_page()
            page.add_init_script(STEALTH_INJECT)

            # Chặn tải tài nguyên rác (ảnh nền, video) để tải nhanh gấp 3 lần
            def intercept_routes(route):
                req = route.request
                if req.resource_type in ["media", "font"] and "qr" not in req.url.lower():
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", intercept_routes)

            print(f"[{sid}] Đang mở chat.zalo.me với máy tính: {pc['name']}")
            page.goto("https://chat.zalo.me/", timeout=40000, wait_until="domcontentloaded")
            
            # Đợi và bấm vào tab QR
            try:
                qr_tab = page.locator("text=/QR|Quét mã/i").first
                if qr_tab.is_visible(timeout=6000):
                    qr_tab.click()
            except Exception:
                pass

            # Chờ QR render (Canvas hoặc ảnh)
            time.sleep(2.5)
            qr_bytes = None
            selectors = ["canvas", ".qrcode-img", "img[alt*='QR']", ".login-box"]
            for sel in selectors:
                try:
                    el = page.locator(sel).first
                    if el.is_visible(timeout=3000):
                        qr_bytes = el.screenshot()
                        break
                except Exception:
                    continue

            if not qr_bytes:
                qr_bytes = page.screenshot(full_page=False)

            # Ghi ảnh ra ổ đĩa
            with open(img_path, "wb") as f:
                f.write(qr_bytes)

            # Cập nhật trạng thái 'waiting' -> Frontend nhận biết đã có ảnh
            save_sess(sid, {
                "status": "waiting",
                "created_at": time.time(),
                "pc_name": pc["name"],
                "ua": pc["ua"],
                "has_img": True,
                "cookies": None,
                "imei": None
            })
            print(f"[{sid}] QR Code đã tạo thành công!")

            # Lắng nghe trạng thái đăng nhập
            poll_start = time.time()
            while time.time() - poll_start < QR_TTL:
                cookies = context.cookies()
                ck_dict = {c["name"]: c["value"] for c in cookies if "zalo" in c.get("domain", "")}

                if "zpw_sek" in ck_dict:
                    time.sleep(1.5)
                    imei = str(uuid.uuid4())
                    try:
                        local_imei = page.evaluate("localStorage.getItem('zpw_imei') || localStorage.getItem('imei')")
                        if local_imei:
                            imei = local_imei
                    except Exception:
                        pass

                    save_sess(sid, {
                        "status": "done",
                        "pc_name": pc["name"],
                        "ua": pc["ua"],
                        "cookies": ck_dict,
                        "imei": imei,
                        "has_img": True
                    })
                    print(f"[{sid}] Đăng nhập thành công! IMEI: {imei}")
                    break

                time.sleep(1.5)

        except Exception as e:
            print(f"[{sid}] Lỗi tiến trình Playwright: {e}")
            save_sess(sid, {"status": "error", "error": str(e), "has_img": False})
        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass

# ------------------------------------------------------------
# 3. API ENDPOINTS (EVENT-DRIVEN CHO FRONTEND)
# ------------------------------------------------------------
@app.route("/api/qr/create", methods=["POST"])
def api_create():
    sid = uuid.uuid4().hex[:12]
    pc = get_next_pc()
    
    save_sess(sid, {
        "status": "initializing",
        "pc_name": pc["name"],
        "has_img": False
    })

    t = threading.Thread(target=worker_generate_qr, args=(sid, pc), daemon=True)
    t.start()

    return jsonify({"ok": True, "session_id": sid, "pc": pc["name"]})

@app.route("/api/qr/status/<sid>")
def api_status(sid):
    data = load_sess(sid)
    if not data:
        return jsonify({"status": "expired"}), 404
    return jsonify(data)

@app.route("/qr-img/<sid>")
def get_qr_image(sid):
    path = os.path.join(DATA_DIR, f"{sid}.png")
    if not os.path.exists(path):
        return "Not Ready", 404
    with open(path, "rb") as f:
        return Response(
            f.read(),
            mimetype="image/png",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
        )

@app.route("/")
def home():
    return render_template_string(HTML_PAGE)

# ------------------------------------------------------------
# 4. GIAO DIỆN NOEL LIQUID GLASS NGHỆ THUẬT CAO CẤP
# ------------------------------------------------------------
HTML_PAGE = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<title>Zalo QR — Noel Liquid Glass</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800;900&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
:root {
    --glass-bg: rgba(255, 255, 255, 0.15);
    --glass-border: rgba(255, 255, 255, 0.35);
    --gold: #f6d365;
    --ice: #e0f2fe;
}
* { margin:0; padding:0; box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
html, body {
    width: 100%; min-height: 100vh; min-height: 100dvh;
    touch-action: manipulation; -webkit-text-size-adjust: 100%;
}
body {
    font-family: 'Plus Jakarta Sans', sans-serif;
    display: flex; align-items: center; justify-content: center;
    background: radial-gradient(circle at 50% 10%, #1e1b4b, #831843, #0f172a);
    background-size: 200% 200%;
    animation: auroraBG 14s ease infinite;
    padding: 16px; color: #fff; overflow-x: hidden;
}
@keyframes auroraBG {
    0% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
    100% { background-position: 0% 50%; }
}
#snow {
    position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
    pointer-events: none; z-index: 1;
}
/* Dàn đèn Giáng Sinh sắc nét */
.lights {
    position: fixed; top: 0; left: 0; width: 100%;
    display: flex; justify-content: space-around;
    padding: 6px 0; z-index: 10; pointer-events: none;
}
.bulb {
    width: 10px; height: 10px; border-radius: 50%;
    animation: glow 1.2s infinite alternate;
}
.bulb:nth-child(4n+1) { background: #ff4d4d; box-shadow: 0 0 12px #ff4d4d; }
.bulb:nth-child(4n+2) { background: #00e676; box-shadow: 0 0 12px #00e676; animation-delay: 0.3s; }
.bulb:nth-child(4n+3) { background: #ffea00; box-shadow: 0 0 12px #ffea00; animation-delay: 0.6s; }
.bulb:nth-child(4n+4) { background: #00e5ff; box-shadow: 0 0 12px #00e5ff; animation-delay: 0.9s; }
@keyframes glow {
    from { opacity: 0.3; transform: scale(0.85); }
    to { opacity: 1; transform: scale(1.15); }
}

/* Thẻ kính lỏng Liquid Glass */
.liquid-card {
    position: relative; z-index: 5;
    background: var(--glass-bg);
    backdrop-filter: blur(35px) saturate(220%);
    -webkit-backdrop-filter: blur(35px) saturate(220%);
    border: 1.5px solid var(--glass-border);
    border-radius: 32px;
    padding: 38px 22px 24px;
    width: 100%; max-width: 440px;
    box-shadow: 0 30px 70px rgba(0, 0, 0, 0.45), inset 0 1.5px 1.5px rgba(255,255,255,0.6);
    text-align: center;
}

/* Biểu tượng Ông Già Noel Vector Nghệ Thuật */
.santa-badge {
    position: relative; width: 92px; height: 92px;
    margin: -76px auto 12px;
    background: radial-gradient(circle, rgba(255,255,255,0.3), rgba(255,255,255,0.05));
    backdrop-filter: blur(15px);
    border-radius: 50%;
    border: 2px solid rgba(255,255,255,0.5);
    box-shadow: 0 12px 28px rgba(0,0,0,0.35);
    display: flex; align-items: center; justify-content: center;
    animation: santaFloat 4s ease-in-out infinite;
}
@keyframes santaFloat {
    0%, 100% { transform: translateY(0); }
    50% { transform: translateY(-8px); }
}

h1 {
    font-size: 20px; font-weight: 900;
    letter-spacing: -0.3px;
    text-shadow: 0 2px 12px rgba(0,0,0,0.4);
}
.pc-pill {
    display: inline-flex; align-items: center; gap: 6px;
    background: rgba(0, 0, 0, 0.35);
    border: 1px solid rgba(255, 255, 255, 0.2);
    padding: 6px 14px; border-radius: 14px;
    font-size: 11px; color: var(--ice); margin: 10px 0 18px;
}
.pc-pill strong { color: var(--gold); }

/* Khung kính chứa QR — Chống méo & chống zoom */
.qr-box {
    background: #ffffff;
    width: 250px; height: 250px;
    margin: 0 auto 14px; padding: 10px;
    border-radius: 22px;
    box-shadow: 0 18px 40px rgba(0,0,0,0.4);
    display: flex; align-items: center; justify-content: center;
    position: relative; overflow: hidden;
}
.qr-box img {
    width: 100%; height: 100%; object-fit: contain;
    border-radius: 14px; display: block;
}

/* Spinner quay tuyết */
.loader-box {
    display: flex; flex-direction: column;
    align-items: center; gap: 12px;
    color: #475569; font-size: 12px; font-weight: 700;
}
.snow-spinner {
    width: 38px; height: 38px;
    border: 4px solid #f1f5f9;
    border-top: 4px solid #e11d48;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* Nút bấm Kính Thủy Tinh (Chống Zoom Safari) */
.btn {
    width: 100%; padding: 15px; border: none; border-radius: 18px;
    font-family: inherit; font-size: 16px; font-weight: 800;
    cursor: pointer; display: flex; align-items: center;
    justify-content: center; gap: 8px; color: #fff;
    margin-top: 10px; transition: all 0.2s ease;
    touch-action: manipulation;
}
.btn-change {
    background: linear-gradient(135deg, #e11d48, #be123c);
    box-shadow: 0 8px 24px rgba(225, 29, 72, 0.4);
}
.btn-change:active { transform: scale(0.97); }
.btn-copy {
    background: linear-gradient(135deg, #059669, #10b981);
    box-shadow: 0 8px 24px rgba(16, 185, 129, 0.4);
}

.result-card {
    display: none; text-align: left; margin-bottom: 12px;
}
.data-panel {
    background: rgba(0, 0, 0, 0.4);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 14px; padding: 10px 12px; margin-top: 8px;
}
.data-panel label {
    font-size: 10px; font-weight: 800; color: var(--gold);
    display: block; margin-bottom: 4px;
}
.data-panel pre {
    font-family: 'JetBrains Mono', monospace; font-size: 11px;
    color: #6ee7b7; word-break: break-all; white-space: pre-wrap;
    max-height: 85px; overflow-y: auto;
}
.toast {
    position: fixed; bottom: 24px; left: 50%;
    transform: translateX(-50%);
    background: rgba(15, 23, 42, 0.95);
    border: 1px solid rgba(255,255,255,0.25);
    color: #fff; padding: 10px 22px; border-radius: 30px;
    font-size: 13px; font-weight: 800; z-index: 100; display: none;
}
</style>
</head>
<body>

<canvas id="snow"></canvas>

<div class="lights">
    <div class="bulb"></div><div class="bulb"></div><div class="bulb"></div>
    <div class="bulb"></div><div class="bulb"></div><div class="bulb"></div>
    <div class="bulb"></div><div class="bulb"></div>
</div>

<div class="liquid-card">
    <!-- Artwork Santa Claus SVG 3D -->
    <div class="santa-badge">
        <svg width="72" height="72" viewBox="0 0 100 100" fill="none">
            <path d="M22 42C25 20 48 10 74 16C78 28 72 38 70 42H22Z" fill="#E11D48"/>
            <path d="M72 16C78 17 84 22 84 28C84 35 77 39 71 36L72 16Z" fill="#BE123C"/>
            <circle cx="82" cy="33" r="7.5" fill="#FFFFFF"/>
            <rect x="18" y="38" width="56" height="11" rx="5.5" fill="#FFFFFF"/>
            <path d="M25 46C25 46 26 62 50 62C74 62 75 46 75 46H25Z" fill="#FBD1A2"/>
            <circle cx="39" cy="51" r="2.5" fill="#1E293B"/>
            <circle cx="61" cy="51" r="2.5" fill="#1E293B"/>
            <circle cx="33" cy="54" r="3.5" fill="#FB7185" opacity="0.6"/>
            <circle cx="67" cy="54" r="3.5" fill="#FB7185" opacity="0.6"/>
            <ellipse cx="50" cy="54" rx="4.5" ry="3.5" fill="#E11D48"/>
            <path d="M20 50C20 68 30 88 50 88C70 88 80 68 80 50C73 57 65 58 50 58C35 58 27 57 20 50Z" fill="#F8FAFC"/>
            <path d="M50 56C42 52 32 57 32 63C42 63 48 58 50 56Z" fill="#FFFFFF"/>
            <path d="M50 56C58 52 68 57 68 63C58 63 52 58 50 56Z" fill="#FFFFFF"/>
        </svg>
    </div>

    <h1>ZALO QR — NOEL EDITION</h1>
    
    <div class="pc-pill">
        <span>💻 PC:</span> <strong id="pc-name">Đang cấp máy tính mới...</strong>
    </div>

    <!-- KHU VỰC QR (CƠ CHẾ POLLING CHỐNG TREO) -->
    <div id="qr-panel">
        <div class="qr-box">
            <div id="qr-loading" class="loader-box">
                <div class="snow-spinner"></div>
                <span id="loading-text">Đang kết nối Zalo...</span>
            </div>
            <img id="qr-img" src="" alt="Mã QR Zalo" style="display:none;">
        </div>
        <p style="font-size: 12px; opacity: 0.9;">Mở app Zalo quét mã & chọn <b>Đồng ý</b></p>
    </div>

    <!-- KHU VỰC KẾT QUẢ ĐĂNG NHẬP -->
    <div id="result-panel" class="result-card">
        <div style="text-align:center; margin-bottom:8px;">
            <span style="font-size:34px;">🎁</span>
            <h3 style="color:#6ee7b7; font-size:16px;">Đăng Nhập Thành Công!</h3>
            <p style="font-size:11px; opacity:0.8;">Đã bảo vệ IMEI & Cookie an toàn</p>
        </div>

        <div class="data-panel">
            <label>IMEI ĐỊNH DANH (CHỐNG DIE):</label>
            <pre id="val-imei"></pre>
        </div>
        <div class="data-panel">
            <label>COOKIE PHIÊN (ZPW_SEK):</label>
            <pre id="val-cookie"></pre>
        </div>

        <button class="btn btn-copy" onclick="copyResult()">📋 SAO CHÉP TOÀN BỘ THÔNG SỐ</button>
    </div>

    <button id="btn-next" class="btn btn-change" onclick="startNewSession()">
        🔄 ĐỔI MÁY TÍNH & TẠO QR MỚI
    </button>
</div>

<div id="toast" class="toast">Đã sao chép vào bộ nhớ!</div>

<script>
let sid = null;
let pollTimer = null;
let sessionResult = null;
let pollCount = 0;

async function startNewSession() {
    clearInterval(pollTimer);
    pollCount = 0;
    
    document.getElementById('result-panel').style.display = 'none';
    document.getElementById('qr-panel').style.display = 'block';
    document.getElementById('qr-img').style.display = 'none';
    document.getElementById('qr-loading').style.display = 'flex';
    document.getElementById('loading-text').innerText = "Đang khởi tạo máy tính...";
    document.getElementById('btn-next').disabled = true;

    try {
        const res = await fetch('/api/qr/create', { method: 'POST' });
        const data = await res.json();
        
        if (data.ok) {
            sid = data.session_id;
            document.getElementById('pc-name').innerText = data.pc;
            document.getElementById('loading-text').innerText = "Đang sinh mã QR Zalo...";
            
            // BẮT ĐẦU VÒNG LẶP POLLING ĐỢI ẢNH CHÍNH XÁC
            pollTimer = setInterval(pollServer, 1800);
        } else {
            alert("Không thể khởi tạo phiên mới!");
        }
    } catch (e) {
        alert("Lỗi kết nối máy chủ: " + e.message);
    } finally {
        document.getElementById('btn-next').disabled = false;
    }
}

async function pollServer() {
    if (!sid) return;
    pollCount++;
    
    try {
        const res = await fetch('/api/qr/status/' + sid);
        if (!res.ok) return;
        const d = await res.json();

        // 1. KHI ẢNH ĐÃ SẴN SÀNG -> TẢI ẢNH VÀO KHUNG
        if (d.has_img && document.getElementById('qr-img').style.display === 'none') {
            const img = document.getElementById('qr-img');
            img.src = '/qr-img/' + sid + '?t=' + Date.now();
            img.onload = () => {
                document.getElementById('qr-loading').style.display = 'none';
                img.style.display = 'block';
            };
        }

        // 2. KHI ĐĂNG NHẬP THÀNH CÔNG
        if (d.status === 'done') {
            clearInterval(pollTimer);
            sessionResult = d;
            document.getElementById('qr-panel').style.display = 'none';
            document.getElementById('result-panel').style.display = 'block';
            document.getElementById('val-imei').innerText = d.imei;
            document.getElementById('val-cookie').innerText = JSON.stringify(d.cookies);
            showToast("✨ Đăng nhập thành công!");
        }

        // 3. XỬ LÝ LỖI HOẶC TIMEOUT
        if (d.status === 'error') {
            clearInterval(pollTimer);
            document.getElementById('loading-text').innerText = "Lỗi tạo QR, hãy bấm đổi máy!";
        }

        // Sau 45s nếu chưa có ảnh, cảnh báo thử lại
        if (pollCount > 25 && !d.has_img) {
            clearInterval(pollTimer);
            document.getElementById('loading-text').innerText = "Máy chủ Render đang quá tải, vui lòng đổi máy!";
        }

    } catch (e) {}
}

function copyResult() {
    if (!sessionResult) return;
    const exportBundle = {
        imei: sessionResult.imei,
        cookies: sessionResult.cookies,
        user_agent: sessionResult.ua,
        device: sessionResult.pc_name
    };
    navigator.clipboard.writeText(JSON.stringify(exportBundle, null, 2)).then(() => {
        showToast("✅ Đã sao chép IMEI + Cookie!");
    });
}

function showToast(msg) {
    const t = document.getElementById('toast');
    t.innerText = msg;
    t.style.display = 'block';
    setTimeout(() => { t.style.display = 'none'; }, 2200);
}

// HIỆU ỨNG TUYẾT RƠI KHÔNG TỐN PIN MOBILE
const canvas = document.getElementById('snow');
const ctx = canvas.getContext('2d');
let flakes = [];

function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
}
window.addEventListener('resize', resize);
resize();

for (let i = 0; i < 60; i++) {
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

// Tự động khởi chạy ngay khi mở web
startNewSession();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"🎄 Noel Zalo QR Server Running on Port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
