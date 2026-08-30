#!/usr/bin/env python3
"""
Montoring — Universal Linux System Dashboard
Monitors system health, checks/fixes issues, manages processes, services,
logs and libvirt VMs — on any Linux flavour.

Run:  python3 app.py   (binds 0.0.0.0:$MONTORING_PORT, default 8050)
Requirements: flask, psutil  (see requirements.txt)
Privileges:   fixes/service/vm actions use sudo automatically when
              not running as root (works best under the bundled systemd
              service which runs as root).
"""
import os
import re
import shlex
import subprocess
import shutil
import threading
import time
import json
from datetime import datetime

from flask import Flask, render_template, jsonify, request

APP_HOME = os.environ.get("MONTORING_HOME", os.path.dirname(os.path.abspath(__file__)))
APP_PORT = int(os.environ.get("MONTORING_PORT", "8050"))

# ------------------------------------------------------------------
# Version
# ------------------------------------------------------------------
def _read_version():
    try:
        with open(os.path.join(APP_HOME, "VERSION")) as f:
            return f.read().strip()
    except Exception:
        return "dev"

APP_VERSION = _read_version()

app = Flask(__name__)

# ------------------------------------------------------------------
# Safe command runner (sudo-aware)
# ------------------------------------------------------------------
def _have_sudo():
    if os.geteuid() == 0:
        return False  # already root, no prefix needed
    return shutil.which("sudo") is not None

HAVE_SUDO = _have_sudo()

def _sudo_test():
    """Detect passwordless sudo once."""
    try:
        r = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False

PASSWORDLESS_SUDO = _sudo_test() if HAVE_SUDO else False

def run(cmd, timeout=10, sudo=False):
    """Run a shell command; optionally prefix sudo when not root."""
    if sudo and os.geteuid() != 0:
        if PASSWORDLESS_SUDO:
            cmd = "sudo -n " + cmd
        else:
            # Try anyway — commands that don't need root will still work.
            cmd = "sudo -n " + cmd
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)

def which(binary):
    return shutil.which(binary) is not None

def safe_name(name):
    """Validate identifiers (unit names, VM names, container names...)."""
    return bool(name) and re.fullmatch(r"[A-Za-z0-9_@.\-: _\[\]]{1,120}", name) is not None

# ------------------------------------------------------------------
# Index
# ------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/health")
def api_health():
    """Cheap liveness/readiness probe used by the UI and service monitors.

    Keep this endpoint independent of psutil and external commands so a
    partially degraded host can still report that the Flask API is alive.
    """
    return jsonify({
        "status": "ok",
        "service": "Montoring API",
        "version": APP_VERSION,
        "pid": os.getpid(),
        "time": datetime.now().isoformat(),
    })

@app.route("/api/version")
def api_version():
    return jsonify({
        "app": "Montoring",
        "version": APP_VERSION,
        "python": os.sys.version.split()[0],
        "home": APP_HOME,
        "euid": os.geteuid(),
        "root": os.geteuid() == 0,
        "sudo": PASSWORDLESS_SUDO,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })

# ------------------------------------------------------------------
# Metrics history (server-side ring buffer, powers charts that survive
# page reloads — 2s resolution, last 60 minutes kept in memory only)
# ------------------------------------------------------------------
HISTORY_MAX = 1800  # 60 min @ 2s
_HISTORY = {"samples": [], "lock": threading.Lock()}

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

threading.Thread(target=_sampler_loop, daemon=True, name="montoring-sampler").start()

@app.route("/api/history")
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

# ------------------------------------------------------------------
# System information (static facts for the info panel)
# ------------------------------------------------------------------
def _cpu_model():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "unknown"

def _virtualization():
    if which("systemd-detect-virt"):
        code, out, _ = run("systemd-detect-virt 2>/dev/null")
        v = (out or "").strip()
        if code == 0 and v and v != "none":
            return v
    return "bare metal"

