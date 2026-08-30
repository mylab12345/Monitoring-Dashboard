#!/usr/bin/env python3
"""
Montoring — Universal Linux System Dashboard
Monitors system health, checks/fixes issues, manages processes, services,
logs, Docker containers and libvirt VMs — on any Linux flavour.

Run:  python3 app.py   (binds 0.0.0.0:$MONTORING_PORT, default 8050)
Requirements: flask, psutil  (see requirements.txt)
Privileges:   fixes/service/vm/docker actions use sudo automatically when
              not running as root (works best under the bundled systemd
              service which runs as root).
"""
import os
import re
import shlex
import subprocess
import shutil
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
    # Kernel errors
    code, out, err = run("dmesg 2>/dev/null | grep -iE 'error|fail|warn' | wc -l")
    dmsg = out.strip() if out else "0"
    results.append({"name": "Kernel Errors", "status": "warn" if _int_or(dmsg) > 5 else "ok",
                    "detail": f"{dmsg} error lines"})
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
    elif action == "docker-prune":
        if which("docker"):
            _, msg, err = run("docker system prune -f", timeout=120, sudo=True)
            msg = (msg or err or "done")[-400:]
        else:
            msg = "Docker not installed"
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
        top_cpu = sorted(procs, key=lambda x: x["cpu"], reverse=True)[:15]
        top_mem = sorted(procs, key=lambda x: x["mem"], reverse=True)[:15]
        seen, merged = set(), []
        for p in top_cpu + top_mem:
            if p["pid"] not in seen:
                seen.add(p["pid"])
                merged.append(p)
        merged.sort(key=lambda x: x["cpu"], reverse=True)
        return jsonify({"processes": merged[:25], "total": len(procs)})
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
# Docker
# ------------------------------------------------------------------
@app.route("/api/docker")
def api_docker():
    if not which("docker"):
        return jsonify({"available": False, "containers": [], "info": ""})
    code, out, err = run("docker ps -a --format '{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}'", timeout=15, sudo=True)
    if code != 0:
        return jsonify({"available": False, "containers": [], "info": (err or "docker not accessible").strip()[:200]})
    containers = []
    for line in out.splitlines():
        parts = (line.split("|") + ["", "", "", ""])[:4]
        if parts[0]:
            containers.append({"name": parts[0], "image": parts[1], "status": parts[2], "ports": parts[3]})
    return jsonify({"available": True, "containers": containers})

@app.route("/api/docker/action", methods=["POST"])
def api_docker_action():
    data = request.json or {}
    name, action = data.get("name", ""), data.get("action", "")
    if not safe_name(name):
        return jsonify({"error": "invalid container name"}), 400
    if action not in ("start", "stop", "restart", "remove"):
        return jsonify({"error": "invalid action"}), 400
    flag = "-f " if action == "remove" else ""
    _, out, err = run(f"docker {action} {flag}{shlex.quote(name)}", timeout=60, sudo=True)
    if err:
        return jsonify({"error": err.strip()[:300]}), 500
    return jsonify({"result": f"{action} {name}: ok"})

@app.route("/api/docker/prune", methods=["POST"])
def api_docker_prune():
    if not which("docker"):
        return jsonify({"error": "docker not installed"}), 400
    _, out, err = run("docker system prune -f", timeout=120, sudo=True)
    return jsonify({"result": (out or err or "pruned")[-400:]})

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
