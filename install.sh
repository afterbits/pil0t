#!/bin/bash
# PiL0t Installer v1.2.0
# Usage: sudo bash /tmp/pil0t/install.sh
# Safe to re-run — preserves all existing data and config files

set -uo pipefail

# ── Constants ─────────────────────────────────────────────────────────────────
LOCAL_SRC="/tmp/pil0t"
INSTALL_DIR="/etc/pil0t"
DATA_DIR="/etc/pil0t/data"
STATIC_DIR="/etc/pil0t/static"
VERSION="1.2.0"
PYTHON_BIN="/usr/bin/python3"

# ── Detect service user ───────────────────────────────────────────────────────
if   [ -n "${SUDO_USER:-}" ];                          then SERVICE_USER="$SUDO_USER"
elif [ -n "${USER:-}" ] && [ "$USER" != "root" ];      then SERVICE_USER="$USER"
else SERVICE_USER=$(logname 2>/dev/null || echo "pi"); fi

# ── Colours & helpers ─────────────────────────────────────────────────────────
GREEN='\033[0;32m'; DIM='\033[2m'; RED='\033[0;31m'
BOLD='\033[1m'; AMBER='\033[0;33m'; CYAN='\033[0;36m'; NC='\033[0m'
header()  { echo -e "\n${BOLD}${GREEN}▶ $1${NC}"; }
ok()      { echo -e "  ${GREEN}✓${NC} $1"; }
info()    { echo -e "  ${DIM}· $1${NC}"; }
warn()    { echo -e "  ${AMBER}⚠${NC} $1"; }
err()     { echo -e "\n  ${RED}✗ ERROR: $1${NC}\n"; exit 1; }
chk()     { echo -e "  ${GREEN}✓${NC} ${1}"; }      # final checklist pass
fail_chk(){ echo -e "  ${RED}✗${NC} ${1}"; }        # final checklist fail

# ── Banner ────────────────────────────────────────────────────────────────────
clear
echo -e "${GREEN}"
cat << 'BANNER'
  ____  _ _     ___  _
 |  _ \(_) |   / _ \| |_
 | |_) | | |  | | | | __|
 |  __/| | |__| |_| | |_
 |_|   |_|_____\___/ \__|

BANNER
echo -e "${NC}${DIM}  Raspberry Pi Management & Print Server${NC}"
echo -e "${DIM}  Installer v${VERSION}${NC}\n"

# ── Must run as root ──────────────────────────────────────────────────────────
header "Checking environment"
[ "$EUID" -ne 0 ] && err "Run with sudo:\n  sudo bash $LOCAL_SRC/install.sh"
ok "Running as root (service user: $SERVICE_USER)"

# ── Detect Pi hardware ────────────────────────────────────────────────────────
if grep -qi "raspberry" /proc/cpuinfo 2>/dev/null || \
   grep -qi "raspberry" /etc/os-release 2>/dev/null || \
   [ -f /proc/device-tree/model ]; then
    PI_MODEL=$(cat /proc/device-tree/model 2>/dev/null | tr -d '\0' || echo "Raspberry Pi")
    ok "Hardware: $PI_MODEL"
else
    warn "Raspberry Pi not detected — continuing anyway"
fi

# ── Detect OS ─────────────────────────────────────────────────────────────────
OS_ID=$(. /etc/os-release 2>/dev/null && echo "$ID" || echo "unknown")
OS_VER=$(. /etc/os-release 2>/dev/null && echo "$VERSION_CODENAME" || echo "unknown")
ok "OS: $OS_ID $OS_VER"

# ── Verify source files ───────────────────────────────────────────────────────
header "Verifying source files"
[ ! -d "$LOCAL_SRC" ] && err "Source directory $LOCAL_SRC not found.\n  Place all PiL0t files there first."

REQUIRED=(
    "install.sh"
    "app.py"
    "print_sku.py"
    "static/index.html"
    "static/system.html"
    "static/filebrowser.html"
    "static/login.html"
    "static/wizard.html"
)
ALL_OK=true
for f in "${REQUIRED[@]}"; do
    if [ -f "$LOCAL_SRC/$f" ]; then
        ok "$f"
    else
        fail_chk "$f — MISSING"
        ALL_OK=false
    fi
