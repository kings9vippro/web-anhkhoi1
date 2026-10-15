# ============================================================
# WEB-QR ZALO — NOEL LIQUID GLASS EDITION
# Multi-Device Fingerprint | Anti-Die Cookie/IMEI | Glassmorphism
# ============================================================
import os
import json
import time
import uuid
import random
import asyncio
import threading
from flask import Flask, jsonify, render_template_string, Response

app = Flask(__name__)
DATA_DIR = "/tmp/zalo_noel_sessions"
os.makedirs(DATA_DIR, exist_ok=True)
QR_TTL = 300  # 5 phút

# ------------------------------------------------------------
# 1. KHO THIẾT BỊ MÁY TÍNH (FINGERPRINTS)
# ------------------------------------------------------------
PC_DEVICES = [
    {
        "name": "Apple MacBook Pro M3 (macOS Sonoma)",
        "ua": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "viewport": {"width": 1440, "height": 900},
        "platform": "MacIntel",
        "cores": 8, "ram": 16,
        "webgl_vendor": "Apple Inc.",
        "webgl_renderer": "Apple M3 Pro"
    },
    {
        "name": "Dell XPS 15 (Windows 11 64-bit)",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "viewport": {"width": 1920, "height": 1080},
        "platform": "Win32",
        "cores": 16, "ram": 32,
        "webgl_vendor": "Google Inc. (NVIDIA)",
        "webgl_renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Laptop GPU Direct3D11 vs_5_0 ps_5_0)"
    },
    {
        "name": "ASUS ROG Zephyrus (Windows 11)",
        "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/127.0.0.0",
        "viewport": {"width": 1536, "height": 864},
        "platform": "Win32",
        "cores": 12, "ram": 16,
        "webgl_vendor": "Google Inc. (AMD)",
        "webgl_renderer": "ANGLE (AMD, AMD Radeon Graphics Direct3D11 vs_5_0 ps_5_0)"
    },
    {
        "name": "Lenovo ThinkPad X1 Carbon (Ubuntu Linux)",
        "ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "viewport": {"width": 1920, "height": 1200},
        "platform": "Linux x86_64",
        "cores": 8, "ram": 16,
        "webgl_vendor": "Intel Open Source Technology Center",
        "webgl_renderer": "Mesa Intel Xe Graphics (TGL GT2)"
    }
]

def get_random_pc():
    return random.choice(PC_DEVICES)

def gen_zalo_imei():
    return str(uuid.uuid4())

# ------------------------------------------------------------
# 2. QUẢN LÝ SESSION
# ------------------------------------------------------------
ACTIVE_CONTEXTS = {}

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
# 3. CORE PLAYWRIGHT TỐI ƯU & CHỐNG DETECT
# ------------------------------------------------------------
SHARED_BROWSER = None
PLAYWRIGHT_INSTANCE = None
BROWSER_LOCK = asyncio.Lock()

async def get_browser():
    global SHARED_BROWSER, PLAYWRIGHT_INSTANCE
    from playwright.async_api import async_playwright
    if not SHARED_BROWSER:
        PLAYWRIGHT_INSTANCE = await async_playwright().start()
        SHARED_BROWSER = await PLAYWRIGHT_INSTANCE.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--lang=vi-VN"
            ]
        )
    return SHARED_BROWSER

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""

