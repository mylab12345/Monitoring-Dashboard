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


_minute_acc = {"key": 0, "rows": []}


def _persist_tick(sample):
    """Once per minute: roll up samples, record top processes, keep alerts in
    sync with the thresholds. Every step is best-effort."""
    try:
        from . import store
        if not store.available():
            return
        key = int(sample["t"] // 60)
        acc = _minute_acc
        if acc["key"] and key != acc["key"] and acc["rows"]:
            rows = acc["rows"]
            avg = {k: round(sum(r.get(k) or 0 for r in rows) / len(rows), 2)
                   for k in ("cpu", "ram", "disk", "load1", "sent_bps",
                             "recv_bps", "temp")}
            try:
                import psutil
                swap = psutil.swap_memory().percent
            except Exception:  # noqa: BLE001
                swap = None
            store.record_metric({
                "t": acc["key"] * 60, "cpu": avg["cpu"], "ram": avg["ram"],
                "swap": swap, "disk": avg["disk"], "load1": avg["load1"],
                "rx": avg["recv_bps"], "tx": avg["sent_bps"],
                "temp": avg["temp"] or None,
            })
            try:
                from .processes import top_processes_snapshot
                store.record_processes(top_processes_snapshot(limit=8))
            except Exception:  # noqa: BLE001
                pass
            acc["rows"] = []
        acc["key"] = key
        acc["rows"].append(sample)
        if len(acc["rows"]) > 120:  # safety cap (~2 min of 2s samples)
            acc["rows"] = acc["rows"][-120:]
    except Exception:  # noqa: BLE001
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
            # Persistent rollups + server-side threshold alerting ride along
            # on the same tick; both are best-effort and cannot break sampling.
            try:
                _persist_tick(sample)
                evaluate_alerts(sample)
            except Exception:  # noqa: BLE001
                pass
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


def _psi():
    """Pressure Stall Information from /proc/pressure (kernel 4.20+).

    Returns {cpu/memory/io: {some: {avg10, avg60, avg300}, full: {...}}} or
    None when the kernel does not expose PSI.
    """
    out = {}
    for res in ("cpu", "memory", "io"):
        entry = {}
        try:
            with open(f"/proc/pressure/{res}") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 2:
                        continue
                    vals = {}
                    for tok in parts[1:]:
                        if "=" in tok:
                            k, v = tok.split("=", 1)
                            try:
                                vals[k] = float(v)
                            except ValueError:
                                pass
                    if vals:
                        entry[parts[0]] = vals
        except Exception:  # noqa: BLE001 — PSI absent on older kernels
            continue
        if entry:
            out[res] = entry
    return out or None


def _mem_breakdown():
    """Memory detail from /proc/meminfo (page cache, slab, commit, ...)."""
    wanted = ("MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached",
              "SReclaimable", "Shmem", "Slab", "SwapTotal", "SwapFree",
              "CommitLimit", "Committed_AS")
    out = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                key = line.split(":", 1)[0]
                if key in wanted:
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            out[key.lower()] = int(parts[1]) * 1024  # bytes
                        except ValueError:
                            pass
    except Exception:  # noqa: BLE001
        return {}
    return out


_oom_cache = {"ts": 0, "data": None}


def _oom_events():
    """Recent OOM-killer activity (journal), cached 5 minutes."""
    now = time.time()
    if _oom_cache["data"] is not None and now - _oom_cache["ts"] < 300:
        return _oom_cache["data"]
    data = {"count": 0, "events": [], "known": False}
    try:
        from .commands import run, which
        if which("journalctl"):
            code, out, _ = run(["journalctl", "-k", "--since", "-24h",
                                "--no-pager", "-o", "short"], timeout=10,
                               sudo=False)
            if code == 0 and out:
                data["known"] = True
                hits = [l for l in out.splitlines()
                        if ("Out of memory" in l or "oom-kill" in l
                            or "oom_reaper" in l)]
                data["count"] = len(hits)
                data["events"] = hits[-5:]
    except Exception:  # noqa: BLE001
        pass
    _oom_cache["data"] = data
    _oom_cache["ts"] = now
    return data


# Server-side threshold alerting (mirrors the UI defaults in core.js). The
# operator may override them via the Automation tab; they live in the store.
_DEFAULT_THRESHOLDS = {"cpuWarn": 80, "cpuCrit": 92, "ramWarn": 85,
                       "ramCrit": 95, "diskWarn": 85, "diskCrit": 93,
                       "tempWarn": 75}
_alert_state = {}          # metric name -> current level (ok/warn/crit)
_alert_notify_ts = {}      # metric name -> last notification time


def _thresholds():
    from . import store
    cfg = store.kv_get("alert_thresholds", {}) or {}
    out = dict(_DEFAULT_THRESHOLDS)
    for key, val in cfg.items():
        if key in out:
            try:
                out[key] = float(val)
            except (TypeError, ValueError):
                pass
    return out


def _level_for(name, value, warn, crit):
    if value is None:
        return "ok"
    if value >= crit:
        return "crit"
    if value >= warn:
        return "warn"
    return "ok"


def evaluate_alerts(sample):
    """Compare one metric sample against thresholds; record + notify on
    transitions only (warn/crit entered, or recovery). Never raises."""
    try:
        from . import store, notify  # lazy: avoids import cycles at startup
        th = _thresholds()
        checks = (
            ("cpu", "CPU usage", sample.get("cpu"), th["cpuWarn"], th["cpuCrit"]),
            ("ram", "Memory usage", sample.get("ram"), th["ramWarn"], th["ramCrit"]),
            ("disk", "Disk usage", sample.get("disk"), th["diskWarn"], th["diskCrit"]),
            ("temp", "CPU temperature", sample.get("temp"), th["tempWarn"],
             th["tempWarn"] + 15),
        )
        now = time.time()
        for key, label, value, warn, crit in checks:
            if value is None:
                continue
            level = _level_for(key, value, warn, crit)
            prev = _alert_state.get(key, "ok")
            _alert_state[key] = level
            if level == prev:
                continue
            if level == "ok":
                store.resolve_alert(label)
                continue
            msg = (f"{label} is {value}{'°C' if key == 'temp' else '%'} "
                   f"({level == 'crit' and 'critical' or 'warning'} threshold: "
                   f"{warn:g} / {crit:g})")
            store.add_alert("critical" if level == "crit" else "warning",
                            label, msg, value=value,
                            threshold=crit if level == "crit" else warn)
            # Cooldown: at most one notification per metric per 15 minutes.
            if now - _alert_notify_ts.get(key, 0) > 900:
                _alert_notify_ts[key] = now
                notify.send_notification(
                    f"Monitoring: {label} {level}", msg)
    except Exception:  # noqa: BLE001 — alerting must never break sampling
        pass


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
            "psi": _psi(),
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


@bp.route("/api/history/long")
@rate_limit("30 per minute")
def api_history_long():
    """Persistent minute-rollup history for long-range charts (?hours=1..168).

    Backed by the SQLite store, so day/week trends survive dashboard and
    machine restarts (the in-memory /api/history ring only holds 60 minutes).
    """
    hours = min(max(_int_or(request.args.get("hours", 24), 24), 1), 168)
    try:
        from . import store
        samples = store.metric_history(time.time() - hours * 3600, limit=400)
        return jsonify({"samples": samples, "hours": hours,
                        "resolution_s": 60,
                        "available": store.available()})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@bp.route("/api/memory")
@rate_limit("60 per minute")
def api_memory():
    """Memory breakdown (page cache/slab/commit) + recent OOM-killer events."""
    try:
        return jsonify({"breakdown": _mem_breakdown(), "oom": _oom_events(),
                        "psi": _psi()})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@bp.route("/api/alerts/history")
@rate_limit("60 per minute")
def api_alerts_history():
    """Persistent server-side alert history (?limit=1..500).

    The browser-local alert log is convenient but per-device; this is the
    authoritative record that survives restarts and is shared with
    notifications.
    """
    try:
        from . import store
        limit = min(max(_int_or(request.args.get("limit", 50), 50), 1), 500)
        return jsonify({"alerts": store.recent_alerts(limit),
                        "available": store.available()})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500
