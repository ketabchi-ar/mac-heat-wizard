#!/usr/bin/env python3
# ponytail: mac-heat-daemon - lightweight macOS Apple Silicon thermal daemon & notification alerts
# upgrade path: add customizable notification sound or threshold presets

import os
import json
import time
import signal
import subprocess
import ctypes
from datetime import datetime
from ctypes import c_void_p, c_int, c_double, c_char_p

LOG_FILE = os.path.expanduser("~/.mac_heat_history.log")
CONFIG_FILE = os.path.expanduser("~/.mac_heat_config.json")
MAX_LOG_BYTES = 2 * 1024 * 1024  # 2 MB max
CHECK_INTERVAL_SEC = 30
NOTIFICATION_COOLDOWN_SEC = 180  # Max 1 notification every 3 minutes

def get_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"notifications_enabled": True, "cpu_threshold": 40.0, "temp_threshold": 75.0, "sound": "Subtle"}

_last_notif_time = 0
_finder_high_count = 0

# --- Apple Silicon IOKit Temperature Reader ---
try:
    iokit = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/IOKit.framework/IOKit')
    cf = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')

    cf.CFArrayGetCount.restype = c_int
    cf.CFArrayGetCount.argtypes = [c_void_p]
    cf.CFArrayGetValueAtIndex.restype = c_void_p
    cf.CFArrayGetValueAtIndex.argtypes = [c_void_p, c_int]

    iokit.IOHIDEventSystemClientCreate.restype = c_void_p
    iokit.IOHIDEventSystemClientCopyServices.restype = c_void_p
    iokit.IOHIDEventSystemClientCopyServices.argtypes = [c_void_p]

    iokit.IOHIDServiceClientCopyProperty.restype = c_void_p
    iokit.IOHIDServiceClientCopyProperty.argtypes = [c_void_p, c_void_p]
    iokit.IOHIDServiceClientCopyEvent.restype = c_void_p
    iokit.IOHIDServiceClientCopyEvent.argtypes = [c_void_p, c_int, c_int, c_int]
    iokit.IOHIDEventGetFloatValue.restype = c_double
    iokit.IOHIDEventGetFloatValue.argtypes = [c_void_p, c_int]

    cf.CFStringCreateWithCString.restype = c_void_p
    cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_int]
    cf.CFStringGetCString.restype = c_int
    cf.CFStringGetCString.argtypes = [c_void_p, c_char_p, c_int, c_int]

    _kIOHIDEventTypeTemperature = 15
    _client = iokit.IOHIDEventSystemClientCreate(None)
    _prop_prod = cf.CFStringCreateWithCString(None, b"Product", 0x08000100)
except Exception:
    _client = None

def get_apple_silicon_temps():
    if not _client:
        return 0.0, 0.0
    try:
        services = iokit.IOHIDEventSystemClientCopyServices(_client)
        count = cf.CFArrayGetCount(services) if services else 0
        vals = []
        for i in range(count):
            svc = cf.CFArrayGetValueAtIndex(services, i)
            event = iokit.IOHIDServiceClientCopyEvent(svc, _kIOHIDEventTypeTemperature, 0, 0)
            if event:
                val = iokit.IOHIDEventGetFloatValue(event, (_kIOHIDEventTypeTemperature << 16))
                if 20.0 <= val <= 110.0:
                    vals.append(val)
        if vals:
            return round(sum(vals) / len(vals), 1), round(max(vals), 1)
    except Exception:
        pass
    return 0.0, 0.0

def send_notification(title, subtitle, message):
    global _last_notif_time
    cfg = get_config()
    if not cfg.get("notifications_enabled", True):
        return
    now = time.time()
    if now - _last_notif_time < NOTIFICATION_COOLDOWN_SEC:
        return
    _last_notif_time = now
    try:
        sound = cfg.get("sound", "Subtle")
        script = f'display notification "{message}" with title "{title}" subtitle "{subtitle}" sound name "{sound}"'
        subprocess.run(["osascript", "-e", script], check=False, timeout=3)
    except Exception:
        pass