async def run_zalo_session(sid, pc_info):
    browser = await get_browser()
    
    # Tạo ngữ cảnh máy tính riêng biệt
    context = await browser.new_context(
        user_agent=pc_info["ua"],
        viewport=pc_info["viewport"],
        locale="vi-VN",
        timezone_id="Asia/Ho_Chi_Minh"
    )
    
    page = await context.new_page()
    await page.add_init_script(STEALTH_SCRIPT)
    
    ACTIVE_CONTEXTS[sid] = {"context": context, "page": page, "pc": pc_info}

    try:
        await page.goto("https://chat.zalo.me/", timeout=45000, wait_until="domcontentloaded")
        await asyncio.sleep(4)

        # Chuyển tab sang quét mã QR
        try:
            tab_qr = await page.wait_for_selector("text=/QR|Quét mã/i", timeout=12000)
            if tab_qr:
                await tab_qr.click()
        except Exception:
            pass

        await asyncio.sleep(3)

        # Định vị phần tử mã QR để chụp chuẩn
        img_bytes = None
        try:
            qr_elem = await page.wait_for_selector(".qrcode-img, canvas, img[alt*='QR']", timeout=8000)
            if qr_elem:
                img_bytes = await qr_elem.screenshot()
        except Exception:
            pass

        # Dự phòng: Nếu không lấy được thẻ QR riêng, chụp vùng màn hình
        if not img_bytes:
            img_bytes = await page.screenshot(full_page=False)

        img_path = os.path.join(DATA_DIR, f"{sid}.png")
        with open(img_path, "wb") as f:
            f.write(img_bytes)

        save_session(sid, {
            "status": "waiting",
            "created_at": time.time(),
            "pc_name": pc_info["name"],
            "ua": pc_info["ua"],
            "qr_version": int(time.time()),
            "cookies": None,
            "imei": None
        })

        # Vòng lặp lắng nghe kết quả quét
        start_time = time.time()
        while time.time() - start_time < QR_TTL:
            cookies = await context.cookies()
            ck_dict = {c["name"]: c["value"] for c in cookies if "zalo" in c.get("domain", "")}

            # Khi phát hiện cookie phiên zpw_sek
            if "zpw_sek" in ck_dict:
                await asyncio.sleep(2)
                imei = gen_zalo_imei()
                try:
                    local_imei = await page.evaluate("localStorage.getItem('zpw_imei') || localStorage.getItem('imei')")
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
                break

            await asyncio.sleep(2)

    except Exception as e:
        save_session(sid, {"status": "error", "error": str(e)})
    finally:
        # Đóng ngữ cảnh máy tính sau khi quét xong hoặc timeout để giải phóng RAM
        await context.close()
        ACTIVE_CONTEXTS.pop(sid, None)

# ------------------------------------------------------------
# 4. API ENDPOINTS
# ------------------------------------------------------------
@app.route("/api/qr/create", methods=["POST"])
def api_create():
    sid = uuid.uuid4().hex[:12]
    pc = get_random_pc()
    
    save_session(sid, {"status": "creating", "pc_name": pc["name"]})

    def worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_zalo_session(sid, pc))

    threading.Thread(target=worker, daemon=True).start()

    # Đợi QR sẵn sàng (tối đa 25s)
    start = time.time()
    while time.time() - start < 25:
        data = load_session(sid)
        if data and data.get("status") in ["waiting", "error"]:
            break
        time.sleep(0.5)

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
        return "Image not found", 404
    with open(path, "rb") as f:
        return Response(f.read(), mimetype="image/png", headers={"Cache-Control": "no-cache"})

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