done
[ "$ALL_OK" = false ] && err "One or more required files are missing from $LOCAL_SRC"

# ── Bootstrap: ensure apt is usable ──────────────────────────────────────────
header "Bootstrapping system"

# Kill any stuck dpkg/apt locks
info "Checking for package manager locks..."
for lockfile in /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/cache/apt/archives/lock; do
    if fuser "$lockfile" >/dev/null 2>&1; then
        warn "Releasing lock: $lockfile"
        fuser -k "$lockfile" 2>/dev/null || true
        sleep 2
    fi
done

# Fix any broken dpkg state
dpkg --configure -a 2>/dev/null || true

# Ensure apt-get itself is available
if ! command -v apt-get >/dev/null 2>&1; then
    err "apt-get not found. This installer requires a Debian-based system."
fi
ok "Package manager ready"

# ── Update package lists ──────────────────────────────────────────────────────
header "Updating package lists"
apt-get update -y -qq 2>&1 | tail -1 || warn "Package list update had warnings — continuing"
ok "Package lists updated"

# ── System packages ───────────────────────────────────────────────────────────
header "Installing system dependencies"

pkg_install() {
    local pkg="$1"
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
        ok "$pkg (already installed)"
        return 0
    fi
    info "Installing $pkg..."
    if apt-get install -y -qq "$pkg" 2>/dev/null; then
        ok "$pkg"
    else
        warn "$pkg failed via apt — will attempt alternative"
        return 1
    fi
}

# Core system packages required for PiL0t
SYSTEM_PKGS=(
    python3
    python3-pip
    python3-venv
    curl
    wget
    git
    tar
    gzip
    unzip
    net-tools
    iproute2
    wireless-tools
    wpasupplicant
    rfkill
    iw
    usbutils
    procps
    ca-certificates
    lsb-release
    sudo
)

for pkg in "${SYSTEM_PKGS[@]}"; do
    pkg_install "$pkg" || true
done

# Python flask via apt (preferred — avoids pip conflicts)
pkg_install python3-flask || true

# ── Python packages via pip ───────────────────────────────────────────────────
header "Installing Python dependencies"

# Detect pip break-system-packages flag (required on Python 3.11+)
BREAK_FLAG=""
if python3 -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
    BREAK_FLAG="--break-system-packages"
fi

pip_install() {
    local pkg="$1"
    local import_name="${2:-$1}"
    import_name=$(echo "$import_name" | sed 's/-/_/g')
    if python3 -c "import $import_name" 2>/dev/null; then
        ok "$pkg (already installed)"
        return 0
    fi
    info "Installing $pkg via pip..."
    if pip3 install "$pkg" $BREAK_FLAG -q 2>/dev/null; then
        ok "$pkg"
    elif pip3 install "$pkg" -q 2>/dev/null; then
        ok "$pkg"
    else
        warn "$pkg pip install failed"
        return 1
    fi
}

pip_install flask         flask
pip_install flask-cors    flask_cors
pip_install flask-sock    flask_sock
pip_install evdev         evdev

# Verify all Python imports work
header "Verifying Python dependencies"
PY_IMPORTS=(flask flask_cors flask_sock evdev)
for mod in "${PY_IMPORTS[@]}"; do
    if python3 -c "import $mod" 2>/dev/null; then
        ok "import $mod"
    else
        warn "import $mod — FAILED (may affect functionality)"
    fi
done

# ── Directory structure ───────────────────────────────────────────────────────
header "Creating directory structure"
mkdir -p "$INSTALL_DIR" "$DATA_DIR" "$STATIC_DIR"
ok "$INSTALL_DIR"
ok "$DATA_DIR"
ok "$STATIC_DIR"

# ── Install application files ─────────────────────────────────────────────────
header "Installing PiL0t application files"

