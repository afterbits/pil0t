from flask import Flask, jsonify, request, send_from_directory, send_file, Response, session, redirect
from flask_cors import CORS
from flask_sock import Sock
import socket as _socket
import os, json, subprocess, shutil, pty, select, threading, time, stat as stat_mod, re, hashlib, secrets
from datetime import datetime
from pathlib import Path

app = Flask(__name__, static_folder="static")
CORS(app)
sock = Sock(app)

# ── Constants ────────────────────────────────────────────────────────────────
PRINTER_IP      = "10.10.10.249"
PRINTER_PORT    = 9100
PRINTER_MODE    = "network"   # "network" or "usb"
USB_DEVICE      = "/dev/usb/lp0"
SKU_FILE        = "/etc/pil0t/data/current_sku.txt"
STARTING_SKU    = 1490
LOG_FILE        = "/etc/pil0t/data/sku_log.txt"
CONFIG_FILE     = "/etc/pil0t/data/printer_config.json"
SETUP_FILE      = "/etc/pil0t/data/.setup_complete"
DEVICE_FILE     = "/etc/pil0t/data/device_config.json"
UPDATE_FILE     = "/etc/pil0t/data/available_updates.json"
SCRIPTS_FILE    = "/etc/pil0t/data/saved_scripts.json"
USERS_FILE      = "/etc/pil0t/data/app_users.json"
AUTH_CONFIG     = "/etc/pil0t/data/auth_config.json"
BRANDING_FILE   = "/etc/pil0t/data/branding.json"
UPDATE_INTERVAL = 6 * 60 * 60

_secret_file = "/etc/pil0t/data/.flask_secret"
if os.path.exists(_secret_file):
    app.secret_key = open(_secret_file).read().strip()
else:
    app.secret_key = secrets.token_hex(32)
    try:
        with open(_secret_file,"w") as f: f.write(app.secret_key)
        os.chmod(_secret_file, 0o600)
    except: pass

def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE) as f: return json.load(f)
    # Default admin user
    default = [{"username":"admin","password":hash_pw("admin"),"role":"admin"}]
    save_users(default)
    return default

def save_users(users):
    with open(USERS_FILE,"w") as f: json.dump(users, f, indent=2)

def load_auth_config():
    defaults = {
        "protected": {"system":True,"files":True,"api_system":True,"api_reboot":True},
        "guest_print": True
    }
    if os.path.exists(AUTH_CONFIG):
        with open(AUTH_CONFIG) as f:
            cfg = json.load(f)
            for k,v in defaults.items():
                if k not in cfg: cfg[k] = v
            return cfg
    save_auth_config(defaults)
    return defaults

def save_auth_config(cfg):
    with open(AUTH_CONFIG,"w") as f: json.dump(cfg, f, indent=2)

def current_user():
    return session.get("user")

def require_auth(role=None):
    user = current_user()
    if not user: return False
    # Check if this session has been kicked
    sid = session.get("sid","")
    if sid and sid in _kicked_sessions:
        session.clear()
        return False
    if role == "admin" and user.get("role") != "admin": return False
    return True

def check_protected(section):
    cfg = load_auth_config()
    if not cfg["protected"].get(section, False): return True  # not protected
    return require_auth()


# ── Background update checker ─────────────────────────────────────────────────
def check_available_updates():
    while True:
        try:
            subprocess.run(["sudo", "apt-get", "update", "-qq"], capture_output=True, timeout=120)
            r = subprocess.run(["apt-get", "-s", "upgrade"], capture_output=True, text=True, timeout=60)
            count = sum(1 for l in r.stdout.splitlines() if l.startswith("Inst "))
            with open(UPDATE_FILE, "w") as f:
                json.dump({"count": count, "checked": datetime.now().strftime("%Y-%m-%d %H:%M")}, f)
        except Exception as e:
            try:
                with open(UPDATE_FILE, "w") as f:
                    json.dump({"count": -1, "checked": "error", "error": str(e)}, f)
            except: pass
        time.sleep(UPDATE_INTERVAL)

threading.Thread(target=check_available_updates, daemon=True).start()

# ── Config helpers ────────────────────────────────────────────────────────────
def load_config():
    global PRINTER_IP, PRINTER_PORT, PRINTER_MODE, USB_DEVICE
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
            PRINTER_IP   = cfg.get("printer_ip",   PRINTER_IP)
            PRINTER_PORT = int(cfg.get("printer_port", PRINTER_PORT))
            PRINTER_MODE = cfg.get("mode",         PRINTER_MODE)
            USB_DEVICE   = cfg.get("usb_device",   USB_DEVICE)

def save_config():
    with open(CONFIG_FILE, "w") as f:
        json.dump({
            "printer_ip":   PRINTER_IP,
            "printer_port": PRINTER_PORT,
            "mode":         PRINTER_MODE,
            "usb_device":   USB_DEVICE,
        }, f, indent=2)

def setup_complete():
    return os.path.exists(SETUP_FILE)

def mark_setup_complete():
    with open(SETUP_FILE, "w") as f:
        f.write(datetime.now().isoformat())

def load_device_config():
    if os.path.exists(DEVICE_FILE):
        with open(DEVICE_FILE) as f:
            return json.load(f)
    return {"zebra_mode": True}