# ------------------------------------------------------------
# 5. GIAO DIỆN KÍNH LỎNG NOEL (LIQUID GLASS + SNOW EFFECTS)
# ------------------------------------------------------------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Zalo QR Login — Merry Christmas</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
:root {
    --glass-bg: rgba(255, 255, 255, 0.12);
    --glass-border: rgba(255, 255, 255, 0.28);
    --glass-shadow: 0 30px 60px rgba(0, 0, 0, 0.35);
    --gold: #f6d365;
    --red: #ff4b4b;
    --green: #2ecc71;
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
    font-family: 'Plus Jakarta Sans', sans-serif;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    background: radial-gradient(circle at 50% 20%, #1a2a6c, #b21f1f, #fdbb2d);
    background-size: 300% 300%;
    animation: aurora 16s ease infinite;
    padding: 16px;
    overflow-x: hidden;
    color: #fff;
}
@keyframes aurora {
    0% { background-position: 0% 50%; }
    50% { background-position: 100% 50%; }
    100% { background-position: 0% 50%; }
}
#snow-canvas {
    position: fixed;
    top: 0; left: 0;
    width: 100%; height: 100%;
    pointer-events: none;
    z-index: 1;
}
.lights-bar {
    position: fixed;
    top: 0; left: 0; width: 100%;
    display: flex; justify-content: space-around;
    padding: 6px 0; z-index: 10;
    pointer-events: none;
}
.bulb {
    width: 12px; height: 12px;
    border-radius: 50%;
    animation: glow 1.2s infinite alternate;
}
.bulb:nth-child(4n+1) { background: #ff4757; box-shadow: 0 0 14px #ff4757; }
.bulb:nth-child(4n+2) { background: #2ed573; box-shadow: 0 0 14px #2ed573; animation-delay: 0.3s; }
.bulb:nth-child(4n+3) { background: #ffa502; box-shadow: 0 0 14px #ffa502; animation-delay: 0.6s; }
.bulb:nth-child(4n+4) { background: #1e90ff; box-shadow: 0 0 14px #1e90ff; animation-delay: 0.9s; }
@keyframes glow {
    from { opacity: 0.3; transform: scale(0.85); }
    to { opacity: 1; transform: scale(1.15); }
}
.card {
    position: relative;
    z-index: 5;
    background: var(--glass-bg);
    backdrop-filter: blur(28px) saturate(190%);
    -webkit-backdrop-filter: blur(28px) saturate(190%);
    border: 1.5px solid var(--glass-border);
    border-radius: 28px;
    padding: 32px 24px;
    width: 100%;
    max-width: 480px;
    box-shadow: var(--glass-shadow);
    text-align: center;
}
.santa-badge {
    position: absolute;
    top: -30px;
    left: 50%;
    transform: translateX(-50%);
    background: rgba(255, 255, 255, 0.2);
    backdrop-filter: blur(10px);
    border: 1px solid rgba(255, 255, 255, 0.3);
    padding: 8px 16px;
    border-radius: 40px;
    font-size: 24px;
    box-shadow: 0 8px 20px rgba(0,0,0,0.2);
}
h1 {
    font-size: 22px;
    font-weight: 800;
    margin-top: 14px;
    letter-spacing: -0.5px;
    text-shadow: 0 2px 10px rgba(0,0,0,0.3);
}
.pc-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(0, 0, 0, 0.25);
    border: 1px solid rgba(255,255,255,0.15);
    padding: 6px 12px;
    border-radius: 12px;
    font-size: 11px;
    color: #e2e8f0;
    margin: 12px 0 20px 0;
}
.qr-frame {
    background: rgba(255, 255, 255, 0.95);
    padding: 12px;
    border-radius: 20px;
    box-shadow: 0 15px 35px rgba(0,0,0,0.3);
    display: inline-block;
    margin-bottom: 16px;
    position: relative;
    overflow: hidden;
}
.qr-frame img {
    display: block;
    max-width: 250px;
    width: 100%;
    height: auto;
    border-radius: 12px;
}
.btn {
    width: 100%;
    padding: 14px;
    border: none;
    border-radius: 16px;
    font-family: inherit;
    font-weight: 700;
    font-size: 14px;
    cursor: pointer;
    transition: all 0.25s ease;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    color: #fff;
    margin-top: 10px;
    text-shadow: 0 1px 2px rgba(0,0,0,0.2);
}
.btn-new-pc {
    background: linear-gradient(135deg, #f093fb, #f5576c);
    box-shadow: 0 8px 25px rgba(245, 87, 108, 0.4);
}
.btn-new-pc:hover { transform: translateY(-2px); opacity: 0.95; }
.btn-copy {
    background: linear-gradient(135deg, #11998e, #38ef7d);
    box-shadow: 0 8px 25px rgba(56, 239, 125, 0.35);
}
.data-box {
    background: rgba(0, 0, 0, 0.35);
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 16px;
    padding: 12px;
    margin-top: 12px;
    text-align: left;
}
.data-box label {
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    color: var(--gold);
    display: block;
    margin-bottom: 4px;
}
.data-box pre {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    color: #a7f3d0;
    word-break: break-all;
    white-space: pre-wrap;
    max-height: 90px;
    overflow-y: auto;
}
.toast {
    position: fixed;
    bottom: 24px; left: 50%;
    transform: translateX(-50%);
    background: rgba(0,0,0,0.85);
    backdrop-filter: blur(10px);
    border: 1px solid rgba(255,255,255,0.2);
    color: #fff;
    padding: 10px 22px;
    border-radius: 30px;
    font-size: 13px;
    font-weight: 600;
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
    <div class="bulb"></div><div class="bulb"></div><div class="bulb"></div>
</div>

<div class="card">
    <div class="santa-badge">🎅🎄</div>
    <h1>ZALO QR — NOEL EDITION</h1>
    
    <div id="pc-tag" class="pc-badge">
        <span>💻 Thiết bị:</span> <strong id="pc-name">Đang phân bổ máy tính...</strong>
    </div>

    <!-- KHU VỰC QR -->
    <div id="qr-box">
        <div class="qr-frame">
            <img id="qr-image" src="" alt="Đang tạo mã QR...">
        </div>
        <p style="font-size: 12px; opacity: 0.85;">Quét bằng app Zalo & nhấn <b>Đồng ý</b> trên điện thoại</p>
    </div>

    <!-- KHU VỰC KẾT QUẢ KHI QUÉT XONG -->
    <div id="result-box" style="display:none;">
        <div style="font-size: 40px; margin-bottom: 6px;">🎁</div>
        <h3 style="color: #a7f3d0; font-size: 16px;">Đăng Nhập Thành Công!</h3>
        <p style="font-size: 11px; opacity: 0.7;">Đã lưu cấu hình máy tính & bảo vệ IMEI/Cookie</p>
        
        <div class="data-box">
            <label>IMEI Định Danh:</label>
            <pre id="res-imei"></pre>
        </div>
        <div class="data-box">
            <label>Cookie JSON:</label>
            <pre id="res-cookie"></pre>
        </div>

        <button class="btn btn-copy" onclick="copyFullCombo()">📋 COPY COMBO (IMEI + COOKIE)</button>
    </div>

    <button id="btn-reload" class="btn btn-new-pc" onclick="startNewSession()">
        🔄 ĐỔI MÁY TÍNH & TẠO QR MỚI
    </button>
</div>

<div id="toast" class="toast">Đã copy vào bộ nhớ tạm!</div>

<script>
let currentSid = null;
let pollTimer = null;
let fullResult = null;

async function startNewSession() {
    clearInterval(pollTimer);
    document.getElementById('result-box').style.display = 'none';
    document.getElementById('qr-box').style.display = 'block';
    document.getElementById('pc-name').innerText = "Đang khởi tạo máy tính mới...";
    document.getElementById('qr-image').src = "";
    document.getElementById('btn-reload').disabled = true;

    try {
        const res = await fetch('/api/qr/create', { method: 'POST' });
        const data = await res.json();
        
        if (data.ok) {
            currentSid = data.session_id;
            document.getElementById('pc-name').innerText = data.pc;
            document.getElementById('qr-image').src = '/qr-img/' + currentSid + '?t=' + Date.now();
            pollTimer = setInterval(checkStatus, 2000);
        } else {
            alert("Lỗi tạo QR!");
        }
    } catch (e) {
        alert("Lỗi kết nối: " + e.message);
    } finally {
        document.getElementById('btn-reload').disabled = false;
    }
}

async function checkStatus() {
    if (!currentSid) return;
    try {
        const res = await fetch('/api/qr/status/' + currentSid);
        const d = await res.json();

        if (d.status === 'done') {
            clearInterval(pollTimer);
            fullResult = d;
            document.getElementById('qr-box').style.display = 'none';
            document.getElementById('result-box').style.display = 'block';
            document.getElementById('res-imei').innerText = d.imei;
            document.getElementById('res-cookie').innerText = JSON.stringify(d.cookies);
            showToast("✨ Đăng nhập thành công! Thiết bị đã sẵn sàng.");
        }
    } catch (e) {}
}

function copyFullCombo() {
    if (!fullResult) return;
    const exportData = {
        imei: fullResult.imei,
        cookies: fullResult.cookies,
        user_agent: fullResult.ua,
        pc: fullResult.pc_name
    };
    navigator.clipboard.writeText(JSON.stringify(exportData, null, 2)).then(() => {
        showToast("✅ Đã copy trọn bộ dữ liệu!");
    });
}

function showToast(msg) {
    const t = document.getElementById('toast');
    t.innerText = msg;
    t.style.display = 'block';
    setTimeout(() => { t.style.display = 'none'; }, 2200);
}

// HIỆU ỨNG TUYẾT RƠI
const canvas = document.getElementById('snow-canvas');
const ctx = canvas.getContext('2d');
let flakes = [];

function resizeCanvas() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
}
window.addEventListener('resize', resizeCanvas);
resizeCanvas();

for (let i = 0; i < 70; i++) {
    flakes.push({
        x: random(0, canvas.width),
        y: random(0, canvas.height),
        r: random(1.5, 4),
        d: random(0.5, 1.8)
    });
}

function random(min, max) { return Math.random() * (max - min) + min; }

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
        f.x += Math.sin(f.y * 0.01);
        if (f.y > canvas.height) { f.y = -10; f.x = random(0, canvas.width); }
    }
    requestAnimationFrame(drawSnow);
}
drawSnow();

// Tự động chạy khi load
startNewSession();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"🎄 Noel Zalo QR Server running on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
