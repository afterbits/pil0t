[README.md](https://github.com/user-attachments/files/27457284/README.md)
# PiL0t

> A lightweight Raspberry Pi management console heavily inspired by Webmin but made for underpowered hardware that could otherwise be upcycled into usable projects.
---

## Overview

PiL0t turns a Raspberry Pi into a self-contained management console and label printing station. Point a browser at it and you get live system metrics, a working terminal, a file browser, WiFi management, and a full ZPL print server running directly from the Pi. No cloud account required, no Windows PC in the loop, no print drivers to fight with.

Think of it as the tool you wished existed when you plugged a Zebra printer into a Pi for the first time and realised nothing just worked out of the box. PiL0t fixes that.

**GitHub project description:**

> PiL0t is a self-hosted Raspberry Pi management console and ZPL label print server. Connect a Zebra or compatible label printer over USB or the network and your Pi becomes a standalone printing station. From any browser on the same network you can print the next label in a sequence, reprint the last one, run batches, manage printer config, and watch every print get logged in real time. On top of that you get live system monitoring with CPU, memory, disk and temperature charts, a full bash terminal, a drag-and-drop file browser, WiFi scanning and connection management, and multi-user authentication with session control. It runs entirely on the Pi, installs in one command on a fresh Raspberry Pi OS Lite image, and needs nothing from the internet to operate.

---

## Features

| Feature | Description |
|---|---|
| **Print Server** | ZPL label printing over TCP/IP or direct USB to any Zebra-compatible printer |
| **System Monitor** | Live CPU, memory, swap, disk, and temperature with sparkline graphs |
| **USB Keypad** | Physical 4-button keypad support, no display needed |
| **Terminal** | Full interactive bash session running in the browser |
| **File Browser** | Upload, download, rename, and delete files with drag-and-drop |
| **WiFi Manager** | Scan for networks, connect, and manage saved profiles from the browser |
| **Log Viewer** | Live-tail any systemd service or log file |
| **Auth** | Multi-user accounts, session tracking, force logout, and login history |
| **Setup Wizard** | First-run wizard covers admin password, device mode, and branding |
| **Project Tools** | Script runner, environment file editor, cron manager, and git integration |

---

## Requirements

**Hardware:**
- Raspberry Pi 3B+ or newer
- SD card, 8 GB minimum and 16 GB recommended
- Zebra ZD620 or any ZPL-compatible label printer via USB or network
- PCsensor MK424BT 4-key USB keypad (optional, for physical print buttons)

**Software:**
- Raspberry Pi OS Lite 32-bit, Bullseye, Bookworm, or Trixie
- No desktop environment needed
- Python 3.9 or newer, which comes pre-installed on all current Pi OS images

---

## Install

### Option A: Clone and install

SSH into your Pi and run:

```bash
git clone https://github.com/MrRobbles/PiL0t.git /tmp/pil0t
sudo bash /tmp/pil0t/install.sh
```

That is it. The installer handles everything including dependencies, directory structure, systemd services, sudoers configuration, and default config files. On a Pi 3B+ with a reasonable SD card it takes about 90 seconds.

### Option B: Install from tarball (no internet required on the Pi)

If the Pi does not have internet access, download the release package on another machine and copy it over.

1. Download `pil0t-v1.2.0.tar.gz` from the [Releases](../../releases) page
2. Copy it to the Pi from your PC:
```bash
scp pil0t-v1.2.0.tar.gz sysadmin@<pi-ip>:/tmp/
```
3. SSH into the Pi and run:
```bash
cd /tmp && tar -xzf pil0t-v1.2.0.tar.gz
sudo bash /tmp/pil0t/install.sh
```

---

## First Run

When the install finishes, open a browser on any device on the same network and go to:

```
http://<your-pi-ip>:5000
```

The setup wizard will appear automatically on the first visit. It walks you through three steps:

1. Setting the admin password, which replaces the default `admin / admin`
2. Choosing your device mode, either Print Server or Standard
3. Customising the app name shown in the header (optional, can be skipped)

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
| Default login | `admin` / `admin` (changed in the wizard) |

---

## Print Modes

**Network (TCP):** ZPL is sent directly to the printer's IP address on port 9100. No driver required. This is the default.

**USB:** ZPL is written directly to the USB device file, usually `/dev/usb/lp0`. No driver, no print spooler. Switch between modes in the Configuration panel on the print page.

To check the USB device path:
```bash
ls /dev/usb/
```

---

## USB Keypad

The PCsensor MK424BT 4-key keypad is plug-and-play:

| Button | Action |
|---|---|
| A | Print the next SKU and increment the counter |
| B | Reprint the last SKU |
| C | Print a blank feed label |
| D | Reserved for future use |

If the keypad was not connected when the installer ran, start the tracker service manually:
```bash
sudo systemctl start pil0t-tracker
sudo systemctl enable pil0t-tracker
```

---

## Services

```bash
# Check status
sudo systemctl status pil0t-web
sudo systemctl status pil0t-tracker

# Follow logs
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
| `/etc/pil0t/data/printer_config.json` | Printer IP, port, and mode |
| `/etc/pil0t/data/current_sku.txt` | SKU counter |
| `/etc/pil0t/data/sku_log.txt` | Full print history |
| `/etc/pil0t/data/app_users.json` | User accounts |
| `/etc/pil0t/data/auth_config.json` | Access control settings |
| `/etc/pil0t/data/branding.json` | App title and subtitle |
| `/etc/pil0t/data/device_config.json` | Device mode setting |
| `/etc/pil0t/data/.setup_complete` | Wizard completion flag |
| `/var/log/pil0t-network.log` | Boot network log |

---

## Updating

Re-run the installer with the new version. All your data and config files are preserved:

```bash
cd /tmp && tar -xzf pil0t-v1.2.0.tar.gz
sudo bash /tmp/pil0t/install.sh
```

The following files are never touched during an update:

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

Delete the completion flag and the wizard will appear again on the next page load:

```bash
sudo rm /etc/pil0t/data/.setup_complete
```

---

## Resetting the SKU Counter

The counter is a plain text file. Edit it directly:

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

**Printer not responding over the network**
```bash
nc -zv 10.10.10.249 9100
```
You can also use Diagnostics and then Port Check in the web UI.

**Printer not responding over USB**
```bash
ls /dev/usb/
groups sysadmin
sudo sh -c 'printf "^XA^FO50,50^ADN,36,20^FDTEST^FS^PQ1^XZ" > /dev/usb/lp0'
```

**WiFi scan returning no results**
```bash
sudo rfkill unblock wifi
sudo ip link set wlan0 up
sudo iwlist wlan0 scan
```

**Keypad not detected**
```bash
ls /dev/input/by-id/
sudo journalctl -u pil0t-tracker -n 20
```

---

## Roadmap

- [ ] Label template editor with browser-based preview
- [ ] Multi-printer support
- [ ] Over-the-air update check from the web UI
- [ ] Debian and Ubuntu server support
- [ ] First-run network configuration in the setup wizard

---

## Contributing

Issues and pull requests are welcome. The project is actively developed and tested on real Pi hardware in a live warehouse environment.

---

## License

MIT