def rotate_log_if_needed():
    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > MAX_LOG_BYTES:
        try:
            with open(LOG_FILE, "r") as f:
                lines = f.readlines()
            with open(LOG_FILE, "w") as f:
                f.writelines(lines[-250:])
        except Exception:
            pass

def get_battery_info():
    try:
        res = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=2)
        state = "AC" if "AC Power" in res.stdout else "Battery"
        for line in res.stdout.splitlines():
            if "InternalBattery" in line and ";" in line:
                pct = line.split("\t")[1].split(";")[0].strip() if "\t" in line else ""
                status = line.split(";")[1].strip()
                return f"{state} | {pct} ({status})"
    except Exception:
        pass
    return "N/A"

def check_spikes():
    global _finder_high_count
    cfg = get_config()
    cpu_thresh = float(cfg.get("cpu_threshold", 40.0))
    temp_thresh = float(cfg.get("temp_threshold", 75.0))
    try:
        res = subprocess.run(["ps", "-A", "-o", "pid,%cpu,comm", "-r"], capture_output=True, text=True, timeout=3)
        lines = [l.strip() for l in res.stdout.splitlines() if l.strip()][1:]
    except Exception:
        return

    hogs = []
    total_top5 = 0.0
    finder_cpu = 0.0

    for idx, line in enumerate(lines[:8]):
        parts = line.split(None, 2)
        if len(parts) >= 3:
            pid, cpu_str, comm = parts[0], parts[1], parts[2]
            try:
                cpu_val = float(cpu_str)
            except ValueError:
                continue

            if idx < 5:
                total_top5 += cpu_val

            if "Finder" in comm:
                finder_cpu = cpu_val

            if any(x in comm for x in ["/ps", "/top", "mac-heat-daemon", "IntelligencePlatform", "mds"]):
                continue

            if cpu_val >= cpu_thresh or (comm.endswith("kernel_task") and cpu_val >= 20.0):
                hogs.append((pid, cpu_val, comm))

    # Auto-mitigate stuck Finder
    if finder_cpu > 55.0:
        _finder_high_count += 1
        if _finder_high_count >= 2:
            try:
                subprocess.run(["killall", "Finder"], check=False)
                send_notification("♻️ خودکارسازی فایندر", "داغی رفع شد", f"فایندر با {finder_cpu:.0f}٪ CPU ری‌استارت شد.")
                _finder_high_count = 0
            except Exception:
                pass
    else:
        _finder_high_count = 0

    avg_temp, max_temp = get_apple_silicon_temps()

    if hogs or total_top5 >= 80.0 or max_temp >= temp_thresh:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        batt = get_battery_info()
        load1, load5, load15 = os.getloadavg()
        temp_str = f"{avg_temp}°C (حداکثر: {max_temp}°C)" if max_temp > 0 else "N/A"

        entry = [f"[{now_str}] ⚠️ SPIKE | دما: {temp_str} | بار: {load1:.2f}, {load5:.2f} | پاور: {batt}"]
        for pid, cpu_val, comm in hogs:
            name = os.path.basename(comm)
            entry.append(f"   • PID: {pid:<7} | CPU: {cpu_val:>5.1f}% | {name} ({comm})")

        rotate_log_if_needed()
        try:
            with open(LOG_FILE, "a") as f:
                f.write("\n".join(entry) + "\n\n")
        except Exception:
            pass

        # Trigger macOS banner notification
        if hogs:
            top_culprit = os.path.basename(hogs[0][2])
            top_cpu = hogs[0][1]
            send_notification("🔥 هشدار داغی پردازنده", f"دمای چیپ: {temp_str}", f"برنامه {top_culprit} مصرف {top_cpu:.0f}٪ دارد.")

def main():
    while True:
        try:
            check_spikes()
        except Exception:
            pass
        time.sleep(CHECK_INTERVAL_SEC)

if __name__ == "__main__":
    main()
