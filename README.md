# PiL0t

> A lightweight Raspberry Pi management console and ZPL label print server — built for Pi hardware, not against it.

---

## Overview

PiL0t gives you a fast, browser-based interface to manage your Raspberry Pi and run a standalone ZPL label printing station. It is inspired by Webmin but purpose-built for Pi — no heavy frameworks, no cloud dependency, no Windows PC required in the loop.

**GitHub project description:**
> Lightweight Raspberry Pi management console and ZPL label print server. Browser-based system monitoring, WiFi manager, terminal, file browser, and Zebra label printing — all in one self-hosted install.

---

## Features

| Feature | Description |
|---|---|
| **System Monitor** | Live CPU, memory, swap, disk, and temperature with sparkline graphs |
| **Print Server** | ZPL label printing via TCP network or direct USB to any Zebra-compatible printer |
| **USB Keypad** | Physical 4-button keypad support via evdev — no display needed |
| **Terminal** | Full interactive bash session in the browser via xterm.js |
| **File Browser** | Upload, download, rename, delete with drag-and-drop |
| **WiFi Manager** | Scan, connect, and manage saved profiles from the browser |
| **Log Viewer** | Live-tail any systemd service or log file |
| **Auth** | Multi-user accounts, session tracking, force logout, login history |
| **Setup Wizard** | First-run wizard sets admin password, device mode, and branding |
| **Project Tools** | Script runner, env editor, cron manager, git integration |

---

## Requirements

**Hardware:**
- Raspberry Pi 3B+ or newer
- SD card — 8 GB minimum, 16 GB recommended
- Zebra ZD620 or any ZPL-compatible label printer (USB or TCP/IP network)
- PCsensor MK424BT 4-key USB keypad *(optional)*

**Software:**
- Raspberry Pi OS Lite 32-bit — Bullseye, Bookworm, or Trixie
- No desktop environment required
- Python 3.9+ *(pre-installed on all current Pi OS images)*

---

## Install

### Option A — From tarball (recommended, no internet required on Pi)

1. Download `pil0t-v1.2.0.tar.gz` from the [Releases](../../releases) page
2. Copy it to your Pi:
```bash
scp pil0t-v1.2.0.tar.gz sysadmin@<pi-ip>:/tmp/
```
3. SSH into the Pi and extract:
```bash
cd /tmp
tar -xzf pil0t-v1.2.0.tar.gz
sudo bash /tmp/pil0t/install.sh
```

### Option B — Clone and install

```bash
git clone https://github.com/MrRobbles/PiL0t.git /tmp/pil0t
sudo bash /tmp/pil0t/install.sh
```

---

## First Run

When the install completes, open a browser and go to:

```
http://<your-pi-ip>:5000
```

The **Setup Wizard** will appear automatically on first visit. It will guide you through:

1. Setting the admin password *(replaces the default `admin / admin`)*
2. Choosing your device mode — **Print Server** or **Standard**
3. Setting app branding *(optional)*

---

## Default Configuration

| Setting | Default |
|---|---|
| Web port | `5000` |
| Printer IP | `10.10.10.249` |
| Printer port | `9100` |
| Print mode | `network` |
| USB device | `/dev/usb/lp0` |
| Starting SKU | `1490` |
| Default login | `admin` / `admin` *(change in wizard)* |

---

## Print Modes

**Network (TCP)** — ZPL sent directly to printer IP:9100. No driver required. Default.

**USB** — ZPL written directly to `/dev/usb/lp0`. No driver, no spooler. Switch modes in the Configuration panel on the print page.

To check USB device path:
```bash
ls /dev/usb/
```

---

## USB Keypad

The PCsensor MK424BT 4-key keypad is plug-and-play:

| Button | Action |
|---|---|
| A | Print next SKU |
| B | Reprint last SKU |
| C | Print blank label |
| D | Reserved |

If the keypad wasn't connected at install time:
```bash
sudo systemctl start pil0t-tracker
sudo systemctl enable pil0t-tracker
```

---

## Services

```bash
# Status
sudo systemctl status pil0t-web
sudo systemctl status pil0t-tracker

# Logs
sudo journalctl -u pil0t-web -f
sudo journalctl -u pil0t-tracker -f

# Restart
sudo systemctl restart pil0t-web
sudo systemctl restart pil0t-tracker
```

---

## File Locations

| Path | Purpose |
|---|---|
| `/etc/pil0t/app.py` | Flask backend |
| `/etc/pil0t/print_sku.py` | Keypad listener |
| `/etc/pil0t/static/` | Frontend HTML files |
| `/etc/pil0t/data/printer_config.json` | Printer IP, port, mode |
| `/etc/pil0t/data/current_sku.txt` | SKU counter |
| `/etc/pil0t/data/sku_log.txt` | Print history |
| `/etc/pil0t/data/app_users.json` | User accounts |
| `/etc/pil0t/data/auth_config.json` | Access control |
| `/etc/pil0t/data/branding.json` | App title and subtitle |
| `/etc/pil0t/data/device_config.json` | Device mode setting |
| `/etc/pil0t/data/.setup_complete` | First-run wizard flag |
| `/var/log/pil0t-network.log` | Boot network log |

---

## Updating

To update, extract the new tarball and re-run the installer. All data and configuration is preserved:

```bash
tar -xzf pil0t-v1.2.0.tar.gz -C /tmp/
sudo bash /tmp/pil0t/install.sh
```

Files that are **never overwritten** during an update:
- `printer_config.json`
- `current_sku.txt`
- `app_users.json`
- `auth_config.json`
- `branding.json`
- `device_config.json`
- `sku_log.txt`
- `.setup_complete`

---

## Resetting the Setup Wizard

To re-run the setup wizard:
```bash
sudo rm /etc/pil0t/data/.setup_complete
```

---

## Resetting the SKU Counter

```bash
echo 2000 > /etc/pil0t/data/current_sku.txt
```

---

## Troubleshooting

**Web UI not loading**
```bash
sudo systemctl status pil0t-web
sudo journalctl -u pil0t-web -n 30
curl http://localhost:5000/api/status
```

**Printer not responding (network)**
```bash
nc -zv 10.10.10.249 9100
```
Also check: Diagnostics → Port Check in the web UI.

**Printer not responding (USB)**
```bash
ls /dev/usb/
groups sysadmin    # should include 'lp'
sudo sh -c 'printf "^XA^FO50,50^ADN,36,20^FDTEST^FS^PQ1^XZ" > /dev/usb/lp0'
```

**WiFi scan returning empty**
```bash
sudo rfkill unblock wifi
sudo ip link set wlan0 up
sudo iwlist wlan0 scan
```

**Keypad not detected**
```bash
ls /dev/input/by-id/    # look for PCsensor
sudo journalctl -u pil0t-tracker -n 20
```

---

## Roadmap

- [ ] Label template editor
- [ ] Multi-printer support
- [ ] Debian/Ubuntu server support
- [ ] First-run network configuration in wizard
- [ ] Over-the-air update check from web UI

---

## License

MIT