cp_file() {
    local src="$1" dest="$2" label="$3"
    info "Installing $label..."
    cp "$src" "$dest" && ok "$label" || err "Failed to install $label"
}

cp_file "$LOCAL_SRC/app.py"                  "$INSTALL_DIR/app.py"              "app.py"
cp_file "$LOCAL_SRC/print_sku.py"            "$INSTALL_DIR/print_sku.py"        "print_sku.py"
cp_file "$LOCAL_SRC/static/index.html"       "$STATIC_DIR/index.html"           "static/index.html"
cp_file "$LOCAL_SRC/static/system.html"      "$STATIC_DIR/system.html"          "static/system.html"
cp_file "$LOCAL_SRC/static/filebrowser.html" "$STATIC_DIR/filebrowser.html"     "static/filebrowser.html"
cp_file "$LOCAL_SRC/static/login.html"       "$STATIC_DIR/login.html"           "static/login.html"
cp_file "$LOCAL_SRC/static/wizard.html"      "$STATIC_DIR/wizard.html"          "static/wizard.html"

echo "$VERSION" > "$INSTALL_DIR/version.txt"
ok "version.txt ($VERSION)"

# ── Default configuration (never overwrites existing data) ────────────────────
header "Setting up configuration"

write_default() {
    local file="$1" content="$2" label="$3"
    if [ ! -f "$file" ]; then
        printf '%s' "$content" > "$file"
        ok "$label (default)"
    else
        ok "$label (existing — preserved)"
    fi
}

write_default "$DATA_DIR/printer_config.json" \
    '{"printer_ip":"10.10.10.249","printer_port":9100,"mode":"network","usb_device":"/dev/usb/lp0"}' \
    "printer_config.json"

write_default "$DATA_DIR/current_sku.txt" \
    "1490" \
    "current_sku.txt"

write_default "$DATA_DIR/branding.json" \
    '{"title":"PiL0t","subtitle":"PRINT SERVER"}' \
    "branding.json"

write_default "$DATA_DIR/auth_config.json" \
    '{"guest_print":true,"protected":{"system":true,"files":true,"terminal":true}}' \
    "auth_config.json"

write_default "$DATA_DIR/app_users.json" \
    '[{"username":"admin","password":"8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918","role":"admin"}]' \
    "app_users.json"

write_default "$DATA_DIR/device_config.json" \
    '{"zebra_mode":true}' \
    "device_config.json"

[ ! -f "$DATA_DIR/sku_log.txt" ] && touch "$DATA_DIR/sku_log.txt"
ok "sku_log.txt"

# ── Permissions ───────────────────────────────────────────────────────────────
header "Setting permissions"

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR" 2>/dev/null || \
    warn "Could not set ownership on $INSTALL_DIR"
chmod -R 755 "$INSTALL_DIR"
chmod 700 "$DATA_DIR"
chmod 600 "$DATA_DIR/app_users.json"
[ -f "$DATA_DIR/.flask_secret" ] && chmod 600 "$DATA_DIR/.flask_secret"
ok "File permissions set (owner: $SERVICE_USER)"

# Add to lp group for USB printer access
if getent group lp > /dev/null 2>&1; then
    usermod -aG lp "$SERVICE_USER" 2>/dev/null && \
        ok "$SERVICE_USER added to lp group (USB printer)" || \
        warn "Could not add $SERVICE_USER to lp group"
else
    warn "lp group not found — USB printing may need manual permission setup"
fi

