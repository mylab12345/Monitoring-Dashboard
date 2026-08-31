"""Live metrics, the server-side history ring buffer, and system facts.

The sampler thread runs once per process (guarded by a module-level flag), so
it is safe for create_app() to call start_sampler() every time it runs (e.g.
in tests and in a WSGI reloader).
"""
import os
import threading
import time
from datetime import datetime

from flask import Blueprint, jsonify, request

from .common import APP_VERSION, _cached, _int_or

bp = Blueprint("metrics", __name__)

HISTORY_MAX = 1800  # 60 min @ 2s
_HISTORY = {"samples": [], "lock": threading.Lock()}
_last_cpu = {"value": 0.0}
_sampler_started = False


def _read_temp_c():
    """Best-effort CPU temperature in °C (float) or None."""
    try:
        import psutil
        temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
        for _, entries in (temps or {}).items():
            if entries and entries[0].current:
                return round(entries[0].current, 1)
    except Exception:
        pass
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        return None


def _sampler_loop():
    import psutil
    psutil.cpu_percent(interval=None)  # prime
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
                if len(_HISTORY["samples"]) > HISTORY_MAX:
                    del _HISTORY["samples"][:len(_HISTORY["samples"]) - HISTORY_MAX]
        except Exception:
            pass
        time.sleep(2)


def start_sampler():
    global _sampler_started
    if _sampler_started:
        return
    _sampler_started = True
    threading.Thread(target=_sampler_loop, daemon=True, name="monitoring-sampler").start()


@bp.route("/api/history")
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


def _build_status():
    try:
        import psutil
        cpu = _last_cpu["value"] or psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage("/")
        uptime = time.time() - psutil.boot_time()
        load = os.getloadavg()
        freq = psutil.cpu_freq()
        temp = "N/A"
        try:
            temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
            if temps:
                for _, entries in temps.items():
                    if entries:
                        temp = f"{entries[0].current:.1f}°C"
                        break
        except Exception:
            pass
        if temp == "N/A":
            try:
                with open("/sys/class/thermal/thermal_zone0/temp") as f:
                    temp = f"{int(f.read().strip()) / 1000:.1f}°C"
            except Exception:
                pass
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
def api_status():
    data = _cached("_status", 2, _build_status)
    if isinstance(data, dict) and "error" in data:
        return jsonify(data), 500
    return jsonify(data)