def save_device_config(cfg):
    with open(DEVICE_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def load_sku():
    if os.path.exists(SKU_FILE):
        with open(SKU_FILE) as f:
            return int(f.read().strip())
    return STARTING_SKU

def save_sku(sku):
    with open(SKU_FILE, "w") as f:
        f.write(str(sku))

def log_print(sku, action="printed"):
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — SKU {sku} — {action}\n")

def get_timestamp():
    n = datetime.now()
    suf = {1:"st",2:"nd",3:"rd"}.get(n.day if n.day < 20 else n.day % 10, "th")
    h = n.hour % 12 or 12
    return f"{n.strftime('%B')} {n.day}{suf} {n.year} at {h}:{n.strftime('%M')}{'am' if n.hour < 12 else 'pm'}"

def send_zpl(zpl):
    try:
        if PRINTER_MODE == "usb":
            with open(USB_DEVICE, "wb") as f:
                f.write(zpl.encode("utf-8"))
                f.flush()
        else:
            with _socket.create_connection((PRINTER_IP, PRINTER_PORT), timeout=5) as s:
                s.sendall(zpl.encode("utf-8"))
        return True, None
    except Exception as e:
        return False, str(e)

def build_sku_label(sku, ts):
    return f"^XA^MMT^PW447^LL146^LS0^CI28^FO0,25^FB447,1,,C^A0N,75,52^FD{sku}^FS^FO0,103^FB447,1,,C^A0N,32,22^FD{ts}^FS^PQ1^XZ"

def build_custom_label(sku, text, ts):
    return f"^XA^MMT^PW447^LL160^LS0^CI28^FO0,8^FB447,1,,C^A0N,75,52^FD{sku}^FS^FO0,88^FB447,1,,C^A0N,28,20^FD{text[:38]}^FS^FO0,122^FB447,1,,C^A0N,24,16^FD{ts}^FS^PQ1^XZ"

load_config()


# ── Session tracking ──────────────────────────────────────────────────────────
_active_sessions  = {}   # token -> {username, ip, login_time}
_kicked_sessions  = set()  # invalidated session IDs
_login_history    = []   # list of {username, ip, time, action}

@app.route("/api/auth/sessions")
def auth_sessions():
    if not require_auth("admin"): return jsonify({"error":"Unauthorized"}), 403
    sessions = [{"id":k,"username":v["username"],"ip":v["ip"],"time":v["time"]} for k,v in _active_sessions.items()]
    return jsonify({"ok":True,"sessions":sessions,"history":_login_history[-50:]})

@app.route("/api/auth/kick", methods=["POST"])
def auth_kick():
    if not require_auth("admin"): return jsonify({"error":"Unauthorized"}), 403
    session_id = request.json.get("id","")
    if session_id in _active_sessions:
        _kicked_sessions.add(session_id)
        del _active_sessions[session_id]
    return jsonify({"ok":True})

# ── Auth routes ───────────────────────────────────────────────────────────────
@app.route("/login")
def login_page(): return send_from_directory("static", "login.html")

@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    d = request.json
    users = load_users()
    user = next((u for u in users if u["username"] == d.get("username") and u["password"] == hash_pw(d.get("password",""))), None)
    if not user: return jsonify({"ok":False,"error":"Invalid credentials"}), 401
    session["user"] = {"username":user["username"],"role":user["role"]}
    session.permanent = True
    # Track session
    sid = secrets.token_hex(8)
    session["sid"] = sid
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    _active_sessions[sid] = {"username":user["username"],"ip":ip,"time":datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    _login_history.append({"username":user["username"],"ip":ip,"time":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),"action":"login"})
    if len(_login_history) > 200: _login_history.pop(0)
    return jsonify({"ok":True,"username":user["username"],"role":user["role"]})

@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    sid = session.get("sid","")
    user = session.get("user",{})
    if sid and sid in _active_sessions:
        del _active_sessions[sid]
    if user:
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)
        _login_history.append({"username":user.get("username","?"),"ip":ip,"time":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),"action":"logout"})
    session.clear()
    return jsonify({"ok":True})

@app.route("/api/auth/me")
def auth_me():
    user = current_user()
    sid = session.get("sid","")
    if user and sid and sid in _kicked_sessions:
        session.clear()
        return jsonify({"ok":False,"kicked":True,"guest_print":load_auth_config().get("guest_print",True)})
    if user: return jsonify({"ok":True,"user":user})
    cfg = load_auth_config()
    return jsonify({"ok":False,"guest_print":cfg.get("guest_print",True)})

@app.route("/api/auth/users")
def auth_users():
    if not require_auth("admin"): return jsonify({"error":"Unauthorized"}), 403
    users = [{"username":u["username"],"role":u["role"]} for u in load_users()]
    return jsonify({"ok":True,"users":users})

@app.route("/api/auth/users/add", methods=["POST"])
def auth_add_user():
    if not require_auth("admin"): return jsonify({"error":"Unauthorized"}), 403
    d = request.json; username = d.get("username","").strip(); password = d.get("password",""); role = d.get("role","user")
    if not username or not password: return jsonify({"ok":False,"error":"Username and password required"}), 400
    users = load_users()
    if any(u["username"] == username for u in users): return jsonify({"ok":False,"error":"User already exists"}), 400
    users.append({"username":username,"password":hash_pw(password),"role":role})
    save_users(users)
    return jsonify({"ok":True})

@app.route("/api/auth/users/update", methods=["POST"])
def auth_update_user():
    if not require_auth("admin"): return jsonify({"error":"Unauthorized"}), 403
    d = request.json; username = d.get("username"); password = d.get("password"); role = d.get("role")
    users = load_users()
    for u in users:
        if u["username"] == username:
            if password: u["password"] = hash_pw(password)
            if role: u["role"] = role
            save_users(users)
            return jsonify({"ok":True})
    return jsonify({"ok":False,"error":"User not found"}), 404

@app.route("/api/auth/users/delete", methods=["POST"])
def auth_delete_user():
    if not require_auth("admin"): return jsonify({"error":"Unauthorized"}), 403
    username = request.json.get("username")
    if username == "admin": return jsonify({"ok":False,"error":"Cannot delete admin"}), 400
    users = [u for u in load_users() if u["username"] != username]
    save_users(users)
    return jsonify({"ok":True})

@app.route("/api/auth/config")
def auth_get_config():
    if not require_auth("admin"): return jsonify({"error":"Unauthorized"}), 403
    return jsonify({"ok":True,"config":load_auth_config()})

