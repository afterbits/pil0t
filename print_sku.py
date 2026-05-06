import socket
import os
import time
import evdev
from evdev import InputDevice, categorize, ecodes
from datetime import datetime

PRINTER_IP   = "10.10.10.249"
PRINTER_PORT = 9100
STARTING_SKU = 1490
SKU_FILE     = "/etc/pil0t/data/current_sku.txt"
LOG_FILE     = "/etc/pil0t/data/sku_log.txt"
DEVICE_PATH  = "/dev/input/by-id/usb-PCsensor_MK424BT-event-kbd"
CONFIG_FILE  = "/etc/pil0t/data/printer_config.json"

def load_sku():
    if os.path.exists(SKU_FILE):
        with open(SKU_FILE) as f:
            return int(f.read().strip())
    return STARTING_SKU

def save_sku(sku):
    with open(SKU_FILE, "w") as f:
        f.write(str(sku))

def log_print(sku, action="printed"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"{now} — SKU {sku} — {action}\n")

def get_timestamp():
    n = datetime.now()
    suf = {1:"st",2:"nd",3:"rd"}.get(n.day if n.day < 20 else n.day % 10, "th")
    h = n.hour % 12 or 12
    return f"{n.strftime('%B')} {n.day}{suf} {n.year} at {h}:{n.strftime('%M')}{'am' if n.hour < 12 else 'pm'}"

def send_zpl(zpl, retries=2):
    for attempt in range(retries + 1):
        try:
            # Re-read config each call so web UI changes take effect immediately
            cfg = {}
            if os.path.exists(CONFIG_FILE):
                import json as _json
                with open(CONFIG_FILE) as f:
                    cfg = _json.load(f)
            mode       = cfg.get("mode",         "network")
            usb_device = cfg.get("usb_device",   "/dev/usb/lp0")
            ip         = cfg.get("printer_ip",   PRINTER_IP)
            port       = int(cfg.get("printer_port", PRINTER_PORT))

            if mode == "usb":
                with open(usb_device, "wb") as f:
                    f.write(zpl.encode("utf-8"))
                    f.flush()
            else:
                with socket.create_connection((ip, port), timeout=5) as s:
                    s.sendall(zpl.encode("utf-8"))
            return
        except Exception as e:
            print(f"[WARN] Print attempt {attempt+1} failed: {e}")
            if attempt < retries:
                time.sleep(1)
    print("[ERROR] All print attempts failed")

def print_next():
    sku = load_sku()
    ts  = get_timestamp()
    zpl = (
        "^XA^MMT^PW447^LL146^LS0^CI28"
        f"^FO0,25^FB447,1,,C^A0N,75,52^FD{sku}^FS"
        f"^FO0,103^FB447,1,,C^A0N,32,22^FD{ts}^FS"
        "^PQ1^XZ"
    )
    send_zpl(zpl)
    save_sku(sku + 1)
    log_print(sku)
    print(f"[A] Printed SKU {sku} — next will be {sku + 1}")

def print_reprint():
    sku = load_sku() - 1
    if sku < STARTING_SKU:
        print("[B] No previous SKU to reprint")
        return
    ts  = get_timestamp()
    zpl = (
        "^XA^MMT^PW447^LL146^LS0^CI28"
        f"^FO0,25^FB447,1,,C^A0N,75,52^FD{sku}^FS"
        f"^FO0,103^FB447,1,,C^A0N,32,22^FD{ts}^FS"
        "^PQ1^XZ"
    )
    send_zpl(zpl)
    log_print(sku, "reprinted")
    print(f"[B] Reprinted SKU {sku}")

def print_blank():
    zpl = "^XA^MMT^PW447^LL146^LS0^FO220,70^GB1,1,1^FS^PQ1^XZ"
    send_zpl(zpl)
    print("[C] Blank label sent")

busy = False

print("SKU Printer ready.")
print("  A = print next SKU")
print("  B = reprint last SKU")
print("  C = print blank label")
print("  D = (reserved)")

device = InputDevice(DEVICE_PATH)
device.grab()

for event in device.read_loop():
    if event.type == ecodes.EV_KEY:
        key = categorize(event)
        if key.keystate == key.key_down:
            if busy:
                continue
            try:
                if key.keycode == "KEY_A":
                    busy = True
                    try:
                        print_next()
                    except Exception as e:
                        print(f"[A] Error: {e}")
                    finally:
                        time.sleep(0.5)
                        busy = False

                elif key.keycode == "KEY_B":
                    busy = True
                    try:
                        print_reprint()
                    except Exception as e:
                        print(f"[B] Error: {e}")
                    finally:
                        time.sleep(0.5)
                        busy = False

                elif key.keycode == "KEY_C":
                    busy = True
                    try:
                        print_blank()
                    except Exception as e:
                        print(f"[C] Error: {e}")
                    finally:
                        time.sleep(0.5)
                        busy = False

                elif key.keycode == "KEY_D":
                    print("[D] Reserved — no action")

            except Exception as e:
                print(f"Key error: {e}")
                busy = False
