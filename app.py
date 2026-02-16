import os
import json
import hashlib
import threading
import time
import requests
import re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
from cryptography.fernet import Fernet

app = Flask(__name__)
app.secret_key = os.urandom(24)  # Change to a fixed string in production

# ================= ENCRYPTION SETUP =================
ENCRYPTION_KEY_FILE = "encryption.key"

def get_encryption_key():
    if not os.path.exists(ENCRYPTION_KEY_FILE):
        key = Fernet.generate_key()
        with open(ENCRYPTION_KEY_FILE, "wb") 
    as f:
            f.write(key)
        return key
    else:
        with open(ENCRYPTION_KEY_FILE, "rb") as f:
            return f.read()

cipher = Fernet(get_encryption_key())

def encrypt_api_key(plain_key):
    return cipher.encrypt(plain_key.encode()).decode()

def decrypt_api_key(encrypted_key):
    return cipher.decrypt(encrypted_key.encode()).decode()

# ================= CONFIGURATION =================
API_URL = "https://smmgen.com/api/v2"
USERS_FILE = "users.json"
AUTOMATION_INTERVAL = 60  # seconds

# ================= FILE HELPERS =================
def load_users():
    try:
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def get_user_orders_file(username):
    return f"orders_{username}.json"

def get_user_automation_file(username):
    return f"automation_{username}.json"

def load_user_orders(username):
    try:
        with open(get_user_orders_file(username), "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_user_orders(username, orders):
    with open(get_user_orders_file(username), "w") as f:
        json.dump(orders, f, indent=2)

def load_user_automation(username):
    try:
        with open(get_user_automation_file(username), "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_user_automation(username, tasks):
    with open(get_user_automation_file(username), "w") as f:
        json.dump(tasks, f, indent=2)

# ================= CURRENCY & HELPERS =================
def get_live_rate():
    try:
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5).json()
        return r["rates"]["BDT"]
    except:
        return 122.0

# ================= ORIGINAL TIKTOK ANALYSIS =================
def resolve_url(url):
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        response = session.get(url, allow_redirects=True, timeout=10)
        return response.url
    except:
        return url

def extract_video_id(url):
    match = re.search(r'/video/(\d+)', url)
    return match.group(1) if match else None

def get_video_views(link):
    video_id = extract_video_id(resolve_url(link))
    if not video_id:
        return None
    url = f"https://www.tiktok.com/@any/video/{video_id}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        match = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__".*?>(.*?)</script>', response.text)
        if not match:
            return None
        data = json.loads(match.group(1))
        item = data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {}).get("itemInfo", {}).get("itemStruct", {})
        return item.get("stats", {}).get("playCount", 0)
    except:
        return None

# ================= API CALLS WITH USER'S KEY =================
def call_smm_api(api_key, action, **params):
    data = {"key": api_key, "action": action, **params}
    try:
        return requests.post(API_URL, data=data).json()
    except:
        return {"error": "API request failed"}

# ================= AUTHENTICATION ROUTES =================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        users = load_users()
        user = users.get(username)
        if user and user["password"] == hash_password(password):
            session["username"] = username
            return redirect(url_for("home"))
        return render_template_string(LOGIN_PAGE, error="Invalid credentials")
    return render_template_string(LOGIN_PAGE)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        api_key = request.form["api_key"]
        users = load_users()
        if username in users:
            return render_template_string(REGISTER_PAGE, error="Username already exists")
        # Test API key
        test = call_smm_api(api_key, "balance")
        if "error" in test or "balance" not in test:
            return render_template_string(REGISTER_PAGE, error="Invalid API key or API not reachable")
        # Encrypt before storing
        encrypted_key = encrypt_api_key(api_key)
        users[username] = {
            "password": hash_password(password),
            "api_key": encrypted_key,
            "created": datetime.now().isoformat()
        }
        save_users(users)
        return redirect(url_for("login"))
    return render_template_string(REGISTER_PAGE)

@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect(url_for("login"))