@app.route("/api/systeminfo")
def api_systeminfo():
    try:
        import psutil
        import platform
        return jsonify({
            "hostname": os.uname().nodename,
            "os": _pretty_distro(),
            "kernel": f"{os.uname().sysname} {os.uname().release}",
            "arch": os.uname().machine,
            "cpu_model": _cpu_model(),
            "cpu_cores": psutil.cpu_count(logical=True) or 1,
            "virtualization": _virtualization(),
            "python": platform.python_version(),
            "boot_time": int(psutil.boot_time()),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ------------------------------------------------------------------
# System status (live metrics)
# ------------------------------------------------------------------
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

@app.route("/api/status")
def api_status():
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.4)
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
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ------------------------------------------------------------------
# Health checks
# ------------------------------------------------------------------
def _int_or(value, default=0):
    try:
        return int(str(value).strip())
    except Exception:
        return default

@app.route("/api/checks")
def api_checks():
    results = []
    # Disk usage
    code, out, err = run("df -h /")
    results.append({"name": "Disk Usage", "status": "ok" if code == 0 else "fail",
                    "detail": out.splitlines()[1].strip() if out and len(out.splitlines()) > 1 else (err or "ok")})
    # Pending updates (package-manager aware)
    updates = _updatable_packages()
    results.append({"name": "Pending Updates", "status": "warn" if updates["count"] > 0 else "ok",
                    "detail": f"{updates['count']} packages upgradable ({updates['manager'] or 'n/a'})"})
    # Broken packages
    code, out, err = run("dpkg --audit 2>&1")
    broken = "found" if "error" in (out + err).lower() else "none"
    results.append({"name": "Broken Packages", "status": "warn" if broken != "none" else "ok", "detail": broken})
    # Failed systemd services
    failed = "0"
    if which("systemctl"):
        code, out, err = run("systemctl --failed --no-pager --quiet 2>/dev/null | wc -l")
        failed = out.strip() if out else "0"
        results.append({"name": "Failed Services", "status": "warn" if _int_or(failed) > 0 else "ok",
                        "detail": f"{failed} failed"})
    # Current-boot kernel errors. Use journal priority instead of matching words
    # such as "error" in otherwise harmless device names/messages.
    if which("journalctl"):
        kernel_cmd = "journalctl -k -b -p err..alert --no-pager -q 2>/dev/null"
    else:
        kernel_cmd = "dmesg --level=err,crit,alert,emerg 2>/dev/null"
    code, out, err = run(f"{kernel_cmd} | wc -l")
    dmsg = out.strip() if code == 0 and out else "0"
    results.append({"name": "Kernel Errors", "status": "warn" if _int_or(dmsg) > 0 else "ok",
                    "detail": f"{dmsg} current-boot error lines"})
    # Zombie processes
    try:
        import psutil
        zombies = [p for p in psutil.process_iter(["status"]) if p.info["status"] == "zombie"]
        results.append({"name": "Zombie Processes", "status": "warn" if zombies else "ok",
                        "detail": f"{len(zombies)} zombies"})
    except Exception:
        pass
    return jsonify(results)

# ------------------------------------------------------------------
# Troubleshooting Hub - Comprehensive diagnostic & auto-fix
# ------------------------------------------------------------------
TROUBLESHOOT_FIXES = {
    "apt": {
        "broken_packages": "dpkg --configure -a && apt install -f -y -qq",
        "clean_cache": "apt clean && apt autoclean",
        "autoremove": "apt autoremove -y -qq",
    },
    "dnf": {
        "broken_packages": "dnf distro-sync -y --quiet",
        "clean_cache": "dnf clean all -y",
        "autoremove": "dnf autoremove -y --quiet",
    },
    "yum": {
        "broken_packages": "yum distro-sync -y",
        "clean_cache": "yum clean all",
        "autoremove": "yum autoremove -y",
    },
    "zypper": {
        "broken_packages": "zypper dist-upgrade -y --quiet",
        "clean_cache": "zypper clean",
        "autoremove": "zypper clean",
    },
    "pacman": {
        "broken_packages": "pacman -Syu --noconfirm",
        "clean_cache": "pacman -Sc --noconfirm",
        "autoremove": "pacman -Rns $(pacman -Qdtq) --noconfirm 2>/dev/null || true",
    },
    "apk": {
        "broken_packages": "apk fix",
        "clean_cache": "apk cache clean",
        "autoremove": "apk autoremove --quiet",
    },
}

SYSTEMD_FIXES = {
    "reset_failed": "systemctl reset-failed",
    "restart_failed": "for s in $(systemctl --failed --no-legend --plain 2>/dev/null | awk '{print $1}'); do systemctl restart $s 2>/dev/null; done",
}

LOG_FIXES = {
    "vacuum_journal": "journalctl --vacuum-time=7d --vacuum-size=100M",
    "clear_old_logs": "find /var/log -type f -name '*.gz' -mtime +30 -delete 2>/dev/null",
}

# ------------------------------------------------------------------
# Troubleshooting Hub — guided diagnostics, fix & verification
# ------------------------------------------------------------------
TROUBLESHOOT_CATEGORIES = {
    "cpu":      {"label": "CPU & Processes",    "icon": "cpu"},
    "memory":   {"label": "Memory & Swap",      "icon": "mem"},
    "disk":     {"label": "Disk & Storage",     "icon": "disk"},
    "services": {"label": "Services & Daemons", "icon": "gear"},
    "network":  {"label": "Network",            "icon": "network"},
    "packages": {"label": "Packages & Updates", "icon": "package"},
    "kernel":   {"label": "Kernel & Hardware",  "icon": "zap"},
}
CATEGORY_ORDER = ["cpu", "memory", "disk", "services", "network", "packages", "kernel"]

def _top_processes(count=6, order="cpu"):
    """Small, fast process snapshot for deep-diagnostics tables."""
    try:
        import psutil
        rows = []
        for p in psutil.process_iter(["pid", "name", "username", "memory_percent", "status"]):
            try:
                rss = p.memory_info().rss if hasattr(p, "memory_info") else 0
                rows.append({
                    "pid": p.info["pid"],
                    "name": p.info["name"] or "?",
                    "user": p.info["username"] or "-",
                    "cpu": round(p.cpu_percent(interval=None) or 0.0, 1),
                    "mem": round(p.info["memory_percent"] or 0.0, 1),
                    "rss_mb": round((rss or 0) / 1024 / 1024, 1),
                    "status": p.info["status"],
                })
            except Exception:
                continue
        key = "mem" if order == "mem" else "cpu"
        rows.sort(key=lambda r: r.get(key, 0), reverse=True)
        return rows[:count]
    except Exception:
        return []

def _diag_issue(iid, name, detail, category, severity, icon=None, fix=None,
                evidence=None, impact="", recommended_fix=None, verify=None,
                deep=None, sample=None, processes=None, services=None):
    """Build one enriched diagnostic issue (keeps legacy fields intact)."""
    rec = recommended_fix or {}
    return {
        "id": iid,
        "name": name,
        "detail": detail,
        "category": category,
        "severity": severity,
        "icon": icon or "alert",
        "evidence": evidence or [],
        "impact": impact or "",
        "verify": verify or [],
        "deep": deep,
        "sample": sample,
        "processes": processes,
        "services": services,
        "fix": rec.get("id") or fix,
        "recommended_fix": rec or None,
    }

def _diag_ev(label, value, cmd=None):
    e = {"label": label, "value": value}
    if cmd:
        e["cmd"] = cmd
    return e

def _diag_scan():
    issues = {"critical": [], "warnings": [], "info": []}
    all_issues = []

    def put(severity, issue):
        issues[severity].append(issue)
        all_issues.append(issue)

    try:
        import psutil
    except Exception:
        psutil = None

    # -------------------------------------------------- 1. Disk & storage
    try:
        du = psutil.disk_usage("/")
        total_gb, used_gb, free_gb = du.total / 1024**3, du.used / 1024**3, du.free / 1024**3
        pct = round(du.percent, 1)
        disk_ev = [
            _diag_ev("Root usage", f"{pct:.0f}%  ·  {used_gb:.1f} / {total_gb:.1f} GB used", "df -h /"),
            _diag_ev("Free space", f"{free_gb:.1f} GB", "df -h /"),
        ]
        disk_fix = {
            "id": "clear-logs",
            "label": "Free disk space",
            "description": "Vacuum the system journal and remove old compressed logs to reclaim space. This never touches recent logs.",
            "risk": "low",
            "commands": ["journalctl --vacuum-time=7d --vacuum-size=100M",
                         "find /var/log -type f -name '*.gz' -delete"],
        }
        disk_verify = [_diag_ev("Re-check root usage", "below 95%", "df -h /")]
        if pct >= 95:
            put("critical", _diag_issue(
                "disk_critical", "Critical Disk Space",
                f"Root filesystem is {pct:.0f}% full — only {free_gb:.1f} GB free.",
                "disk", "critical", "disk", fix="clear-logs", evidence=disk_ev,
                impact="Writes will fail: log rotation, package installs, temp files and application data. A full root filesystem can stop services and block logins.",
                recommended_fix=disk_fix, verify=disk_verify))
        elif pct >= 85:
            put("warnings", _diag_issue(
                "disk_warning", "Low Disk Space",
                f"Root filesystem is {pct:.0f}% full — {free_gb:.1f} GB free.",
                "disk", "warning", "disk", fix="clear-logs", evidence=disk_ev,
                impact="Continuous growth may soon trigger failed writes, truncated logs and service failures.",
                recommended_fix=disk_fix, verify=disk_verify))
        # inodes
        code, out, _ = run("df -i / 2>/dev/null")
        if code == 0 and out:
            lines = out.splitlines()
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 5:
                    try:
                        ipct = int(parts[4].replace("%", ""))
                        if ipct >= 90:
                            put("warnings", _diag_issue(
                                "disk_inodes", "Inodes Nearly Exhausted",
                                f"Root filesystem uses {ipct}% of inodes; new files may be refused even with free space.",
                                "disk", "warning", "disk", fix="clear-old-logs",
                                evidence=[_diag_ev("Inode usage", f"{ipct}%  ·  {parts[3]} free", "df -i /")],
                                impact="File creation will fail across the system, breaking caches, mail, logs and package operations.",
                                recommended_fix={
                                    "id": "clear-old-logs",
                                    "label": "Remove old log files",
                                    "description": "Delete compressed log files older than 30 days to free inodes in /var/log.",
                                    "risk": "low",
                                    "commands": ["find /var/log -type f -name '*.gz' -mtime +30 -delete"],
                                },
                                verify=[_diag_ev("Re-check inode usage", "below 90%", "df -i /")]))
                    except ValueError:
                        pass
    except Exception:
        pass

    # -------------------------------------------------- 2. Memory & swap
    try:
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        mem_ev = [
            _diag_ev("Memory used", f"{mem.percent:.0f}%  ·  {mem.used / 1024**3:.1f} / {mem.total / 1024**3:.1f} GB", "free -h"),
            _diag_ev("Swap used", f"{swap.percent:.0f}%  ·  {swap.used / 1024**3:.1f} / {swap.total / 1024**3:.1f} GB",
                     "free -h") if swap.total else None,
        ]
        mem_ev = [e for e in mem_ev if e]
        mem_fix = {
            "id": None,
            "label": "Free memory / tune workload",
            "description": "Use the Processes dashboard to inspect and stop a confirmed runaway process, or run the read-only commands below. No process is killed automatically.",
            "risk": "medium",
            "commands": [
                "ps -eo pid,user,%mem,rss,comm --sort=-rss | head -20",
                "systemd-cgtop --iterations=1 --depth=2",
            ],
        }
        mem_verify = [_diag_ev("Re-check memory usage", "below 85%", "free -h")]
        deep_mem = {"kind": "procs", "title": "Top memory consumers", "columns": ["PID", "Process", "CPU %", "MEM %", "RSS"],
                    "rows": _top_processes(6, "mem")}
        top = _top_processes(1, "mem")
        top_txt = f"Top consumer: {top[0]['name']} (PID {top[0]['pid']}, {top[0]['mem']}%)" if top else "No consumer info"
        if mem.percent >= 95:
            put("critical", _diag_issue(
                "memory_critical", "Critical Memory Usage",
                f"System memory is {mem.percent:.0f}% full ({mem.used / 1024**3:.1f} / {mem.total / 1024**3:.1f} GB). {top_txt}",
                "memory", "critical", "mem", evidence=mem_ev, deep=deep_mem,
                impact="The kernel will swap aggressively, causing severe slowdowns; the OOM killer may terminate processes and services.",
                recommended_fix=mem_fix, verify=mem_verify))
        elif mem.percent >= 85:
            put("warnings", _diag_issue(
                "memory_warning", "High Memory Usage",
                f"System memory is {mem.percent:.0f}% full. {top_txt}",
                "memory", "warning", "mem", evidence=mem_ev, deep=deep_mem,
                impact="Memory pressure can degrade performance, increase swap activity and eventually trigger OOM kills.",
                recommended_fix=mem_fix, verify=mem_verify))
        if swap.total and swap.percent >= 40:
            put("warnings", _diag_issue(
                "swap_warning", "High Swap Usage",
                f"Swap is {swap.percent:.0f}% used ({swap.used / 1024**3:.1f} / {swap.total / 1024**3:.1f} GB) — sustained swapping slows the whole system.",
                "memory", "warning", "mem",
                evidence=[_diag_ev("Swap used", f"{swap.percent:.0f}%", "free -h"),
                          _diag_ev("Memory pressure", f"{mem.percent:.0f}% RAM used", "free -h")],
                impact="Disk-backed paging causes high latency for every process; heavy swapping also wears SSDs.",
                recommended_fix={
                    "id": None, "label": "Reduce memory pressure",
                    "description": "Use the Processes dashboard to stop only a confirmed runaway process. These read-only commands identify memory and swap consumers before you act.",
                    "risk": "medium", "commands": [
                        "ps -eo pid,user,%mem,rss,comm --sort=-rss | head -20",
                        "vmstat 1 5",
                    ],
                },
                verify=[_diag_ev("Re-check swap usage", "below 40%", "free -h")]))
    except Exception:
        pass

    # -------------------------------------------------- 3. CPU & load
    try:
        cores = psutil.cpu_count(logical=True) or 1
        l1, l5, l15 = os.getloadavg()
        load_ev = [
            _diag_ev("Load average", f"{l1:.2f} / {l5:.2f} / {l15:.2f}  (over {cores} logical cores)", "uptime"),
        ]
        load_verify = [_diag_ev("Re-check load average", f"below {cores * 1.2:.0f} (load1)", "uptime")]
        deep_cpu = {"kind": "procs", "title": "Top CPU consumers", "columns": ["PID", "Process", "CPU %", "MEM %", "RSS"],
                    "rows": _top_processes(6, "cpu")}
        if l1 >= cores * 2:
            put("critical", _diag_issue(
                "cpu_load_critical", "Extreme CPU Load",
                f"Load average {l1:.2f} is more than {cores * 2:.0f} (2× {cores} cores) — the system is overloaded.",
                "cpu", "critical", "cpu", evidence=load_ev, deep=deep_cpu,
                impact="Interactive sessions stall, scheduled jobs miss deadlines, and timeouts cascade across services.",
                recommended_fix={
                    "id": None, "label": "Identify and tame the run-away workload",
                    "description": "Inspect the top CPU consumers below. Restarting or adjusting them is safer than killing blindly.",
                    "risk": "medium", "commands": ["top -b -n1 | head -20"],
                }, verify=load_verify))
        elif l1 >= cores * 1.2:
            put("warnings", _diag_issue(
                "cpu_load_high", "High CPU Load",
                f"Load average {l1:.2f} exceeds {cores} logical cores — run-away jobs may be competing for CPU.",
                "cpu", "warning", "cpu", evidence=load_ev, deep=deep_cpu,
                impact="Responsiveness drops; CPU-bound tasks queue up and can starve interactive work.",
                recommended_fix={
                    "id": None, "label": "Balance CPU demand",
                    "description": "Look at the top consumers; defer batch jobs or restart misbehaving processes found below.",
                    "risk": "medium", "commands": ["top -b -n1 | head -20"],
                }, verify=load_verify))
        top_cpu = _top_processes(1, "cpu")
        if top_cpu and top_cpu[0]["cpu"] >= 75:
            p = top_cpu[0]
            put("warnings", _diag_issue(
                "high_cpu_process", "Single Process Saturating CPU",
                f"{p['name']} (PID {p['pid']}) is using {p['cpu']}% CPU — it may be hung or in a hot loop.",
                "cpu", "warning", "cpu",
                evidence=[_diag_ev("Busiest process", f"{p['name']} — {p['cpu']}% CPU, {p['mem']}% MEM (PID {p['pid']})", f"ps -p {p['pid']} -o pid,pcpu,pmem,comm"),
                          _diag_ev("Status", p["status"], "ps aux")],
                impact="A single hot process can eat an entire core, delaying everything else and burning power.",
                recommended_fix={
                    "id": None, "label": "Review the process",
                    "description": "Stop or restart the process if it is stuck; only kill it after inspecting its behaviour in the Processes tab.",
                    "risk": "high", "commands": [f"ps -p {p['pid']} -o pid,pcpu,pmem,stat,cmd"],
                },
                verify=[_diag_ev("Re-check process CPU", "below 75%", "ps -eo pid,pcpu,comm --sort=-pcpu | head")],
                deep={"kind": "procs", "title": "Top CPU consumers", "columns": ["PID", "Process", "CPU %", "MEM %", "RSS"],
                      "rows": _top_processes(6, "cpu")}))
    except Exception:
        pass

    # zombies
    try:
        zombies = [p for p in psutil.process_iter(["pid", "name", "status", "username"]) if p.info["status"] == "zombie"]
        if zombies:
            zinfo = [{"pid": z.info["pid"], "name": z.info["name"]} for z in zombies[:5]]
            put("warnings", _diag_issue(
                "zombie_processes", "Zombie Processes",
                f"{len(zombies)} zombie process(es) detected — they hold PIDs and process slots until their parent reaps them.",
                "cpu", "warning", "cpu", fix="clean-zombies", processes=zinfo,
                evidence=[_diag_ev("Zombie count", f"{len(zombies)}", "ps -eo stat,pid,comm | awk '$1 ~ /^Z/ {print}'")],
                impact="Zombies consume PIDs and kernel process table entries. Many of them can exhaust the PID limit and block new processes.",
                recommended_fix={
                    "id": "clean-zombies",
                    "label": "Reap zombie processes",
                    "description": "Sends SIGTERM to the parent processes of zombies so the kernel reaps them.",
                    "risk": "medium",
                    "commands": ["ps -eo stat,pid,ppid,comm | grep \'^Z\'"],
                },
                verify=[_diag_ev("Re-check zombie count", "0 zombies", "ps -eo stat | grep -c '^Z'")]))
    except Exception:
        pass

    # -------------------------------------------------- 4. Services
    if which("systemctl"):
        code, out, err = run("systemctl --failed --no-pager --plain --no-legend 2>/dev/null")
        failed_services = []
        for line in (out or "").splitlines():
            parts = line.split()
            if parts:
                failed_services.append(parts[0])
        if failed_services:
            svc_rows = [{"name": s, "status": "failed"} for s in failed_services[:20]]
            put("critical", _diag_issue(
                "failed_services", "Failed Systemd Services",
                f"{len(failed_services)} service(s) failed: {', '.join(failed_services[:5])}{'…' if len(failed_services) > 5 else ''}",
                "services", "critical", "gear", fix="restart-failed-services", services=failed_services,
                evidence=[_diag_ev("Failed units", ", ".join(failed_services[:8]) + ("…" if len(failed_services) > 8 else ""), "systemctl --failed"),
                          _diag_ev("Unit count", f"{len(failed_services)} failed", "systemctl --failed | wc -l")],
                impact="Failed services can break network, storage, logging and application availability until repaired.",
                recommended_fix={
                    "id": "restart-failed-services",
                    "label": "Reset and restart failed services",
                    "description": "Clears failed state and restarts each failed unit; services that fail again stay visible.",
                    "risk": "medium",
                    "commands": ["systemctl reset-failed"] + [f"systemctl restart {shlex.quote(unit)}" for unit in failed_services[:8]],
                },
                verify=[_diag_ev("Re-check failed services", "0 failed", "systemctl --failed")],
                deep={"kind": "services", "title": "Failed units", "rows": svc_rows}))

    # -------------------------------------------------- 5. Network
    try:
        io = psutil.net_io_counters()
        errs = (io.errin or 0) + (io.errout or 0) + (io.dropin or 0) + (io.dropout or 0)
        if errs > 0:
            sev = "warning" if errs >= 100 else "info"
            put(sev, _diag_issue(
                "network_errors", "Network Errors Detected",
                f"Cumulative NIC errors/drops: {errs} (errin {io.errin}, errout {io.errout}, dropin {io.dropin}, dropout {io.dropout}).",
                "network", sev, "network",
                evidence=[_diag_ev("Interface counters", f"errin {io.errin} · errout {io.errout} · dropin {io.dropin} · dropout {io.dropout}", "ip -s link")],
                impact="Packet errors and drops cause retransmissions, timeouts, slow throughput and flaky connections.",
                recommended_fix={
                    "id": None, "label": "Inspect interfaces & cabling",
                    "description": "Check `ip -s link` for which interface is dropping; verify cables, duplex settings and driver warnings in dmesg.",
                    "risk": "low", "commands": ["ip -s link", "dmesg --level=err,warn | tail -40"],
                },
                verify=[_diag_ev("Re-check counters", "no new errors", "ip -s link")]))
        # interface down but configured with IPv4
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        import socket as _socket
        down = []
        for name, st in stats.items():
            if name.startswith("lo") or st.isup:
                continue
            has_ipv4 = any(a.family == _socket.AF_INET for a in addrs.get(name, []))
            if has_ipv4:
                down.append(name)
        if down:
            put("warnings", _diag_issue(
                "interface_down", "Network Interface Down",
                f"Configured interface(s) are down: {', '.join(down)}.",
                "network", "warning", "network",
                evidence=[_diag_ev("Interfaces down", ", ".join(down), "ip -br addr")],
                impact="Any service bound to these interfaces is unreachable; connectivity and remote access may be lost.",
                recommended_fix={
                    "id": None, "label": "Bring the interface up",
                    "description": "Use nmcli/ip to bring the link up, or check physical cabling and network-manager state.",
                    "risk": "medium", "commands": ["ip -br addr"] + [f"ip link set {shlex.quote(iface)} up" for iface in down],
                },
                verify=[_diag_ev("Re-check interface state", "up", "ip -br addr")]))
    except Exception:
        pass

    # -------------------------------------------------- 6. Packages
    mgr = _pkg_manager()
    code, out, err = run("dpkg --audit 2>/dev/null")
    if (out or err) and ("error" in (out + err).lower()):
        put("critical", _diag_issue(
            "broken_packages", "Broken Packages",
            "Package database has inconsistencies (dpkg reports errors).",
            "packages", "critical", "package", fix="fix-broken-packages",
            evidence=[_diag_ev("dpkg audit", (out or err).strip().splitlines()[0][:160] if (out or err).strip() else "errors", "dpkg --audit")],
            impact="Package installs, upgrades and removals can fail; broken dependencies can also break applications.",
            recommended_fix={
                "id": "fix-broken-packages",
                "label": "Repair package database",
                "description": "Reconfigures pending packages and installs missing dependencies (apt systems).",
                "risk": "medium",
                "commands": ["dpkg --configure -a", "apt install -f -y"],
            },
            verify=[_diag_ev("Re-run dpkg audit", "no errors", "dpkg --audit")]))
    updates = _updatable_packages()
    check_commands = {
        "apt": "apt list --upgradable",
        "dnf": "dnf check-update",
        "yum": "yum check-update",
        "zypper": "zypper list-updates",
        "pacman": "pacman -Qu",
        "apk": "apk version -l '<'",
    }
    check_command = check_commands.get(updates["manager"], "")
    upd_ev = [_diag_ev("Packages upgradable", f"{updates['count']} ({updates['manager'] or 'n/a'})", check_command)]
    upgrade_commands = {
        "apt": "apt update && apt upgrade -y",
        "dnf": "dnf upgrade -y",
        "yum": "yum upgrade -y",
        "zypper": "zypper update -y",
        "pacman": "pacman -Syu --noconfirm",
        "apk": "apk update && apk upgrade",
    }
    upd_fix = {
        "id": "upgrade-packages",
        "label": "Install available updates",
        "description": f"Runs the package-manager upgrade for {updates['manager'] or 'your system'}.",
        "risk": "medium",
        "commands": [upgrade_commands[updates["manager"]]] if updates["manager"] in upgrade_commands else [],
    }
    upd_verify = [_diag_ev("Re-check pending updates", "0 packages", check_command)]
    if updates["count"] > 50:
        put("warnings", _diag_issue(
            "many_updates", "Many Pending Updates",
            f"{updates['count']} packages can be updated ({updates['manager'] or 'unknown'}).",
            "packages", "warning", "package", fix="upgrade-packages", evidence=upd_ev,
            impact="Unpatched packages accumulate security and stability bugs; a large backlog also lengthens future update windows.",
            recommended_fix=upd_fix, verify=upd_verify))
    elif updates["count"] > 0:
        put("info", _diag_issue(
            "pending_updates", "Pending Updates",
            f"{updates['count']} packages upgradable ({updates['manager'] or 'n/a'}).",
            "packages", "info", "package", fix="upgrade-packages", evidence=upd_ev,
            impact="Updates fix bugs and security issues; keeping them current reduces risk.",
            recommended_fix=upd_fix, verify=upd_verify))

    # -------------------------------------------------- 7. Kernel
    # Restrict the scan to real err..alert priority records from this boot.
    # Text matching produced false warnings for harmless lines containing words
    # such as "failed" or "error".
    kernel_log_cmd = "journalctl -k -b -p err..alert --no-pager -q"
    code, out, err = run(f"{kernel_log_cmd} -n 20 2>/dev/null")
    if code != 0:
        kernel_log_cmd = "dmesg --level=err,crit,alert,emerg"
        code, out, err = run(f"{kernel_log_cmd} 2>/dev/null | tail -20")
    error_lines = [l.strip() for l in (out or "").splitlines() if l.strip()]
    if error_lines:
        text = "\n".join(error_lines).lower()
        commands = [kernel_log_cmd + " | tail -80"]
        guidance = "Review the exact component in the log before changing the system; kernel faults do not have a safe universal automatic fix."
        if any(k in text for k in ("i/o error", "blk_update", "buffer i/o", "nvme", "ata1:", "ata2:", "ata error")):
            guidance = "A storage fault is indicated. Back up important data first, identify the affected disk, then inspect its health."
            commands += [
                "lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL",
                "for d in $(lsblk -dn -o NAME,TYPE | awk '$2==\"disk\" {print \"/dev/\"$1}'); do smartctl -H \"$d\"; done",
            ]
        elif any(k in text for k in ("ext4-fs error", "xfs", "btrfs error", "filesystem error")):
            guidance = "A filesystem fault is indicated. Back up data and schedule an offline filesystem check; never run fsck on a mounted filesystem."
            commands += ["findmnt -no SOURCE,FSTYPE,OPTIONS /", "systemctl --failed --no-pager"]
        elif any(k in text for k in ("out of memory", "oom-killer", "killed process")):
            guidance = "The kernel ran out of memory. Identify the largest consumers in the Processes dashboard before restarting or stopping one."
            commands += ["ps -eo pid,user,%mem,rss,comm --sort=-rss | head -20", "journalctl -k -b -g 'oom|killed process' --no-pager"]
        elif any(k in text for k in ("firmware", "microcode", "acpi", "bios")):
            guidance = "Firmware or platform code is implicated. Check vendor firmware updates and current microcode before changing drivers."
            commands += ["fwupdmgr get-updates", "journalctl -k -b -g 'firmware|microcode|acpi|bios' --no-pager"]
        put("warnings", _diag_issue(
            "kernel_errors", "Kernel Errors Detected",
            f"{len(error_lines)} current-boot kernel error line(s) found.",
            "kernel", "warning", "zap", evidence=[
                _diag_ev("Error lines (last 20)", f"{len(error_lines)}", kernel_log_cmd),
                _diag_ev("Sample", error_lines[0][:180], kernel_log_cmd + " | tail"),
            ],
            sample=error_lines[:3],
            impact="Kernel errors can indicate failing hardware, driver bugs, memory pressure or filesystem corruption.",
            recommended_fix={
                "id": None, "label": "Resolve the reported kernel component",
                "description": guidance,
                "risk": "medium", "commands": commands,
            },
            verify=[_diag_ev("Re-check current-boot kernel errors", "no new errors after remediation/reboot", kernel_log_cmd)],
            deep={"kind": "logs", "title": "Current-boot kernel errors", "lines": error_lines[:8]}))

    # -------------------------------------------------- 9. Log hygiene (disk)
    code, out, err = run("find /var/log -type f -name '*.gz' -mtime +30 2>/dev/null | wc -l")
    old_logs = _int_or((out or "").strip()) if out else 0
    if old_logs > 10:
        put("info", _diag_issue(
            "old_logs", "Old Log Files",
            f"{old_logs} compressed log files older than 30 days still stored.",
            "disk", "info", "terminal", fix="clear-old-logs",
            evidence=[_diag_ev("Old logs", f"{old_logs} files", "find /var/log -name '*.gz' -mtime +30 | wc -l")],
            impact="Rotated logs consume inodes and disk space that belong to active data.",
            recommended_fix={
                "id": "clear-old-logs",
                "label": "Delete old rotated logs",
                "description": "Removes compressed logs older than 30 days; active logs are untouched.",
                "risk": "low", "commands": ["find /var/log -type f -name '*.gz' -mtime +30 -delete"],
            },
            verify=[_diag_ev("Re-check old logs", "10 or fewer", "find /var/log -name '*.gz' -mtime +30 | wc -l")]))
    code, out, err = run("journalctl --disk-usage 2>/dev/null")
    if out:
        match = re.search(r'(\d+(?:\.\d+)?)([KMGT]?)B', out)
        if match:
            size_mb = float(match.group(1))
            unit = match.group(2)
            if unit == "K":
                size_mb = size_mb / 1024
            elif unit == "G":
                size_mb = size_mb * 1024
            elif unit == "T":
                size_mb = size_mb * 1024 * 1024
            if size_mb > 2048:
                put("info", _diag_issue(
                    "large_journal", "Large Journal",
                    f"System journal uses {match.group(1)}{unit} — above the 2 GB comfort mark.",
                    "disk", "info", "terminal", fix="vacuum-journal",
                    evidence=[_diag_ev("Journal size", f"{match.group(1)}{unit}", "journalctl --disk-usage")],
                    impact="A growing journal consumes disk space and makes log searches slower.",
                    recommended_fix={
                        "id": "vacuum-journal",
                        "label": "Vacuum journal",
                        "description": "Trims the journal to 7 days / 100 MB.",
                        "risk": "low", "commands": ["journalctl --vacuum-time=7d --vacuum-size=100M"],
                    },
                    verify=[_diag_ev("Re-check journal size", "≤ 2 GB", "journalctl --disk-usage")]))

    # -------------------------------------------------- Summary
    total = len(all_issues)
    health_score = max(0, 100 - len(issues["critical"]) * 20 - len(issues["warnings"]) * 10 - len(issues["info"]) * 3)
    if health_score >= 95:
        grade = "Excellent"
    elif health_score >= 80:
        grade = "Good"
    elif health_score >= 60:
        grade = "Fair"
    elif health_score >= 40:
        grade = "At Risk"
    else:
        grade = "Poor"

    categories = {}
    for key in CATEGORY_ORDER:
        meta = TROUBLESHOOT_CATEGORIES.get(key, {"label": key, "icon": ""})
        cat = [i for i in all_issues if i.get("category") == key]
        state = "ok"
        if any(i["severity"] == "critical" for i in cat):
            state = "critical"
        elif any(i["severity"] == "warning" for i in cat):
            state = "warning"
        elif cat:
            state = "info"
        categories[key] = {
            "label": meta["label"], "icon": meta["icon"],
            "count": len(cat), "state": state, "issues": cat,
        }

    system = {}
    try:
        import psutil as _p
        import platform as _pl
        memv = _p.virtual_memory()
        diskv = _p.disk_usage("/")
        system = {
            "hostname": os.uname().nodename,
            "os": _pretty_distro(),
            "kernel": f"{os.uname().sysname} {os.uname().release}",
            "arch": os.uname().machine,
            "cpu_model": _cpu_model(),
            "cores": _p.cpu_count(logical=True) or 1,
            "uptime_s": int(time.time() - _p.boot_time()),
            "memory_total_gb": round(memv.total / 1024**3, 1),
            "memory_used_gb": round(memv.used / 1024**3, 1),
            "disk_total_gb": round(diskv.total / 1024**3, 1),
            "disk_used_gb": round(diskv.used / 1024**3, 1),
            "load": [round(x, 2) for x in os.getloadavg()] if hasattr(os, "getloadavg") else [],
            "temp_c": _read_temp_c(),
        }
    except Exception:
        pass

    capabilities = {
        "root": os.geteuid() == 0,
        "sudo": PASSWORDLESS_SUDO,
        "pkg_manager": mgr,
        "systemd": which("systemctl"),
        "journal": which("journalctl"),
    }

    return {
        "issues": issues,
        "total": total,
        "health_score": health_score,
        "grade": grade,
        "categories": categories,
        "capabilities": capabilities,
        "system": system,
        "timestamp": datetime.now().isoformat(),
    }

@app.route("/api/troubleshooting")
def api_troubleshooting():
    """Guided diagnostics: enriched issues, categories, evidence & fixes.

    Backwards-compatible with the previous schema (issues/total/health_score/
    timestamp) and additionally returns categories, grade, capabilities and
    system context so the UI can render a guided troubleshooting center.
    """
    try:
        return jsonify(_diag_scan())
    except Exception as e:
        return jsonify({"issues": {"critical": [], "warnings": [], "info": []},
                        "total": 0, "health_score": 100, "error": str(e)}), 500

# ------------------------------------------------------------------
# Post-fix verification (per issue)
# ------------------------------------------------------------------
def _verify_issue(iid):
    """Run the targeted check for one issue id."""
    def res(resolved, message, evidence=None):
        return {"resolved": bool(resolved),
                "state": "resolved" if resolved else "still_present",
                "message": message, "evidence": evidence or []}

    try:
        import psutil
    except Exception:
        psutil = None

    try:
        if iid in ("disk_critical", "disk_warning", "disk_inodes"):
            pct = psutil.disk_usage("/").percent
            if iid == "disk_inodes":
                _, out, _ = run("df -i / 2>/dev/null")
                parts = (out.splitlines() or ["", ""])[1].split() if out and len(out.splitlines()) > 1 else []
                ipct = int(parts[4].replace("%", "")) if len(parts) >= 5 else 0
                return res(ipct < 90, f"Inode usage {ipct}%",
                           [_diag_ev("Inode usage", f"{ipct}%", "df -i /")])
            return res(pct < 85, f"Root filesystem at {pct:.1f}%",
                       [_diag_ev("Root usage", f"{pct:.1f}%", "df -h /")])
        if iid in ("memory_critical", "memory_warning", "swap_warning"):
            mem = psutil.virtual_memory()
            swap = psutil.swap_memory()
            if iid == "swap_warning":
                return res(swap.total == 0 or swap.percent < 40, f"Swap at {swap.percent:.1f}%",
                           [_diag_ev("Swap usage", f"{swap.percent:.1f}%", "free -h")])
            return res(mem.percent < 85, f"Memory at {mem.percent:.1f}%",
                       [_diag_ev("Memory usage", f"{mem.percent:.1f}%", "free -h")])
        if iid in ("cpu_load_high", "cpu_load_critical"):
            cores = psutil.cpu_count(logical=True) or 1
            l1 = os.getloadavg()[0]
            threshold = cores * 2 if iid == "cpu_load_critical" else cores * 1.2
            return res(l1 < threshold, f"Load1 {l1:.2f} (cores {cores})",
                       [_diag_ev("Load average", f"{l1:.2f}", "uptime")])
        if iid == "high_cpu_process":
            top = _top_processes(1, "cpu")
            pct = top[0]["cpu"] if top else 0
            return res(pct < 75, f"Busiest process {pct:.1f}% CPU",
                       [_diag_ev("Busiest process", f"{pct:.1f}%", "ps -eo pcpu,comm --sort=-pcpu | head")])
        if iid == "zombie_processes":
            zb = [p for p in psutil.process_iter(["status"]) if p.info["status"] == "zombie"]
            return res(not zb, f"{len(zb)} zombie(s)", [_diag_ev("Zombies", str(len(zb)), "ps -eo stat | grep -c '^Z'")])
        if iid == "failed_services":
            _, out, _ = run("systemctl --failed --no-pager --quiet 2>/dev/null | wc -l")
            n = _int_or((out or "").strip()) if out else 0
            return res(n == 0, f"{n} failed service(s)", [_diag_ev("Failed units", str(n), "systemctl --failed")])
        if iid == "broken_packages":
            _, out, err = run("dpkg --audit 2>&1")
            bad = "error" in (out + err).lower()
            return res(not bad, "dpkg audit clean" if not bad else "dpkg reports errors",
                       [_diag_ev("dpkg audit", "clean" if not bad else "errors", "dpkg --audit")])
        if iid in ("many_updates", "pending_updates"):
            u = _updatable_packages()
            check_cmd = {
                "apt": "apt list --upgradable", "dnf": "dnf check-update",
                "yum": "yum check-update", "zypper": "zypper list-updates",
                "pacman": "pacman -Qu", "apk": "apk version -l '<'",
            }.get(u["manager"], "")
            return res(u["count"] == 0, f"{u['count']} pending update(s)",
                       [_diag_ev("Pending updates", str(u["count"]), check_cmd)])
        if iid == "kernel_errors":
            cmd = "journalctl -k -b -p err..alert --no-pager -q 2>/dev/null"
            code, out, _ = run(cmd)
            if code != 0:
                cmd = "dmesg --level=err,crit,alert,emerg 2>/dev/null"
                _, out, _ = run(cmd)
            n = len([line for line in (out or "").splitlines() if line.strip()])
            return res(n == 0, f"{n} current-boot kernel error line(s)",
                       [_diag_ev("Kernel errors", str(n), cmd)])
        if iid == "network_errors":
            io = psutil.net_io_counters()
            errs = (io.errin or 0) + (io.errout or 0) + (io.dropin or 0) + (io.dropout or 0)
            return res(errs == 0, f"{errs} errors/drops", [_diag_ev("Counters", str(errs), "ip -s link")])
        if iid == "interface_down":
            stats = psutil.net_if_stats()
            addrs = psutil.net_if_addrs()
            import socket as _s
            down = [n for n, st in stats.items() if not st.isup and not n.startswith("lo")
                    and any(a.family == _s.AF_INET for a in addrs.get(n, []))]
            return res(not down, "all configured interfaces up" if not down else "down: " + ", ".join(down),
                       [_diag_ev("Interfaces", "up" if not down else ", ".join(down), "ip -br addr")])
        if iid == "old_logs":
            _, out, _ = run("find /var/log -type f -name '*.gz' -mtime +30 2>/dev/null | wc -l")
            n = _int_or((out or "").strip()) if out else 0
            return res(n <= 10, f"{n} old log file(s)", [_diag_ev("Old logs", str(n), "find /var/log -name '*.gz' -mtime +30 | wc -l")])
        if iid == "large_journal":
            _, out, _ = run("journalctl --disk-usage 2>/dev/null")
            m = re.search(r'(\d+(?:\.\d+)?)([KMGT]?)B', out or "")
            if m:
                size_mb = float(m.group(1))
                unit = m.group(2)
                if unit == "K":
                    size_mb /= 1024
                elif unit == "G":
                    size_mb *= 1024
                elif unit == "T":
                    size_mb *= 1024 * 1024
                return res(size_mb <= 2048, f"Journal {m.group(1)}{unit}",
                           [_diag_ev("Journal size", f"{m.group(1)}{unit}", "journalctl --disk-usage")])
            return res(True, "journalctl unavailable")
    except Exception as e:
        return res(False, "verification error: " + str(e)[:200])
    return res(False, "no automated verification for this issue")

@app.route("/api/troubleshooting/verify", methods=["POST"])
def api_troubleshooting_verify():
    """Targeted post-fix verification for one or more issue ids."""
    data = request.json or {}
    ids = list(data.get("issue_ids") or [])
    if data.get("issue_id"):
        ids.append(data["issue_id"])
    ids = [str(i)[:64] for i in ids][:60]
    results = {i: _verify_issue(i) for i in ids}
    return jsonify({"results": results, "timestamp": datetime.now().isoformat()})


@app.route("/api/troubleshooting/fix-all", methods=["POST"])
def api_troubleshooting_fix_all():
    """Fix all detected issues with sudo access."""
    results = {"fixed": [], "failed": [], "skipped": []}
    mgr = _pkg_manager()
    
    data = request.json or {}
    fixes_to_run = data.get("fixes", [])
    
    # Scan in-process. A localhost curl could fail behind a proxy, with a
    # non-default port, or during startup even though this API request worked.
    try:
        issues_data = _diag_scan()
    except Exception as exc:
        return jsonify({"fixed": [], "failed": [{"fix": "scan", "output": str(exc)[:300]}], "skipped": []}), 500

    all_issues = issues_data.get("issues", {}).get("critical", []) + \
                 issues_data.get("issues", {}).get("warnings", []) + \
                 issues_data.get("issues", {}).get("info", [])
    
    # Determine which fixes to run
    fixes_needed = set()
    for issue in all_issues:
        # Manual-guidance issues have fix=None; never send that sentinel to
        # the executor as an "Unknown fix" action.
        fix_id = issue.get("fix")
        if fix_id:
            fixes_needed.add(str(fix_id)[:80])
    
    # If specific fixes requested, filter
    if fixes_to_run:
        fixes_needed = fixes_needed.intersection(set(fixes_to_run))
    
    def record_fix(fix_result, code=0):
        """Do not report a shell command as fixed when it actually failed."""
        if code == 0:
            results["fixed"].append(fix_result)
        else:
            results["failed"].append(fix_result)

    # Execute fixes
    for fix in fixes_needed:
        fix_result = {"fix": fix, "output": ""}
        code = 0
        try:
            if fix == "fix-broken-packages" and mgr:
                cmd = TROUBLESHOOT_FIXES.get(mgr, {}).get("broken_packages", "")
                if cmd:
                    code, out, err = run(cmd, timeout=300, sudo=True)
                    fix_result["output"] = (out or err or ("Done" if code == 0 else "command failed")).strip()[-500:]
                    record_fix(fix_result, code)
                else:
                    fix_result["output"] = f"No fix available for {mgr}"
                    results["skipped"].append(fix_result)
            
            elif fix == "clear-logs":
                code, out, err = run(
                    "journalctl --vacuum-time=7d --vacuum-size=100M >/dev/null 2>&1; "
                    "find /var/log -type f -name '*.gz' -delete 2>/dev/null; echo 'Logs cleared'",
                    timeout=60, sudo=True)
                fix_result["output"] = (out or err or "Logs cleared").strip()[-500:]
                record_fix(fix_result, code)
            
            elif fix == "clear-old-logs":
                code, out, err = run("find /var/log -type f -name '*.gz' -mtime +30 -delete 2>/dev/null; echo 'Old logs cleared'", timeout=60, sudo=True)
                fix_result["output"] = (out or err or ("Done" if code == 0 else "command failed")).strip()[-500:]
                record_fix(fix_result, code)
            
            elif fix == "vacuum-journal":
                code, out, err = run("journalctl --vacuum-time=7d --vacuum-size=100M 2>/dev/null; echo 'Journal vacuumed'", timeout=60, sudo=True)
                fix_result["output"] = (out or err or ("Done" if code == 0 else "command failed")).strip()[-500:]
                record_fix(fix_result, code)
            
            elif fix == "update-packages" and mgr:
                # Updating package metadata is not the same as removing
                # packages. Use the manager's update command here.
                cmd = FIX_COMMANDS.get(mgr, {}).get("update", "")
                if cmd:
                    code, out, err = run(cmd, timeout=300, sudo=True)
                    fix_result["output"] = (out or err or ("Done" if code == 0 else "command failed")).strip()[-500:]
                    record_fix(fix_result, code)

            elif fix == "upgrade-packages" and mgr:
                cmd = FIX_COMMANDS.get(mgr, {}).get("upgrade", "")
                if cmd:
                    code, out, err = run(cmd, timeout=600, sudo=True)
                    fix_result["output"] = (out or err or "Upgrade complete").strip()[-500:]
                    record_fix(fix_result, code)
                else:
                    fix_result["output"] = f"No upgrade command available for {mgr}"
                    results["skipped"].append(fix_result)

            elif fix == "restart-failed-services":
                if which("systemctl"):
                    code, out, err = run(SYSTEMD_FIXES["reset_failed"], timeout=30, sudo=True)
                    code2, out2, err2 = run(SYSTEMD_FIXES["restart_failed"], timeout=60, sudo=True)
                    fix_result["output"] = ((out or "") + (out2 or "")).strip()[-500:] or "Reset complete"
                    record_fix(fix_result, code)
            
            elif fix == "clean-zombies":
                try:
                    import psutil
                    killed = 0
                    for p in psutil.process_iter(["pid", "name", "status"]):
                        try:
                            if p.info["status"] == "zombie":
                                parent = p.parent()
                                if parent:
                                    parent.terminate()
                                    killed += 1
                        except:
                            pass
                    fix_result["output"] = f"Attempted to clean {killed} zombie processes"
                    record_fix(fix_result, code)
                except Exception as e:
                    fix_result["output"] = str(e)
                    results["failed"].append(fix_result)
            
            else:
                fix_result["output"] = f"Unknown fix: {fix}"
                results["skipped"].append(fix_result)
                
        except Exception as e:
            fix_result["output"] = str(e)
            results["failed"].append(fix_result)
    
    if not fixes_needed:
        results["skipped"].append({"fix": "all", "output": "No fixes needed - system is healthy!"})
    
    return jsonify(results)

# ------------------------------------------------------------------
# Maintenance / fix actions (safe, non-destructive)
# ------------------------------------------------------------------
def _pkg_manager():
    for mgr, cmd in (("apt", "apt-get"), ("dnf", "dnf"), ("yum", "yum"),
                     ("zypper", "zypper"), ("pacman", "pacman"), ("apk", "apk")):
        if which(cmd):
            return mgr
    return None

FIX_COMMANDS = {
    "apt":    {"update": "apt update -qq", "upgrade": "apt upgrade -y -qq",
               "autoremove": "apt autoremove -y -qq", "clean": "apt clean"},
    "dnf":    {"update": "dnf makecache --quiet", "upgrade": "dnf upgrade -y --quiet",
               "autoremove": "dnf autoremove -y --quiet", "clean": "dnf clean all -y"},
    "yum":    {"update": "yum makecache --quiet", "upgrade": "yum upgrade -y --quiet",
               "autoremove": "yum autoremove -y", "clean": "yum clean all"},
    "zypper": {"update": "zypper refresh", "upgrade": "zypper update -y --quiet",
               "autoremove": "zypper clean", "clean": "zypper clean"},
    "pacman": {"update": "pacman -Sy --noconfirm --quiet", "upgrade": "pacman -Syu --noconfirm",
               "autoremove": "pacman -Rns $(pacman -Qdtq) --noconfirm", "clean": "pacman -Sc --noconfirm"},
    "apk":    {"update": "apk update --quiet", "upgrade": "apk upgrade --quiet",
               "autoremove": "apk autoremove --quiet", "clean": "apk cache clean"},
}

@app.route("/api/fix", methods=["POST"])
def api_fix():
    action = (request.json or {}).get("action", "")
    mgr = _pkg_manager()
    msg = ""
    cmds = FIX_COMMANDS.get(mgr, {})
    if action in ("update", "upgrade", "autoremove", "clean"):
        if mgr:
            _, msg, err = run(cmds[action], timeout=300, sudo=True)
            if err:
                msg = (msg or "") + (err or "")
        else:
            msg = "No supported package manager found"
    elif action == "fix-broken":
        if mgr == "apt":
            _, msg, err = run("dpkg --configure -a && apt install -f -y -qq", timeout=300, sudo=True)
        else:
            msg = "fix-broken is only supported on apt-based systems"
    elif action == "clear-logs":
        _, msg, _ = run(
            "journalctl --vacuum-time=7d >/dev/null 2>&1; "
            "find /var/log -type f -name '*.gz' -delete 2>/dev/null; echo 'Logs cleared'",
            timeout=60, sudo=True)
    else:
        msg = "Unknown action"
    return jsonify({"result": (msg or "Done")[:500]})

# ------------------------------------------------------------------
# Upgradable packages (detailed)
# ------------------------------------------------------------------
def _updatable_packages():
    mgr = _pkg_manager()
    pkgs = []
    try:
        if mgr == "apt":
            _, out, _ = run("apt list --upgradable 2>/dev/null", timeout=30)
            for line in out.splitlines():
                if "/" in line and " " in line:
                    name = line.split("/")[0].strip()
                    ver = line.split()[1] if len(line.split()) > 1 else ""
                    pkgs.append({"name": name, "version": ver})
        elif mgr in ("dnf", "yum"):
            _, out, _ = run(f"{mgr} check-update -q", timeout=60)
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 3 and not line.startswith(("Last metadata", "Loaded plugins")):
                    pkgs.append({"name": parts[0], "version": parts[1]})
        elif mgr == "pacman":
            _, out, _ = run("pacman -Qu --quiet", timeout=30)
            pkgs = [{"name": l.strip(), "version": ""} for l in out.splitlines() if l.strip()]
        elif mgr == "apk":
            _, out, _ = run("apk version -l '<' 2>/dev/null", timeout=30)
            for line in out.splitlines()[1:]:
                parts = line.split()
                if parts:
                    pkgs.append({"name": parts[0], "version": parts[1] if len(parts) > 1 else ""})
        elif mgr == "zypper":
            _, out, _ = run("zypper -q list-updates", timeout=60)
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[0] in ("v",):
                    pkgs.append({"name": parts[2], "version": parts[3] if len(parts) > 3 else ""})
    except Exception:
        pass
    return {"manager": mgr, "count": len(pkgs), "packages": pkgs[:200]}

@app.route("/api/updates")
def api_updates():
    return jsonify(_updatable_packages())

# ------------------------------------------------------------------
# Processes
# ------------------------------------------------------------------
_PRIMED = {"flag": False}

@app.route("/api/processes")
def api_processes():
    limit = min(max(_int_or(request.args.get("limit"), 25), 5), 200)
    try:
        import psutil
        procs = []
        attrs = ["pid", "name", "username", "memory_percent", "status", "nice"]
        for p in psutil.process_iter(attrs):
            try:
                info = p.info
                cpu = p.cpu_percent(interval=None)  # 0.0 on first (priming) call
                procs.append({
                    "pid": info["pid"],
                    "name": info["name"] or "?",
                    "user": info["username"] or "-",
                    "cpu": round(cpu, 1),
                    "mem": round(info["memory_percent"] or 0.0, 1),
                    "status": info["status"],
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        _PRIMED["flag"] = True  # subsequent polls return real CPU values
        top_cpu = sorted(procs, key=lambda x: x["cpu"], reverse=True)[:limit]
        top_mem = sorted(procs, key=lambda x: x["mem"], reverse=True)[:limit]
        seen, merged = set(), []
        for p in top_cpu + top_mem:
            if p["pid"] not in seen:
                seen.add(p["pid"])
                merged.append(p)
        merged.sort(key=lambda x: x["cpu"], reverse=True)
        return jsonify({"processes": merged[:limit * 2], "total": len(procs)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/process/kill", methods=["POST"])
def api_process_kill():
    data = request.json or {}
    try:
        pid = int(data.get("pid"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid pid"}), 400
    if pid <= 1:
        return jsonify({"error": "refusing to kill init"}), 400
    try:
        import psutil
        p = psutil.Process(pid)
        name = p.name()
        p.terminate()
        try:
            p.wait(timeout=3)
        except psutil.TimeoutExpired:
            p.kill()
        return jsonify({"result": f"Terminated {name} (pid {pid})"})
    except psutil.NoSuchProcess:
        return jsonify({"error": f"No process with pid {pid}"}), 404
    except psutil.AccessDenied:
        _, _, err = run(f"kill -9 {pid}", sudo=True)
        if err:
            return jsonify({"error": err.strip()[:200]}), 403
        return jsonify({"result": f"Killed pid {pid} (sudo)"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ------------------------------------------------------------------
# systemd services
# ------------------------------------------------------------------
@app.route("/api/services")
def api_services():
    if not which("systemctl"):
        return jsonify({"available": False, "services": []})
    _, out, err = run("systemctl list-units --type=service --all --plain --no-pager --no-legend", timeout=15)
    services = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 4:
            unit = parts[0]
            desc = parts[4].strip() if len(parts) > 4 else ""
            services.append({
                "unit": unit,
                "load": parts[1],
                "active": parts[2],
                "sub": parts[3],
                "description": desc,
            })
    return jsonify({"available": True, "services": services})

SERVICE_ACTIONS = ("start", "stop", "restart", "reload", "enable", "disable")

@app.route("/api/service/action", methods=["POST"])
def api_service_action():
    data = request.json or {}
    name, action = data.get("name", ""), data.get("action", "")
    if not safe_name(name):
        return jsonify({"error": "invalid unit name"}), 400
    if action not in SERVICE_ACTIONS:
        return jsonify({"error": "invalid action"}), 400
    _, out, err = run(f"systemctl {action} {shlex.quote(name)}", timeout=30, sudo=True)
    if err and "Created symlink" not in err:
        return jsonify({"error": err.strip()[:300]}), 500
    return jsonify({"result": f"{action} {name}: ok"})

# ------------------------------------------------------------------
# Logs (journalctl with fallback)
# ------------------------------------------------------------------
@app.route("/api/logs")
def api_logs():
    lines = min(max(_int_or(request.args.get("lines", 60), 60), 10), 500)
    prio = _int_or(request.args.get("prio", 0), 0)  # 0 = all
    grep = request.args.get("grep", "").strip()
    if grep:
        grep = re.sub(r"[^\w\s\-\.\[\]/:]", "", grep)[:80]
    if which("journalctl"):
        cmd = f"journalctl --no-pager -n {lines} -o short"
        if prio:
            cmd += f" -p {prio}"
        code, out, err = run(cmd, timeout=15, sudo=True)
        if grep and out:
            out = "\n".join(l for l in out.splitlines() if grep.lower() in l.lower())
        if code != 0 and not out:
            out = err or "journalctl failed"
    else:
        for cand in ("/var/log/syslog", "/var/log/messages"):
            if os.path.exists(cand):
                code, out, _ = run(f"tail -n {lines} {cand}", timeout=10, sudo=True)
                break
        else:
            out = "No journalctl and no readable syslog found."
        if grep and out:
            out = "\n".join(l for l in out.splitlines() if grep.lower() in l.lower())
    return jsonify({"logs": out[-60000:]})

# ------------------------------------------------------------------
# Listening ports
# ------------------------------------------------------------------
@app.route("/api/ports")
def api_ports():
    _, out, _ = run("ss -tulnpH 2>/dev/null || ss -tulnH 2>/dev/null || netstat -tulpn 2>/dev/null", timeout=10, sudo=True)
    ports = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        if parts[0] in ("tcp", "udp", "tcp6", "udp6"):
            # ss -tulnpH: Netid State Recv-Q Send-Q Local-addr:port Peer Process
            local = parts[4]
            proc = parts[6] if len(parts) > 6 else ""
        elif parts[0] == "Proto" or parts[0] in ("Active", "Netid"):
            continue
        else:
            # netstat -tulpn: Proto Recv-Q Send-Q Local Foreign State Program
            if parts[0] not in ("tcp", "udp", "tcp6", "udp6"):
                continue
            local = parts[3]
            proc = parts[6] if len(parts) > 6 else ""
        addr, _, port = local.rpartition(":")
        ports.append({"proto": parts[0], "addr": addr or "*", "port": port, "process": proc})
    # dedupe
    seen, uniq = set(), []
    for p in ports:
        key = (p["proto"], p["addr"], p["port"])
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return jsonify({"ports": uniq[:100]})

# ------------------------------------------------------------------
# All mounted filesystems
# ------------------------------------------------------------------
@app.route("/api/disks")
def api_disks():
    try:
        import psutil
        disks = []
        for part in psutil.disk_partitions(all=False):
            if part.mountpoint.startswith(("/snap", "/boot/efi")) and "squashfs" in (part.fstype or ""):
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
                disks.append({
                    "device": part.device,
                    "mount": part.mountpoint,
                    "fstype": part.fstype or "-",
                    "total_gb": round(u.total / 1024**3, 1),
                    "used_gb": round(u.used / 1024**3, 1),
                    "free_gb": round(u.free / 1024**3, 1),
                    "percent": u.percent,
                })
            except (PermissionError, OSError):
                continue
        return jsonify({"disks": disks})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ------------------------------------------------------------------
# Network interfaces
# ------------------------------------------------------------------
@app.route("/api/network")
def api_network():
    try:
        import psutil
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        io = psutil.net_io_counters(pernic=True)
        nics = []
        import socket
        for name, addr_list in addrs.items():
            nic = {"name": name, "ipv4": "", "ipv6": "", "mac": "",
                   "up": stats[name].isup if name in stats else False,
                   "speed": stats[name].speed if name in stats else 0,
                   "sent_mb": round(io[name].bytes_sent / 1024**2, 1) if name in io else 0,
                   "recv_mb": round(io[name].bytes_recv / 1024**2, 1) if name in io else 0}
            for a in addr_list:
                fam = socket.AddressFamily(a.family)
                if fam == socket.AF_INET and not nic["ipv4"]:
                    nic["ipv4"] = f"{a.address}/{a.netmask}"
                elif fam == socket.AF_INET6 and not nic["ipv6"]:
                    nic["ipv6"] = a.address.split("%")[0]
                elif fam == socket.AF_PACKET and not nic["mac"]:
                    nic["mac"] = a.address
            nics.append(nic)
        return jsonify({"nics": nics})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ------------------------------------------------------------------
# Desktop app integration
# ------------------------------------------------------------------
@app.route("/api/open_app", methods=["POST"])
def api_open_app():
    """Open the native desktop window (montoring-app) from the dashboard.
    Only meaningful when browsing on the same machine."""
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return jsonify({"error": "No graphical session on the server — run 'montoring-app' locally instead."}), 400
    launcher = shutil.which("montoring-app") or "/usr/local/bin/montoring-app"
    if not os.path.exists(launcher):
        return jsonify({"error": "montoring-app launcher not installed"}), 404
    subprocess.Popen([launcher], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return jsonify({"result": "Desktop window launched"})

# ------------------------------------------------------------------
# VM / Libvirt management
# ------------------------------------------------------------------
VM_ACTIONS = ("start", "shutdown", "reboot", "reset", "destroy", "resume", "suspend")

@app.route("/api/vms")
def api_vms():
    vms = []
    code, out, err = run("virsh list --all --name 2>/dev/null")
    if code == 0 and out.strip():
        names = [n.strip() for n in out.splitlines() if n.strip()]
        for n in names:
            _, info, _ = run(f"virsh dominfo '{n}' 2>/dev/null")
            _, state_raw, _ = run(f"virsh domstate '{n}' 2>/dev/null")
            state = state_raw.strip().splitlines()[0] if state_raw else "unknown"
            mem, vcpus = "N/A", "N/A"
            for line in info.splitlines():
                if "Max memory" in line:
                    try:
                        mem = f"{int(line.split(':')[1].strip().split()[0]) // 1024 // 1024} GB"
                    except Exception:
                        mem = line.split(":")[1].strip()
                if "CPU(s)" in line:
                    vcpus = line.split(":")[1].strip()
            vms.append({"name": n, "state": state, "mem": mem, "vcpus": vcpus})
    if not vms:
        try:
            import libvirt
            conn = libvirt.open("qemu:///system")
            if conn:
                state_map = {0: "nodomain", 1: "running", 2: "blocked", 3: "paused",
                             4: "shutdown", 5: "shutoff", 6: "crashed", 7: "pmsuspended"}
                for dom in conn.listAllDomains():
                    info = dom.info()
                    vms.append({"name": dom.name(), "state": state_map.get(info[0], "unknown"),
                                "mem": f"{info[1] // 1024 // 1024} MB", "vcpus": str(info[3])})
                conn.close()
        except Exception:
            pass
    return jsonify(vms)

@app.route("/api/vm/action", methods=["POST"])
def api_vm_action():
    data = request.json or {}
    if not which("virsh") and not which("libvirt"):
        return jsonify({"error": "virsh/libvirt not available on this system"}), 400
    name, action = data.get("name", ""), data.get("action", "")
    if not safe_name(name):
        return jsonify({"error": "invalid VM name"}), 400
    if action not in VM_ACTIONS:
        return jsonify({"error": "invalid action"}), 400
    _, out, err = run(f"virsh {action} '{name}' 2>&1", timeout=60, sudo=True)
    if err and "error" in (out + err).lower():
        return jsonify({"error": (out + err).strip()[:300]}), 500
    return jsonify({"result": (out or err or f"{action} {name}: ok").strip()[:300]})

@app.route("/api/vm_info/<name>")
def api_vm_info(name):
    if not safe_name(name):
        return jsonify({"error": "invalid name"}), 400
    info = {}
    _, out, _ = run(f"virsh dominfo '{name}' 2>/dev/null")
    info["virsh"] = out
    _, out2, _ = run(f"virsh domblklist '{name}' 2>/dev/null")
    info["disks"] = out2
    return jsonify(info)

@app.route("/api/vm_resize", methods=["POST"])
def api_vm_resize():
    data = request.json or {}
    name = data.get("name")
    disk_path = data.get("disk_path")
    new_size_gb = data.get("new_size_gb")
    if not (name and disk_path and new_size_gb):
        return jsonify({"error": "name, disk_path, new_size_gb required"}), 400
    if not (safe_name(name) and re.fullmatch(r"[\w./\-_ ]{1,200}", disk_path)):
        return jsonify({"error": "invalid input"}), 400
    _, msg, err = run(f"qemu-img resize '{disk_path}' {int(new_size_gb)}G 2>&1", sudo=True)
    return jsonify({"result": (msg or err or "").strip()[:400], "disk": disk_path, "size_gb": new_size_gb})

# ------------------------------------------------------------------
# Start
# ------------------------------------------------------------------
if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    for d in ("templates", "static"):
        p = os.path.join(here, d)
        if not os.path.isdir(p):
            os.makedirs(p, exist_ok=True)
    app.run(host="0.0.0.0", port=APP_PORT, debug=False, threaded=True)