# ── Sudoers ───────────────────────────────────────────────────────────────────
header "Configuring sudoers"
cat > /etc/sudoers.d/pil0t << SUDOERS
$SERVICE_USER ALL=(ALL) NOPASSWD: /usr/bin/apt-get, /sbin/reboot, /sbin/shutdown, /bin/systemctl, /usr/bin/systemctl, /usr/bin/sysctl, /usr/sbin/iwlist, /usr/bin/ip, /sbin/ip, /bin/ip, /bin/cp, /bin/chmod, /usr/sbin/wpa_cli, /usr/bin/hostnamectl, /bin/sed, /usr/bin/tee, /usr/bin/rfkill, /sbin/iptables, /usr/sbin/wpa_supplicant, /usr/sbin/dhcpcd, /sbin/dhclient, /usr/bin/dhclient, /usr/bin/nmcli, /usr/bin/killall, /usr/bin/iw
SUDOERS
chmod 440 /etc/sudoers.d/pil0t
# Validate sudoers file
visudo -cf /etc/sudoers.d/pil0t 2>/dev/null && ok "Sudoers configured for $SERVICE_USER" || warn "Sudoers validation warning — check /etc/sudoers.d/pil0t"

# ── WiFi: ensure wpa_supplicant.conf exists ───────────────────────────────────
header "Configuring WiFi"
WPA_CONF="/etc/wpa_supplicant/wpa_supplicant.conf"
if [ ! -f "$WPA_CONF" ]; then
    COUNTRY=$(raspi-config nonint get_wifi_country 2>/dev/null || echo "US")
    cat > "$WPA_CONF" << WPAEOF
country=${COUNTRY}
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
WPAEOF
    chmod 600 "$WPA_CONF"
    ok "wpa_supplicant.conf created (country: ${COUNTRY})"
else
    ok "wpa_supplicant.conf (existing)"
fi

# Ensure rfkill is unblocked
rfkill unblock wifi 2>/dev/null && ok "WiFi radio unblocked" || true
ip link set wlan0 up 2>/dev/null || true

# ── Systemd services ──────────────────────────────────────────────────────────
header "Installing systemd services"

# pil0t-web — Flask web application
cat > /etc/systemd/system/pil0t-web.service << SERVICE
[Unit]
Description=PiL0t Web UI
After=network.target
Wants=network.target

[Service]
ExecStart=${PYTHON_BIN} /etc/pil0t/app.py
WorkingDirectory=/etc/pil0t
StandardOutput=journal
StandardError=journal
Restart=always
RestartSec=5
User=${SERVICE_USER}
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICE
ok "pil0t-web.service written"

# pil0t-tracker — USB keypad listener
cat > /etc/systemd/system/pil0t-tracker.service << SERVICE
[Unit]
Description=PiL0t Keypad Tracker
After=network.target

[Service]
ExecStart=${PYTHON_BIN} /etc/pil0t/print_sku.py
WorkingDirectory=/etc/pil0t
StandardOutput=journal
StandardError=journal
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
SERVICE
ok "pil0t-tracker.service written"

# Reload, enable, and start
systemctl daemon-reload
ok "systemd daemon reloaded"

systemctl enable pil0t-web 2>/dev/null  && ok "pil0t-web enabled (start on boot)"
systemctl enable pil0t-tracker 2>/dev/null && ok "pil0t-tracker enabled (start on boot)"

# ── Network boot script ───────────────────────────────────────────────────────
header "Installing network boot script"
cat > /etc/network/if-up.d/pil0t-network << 'NETSCRIPT'
#!/bin/bash
LOG="/var/log/pil0t-network.log"
echo "$(date): PiL0t network startup" >> $LOG
rfkill unblock wifi 2>/dev/null
ip link set wlan0 up 2>/dev/null
sleep 1
if ! pgrep -x wpa_supplicant > /dev/null; then
    wpa_supplicant -B -i wlan0 -c /etc/wpa_supplicant/wpa_supplicant.conf 2>> $LOG
    sleep 5
else
    wpa_cli -i wlan0 reconfigure 2>> $LOG
    sleep 5
fi
get_ip() { command -v dhcpcd &>/dev/null && dhcpcd "$1" 2>>$LOG || dhclient "$1" 2>>$LOG; }
SSID=$(iwgetid wlan0 --raw 2>/dev/null)
if [ -n "$SSID" ]; then
    echo "$(date): WiFi connected: $SSID" >> $LOG
    get_ip wlan0
else
    echo "$(date): WiFi not available, using eth0 DHCP" >> $LOG
    ip link set eth0 up 2>/dev/null
    get_ip eth0