# ================= MAIN PANEL =================
@app.route("/")
def home():
    if "username" not in session:
        return redirect(url_for("login"))
    return render_template_string(MAIN_PAGE, username=session["username"])

# ================= API ROUTES (PROTECTED) =================
@app.route("/init-data")
def init_data():
    if "username" not in session:
        return jsonify({"error": "Not logged in"}), 401
    username = session["username"]
    users = load_users()
    encrypted_key = users[username]["api_key"]
    api_key = decrypt_api_key(encrypted_key)
    try:
        balance_r = call_smm_api(api_key, "balance")
        services_r = call_smm_api(api_key, "services")
        return jsonify({
            "balance": balance_r.get("balance", "0.00"),
            "rate": get_live_rate(),
            "services": services_r
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/analyze", methods=["POST"])
def analyze():
    d = request.json
    user_input = d.get("url", "")
    if user_input.isdigit():
        video_id = user_input
    else:
        video_id = extract_video_id(resolve_url(user_input))
    if not video_id:
        return jsonify({"error": "Invalid TikTok link"})
    url = f"https://www.tiktok.com/@any/video/{video_id}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        match = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__".*?>(.*?)</script>', response.text)
        if not match:
            return jsonify({"error": "Data extraction failed"})
        data = json.loads(match.group(1))
        item = data.get("__DEFAULT_SCOPE__", {}).get("webapp.video-detail", {}).get("itemInfo", {}).get("itemStruct", {})
        stats = item.get("stats", {})
        return jsonify({
            "video_id": video_id,
            "description": item.get("desc", "No description"),
            "views": stats.get("playCount", 0),
            "likes": stats.get("diggCount", 0)
        })
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/create-order", methods=["POST"])
def create_order():
    if "username" not in session:
        return jsonify({"error": "Not logged in"}), 401
    username = session["username"]
    users = load_users()
    api_key = decrypt_api_key(users[username]["api_key"])
    d = request.json
    payload = {
        "service": d["service"],
        "link": d["link"],
        "quantity": d["quantity"]
    }
    r = call_smm_api(api_key, "add", **payload)
    if "order" in r:
        orders = load_user_orders(username)
        orders.append({
            "order_id": str(r["order"]),
            "service": d["service"],
            "link": d["link"],
            "quantity": d["quantity"],
            "status": "Pending",
            "created_at": datetime.now().isoformat()
        })
        save_user_orders(username, orders)
    return jsonify(r)

@app.route("/history")
def history():
    if "username" not in session:
        return jsonify({"error": "Not logged in"}), 401
    username = session["username"]
    orders = load_user_orders(username)
    if not orders:
        return jsonify([])
    order_ids = [o["order_id"] for o in orders]
    users = load_users()
    api_key = decrypt_api_key(users[username]["api_key"])
    try:
        r = call_smm_api(api_key, "status", orders=",".join(order_ids))
        for o in orders:
            if o["order_id"] in r:
                o["status"] = r[o["order_id"]].get("status", o["status"])
                o["remains"] = r[o["order_id"]].get("remains", "0")
        save_user_orders(username, orders)
        return jsonify([{"order_id": o["order_id"], "status": o["status"], "remains": o.get("remains", "0"),
                         "link": o["link"], "service": o["service"], "quantity": o["quantity"]} for o in orders])
    except Exception as e:
        return jsonify([])

# ================= SETTINGS ROUTE =================
@app.route("/settings", methods=["GET", "POST"])
def settings():
    if "username" not in session:
        return redirect(url_for("login"))
    username = session["username"]
    users = load_users()
    encrypted_key = users[username]["api_key"]
    api_key = decrypt_api_key(encrypted_key)
    status = None
    if request.method == "POST":
        new_api_key = request.form["api_key"]
        test = call_smm_api(new_api_key, "balance")
        if "error" in test or "balance" not in test:
            status = "Invalid API key or API not reachable"
        else:
            users[username]["api_key"] = encrypt_api_key(new_api_key)
            save_users(users)
            api_key = new_api_key
            status = "API key updated successfully"
    test = call_smm_api(api_key, "balance")
    connected = "balance" in test
    return render_template_string(SETTINGS_PAGE, username=username, api_key=api_key, connected=connected, status=status)

# ================= AUTOMATION ROUTES =================
@app.route("/automation/tasks", methods=["GET"])
def get_automation_tasks():
    if "username" not in session:
        return jsonify({"error": "Not logged in"}), 401
    username = session["username"]
    tasks = load_user_automation(username)
    return jsonify(tasks)

@app.route("/automation/add", methods=["POST"])
def add_automation():
    if "username" not in session:
        return jsonify({"error": "Not logged in"}), 401
    username = session["username"]
    data = request.json
    order_id = data.get("order_id")
    target = int(data.get("target"))
    orders = load_user_orders(username)
    order = next((o for o in orders if o["order_id"] == order_id), None)
    if not order:
        return jsonify({"error": "Order not found"}), 404
    if order.get("status") != "Completed":
        return jsonify({"error": "Only completed orders can be automated"}), 400

    tasks = load_user_automation(username)
    if any(t.get("order_id") == order_id and t.get("active")):
        return jsonify({"error": "This order is already being automated"}), 400

    task = {
        "order_id": order_id,
        "service": order["service"],
        "link": order["link"],
        "quantity": order["quantity"],
        "target": target,
        "last_views": 0,
        "last_order_time": None,
        "active": True,
        "created_at": datetime.now().isoformat()
    }
    tasks.append(task)
    save_user_automation(username, tasks)
    return jsonify({"success": True, "task": task})

@app.route("/automation/remove", methods=["POST"])
def remove_automation():
    if "username" not in session:
        return jsonify({"error": "Not logged in"}), 401
    username = session["username"]
    data = request.json
    order_id = data.get("order_id")
    tasks = load_user_automation(username)
    tasks = [t for t in tasks if t.get("order_id") != order_id]
    save_user_automation(username, tasks)
    return jsonify({"success": True})

# ================= BACKGROUND AUTOMATION WORKER =================
def automation_worker():
    while True:
        time.sleep(AUTOMATION_INTERVAL)
        users = load_users()
        for username, user_data in users.items():
            encrypted_key = user_data["api_key"]
            api_key = decrypt_api_key(encrypted_key)
            tasks = load_user_automation(username)
            if not tasks:
                continue
            updated = False
            for task in tasks:
                if not task.get("active"):
                    continue
                if task.get("last_order_time"):
                    last = datetime.fromisoformat(task["last_order_time"])
                    if datetime.now() - last < timedelta(minutes=10):
                        continue
                views = get_video_views(task["link"])
                if views is None:
                    continue
                task["last_views"] = views
                if views < task["target"]:
                    payload = {
                        "service": task["service"],
                        "link": task["link"],
                        "quantity": task["quantity"]
                    }
                    resp = call_smm_api(api_key, "add", **payload)
                    if "order" in resp:
                        orders = load_user_orders(username)
                        orders.append({
                            "order_id": str(resp["order"]),
                            "service": task["service"],
                            "link": task["link"],
                            "quantity": task["quantity"],
                            "status": "Pending",
                            "created_at": datetime.now().isoformat()
                        })
                        save_user_orders(username, orders)
                        task["last_order_time"] = datetime.now().isoformat()
                        updated = True
                else:
                    task["active"] = False
                    updated = True
            if updated:
                save_user_automation(username, tasks)

if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    thread = threading.Thread(target=automation_worker, daemon=True)
    thread.start()

# ================= IMPROVED UI TEMPLATES =================
BASE_CSS = """
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        min-height: 100vh;
        padding: 20px;
        color: #fff;
    }
    .glass-card {
        background: rgba(255, 255, 255, 0.1);
        backdrop-filter: blur(10px);
        border-radius: 20px;
        padding: 25px;
        box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
        border: 1px solid rgba(255, 255, 255, 0.18);
        margin-bottom: 20px;
    }
    input, select, button {
        font-family: inherit;
        transition: all 0.3s ease;
    }
    input, select {
        background: rgba(255,255,255,0.2);
        border: 1px solid rgba(255,255,255,0.3);
        color: white;
        padding: 12px 15px;
        border-radius: 12px;
        width: 100%;
        margin: 8px 0;
        font-size: 16px;
    }
    input::placeholder { color: rgba(255,255,255,0.7); }
    input:focus, select:focus {
        outline: none;
        background: rgba(255,255,255,0.25);
        border-color: #fff;
    }
    button {
        background: #fff;
        color: #764ba2;
        border: none;
        padding: 14px 20px;
        border-radius: 12px;
        font-weight: 600;
        font-size: 16px;
        cursor: pointer;
        width: 100%;
        box-shadow: 0 4px 15px rgba(0,0,0,0.2);
    }
    button:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 25px rgba(0,0,0,0.3);
    }
    .flex { display: flex; gap: 15px; align-items: center; flex-wrap: wrap; }
    .badge {
        background: rgba(255,255,255,0.2);
        padding: 8px 16px;
        border-radius: 30px;
        font-weight: 500;
        backdrop-filter: blur(5px);
    }
    table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 15px;
    }
    th {
        text-align: left;
        padding: 12px;
        color: rgba(255,255,255,0.9);
        font-weight: 600;
        border-bottom: 2px solid rgba(255,255,255,0.2);
    }
    td {
        padding: 12px;
        border-bottom: 1px solid rgba(255,255,255,0.1);
    }
    tr:hover { background: rgba(255,255,255,0.05); }
    .price-tag {
        background: rgba(0,0,0,0.3);
        padding: 12px;
        border-radius: 12px;
        text-align: center;
        font-weight: 600;
        margin: 10px 0;
    }
    .tab-buttons {
        display: flex;
        gap: 10px;
        margin-bottom: 25px;
        flex-wrap: wrap;
    }
    .tab-buttons button {
        width: auto;
        padding: 10px 25px;
        background: rgba(255,255,255,0.15);
        color: white;
        border: 1px solid rgba(255,255,255,0.3);
    }
    .tab-buttons button.active {
        background: white;
        color: #764ba2;
        border-color: white;
    }
    .section { display: none; }
    .section.active { display: block; }
    .top-bar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 25px;
        flex-wrap: wrap;
        gap: 15px;
    }
    a { color: white; text-decoration: none; font-weight: 500; }
    a:hover { text-decoration: underline; }
    .spinner {
        border: 3px solid rgba(255,255,255,0.3);
        border-top: 3px solid white;
        border-radius: 50%;
        width: 24px;
        height: 24px;
        animation: spin 1s linear infinite;
        display: inline-block;
    }
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
"""

LOGIN_PAGE = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Login - SMM Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>{BASE_CSS}</style>
</head>
<body>
    <div style="max-width: 400px; margin: 50px auto;">
        <div class="glass-card">
            <h2 style="margin-bottom: 20px;">🔐 Login</h2>
            {{% if error %}}<div style="background: rgba(255,0,0,0.2); padding: 10px; border-radius: 8px;">{{{{ error }}}}</div>{{% endif %}}
            <form method="post">
                <input type="text" name="username" placeholder="Username" required>
                <input type="password" name="password" placeholder="Password" required>
                <button type="submit">Login</button>
            </form>
            <p style="margin-top: 20px; text-align: center;">Don't have an account? <a href="/register">Register</a></p>
        </div>
    </div>
</body>
</html>
"""

REGISTER_PAGE = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Register - SMM Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>{BASE_CSS}</style>
</head>
<body>
    <div style="max-width: 400px; margin: 50px auto;">
        <div class="glass-card">
            <h2 style="margin-bottom: 20px;">📝 Register</h2>
            {{% if error %}}<div style="background: rgba(255,0,0,0.2); padding: 10px; border-radius: 8px;">{{{{ error }}}}</div>{{% endif %}}
            <form method="post">
                <input type="text" name="username" placeholder="Username" required>
                <input type="password" name="password" placeholder="Password" required>
                <input type="text" name="api_key" placeholder="SMM API Key" required>
                <button type="submit">Register</button>
            </form>
            <p style="margin-top: 20px; text-align: center;">Already have an account? <a href="/login">Login</a></p>
        </div>
    </div>
</body>
</html>
"""

SETTINGS_PAGE = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Settings - SMM Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>{BASE_CSS}</style>
</head>
<body>
    <div style="max-width: 600px; margin: 0 auto;">
        <div class="glass-card">
            <h2>⚙️ Settings - {{{{ username }}}}</h2>
            <div style="padding: 10px; border-radius: 8px; margin: 15px 0; background: {{% if connected %}}rgba(0,200,0,0.2){{% else %}}rgba(200,0,0,0.2){{% endif %}};">
                API Status: <strong>{{% if connected %}}✅ Connected{{% else %}}❌ Disconnected{{% endif %}}</strong>
            </div>
            {{% if status %}}<div style="background: rgba(0,200,0,0.2); padding: 10px; border-radius: 8px;">{{{{ status }}}}</div>{{% endif %}}
            <form method="post">
                <label>API Key</label>
                <input type="text" name="api_key" value="{{{{ api_key }}}}" required>
                <button type="submit">Update API Key</button>
            </form>
            <p style="margin-top: 20px;"><a href="/">⬅ Back to Panel</a> | <a href="/logout">Logout</a></p>
        </div>
    </div>
</body>
</html>
"""

MAIN_PAGE = f"""
<!DOCTYPE html>
<html>
<head>
    <title>SMM Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>{BASE_CSS}</style>
</head>
<body>
    <div class="top-bar">
        <h2 style="margin:0;">🚀 SMM Automation Panel</h2>
        <div>
            <span class="badge" id="balUSD">Loading...</span>
            <a href="/settings" style="margin-left: 15px;">⚙️ Settings</a>
            <a href="/logout" style="margin-left: 15px;">🚪 Logout ({{{{ username }}}})</a>
        </div>
    </div>

    <div class="tab-buttons">
        <button id="tab1" class="active" onclick="showSection(1)">📦 Order</button>
        <button id="tab2" onclick="showSection(2)">🤖 Automation</button>
        <button id="tab3" onclick="showSection(3)">📜 History</button>
    </div>

    <!-- Section 1: Order -->
    <div id="section1" class="section active">
        <div class="glass-card">
            <h3>🎬 TikTok Video Analyzer</h3>
            <div class="flex">
                <input type="text" id="vUrl" placeholder="Video Link or ID" style="flex:1;">
                <button id="vBtn" onclick="analyzeVideo()" style="width: auto; padding: 12px 25px;">Check</button>
            </div>
            <div id="vStats" style="display:none; margin-top:20px;">
                <div style="background: rgba(255,255,255,0.15); padding: 15px; border-radius: 12px;">
                    <span id="sViews"></span> Views · <span id="sLikes"></span> Likes
                </div>
            </div>
        </div>

        <div class="glass-card">
            <h3>📦 Place New Order</h3>
            <label>1. Platform</label>
            <select id="platSelect" onchange="filterCategories()">
                <option value="">Select Platform</option>
                <option value="TikTok">TikTok</option>
                <option value="Instagram">Instagram</option>
                <option value="Facebook">Facebook</option>
                <option value="YouTube">YouTube</option>
                <option value="Telegram">Telegram</option>
                <option value="Twitter">Twitter/X</option>
            </select>

            <label>2. Category</label>
            <select id="catSelect" onchange="filterServices()"><option value="">Select Category</option></select>
            
            <label>3. Service</label>
            <select id="serSelect" onchange="updateCalc()"><option value="">Select Service</option></select>
            
            <label>4. Details</label>
            <input type="text" id="oLink" placeholder="Link (URL)">
            <input type="number" id="oQty" placeholder="Quantity" oninput="updateCalc()">
            
            <div class="price-tag" id="priceDisplay">Total: $0.00 | ৳0.00</div>
            <button onclick="placeOrder()">🚀 Submit Order</button>
        </div>
    </div>

    <!-- Section 2: Automation -->
    <div id="section2" class="section">
        <div class="glass-card">
            <h3>➕ New Automation Task</h3>
            <label>Select Completed Order</label>
            <select id="autoOrderSelect">
                <option value="">-- Load orders first --</option>
            </select>
            <label>Target Views</label>
            <input type="number" id="autoTarget" placeholder="e.g. 10000">
            <button onclick="addAutomation()">▶ Start Automation</button>
        </div>

        <div class="glass-card">
            <h3>⚙️ Active Tasks</h3>
            <div id="autoTasks" style="overflow-x: auto;">Loading...</div>
        </div>
    </div>

    <!-- Section 3: History -->
    <div id="section3" class="section">
        <div class="glass-card">
            <h3>📜 Order History</h3>
            <div id="hTable" style="overflow-x: auto;">No orders yet.</div>
        </div>
    </div>

<script>
    let allServices = [];
    let bdtRate = 0;

    function showSection(num) {{
        document.querySelectorAll('.section').forEach(el => el.classList.remove('active'));
        document.getElementById('section'+num).classList.add('active');
        document.querySelectorAll('.tab-buttons button').forEach(btn => btn.classList.remove('active'));
        document.getElementById('tab'+num).classList.add('active');
        if (num == 2) {{
            loadAutomationTasks();
            loadCompletedOrders();
        }}
        if (num == 3) loadHistory();
    }}

    async function init() {{
        const r = await fetch("/init-data");
        const d = await r.json();
        allServices = d.services;
        bdtRate = d.rate;
        document.getElementById("balUSD").innerText = `$${{d.balance}} | ৳${{(d.balance * bdtRate).toFixed(2)}}`;
    }}

    function filterCategories() {{
        const platform = document.getElementById("platSelect").value.toLowerCase();
        const catSelect = document.getElementById("catSelect");
        const filteredCats = [...new Set(allServices
            .filter(s => s.category.toLowerCase().includes(platform))
            .map(s => s.category)
        )];
        catSelect.innerHTML = '<option value="">-- Choose Category --</option>' + 
            filteredCats.map(c => `<option value="${{c}}">${{c}}</option>`).join('');
        document.getElementById("serSelect").innerHTML = '<option value="">-- Select Category First --</option>';
        updateCalc();
    }}

    function filterServices() {{
        const cat = document.getElementById("catSelect").value;
        const serS = document.getElementById("serSelect");
        const filtered = allServices.filter(s => s.category === cat);
        serS.innerHTML = '<option value="">-- Choose Service --</option>' + 
            filtered.map(s => `<option value="${{s.service}}" data-rate="${{s.rate}}">${{s.name}} ($${{s.rate}}/1k)</option>`).join('');
        updateCalc();
    }}

    function updateCalc() {{
        const qty = document.getElementById("oQty").value || 0;
        const selected = document.getElementById("serSelect").selectedOptions[0];
        const rate = selected ? selected.dataset.rate : 0;
        const costUSD = (qty * rate / 1000).toFixed(4);
        const costBDT = (costUSD * bdtRate).toFixed(2);
        document.getElementById("priceDisplay").innerText = `Total: ${{costUSD}} USD | ৳${{costBDT}} BDT`;
    }}

    async function analyzeVideo() {{
        const btn = document.getElementById("vBtn");
        const urlInput = document.getElementById("vUrl").value;
        if(!urlInput) return;
        btn.innerHTML = '<span class="spinner"></span>';
        const r = await fetch("/analyze", {{
            method: "POST",
            headers: {{"Content-Type":"application/json"}},
            body: JSON.stringify({{url: urlInput}})
        }});
        const d = await r.json();
        btn.innerText = "Check";
        if(d.video_id) {{
            document.getElementById("vStats").style.display = "block";
            document.getElementById("sViews").innerText = d.views.toLocaleString();
            document.getElementById("sLikes").innerText = d.likes.toLocaleString();
            document.getElementById("oLink").value = "https://www.tiktok.com/@user/video/" + d.video_id;
            document.getElementById("platSelect").value = "TikTok";
            filterCategories();
        }} else {{
            alert(d.error);
        }}
    }}

    async function placeOrder() {{
        const service = document.getElementById("serSelect").value;
        const link = document.getElementById("oLink").value;
        const quantity = document.getElementById("oQty").value;
        if(!service || !link || !quantity) return alert("Please fill all fields");
        const btn = event.target;
        btn.innerHTML = '<span class="spinner"></span>';
        const r = await fetch("/create-order", {{
            method: "POST",
            headers: {{"Content-Type":"application/json"}},
            body: JSON.stringify({{service, link, quantity}})
        }});
        const d = await r.json();
        btn.innerText = "🚀 Submit Order";
        if(d.order) {{
            alert("Order Success! ID: " + d.order);
            loadHistory();
            init();
        }} else {{
            alert("Error: " + d.error);
        }}
    }}

    async function loadHistory() {{
        const r = await fetch("/history");
        const d = await r.json();
        if(d.length > 0) {{
            let h = '<table><tr><th>ID</th><th>Status</th><th>Left</th><th>Link</th><th>Qty</th></tr>';
            d.forEach(o => h += `<tr><td>${{o.order_id}}</td><td style="color:#a78bfa">${{o.status}}</td><td>${{o.remains}}</td><td>${{o.link.substring(0,30)}}...</td><td>${{o.quantity}}</td></tr>`);
            document.getElementById("hTable").innerHTML = h + '</table>';
        }} else {{
            document.getElementById("hTable").innerHTML = "No orders yet.";
        }}
    }}

    async function loadCompletedOrders() {{
        const r = await fetch("/history");
        const orders = await r.json();
        const completed = orders.filter(o => o.status === "Completed");
        const select = document.getElementById("autoOrderSelect");
        select.innerHTML = '<option value="">-- Select Completed Order --</option>' +
            completed.map(o => `<option value="${{o.order_id}}">${{o.order_id}} - ${{o.link.substring(0,30)}} (${{o.quantity}})</option>`).join('');
    }}

    async function addAutomation() {{
        const orderId = document.getElementById("autoOrderSelect").value;
        const target = document.getElementById("autoTarget").value;
        if (!orderId || !target) return alert("Select order and enter target");
        const r = await fetch("/automation/add", {{
            method: "POST",
            headers: {{"Content-Type":"application/json"}},
            body: JSON.stringify({{order_id: orderId, target: parseInt(target)}})
        }});
        const res = await r.json();
        if (res.success) {{
            alert("Automation started");
            loadAutomationTasks();
        }} else {{
            alert("Error: " + res.error);
        }}
    }}

    async function loadAutomationTasks() {{
        const r = await fetch("/automation/tasks");
        const tasks = await r.json();
        let html = '<table><tr><th>Order ID</th><th>Target</th><th>Current</th><th>Status</th><th>Action</th></tr>';
        tasks.forEach(t => {{
            html += `<tr>
                <td>${{t.order_id}}</td>
                <td>${{t.target}}</td>
                <td>${{t.last_views || '?'}}</td>
                <td>${{t.active ? 'Active' : 'Completed'}}</td>
                <td><button onclick="removeAutomation('${{t.order_id}}')" style="padding:5px 10px;">Remove</button></td>
            </tr>`;
        }});
        document.getElementById("autoTasks").innerHTML = html + '</table>';
    }}

    async function removeAutomation(orderId) {{
        if (!confirm("Remove this automation?")) return;
        await fetch("/automation/remove", {{
            method: "POST",
            headers: {{"Content-Type":"application/json"}},
            body: JSON.stringify({{order_id: orderId}})
        }});
        loadAutomationTasks();
    }}

    init();
    setInterval(() => {{
        if (document.getElementById('section3').classList.contains('active')) loadHistory();
        if (document.getElementById('section2').classList.contains('active')) loadAutomationTasks();
    }}, 10000);
</script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=4000, debug=True)