@app.route("/api/auth/config", methods=["POST"])
def auth_set_config():
    if not require_auth("admin"): return jsonify({"error":"Unauthorized"}), 403
    cfg = request.json
    save_auth_config(cfg)
    return jsonify({"ok":True})

@app.route("/api/auth/verify-password", methods=["POST"])
def auth_verify_password():
    user = current_user()
    if not user: return jsonify({"ok":False}), 401
    d = request.json
    users = load_users()
    match = next((u for u in users if u["username"] == user["username"] and u["password"] == hash_pw(d.get("password",""))), None)
    return jsonify({"ok": bool(match)})

# ── Page routes ───────────────────────────────────────────────────────────────
@app.route("/")
def index():
    if not setup_complete():
        return redirect("/wizard")
    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    user = session.get("user", {})
    _login_history.append({"username": user.get("username","guest"), "ip": ip, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "action":"page_hit_/"})
    if len(_login_history) > 200: _login_history.pop(0)
    return send_from_directory("static", "index.html")

@app.route("/wizard")
def wizard_page():
    if setup_complete():
        return redirect("/")
    return send_from_directory("static", "wizard.html")

@app.route("/system")
def system_page():
    cfg = load_auth_config()
    if cfg["protected"].get("system", True) and not require_auth():
        return send_from_directory("static", "login.html")
    return send_from_directory("static", "system.html")

@app.route("/files")
def files_page(): return send_from_directory("static", "filebrowser.html")

# ── Setup Wizard API ──────────────────────────────────────────────────────────
@app.route("/api/setup/complete", methods=["POST"])
def api_setup_complete():
    d = request.json or {}
    # Set admin password
    password = d.get("password","").strip()
    if not password:
        return jsonify({"ok": False, "error": "Password required"}), 400
    hashed = hashlib.sha256(password.encode()).hexdigest()
    users_file = "/etc/pil0t/data/app_users.json"
    users = []
    if os.path.exists(users_file):
        with open(users_file) as f:
            users = json.load(f)
    for u in users:
        if u["username"] == "admin":
            u["password"] = hashed
            break
    else:
        users.append({"username":"admin","password":hashed,"role":"admin"})
    with open(users_file, "w") as f:
        json.dump(users, f, indent=2)
    # Save device config
    zebra_mode = d.get("zebra_mode", True)
    save_device_config({"zebra_mode": zebra_mode})
    # Mark setup done
    mark_setup_complete()
    return jsonify({"ok": True})

@app.route("/api/setup/status")
def api_setup_status():
    return jsonify({"complete": setup_complete()})

@app.route("/api/device/config", methods=["GET","POST"])
def api_device_config():
    if request.method == "POST":
        d = request.json or {}
        cfg = load_device_config()
        if "zebra_mode" in d:
            cfg["zebra_mode"] = bool(d["zebra_mode"])
        save_device_config(cfg)
        return jsonify({"ok": True, **cfg})
    return jsonify({"ok": True, **load_device_config()})

# ── Printer API ───────────────────────────────────────────────────────────────
@app.route("/api/status")
def status():
    online = False
    try:
        if PRINTER_MODE == "usb":
            online = os.path.exists(USB_DEVICE)
        else:
            with _socket.create_connection((PRINTER_IP, PRINTER_PORT), timeout=2): online = True
    except: online = False
    return jsonify({
        "printer_ip":     PRINTER_IP,
        "printer_port":   PRINTER_PORT,
        "printer_mode":   PRINTER_MODE,
        "usb_device":     USB_DEVICE,
        "printer_online": online,
        "current_sku":    load_sku()
    })

@app.route("/api/config", methods=["POST"])
def update_config():
    global PRINTER_IP, PRINTER_PORT, PRINTER_MODE, USB_DEVICE
    d = request.json
    if "printer_ip"   in d: PRINTER_IP   = d["printer_ip"]
    if "printer_port" in d: PRINTER_PORT = int(d["printer_port"])
    if "mode"         in d: PRINTER_MODE = d["mode"]
    if "usb_device"   in d: USB_DEVICE   = d["usb_device"]
    save_config()
    if "sku" in d: save_sku(int(d["sku"]))
    return jsonify({"ok": True, "printer_ip": PRINTER_IP, "printer_port": PRINTER_PORT,
                    "mode": PRINTER_MODE, "usb_device": USB_DEVICE, "sku": load_sku()})

@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify({"ok": True, "printer_ip": PRINTER_IP, "printer_port": PRINTER_PORT,
                    "mode": PRINTER_MODE, "usb_device": USB_DEVICE, "sku": load_sku()})

@app.route("/api/print/next", methods=["POST"])
def print_next():
    sku = load_sku(); ts = get_timestamp()
    ok, err = send_zpl(build_sku_label(sku, ts))
    if ok: save_sku(sku + 1); log_print(sku); return jsonify({"ok": True, "sku": sku, "next": sku+1, "ts": ts})
    return jsonify({"ok": False, "error": err}), 500

@app.route("/api/print/reprint", methods=["POST"])
def reprint():
    sku = load_sku() - 1
    if sku < STARTING_SKU: return jsonify({"ok": False, "error": "No previous SKU"}), 400
    ts = get_timestamp(); ok, err = send_zpl(build_sku_label(sku, ts))
    if ok: log_print(sku, "reprinted"); return jsonify({"ok": True, "sku": sku, "ts": ts})
    return jsonify({"ok": False, "error": err}), 500

@app.route("/api/print/blank", methods=["POST"])
def print_blank():
    ok, err = send_zpl("^XA^MMT^PW447^LL146^LS0^FO220,70^GB1,1,1^FS^PQ1^XZ")
    return jsonify({"ok": ok, "error": err})

@app.route("/api/print/custom", methods=["POST"])
def print_custom():
    d = request.json; sku = str(d.get("sku","")); text = str(d.get("text",""))
    if not sku: return jsonify({"ok": False, "error": "SKU required"}), 400
    ok, err = send_zpl(build_custom_label(sku, text, get_timestamp()))
    if ok: log_print(sku, f"custom: {text}"); return jsonify({"ok": True, "sku": sku, "text": text})
    return jsonify({"ok": False, "error": err}), 500