fi
echo "$(date): eth0=$(ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet )\S+') wlan0=$(ip -4 addr show wlan0 2>/dev/null | grep -oP '(?<=inet )\S+')" >> $LOG
NETSCRIPT
chmod +x /etc/network/if-up.d/pil0t-network
ok "Network boot script installed"

# ── Start and verify services ─────────────────────────────────────────────────
header "Starting services"

# Start web service
systemctl restart pil0t-web 2>/dev/null
sleep 3
if systemctl is-active --quiet pil0t-web; then
    ok "pil0t-web is running"
else
    warn "pil0t-web failed to start — attempting recovery..."
    journalctl -u pil0t-web -n 5 --no-pager 2>/dev/null || true
    systemctl start pil0t-web 2>/dev/null
    sleep 3
    if systemctl is-active --quiet pil0t-web; then
        ok "pil0t-web recovered and is running"
    else
        warn "pil0t-web still not running — check: sudo journalctl -u pil0t-web -n 20"
    fi
fi

# Start tracker (non-fatal — keypad may not be plugged in)
systemctl restart pil0t-tracker 2>/dev/null || true
sleep 1
if systemctl is-active --quiet pil0t-tracker; then
    ok "pil0t-tracker is running (keypad detected)"
else
    warn "pil0t-tracker not running (plug in USB keypad then: sudo systemctl start pil0t-tracker)"
fi

# ══════════════════════════════════════════════════════════════════════════════
# ── FINAL VERIFICATION CHECKLIST ─────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}${CYAN}  FINAL VERIFICATION${NC}"
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

FAIL_COUNT=0

run_check() {
    local label="$1"
    local check_cmd="$2"
    local fix_cmd="${3:-}"

    if eval "$check_cmd" > /dev/null 2>&1; then
        chk "$label"
        return 0
    else
        fail_chk "$label"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        if [ -n "$fix_cmd" ]; then
            info "  Fixing: $fix_cmd"
            eval "$fix_cmd" > /dev/null 2>&1 || true
            sleep 2
            if eval "$check_cmd" > /dev/null 2>&1; then
                ok "  Fixed: $label"
                FAIL_COUNT=$((FAIL_COUNT - 1))
            else
                warn "  Could not fix: $label"
            fi
        fi
        return 1
    fi
}

echo -e "  ${DIM}── Python dependencies ──────────────────────────${NC}"
run_check "python3 available"      "command -v python3"
run_check "import flask"           "python3 -c 'import flask'"          "pip3 install flask $BREAK_FLAG -q"
run_check "import flask_cors"      "python3 -c 'import flask_cors'"     "pip3 install flask-cors $BREAK_FLAG -q"
run_check "import flask_sock"      "python3 -c 'import flask_sock'"     "pip3 install flask-sock $BREAK_FLAG -q"
run_check "import evdev"           "python3 -c 'import evdev'"          "pip3 install evdev $BREAK_FLAG -q"
run_check "import hashlib"         "python3 -c 'import hashlib'"
run_check "import socket"          "python3 -c 'import socket'"

echo ""
echo -e "  ${DIM}── Application files ────────────────────────────${NC}"
run_check "app.py"                 "[ -f $INSTALL_DIR/app.py ]"
run_check "print_sku.py"           "[ -f $INSTALL_DIR/print_sku.py ]"
run_check "version.txt"            "[ -f $INSTALL_DIR/version.txt ]"
run_check "static/index.html"      "[ -f $STATIC_DIR/index.html ]"
run_check "static/system.html"     "[ -f $STATIC_DIR/system.html ]"
run_check "static/filebrowser.html" "[ -f $STATIC_DIR/filebrowser.html ]"
run_check "static/login.html"      "[ -f $STATIC_DIR/login.html ]"
run_check "static/wizard.html"     "[ -f $STATIC_DIR/wizard.html ]"

