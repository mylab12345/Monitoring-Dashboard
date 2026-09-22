"""Live metrics, the server-side history ring buffer, and system facts.

The sampler thread runs once per process (guarded by a module-level flag), so
it is safe for create_app() to call start_sampler() every time it runs (e.g.
in tests and in a WSGI reloader).
"""
import os
import threading
import time
from collections import deque
from datetime import datetime

from flask import Blueprint, jsonify, request

from .common import APP_VERSION, _cached, _int_or
from .security import rate_limit

bp = Blueprint("metrics", __name__)

HISTORY_MAX = 1800  # 60 min @ 2s
# A bounded deque evicts the oldest sample in O(1) on append; the previous
# list + slice-delete re-copied up to 1800 samples on every 2s tick.
_HISTORY = {"samples": deque(maxlen=HISTORY_MAX), "lock": threading.Lock()}
_last_cpu = {"value": 0.0}
_sampler_started = False
_cpu_primed = False


def _read_temp_c():
    """Best-effort CPU temperature in °C (float) or None."""
    try:
        import psutil
        temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
        for _, entries in (temps or {}).items():
            if entries:
                # Check current is not None (0 is valid, but None means unavailable)
                cur = getattr(entries[0], "current", None)
                if cur is not None:
                    return round(float(cur), 1)
    except Exception:
        pass
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        return None


def _prime_cpu():
    """Prime psutil cpu_percent so first real reading is not 0.0."""
    global _cpu_primed
    if _cpu_primed:
        return
    try:
        import psutil
        psutil.cpu_percent(interval=None)
        _cpu_primed = True
    except Exception:
        pass


def _sampler_loop():
    import psutil
    psutil.cpu_percent(interval=None)  # prime
    global _cpu_primed
    _cpu_primed = True
    prev_net = None
    while True:
        try:
            now = time.time()
            cpu = psutil.cpu_percent(interval=None)
            _last_cpu["value"] = cpu
            mem = psutil.virtual_memory()
            net = psutil.net_io_counters()
            sent_bps = recv_bps = 0
            if prev_net is not None and now > prev_net[0]:
                dt = now - prev_net[0]
                sent_bps = max(0, (net.bytes_sent - prev_net[1]) / dt)
                recv_bps = max(0, (net.bytes_recv - prev_net[2]) / dt)
            prev_net = (now, net.bytes_sent, net.bytes_recv)
            disk = psutil.disk_usage("/")
            sample = {
                "t": round(now, 2),
                "cpu": round(cpu, 1),
                "ram": round(mem.percent, 1),
                "disk": round(disk.percent, 1),
                "sent_bps": round(sent_bps), "recv_bps": round(recv_bps),
                "temp": _read_temp_c(),
                "load1": round(os.getloadavg()[0], 2),
            }
            with _HISTORY["lock"]:
                _HISTORY["samples"].append(sample)
        except Exception:
            pass
        time.sleep(2)


def start_sampler():
    global _sampler_started
    if _sampler_started:
        return
    _sampler_started = True
    _prime_cpu()
    threading.Thread(target=_sampler_loop, daemon=True, name="monitoring-sampler").start()


@bp.route("/api/history")
@rate_limit("120 per minute")
def api_history():
    """Return recent metric samples. ?points=N (default 150, max 1800)."""
    points = min(max(_int_or(request.args.get("points", 150), 150), 10), HISTORY_MAX)
    with _HISTORY["lock"]:
        samples = list(_HISTORY["samples"])
    if not samples:
        return jsonify({"samples": []})
    n = len(samples)
    step = max(1, -(-n // 400))  # downsample to <=400 points for payload
    sliced = samples[max(0, n - points):]
    out = sliced[::step]
    if sliced and (not out or out[-1] is not sliced[-1]):
        out.append(sliced[-1])
    return jsonify({"samples": out, "resolution_s": 2})


def _cpu_model():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "unknown"


def _pretty_distro():
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return f"{os.uname().sysname} {os.uname().release}"


def _battery():
    try:
        import psutil
        bat = psutil.sensors_battery()
        if bat is None:
            return None
        return {"percent": round(bat.percent, 1), "plugged": bool(bat.power_plugged)}
    except Exception:
        return None


def _temp_string():
    """CPU temperature as a display string ("42.0°C") or "N/A"."""
    temp_c = _read_temp_c()
    return f"{temp_c:.1f}°C" if temp_c is not None else "N/A"


def _build_status():
    try:
        import psutil
        _prime_cpu()
        # Prefer the sampler's last reading; on a cold start (no samples yet
        # and a 0.0 reading) take one short blocking sample instead of
        # showing a bogus 0%.
        cpu = _last_cpu["value"] or psutil.cpu_percent(interval=None)
        if cpu == 0.0 and not _HISTORY["samples"]:
            try:
                cpu = psutil.cpu_percent(interval=0.1)
            except Exception:
                cpu = psutil.cpu_percent(interval=None)
            _last_cpu["value"] = cpu
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage("/")
        uptime = time.time() - psutil.boot_time()
        load = os.getloadavg()
        freq = psutil.cpu_freq()
        temp = _temp_string()
        net = psutil.net_io_counters()
        net_sent = f"{net.bytes_sent / (1024**2):.1f} MB"
        net_recv = f"{net.bytes_recv / (1024**2):.1f} MB"
        procs = len(psutil.pids())
        users = len(psutil.users())
        bat = _battery()
        data = {
            "cpu_percent": round(cpu, 1),
            "cpu_count": psutil.cpu_count() or 1,
            "cpu_freq_mhz": round(freq.current) if freq and freq.current else None,
            "ram_percent": mem.percent,
            "ram_used_gb": round(mem.used / (1024**3), 2),
            "ram_total_gb": round(mem.total / (1024**3), 2),
            "swap_percent": swap.percent,
            "swap_used_gb": round(swap.used / (1024**3), 2),
            "swap_total_gb": round(swap.total / (1024**3), 2),
            "disk_percent": disk.percent,
            "disk_used_gb": round(disk.used / (1024**3), 2),
            "disk_total_gb": round(disk.total / (1024**3), 2),
            "uptime_sec": int(uptime),
            "load_1": round(load[0], 2),
            "load_5": round(load[1], 2),
            "load_15": round(load[2], 2),
            "temp": temp,
            "net_sent": net_sent,
            "net_recv": net_recv,
            "net_sent_bytes": net.bytes_sent,
            "net_recv_bytes": net.bytes_recv,
            "processes": procs,
            "users": users,
            "battery": bat,
            "hostname": os.uname().nodename,
            "os": _pretty_distro(),
            "kernel": f"{os.uname().sysname} {os.uname().release}",
            "time": datetime.now().strftime("%H:%M:%S"),
            "version": APP_VERSION,
        }
        return data
    except Exception as e:
        return {"error": str(e)}


@bp.route("/api/status")
@rate_limit("120 per minute")
def api_status():
    data = _cached("_status", 2, _build_status)
    if isinstance(data, dict) and "error" in data:
        return jsonify(data), 500
    return jsonify(data)