@app.route("/api/print/batch", methods=["POST"])
def print_batch():
    d = request.json; start = int(d.get("start", load_sku())); count = min(int(d.get("count",1)), 100)
    results = []
    for i in range(count):
        sku = start + i; ts = get_timestamp()
        ok, err = send_zpl(build_sku_label(sku, ts))
        results.append({"sku": sku, "ok": ok, "error": err})
        if ok: log_print(sku, "batch")
        else: break
    return jsonify({"ok": True, "results": results, "printed": sum(1 for r in results if r["ok"])})

@app.route("/api/log")
def get_log():
    if not os.path.exists(LOG_FILE): return jsonify({"lines": []})
    with open(LOG_FILE) as f: lines = f.readlines()
    return jsonify({"lines": [l.strip() for l in reversed(lines[-50:])]})

# ── System metrics ────────────────────────────────────────────────────────────
@app.route("/api/system/metrics")
def system_metrics():
    def cpu_sample():
        with open("/proc/stat") as f: line = f.readline()
        v = list(map(int, line.split()[1:])); return v[3], sum(v)
    try:
        i1,t1 = cpu_sample(); time.sleep(0.2); i2,t2 = cpu_sample()
        cpu_pct = round((1-(i2-i1)/max(t2-t1,1))*100, 1)
    except: cpu_pct = 0.0
    try:
        with open("/proc/meminfo") as f:
            m = {l.split(":")[0].strip(): int(l.split()[1]) for l in f if ":" in l}
        mt = m.get("MemTotal",0)//1024; ma = m.get("MemAvailable",0)//1024
        mu = mt-ma; mp = round(mu/max(mt,1)*100,1)
        st = m.get("SwapTotal",0)//1024; sf = m.get("SwapFree",0)//1024
        su = st-sf; sp = round(su/max(st,1)*100,1) if st else 0
    except: mt=mu=mp=st=su=sp=0
    try:
        tot,used,_ = shutil.disk_usage("/")
        dg = round(used/(1024**3),1); dtg = round(tot/(1024**3),1); dp = round(used/tot*100,1)
    except: dg=dtg=dp=0
    return jsonify({"cpu_pct":cpu_pct,"mem_used_mb":mu,"mem_total_mb":mt,"mem_pct":mp,
                    "swap_used_mb":su,"swap_total_mb":st,"swap_pct":sp,
                    "disk_used_gb":dg,"disk_total_gb":dtg,"disk_pct":dp})

@app.route("/api/system/info")
def system_info():
    # CPU
    try:
        cpu_model = "Unknown"
        with open("/proc/cpuinfo") as f:
            for line in f:
                if "model name" in line.lower() or "Model" in line:
                    cpu_model = line.split(":")[1].strip(); break
    except: cpu_model = "Unknown"
    try: cpu_arch = subprocess.run(["uname","-m"],capture_output=True,text=True).stdout.strip()
    except: cpu_arch = "--"
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            t = int(f.read().strip())/1000
            cpu_temp = f"{t:.1f}°C" + (" ⚠ HOT" if t>70 else "")
    except: cpu_temp = "N/A"
    # OS
    try:
        os_name = "Unknown"
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME"): os_name = line.split("=")[1].strip().strip('"'); break
    except: os_name = "Unknown"
    try: os_kernel = subprocess.run(["uname","-r"],capture_output=True,text=True).stdout.strip()
    except: os_kernel = "--"
    try: hostname = _socket.gethostname()
    except: hostname = "--"
    try:
        with open("/proc/uptime") as f: s = float(f.read().split()[0])
        uptime = f"{int(s//86400)}d {int((s%86400)//3600)}h {int((s%3600)//60)}m"
    except: uptime = "--"
    # Network
    try:
        r = subprocess.run(["ip","-o","-4","addr","show"],capture_output=True,text=True)
        lan_ip=lan_iface=lan_mac=lan_status="--"
        for line in r.stdout.splitlines():
            parts = line.split()
            if parts[1] != "lo":
                lan_iface = parts[1]; lan_ip = parts[3].split("/")[0]
                lan_mac = Path(f"/sys/class/net/{lan_iface}/address").read_text().strip()
                lan_status = Path(f"/sys/class/net/{lan_iface}/operstate").read_text().strip().upper()
                break
    except: lan_ip=lan_iface=lan_mac=lan_status="--"
    return jsonify({"cpu_model":cpu_model,"cpu_cores":str(os.cpu_count()),"cpu_arch":cpu_arch,
                    "cpu_temp":cpu_temp,"os_name":os_name,"os_kernel":os_kernel,"hostname":hostname,
                    "uptime":uptime,"lan_ip":lan_ip,"lan_iface":lan_iface,"lan_mac":lan_mac,"lan_status":lan_status})

@app.route("/api/system/update-status")
def update_status():
    try:
        if os.path.exists(UPDATE_FILE):
            with open(UPDATE_FILE) as f: return jsonify(json.load(f))
        return jsonify({"count": None, "checked": None})
    except Exception as e: return jsonify({"count": -1, "error": str(e)})