echo ""
echo -e "  ${DIM}── Configuration files ──────────────────────────${NC}"
run_check "printer_config.json"    "[ -f $DATA_DIR/printer_config.json ]"
run_check "app_users.json"         "[ -f $DATA_DIR/app_users.json ]"
run_check "auth_config.json"       "[ -f $DATA_DIR/auth_config.json ]"
run_check "branding.json"          "[ -f $DATA_DIR/branding.json ]"
run_check "device_config.json"     "[ -f $DATA_DIR/device_config.json ]"
run_check "current_sku.txt"        "[ -f $DATA_DIR/current_sku.txt ]"

echo ""
echo -e "  ${DIM}── Permissions ──────────────────────────────────${NC}"
run_check "app.py readable"        "[ -r $INSTALL_DIR/app.py ]"
run_check "data dir accessible"    "[ -r $DATA_DIR ]"
run_check "app_users.json secure"  "[ \$(stat -c %a $DATA_DIR/app_users.json) = '600' ]" \
    "chmod 600 $DATA_DIR/app_users.json"

echo ""
echo -e "  ${DIM}── Systemd services ─────────────────────────────${NC}"
run_check "pil0t-web.service exists"     "[ -f /etc/systemd/system/pil0t-web.service ]"
run_check "pil0t-tracker.service exists" "[ -f /etc/systemd/system/pil0t-tracker.service ]"
run_check "pil0t-web enabled"            "systemctl is-enabled pil0t-web" \
    "systemctl enable pil0t-web"
run_check "pil0t-tracker enabled"        "systemctl is-enabled pil0t-tracker" \
    "systemctl enable pil0t-tracker"
run_check "pil0t-web running"            "systemctl is-active pil0t-web" \
    "systemctl restart pil0t-web && sleep 3"
run_check "pil0t-tracker running" \
    "systemctl is-active pil0t-tracker" \
    "systemctl restart pil0t-tracker 2>/dev/null; sleep 2"

echo ""
echo -e "  ${DIM}── Network & system tools ───────────────────────${NC}"
run_check "sudoers file"           "[ -f /etc/sudoers.d/pil0t ]"
run_check "network boot script"    "[ -x /etc/network/if-up.d/pil0t-network ]"
run_check "wpa_supplicant.conf"    "[ -f /etc/wpa_supplicant/wpa_supplicant.conf ]"
run_check "ip command available"   "command -v ip"
run_check "iwlist available"       "command -v iwlist"
run_check "rfkill available"       "command -v rfkill"

echo ""
echo -e "  ${DIM}── Web service reachability ─────────────────────${NC}"
sleep 2
run_check "port 5000 responding" \
    "curl -sf --max-time 5 http://localhost:5000/api/status" \
    "systemctl restart pil0t-web && sleep 5"

# ── Final result ──────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

IP=$(ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet )\d+\.\d+\.\d+\.\d+' | head -1)
[ -z "$IP" ] && IP=$(ip -4 addr show wlan0 2>/dev/null | grep -oP '(?<=inet )\d+\.\d+\.\d+\.\d+' | head -1)
[ -z "$IP" ] && IP="<your-pi-ip>"

if [ "$FAIL_COUNT" -eq 0 ]; then
    echo -e "${BOLD}${GREEN}  ALL CHECKS PASSED — PiL0t is ready!${NC}"
    echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  Open in browser:  ${BOLD}http://$IP:5000${NC}"
    echo -e "  First time?       ${BOLD}The setup wizard will appear automatically${NC}"
    echo ""
    echo -e "  ${DIM}Logs:     sudo journalctl -u pil0t-web -f${NC}"
    echo -e "  ${DIM}Restart:  sudo systemctl restart pil0t-web${NC}"
else
    echo -e "${BOLD}${AMBER}  $FAIL_COUNT check(s) could not be resolved automatically.${NC}"
    echo -e "${BOLD}${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "  ${AMBER}Review warnings above and run the installer again:${NC}"
    echo -e "  ${BOLD}sudo bash $LOCAL_SRC/install.sh${NC}"
    echo ""
    echo -e "  ${DIM}Logs:  sudo journalctl -u pil0t-web -n 30${NC}"
fi
echo ""
