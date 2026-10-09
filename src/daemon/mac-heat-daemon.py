#!/usr/bin/env python3
# ponytail: mac-heat-daemon - lightweight macOS Apple Silicon thermal daemon & notification alerts
# upgrade path: multi-profile hardware policy presets

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
CHECK_INTERVAL_SEC = 20

def get_config():
    default_cfg = {
        "notifications_enabled": True,
        "mode": "critical",           # "critical" | "balanced" | "silent"
        "temp_threshold": 80,          # Alert only when M3 core temp >= 80°C
        "cpu_threshold": 75,
        "cooldown_minutes": 20,       # At most 1 alert every 20 minutes
        "sound_enabled": True,
        "sound": "Subtle"
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                c = json.load(f)
                default_cfg.update(c)
                return default_cfg
        except Exception:
            pass
    return default_cfg

_last_notif_time = 0
_finder_high_count = 0
_sustained_spike_count = 0

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
except Exception:
    _client = None

def get_apple_silicon_temps():
    if not _client:
        return 0.0, 0.0
    try:
        services = iokit.IOHIDEventSystemClientCopyServices(_client)
        if not services:
            return 0.0, 0.0
        count = cf.CFArrayGetCount(services)
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

def send_notification(title, subtitle, message, is_critical=False):
    global _last_notif_time
    cfg = get_config()
    
    # 1. Master toggle & Silent mode
    if not cfg.get("notifications_enabled", True) or cfg.get("mode") == "silent":
        return
        
    mode = cfg.get("mode", "critical")
    if mode == "critical" and not is_critical:
        return
        
    now = time.time()
    cooldown_sec = max(60, float(cfg.get("cooldown_minutes", 20)) * 60)
    if now - _last_notif_time < cooldown_sec:
        return
    _last_notif_time = now

    try:
        sound_clause = ""
        if cfg.get("sound_enabled", True) and cfg.get("sound", "Subtle") != "None":
            snd = cfg.get("sound", "Subtle")
            sound_clause = f' sound name "{snd}"'
            
        script = f'display notification "{message}" with title "{title}" subtitle "{subtitle}"{sound_clause}'
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
    global _finder_high_count, _sustained_spike_count
    cfg = get_config()
    mode = cfg.get("mode", "critical")
    cpu_thresh = float(cfg.get("cpu_threshold", 75.0))
    temp_thresh = float(cfg.get("temp_threshold", 80.0))

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

            if cpu_val >= cpu_thresh or (comm.endswith("kernel_task") and cpu_val >= 25.0):
                hogs.append((pid, cpu_val, comm))

    # Auto-mitigate runaway stuck Finder (silently or with clean notification)
    if finder_cpu > 70.0:
        _finder_high_count += 1
        if _finder_high_count >= 3:
            try:
                subprocess.run(["killall", "Finder"], check=False)
                send_notification("♻️ مدیریت خودکار Finder", "رفع داغی پس‌زمینه", f"فایندر با {finder_cpu:.0f}٪ مصرف ری‌استارت شد.", is_critical=True)
                _finder_high_count = 0
            except Exception:
                pass
    else:
        _finder_high_count = 0

    avg_temp, max_temp = get_apple_silicon_temps()
    temp_str = f"{avg_temp}°C (اوج: {max_temp}°C)" if max_temp > 0 else "N/A"

    # Always log real spikes to file for history audit
    if hogs or total_top5 >= 80.0 or (max_temp >= temp_thresh and max_temp > 0):
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        batt = get_battery_info()
        load1, load5, load15 = os.getloadavg()

        entry = [f"[{now_str}] ⚠️ SPIKE | دما: {temp_str} | بار: {load1:.2f}, {load5:.2f} | توان: {batt}"]
        for pid, cpu_val, comm in hogs:
            name = os.path.basename(comm)
            entry.append(f"   • PID: {pid:<7} | CPU: {cpu_val:>5.1f}% | {name}")

        rotate_log_if_needed()
        try:
            with open(LOG_FILE, "a") as f:
                f.write("\n".join(entry) + "\n\n")
        except Exception:
            pass

    # --- Intelligent Notification Policy (No Spams) ---
    is_real_critical = False
    alert_reason = ""

    if max_temp >= temp_thresh and max_temp > 0:
        is_real_critical = True
        alert_reason = f"دمای هسته‌های چیپ M3 به {max_temp:.0f}°C رسیده است."
    elif total_top5 >= 85.0:
        _sustained_spike_count += 1
        # Must be sustained for 3 cycles (~60s) AND hot (>= 72°C)
        if _sustained_spike_count >= 3 and max_temp >= 72.0:
            is_real_critical = (mode == "critical")
            alert_reason = f"بار پردازشی مداوم سنگین ({total_top5:.0f}٪)."
    else:
        _sustained_spike_count = 0

    if is_real_critical or (mode == "balanced" and max_temp >= 70.0 and hogs):
        culprit = os.path.basename(hogs[0][2]) if hogs else "سیستم"
        pct = f"({hogs[0][1]:.0f}٪)" if hogs else ""
        send_notification("🔥 هشدار حرارتی مک", f"دمای چیپ: {temp_str}", f"{alert_reason} برنامه: {culprit} {pct}", is_critical=is_real_critical)

def main():
    while True:
        try:
            check_spikes()
        except Exception:
            pass
        time.sleep(CHECK_INTERVAL_SEC)

if __name__ == "__main__":
    main()