# ── Streaming apt output ──────────────────────────────────────────────────────
@app.route("/api/system/update/stream", methods=["POST"])
def system_update_stream():
    def generate():
        proc = subprocess.Popen(
            ["sudo", "apt-get", "update"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        for line in proc.stdout:
            yield line
        proc.wait()
        yield "\n[Done — exit code: " + str(proc.returncode) + "]\n"
    return Response(generate(), mimetype="text/plain", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

@app.route("/api/system/upgrade/stream", methods=["POST"])
def system_upgrade_stream():
    def generate():
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        proc = subprocess.Popen(
            ["sudo", "apt-get", "upgrade", "-y"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env
        )
        for line in proc.stdout:
            yield line
        proc.wait()
        yield "\n[Done — exit code: " + str(proc.returncode) + "]\n"
    return Response(generate(), mimetype="text/plain", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

@app.route("/api/system/update", methods=["POST"])
def system_update():
    try:
        r = subprocess.run(["sudo","apt-get","update","-y"],capture_output=True,text=True,timeout=120)
        return jsonify({"ok": True, "output": r.stdout[-3000:]})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/system/upgrade", methods=["POST"])
def system_upgrade():
    try:
        r = subprocess.run(["sudo","apt-get","upgrade","-y"],capture_output=True,text=True,timeout=300)
        return jsonify({"ok": True, "output": r.stdout[-3000:]})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/system/trash", methods=["POST"])
def empty_trash():
    try:
        tp = os.path.expanduser("~/.local/share/Trash")
        if os.path.exists(tp): shutil.rmtree(tp)
        os.makedirs(tp+"/files", exist_ok=True); os.makedirs(tp+"/info", exist_ok=True)
        # Log it
        with open(LOG_FILE, "a") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — SYSTEM — Trash emptied\n")
        return jsonify({"ok": True})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/system/reboot", methods=["POST"])
def reboot():
    try: subprocess.Popen(["sudo","reboot"]); return jsonify({"ok": True})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/system/shutdown", methods=["POST"])
def shutdown():
    try: subprocess.Popen(["sudo","shutdown","-h","now"]); return jsonify({"ok": True})
    except Exception as e: return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/system/hostname", methods=["POST"])
def set_hostname():
    hostname = request.json.get("hostname","").strip()
    if not hostname or not re.match(r'^[a-zA-Z0-9-]{1,63}$', hostname):
        return jsonify({"ok":False,"error":"Invalid hostname"}), 400
    try:
        r1 = subprocess.run(["sudo","hostnamectl","set-hostname",hostname],capture_output=True,text=True)
        subprocess.run(["sudo","sed","-i",f"s/127.0.1.1.*/127.0.1.1\t{hostname}/","/etc/hosts"],capture_output=True)
        return jsonify({"ok": r1.returncode==0, "output": r1.stdout+r1.stderr, "hostname": hostname})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500


@app.route("/api/system/service/<string:name>")
def service_status(name):
    try:
        r  = subprocess.run(["systemctl","is-active",name],capture_output=True,text=True)
        r2 = subprocess.run(["systemctl","is-failed",name],capture_output=True,text=True)
        return jsonify({"ok":True,"active":r.stdout.strip()=="active","failed":r2.stdout.strip()=="failed","status":r.stdout.strip()})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/system/service/<string:name>/<string:action>", methods=["POST"])
def service_action(name, action):
    if action not in ["start","stop","restart","enable","disable"]:
        return jsonify({"error":"not allowed"}), 403
    try: subprocess.run(["sudo","systemctl",action,name],check=True); return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/system/services")
def list_services():
    try:
        r = subprocess.run(["systemctl","list-units","--type=service","--no-pager","--no-legend",
                            "--all"],capture_output=True,text=True)
        svcs = []
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                svcs.append({"name":parts[0].replace(".service",""),"load":parts[1],"active":parts[2],"sub":parts[3]})
        return jsonify({"ok":True,"services":svcs[:60]})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

# ── Diagnostics ───────────────────────────────────────────────────────────────
@app.route("/api/diag/ping", methods=["POST"])
def diag_ping():
    host = request.json.get("host","")
    if not host: return jsonify({"ok":False,"error":"No host"}), 400
    try:
        r = subprocess.run(["ping","-c","4","-W","2",host],capture_output=True,text=True,timeout=15)
        return jsonify({"ok":True,"output":r.stdout+r.stderr,"success":r.returncode==0})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/diag/traceroute", methods=["POST"])
def diag_traceroute():
    host = request.json.get("host","")
    if not host: return jsonify({"ok":False,"error":"No host"}), 400
    try:
        r = subprocess.run(["traceroute","-m","15","-w","2",host],capture_output=True,text=True,timeout=60)
        return jsonify({"ok":True,"output":r.stdout+r.stderr})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/diag/portcheck", methods=["POST"])
def diag_portcheck():
    d = request.json; host = d.get("host",""); port = int(d.get("port",80))
    try:
        with _socket.create_connection((host, port), timeout=3): open_ = True
    except: open_ = False
    return jsonify({"ok":True,"host":host,"port":port,"open":open_})

@app.route("/api/diag/processes")
def diag_processes():
    try:
        r = subprocess.run(["ps","aux","--sort=-%cpu"],capture_output=True,text=True)
        lines = r.stdout.splitlines()[1:26]
        procs = []
        for line in lines:
            parts = line.split(None, 10)
            if len(parts) >= 11:
                procs.append({"pid":parts[1],"cpu":parts[2],"mem":parts[3],"user":parts[0],"cmd":parts[10][:60]})
        return jsonify({"ok":True,"processes":procs})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/diag/kill", methods=["POST"])
def diag_kill():
    pid = request.json.get("pid","")
    if not pid: return jsonify({"ok":False,"error":"No PID"}), 400
    try: subprocess.run(["kill",str(pid)],check=True); return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/diag/hardware")
def diag_hardware():
    try: usb = subprocess.run(["lsusb"],capture_output=True,text=True).stdout.strip()
    except: usb = "lsusb not available"
    try: i2c = subprocess.run(["i2cdetect","-y","1"],capture_output=True,text=True).stdout.strip()
    except: i2c = "i2cdetect not available (install i2c-tools)"
    try:
        gpio_out = []
        for p in Path("/sys/class/gpio").glob("gpio*"):
            try:
                direction = (p/"direction").read_text().strip()
                value     = (p/"value").read_text().strip()
                gpio_out.append(f"{p.name}: {direction} = {value}")
            except: pass
        gpio = "\n".join(gpio_out) if gpio_out else "No exported GPIO pins"
    except: gpio = "GPIO info unavailable"
    try: vcgencmd = subprocess.run(["vcgencmd","get_throttled"],capture_output=True,text=True).stdout.strip()
    except: vcgencmd = "vcgencmd not available"
    return jsonify({"ok":True,"usb":usb,"i2c":i2c,"gpio":gpio,"throttle":vcgencmd})

# ── Logs ──────────────────────────────────────────────────────────────────────
@app.route("/api/logs/services")
def logs_list_services():
    try:
        r = subprocess.run(["systemctl","list-units","--type=service","--no-pager","--no-legend","--state=loaded"],
                           capture_output=True,text=True)
        names = []
        for line in r.stdout.splitlines():
            parts = line.split()
            if parts: names.append(parts[0].replace(".service",""))
        return jsonify({"ok":True,"services":sorted(names)})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/logs/service")
def logs_service():
    name = request.args.get("name","")
    lines = int(request.args.get("lines","100"))
    if not name: return jsonify({"ok":False,"error":"No service name"}), 400
    try:
        r = subprocess.run(["journalctl","-u",name,"-n",str(lines),"--no-pager","--output=short-iso"],
                           capture_output=True,text=True)
        return jsonify({"ok":True,"output":r.stdout,"lines":r.stdout.splitlines()})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/logs/file")
def logs_file():
    path = request.args.get("path","")
    lines = int(request.args.get("lines","100"))
    if not path: return jsonify({"ok":False,"error":"No path"}), 400
    try:
        r = subprocess.run(["tail","-n",str(lines),path],capture_output=True,text=True)
        return jsonify({"ok":True,"lines":r.stdout.splitlines()})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/logs/varlog")
def logs_varlog():
    try:
        files = []
        for root,_,fs in os.walk("/var/log"):
            for f in fs:
                fp = os.path.join(root,f)
                try: files.append(fp)
                except: pass
        return jsonify({"ok":True,"files":sorted(files)[:80]})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500


# ── Connection status ─────────────────────────────────────────────────────────
@app.route("/api/network/status")
def network_status():
    try:
        eth_up = False
        wlan_ip = None
        eth_ip = None
        r = subprocess.run(["ip","-o","-4","addr","show"],capture_output=True,text=True)
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                iface = parts[1]
                ip    = parts[3].split("/")[0]
                state = Path(f"/sys/class/net/{iface}/operstate").read_text().strip()
                if iface.startswith("eth") and state == "up":
                    eth_up = True
                    eth_ip = ip
                if iface.startswith("wlan"):
                    wlan_ip = ip
        return jsonify({"ok":True,"eth_up":eth_up,"eth_ip":eth_ip,"wlan_ip":wlan_ip})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 500

# ── WiFi ──────────────────────────────────────────────────────────────────────
@app.route("/api/wifi/scan")
def wifi_scan():
    try:
        # Ensure wlan0 is up and unblocked before scanning
        subprocess.run(["sudo","rfkill","unblock","wifi"], capture_output=True, timeout=5)
        subprocess.run(["sudo","ip","link","set","wlan0","up"], capture_output=True, timeout=5)
        time.sleep(1)
        r = subprocess.run(["sudo","iwlist","wlan0","scan"],capture_output=True,text=True,timeout=15)
        networks = []
        current = {}
        for line in r.stdout.splitlines():
            line = line.strip()
            if "Cell" in line and "Address" in line:
                if current: networks.append(current)
                current = {"mac":line.split("Address:")[-1].strip()}
            elif "ESSID:" in line:
                current["ssid"] = line.split("ESSID:")[-1].strip().strip('"')
            elif "Signal level" in line:
                try:
                    raw = line.split("Signal level=")[-1].split(" ")[0]
                    current["signal"] = raw
                    current["signal_int"] = int(raw.split("/")[0]) if "/" in raw else int(raw)
                except: current["signal_int"] = -999
            elif "Encryption key:" in line:
                current["encrypted"] = "on" in line
        if current: networks.append(current)
        networks = [n for n in networks if n.get("ssid")]
        networks.sort(key=lambda x: x.get("signal_int", -999), reverse=True)
        return jsonify({"ok":True,"networks":networks})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/wifi/current")
def wifi_current():
    try:
        r = subprocess.run(["iwgetid","wlan0","--raw"],capture_output=True,text=True)
        ssid = r.stdout.strip()
        r2 = subprocess.run(["ip","addr","show","wlan0"],capture_output=True,text=True)
        ip = re.search(r'inet (\d+\.\d+\.\d+\.\d+)',r2.stdout)
        return jsonify({"ok":True,"ssid":ssid,"ip":ip.group(1) if ip else "--"})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/wifi/connect", methods=["POST"])
def wifi_connect():
    d = request.json; ssid = d.get("ssid",""); password = d.get("password","")
    if not ssid: return jsonify({"ok":False,"error":"SSID required"}), 400
    try:
        if password:
            # Use wpa_passphrase to generate proper hashed PSK
            r = subprocess.run(["wpa_passphrase", ssid, password], capture_output=True, text=True)
            if r.returncode != 0:
                return jsonify({"ok":False,"error":f"wpa_passphrase failed: {r.stderr}"}), 500
            # wpa_passphrase output is already a valid network block - strip the #psk comment line
            wpa_block = "\n" + "\n".join(l for l in r.stdout.splitlines() if "#psk" not in l) + "\n"
        else:
            wpa_block = f'\nnetwork={{\n    ssid="{ssid}"\n    key_mgmt=NONE\n}}\n'

        proc = subprocess.run(
            ["sudo", "tee", "-a", "/etc/wpa_supplicant/wpa_supplicant.conf"],
            input=wpa_block, capture_output=True, text=True
        )
        if proc.returncode != 0:
            return jsonify({"ok":False,"error":f"Failed to write config: {proc.stderr}"}), 500
        subprocess.run(["sudo","wpa_cli","-i","wlan0","reconfigure"],capture_output=True)
        time.sleep(3)
        r = subprocess.run(["iwgetid","wlan0","--raw"],capture_output=True,text=True)
        return jsonify({"ok":True,"connected":r.stdout.strip()==ssid,"ssid":ssid})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/wifi/clear", methods=["POST"])
def wifi_clear():
    try:
        # Read current file to preserve the header lines
        with open("/etc/wpa_supplicant/wpa_supplicant.conf") as f:
            lines = f.readlines()
        # Keep only lines before the first network={ block
        header = []
        for line in lines:
            if line.strip().startswith("network={"):
                break
            header.append(line)
        # Ensure header has required lines
        header_str = "".join(header).strip()
        if not header_str:
            # Detect country from existing config or default to US
            _country = "US"
            try:
                with open("/etc/wpa_supplicant/wpa_supplicant.conf") as _f:
                    for _l in _f:
                        if _l.startswith("country="):
                            _country = _l.split("=",1)[1].strip(); break
            except: pass
            header_str = f"country={_country}\nctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\nupdate_config=1"
        proc = subprocess.run(
            ["sudo", "tee", "/etc/wpa_supplicant/wpa_supplicant.conf"],
            input=header_str + "\n", capture_output=True, text=True
        )
        if proc.returncode != 0:
            return jsonify({"ok":False,"error":proc.stderr}), 500
        subprocess.run(["sudo","wpa_cli","-i","wlan0","reconfigure"],capture_output=True)
        return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/wifi/disconnect", methods=["POST"])
def wifi_disconnect():
    try:
        subprocess.run(["sudo","ip","link","set","wlan0","down"],capture_output=True)
        time.sleep(1)
        subprocess.run(["sudo","ip","link","set","wlan0","up"],capture_output=True)
        return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

# ── Cron ──────────────────────────────────────────────────────────────────────
@app.route("/api/cron/list")
def cron_list():
    try:
        r = subprocess.run(["crontab","-l"],capture_output=True,text=True)
        lines = [l for l in r.stdout.splitlines() if l.strip() and not l.startswith("#")]
        return jsonify({"ok":True,"jobs":lines,"raw":r.stdout})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/cron/save", methods=["POST"])
def cron_save():
    raw = request.json.get("raw","")
    try:
        proc = subprocess.run(["crontab","-"],input=raw,capture_output=True,text=True)
        if proc.returncode != 0: return jsonify({"ok":False,"error":proc.stderr}), 400
        return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

# ── Env editor ────────────────────────────────────────────────────────────────
@app.route("/api/env/read")
def env_read():
    path = request.args.get("path","/home/sysadmin/.env")
    try:
        if not os.path.exists(path): return jsonify({"ok":True,"vars":[],"raw":""})
        with open(path) as f: raw = f.read()
        vars_ = []
        for line in raw.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k,v = line.split("=",1)
                vars_.append({"key":k.strip(),"value":v.strip().strip('"').strip("'")})
        return jsonify({"ok":True,"vars":vars_,"raw":raw})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/env/save", methods=["POST"])
def env_save():
    d = request.json; path = d.get("path","/home/sysadmin/.env"); raw = d.get("raw","")
    try:
        with open(path,"w") as f: f.write(raw)
        return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

# ── Script runner ─────────────────────────────────────────────────────────────
def load_scripts():
    if os.path.exists(SCRIPTS_FILE):
        with open(SCRIPTS_FILE) as f: return json.load(f)
    return []

def save_scripts(scripts):
    with open(SCRIPTS_FILE,"w") as f: json.dump(scripts, f, indent=2)

@app.route("/api/scripts/list")
def scripts_list():
    return jsonify({"ok":True,"scripts":load_scripts()})

@app.route("/api/scripts/save", methods=["POST"])
def scripts_save():
    scripts = request.json.get("scripts",[])
    save_scripts(scripts)
    return jsonify({"ok":True})

@app.route("/api/scripts/run", methods=["POST"])
def scripts_run():
    cmd = request.json.get("cmd","")
    if not cmd: return jsonify({"ok":False,"error":"No command"}), 400
    try:
        r = subprocess.run(cmd,shell=True,capture_output=True,text=True,timeout=30)
        return jsonify({"ok":True,"stdout":r.stdout[-3000:],"stderr":r.stderr[-1000:],"returncode":r.returncode})
    except subprocess.TimeoutExpired:
        return jsonify({"ok":False,"error":"Command timed out after 30s"}), 500
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

# ── Git ───────────────────────────────────────────────────────────────────────
@app.route("/api/git/status")
def git_status():
    path = request.args.get("path","/home")
    try:
        def run(cmd): return subprocess.run(cmd,capture_output=True,text=True,cwd=path)
        branch = run(["git","branch","--show-current"]).stdout.strip()
        status = run(["git","status","--short"]).stdout.strip()
        log    = run(["git","log","--oneline","-10"]).stdout.strip()
        remote = run(["git","remote","-v"]).stdout.strip()
        if not branch and not log: return jsonify({"ok":False,"error":"Not a git repository"})
        return jsonify({"ok":True,"branch":branch,"status":status,"log":log,"remote":remote,"path":path})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/git/action", methods=["POST"])
def git_action():
    d = request.json; path = d.get("path","/home/sysadmin"); action = d.get("action","")
    allowed = ["pull","fetch","status","log","reset"]
    if action not in allowed: return jsonify({"ok":False,"error":"Not allowed"}), 403
    try:
        if action == "reset":
            r = subprocess.run(["git","reset","--hard","HEAD"],capture_output=True,text=True,cwd=path)
        elif action == "log":
            r = subprocess.run(["git","log","--oneline","-20"],capture_output=True,text=True,cwd=path)
        else:
            r = subprocess.run(["git",action],capture_output=True,text=True,cwd=path,timeout=30)
        return jsonify({"ok":True,"output":r.stdout+r.stderr,"returncode":r.returncode})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

# ── File browser ──────────────────────────────────────────────────────────────
@app.route("/api/files/list")
def files_list():
    path = request.args.get("path","/home")
    try:
        path = os.path.realpath(path); entries = []
        with os.scandir(path) as it:
            for e in sorted(it, key=lambda x:(not x.is_dir(follow_symlinks=False),x.name.lower())):
                try:
                    st = e.stat(follow_symlinks=False)
                    entries.append({"name":e.name,"path":os.path.join(path,e.name),
                                    "is_dir":e.is_dir(follow_symlinks=False),"is_link":e.is_symlink(),
                                    "size":st.st_size,"perms":stat_mod.filemode(st.st_mode),
                                    "modified":datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
                except: continue
        parent = str(os.path.dirname(path)) if path != "/" else None
        return jsonify({"ok":True,"path":path,"parent":parent,"entries":entries})
    except PermissionError: return jsonify({"ok":False,"error":"Permission denied"}), 403
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/files/download")
def files_download():
    path = request.args.get("path","")
    try:
        path = os.path.realpath(path)
        if not os.path.isfile(path): return jsonify({"error":"Not a file"}), 400
        return send_file(path, as_attachment=True, download_name=os.path.basename(path))
    except PermissionError: return jsonify({"error":"Permission denied"}), 403
    except Exception as e: return jsonify({"error":str(e)}), 500

@app.route("/api/files/upload", methods=["POST"])
def files_upload():
    dest = request.args.get("path","/home/sysadmin")
    try:
        dest = os.path.realpath(dest)
        uploaded = []
        for f in request.files.getlist("files"):
            fn = os.path.basename(f.filename)
            if fn: f.save(os.path.join(dest,fn)); uploaded.append(fn)
        return jsonify({"ok":True,"uploaded":uploaded})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/files/mkdir", methods=["POST"])
def files_mkdir():
    d = request.json
    try:
        target = os.path.realpath(os.path.join(d.get("path",""),d.get("name","").strip()))
        os.makedirs(target,exist_ok=True); return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/files/rename", methods=["POST"])
def files_rename():
    d = request.json
    try:
        src = os.path.realpath(d.get("path",""))
        os.rename(src, os.path.join(os.path.dirname(src),d.get("name","").strip()))
        return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

@app.route("/api/files/delete", methods=["POST"])
def files_delete():
    path = request.json.get("path","")
    try:
        path = os.path.realpath(path)
        if path in ["/","/home","/etc","/usr","/bin","/sbin","/boot"]:
            return jsonify({"ok":False,"error":"Refusing to delete system path"}), 403
        shutil.rmtree(path) if os.path.isdir(path) and not os.path.islink(path) else os.remove(path)
        return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

# ── WebSocket terminal & log tail ─────────────────────────────────────────────
@sock.route("/ws/terminal")
def terminal(ws):
    import struct, termios, fcntl, signal
    master_fd, slave_fd = pty.openpty()
    # Set initial terminal size (80x24 default)
    winsize = struct.pack('HHHH', 24, 80, 0, 0)
    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
    proc = subprocess.Popen(["/bin/bash", "--login"],stdin=slave_fd,stdout=slave_fd,stderr=slave_fd,
                             close_fds=True,env={**os.environ,"TERM":"xterm-256color","COLUMNS":"80","LINES":"24"})
    os.close(slave_fd)
    # Lines to suppress from PTY output
    _suppress = [b'cannot set terminal process group', b'no job control in this shell']

    def reader():
        while proc.poll() is None:
            r,_,_ = select.select([master_fd],[],[],0.1)
            if r:
                try:
                    data = os.read(master_fd, 4096)
                    # Filter suppressed lines
                    lines = data.split(b'\n')
                    filtered = [l for l in lines if not any(s in l for s in _suppress)]
                    clean = b'\n'.join(filtered)
                    if clean.strip():
                        ws.send(clean.decode('utf-8', errors='replace'))
                except: break
    threading.Thread(target=reader,daemon=True).start()
    try:
        while proc.poll() is None:
            msg = ws.receive(timeout=1)
            if msg:
                try:
                    import json as _json
                    d = _json.loads(msg)
                    if d.get("type") == "resize":
                        cols = max(int(d.get("cols", 80)), 10)
                        rows = max(int(d.get("rows", 24)), 5)
                        winsize = struct.pack("HHHH", rows, cols, 0, 0)
                        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                        os.kill(proc.pid, signal.SIGWINCH)
                except (ValueError, KeyError):
                    os.write(master_fd, msg.encode("utf-8"))
    except: pass
    finally: proc.terminate(); os.close(master_fd)

@sock.route("/ws/logs")
def ws_logs(ws):
    data = ws.receive(timeout=5)
    if not data: return
    try: cfg = json.loads(data)
    except: return
    svc  = cfg.get("service","")
    path = cfg.get("path","")
    if svc:
        cmd = ["journalctl","-u",svc,"-f","-n","50","--output=short-iso","--no-pager"]
    elif path:
        cmd = ["tail","-f","-n","50",path]
    else: return
    try:
        proc = subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        def reader():
            for line in proc.stdout:
                try: ws.send(line.rstrip())
                except: break
            proc.wait()
        threading.Thread(target=reader,daemon=True).start()
        while True:
            msg = ws.receive(timeout=2)
            if msg == "stop": break
    except: pass
    finally:
        try: proc.terminate()
        except: pass


@app.route("/api/branding", methods=["GET","POST"])
def branding():
    try:
        if request.method == "POST":
            d = request.json
            with open(BRANDING_FILE,"w") as f: json.dump(d,f)
            return jsonify({"ok":True})
        else:
            if os.path.exists(BRANDING_FILE):
                with open(BRANDING_FILE) as f: return jsonify({"ok":True,**json.load(f)})
            return jsonify({"ok":True,"title":"PiL0t","subtitle":"PRINT SERVER"})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
