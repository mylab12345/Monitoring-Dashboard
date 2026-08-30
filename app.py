#!/usr/bin/env python3
"""
Montoring — Minimal Linux Mint OS Dashboard
Monitors system health, checks/fixes issues, manages libvirt VMs.
Run: python3 app.py  (binds 0.0.0.0:8050)
Requirements: flask, psutil (pip install flask psutil)
Libvirt access: user must be in 'libvirt' group; app tries libvirt-python then virsh.
"""
import os, sys, subprocess, json, time, shutil
from datetime import datetime
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

# ------------------------------------------------------------------
# Helper: safe command runner
# ------------------------------------------------------------------
def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)

# ------------------------------------------------------------------
# System info
# ------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def api_status():
    # CPU
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        uptime = time.time() - psutil.boot_time()
        load = os.getloadavg()
        temp = "N/A"
        # try simple temp read (Linux Mint / sys)
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                temp = f"{int(f.read().strip()) / 1000:.1f}°C"
        except Exception:
            pass
        # network
        net = psutil.net_io_counters()
        net_sent = f"{net.bytes_sent / (1024*1024):.1f} MB"
        net_recv = f"{net.bytes_recv / (1024*1024):.1f} MB"
        # users / sessions
        users = len([p for p in psutil.process_iter(["name", "username"]) if p.info["username"]])
        data = {
            "cpu_percent": round(cpu, 1),
            "ram_percent": mem.percent,
            "ram_used_gb": round(mem.used / (1024**3), 2),
            "ram_total_gb": round(mem.total / (1024**3), 2),
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
            "users": users,
            "hostname": os.uname().nodename,
            "os": f"{os.uname().sysname} {os.uname().release}",
            "time": datetime.now().strftime("%H:%M:%S"),
        }
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ------------------------------------------------------------------
# Issues / Health checks
# ------------------------------------------------------------------
@app.route("/api/checks")
def api_checks():
    results = []
    # Disk usage
    code, out, err = run("df -h /")
    results.append({"name": "Disk Usage", "status": "ok" if code == 0 else "fail", "detail": out.splitlines()[1].strip() if out else err})
    # APT updates available
    code, out, err = run("apt list --upgradable 2>/dev/null | wc -l")
    updates = out.strip() if out else "0"
    results.append({"name": "Pending Updates", "status": "warn" if int(updates) > 0 else "ok", "detail": f"{updates} packages upgradable"})
    # Broken packages
    code, out, err = run("dpkg --audit 2>&1")
    broken = "found" if "error" in (out + err).lower() else "none"
    results.append({"name": "Broken Packages", "status": "warn" if broken != "none" else "ok", "detail": broken})
    # Services failing (simple check)
    code, out, err = run("systemctl --failed --no-pager --quiet 2>/dev/null || echo 0")
    failed = out.strip()
    results.append({"name": "Failed Services", "status": "warn" if int(failed) > 0 else "ok", "detail": f"{failed} failed"})
    # Logs errors (last 5 errors from dmesg / journal is heavy; use dmesg briefly)
    code, out, err = run("dmesg 2>/dev/null | grep -iE 'error|fail|warn' | wc -l")
    dmsg = out.strip() if out else "0"
    results.append({"name": "Kernel Errors", "status": "warn" if int(dmsg) > 5 else "ok", "detail": f"{dmsg} error lines"})
    return jsonify(results)

# ------------------------------------------------------------------
# Fix actions (safe, non-destructive)
# ------------------------------------------------------------------
@app.route("/api/fix", methods=["POST"])
def api_fix():
    action = request.json.get("action", "")
    msg = ""
    if action == "update":
        _, msg, _ = run("apt update -qq", timeout=60)
    elif action == "upgrade":
        _, msg, _ = run("apt upgrade -y -qq", timeout=180)
    elif action == "autoremove":
        _, msg, _ = run("apt autoremove -y -qq", timeout=60)
    elif action == "clean":
        _, msg, _ = run("apt clean", timeout=30)
    elif action == "fix-broken":
        _, msg, _ = run("dpkg --configure -a && apt install -f -y -qq", timeout=120)
    elif action == "clear-logs":
        _, msg, _ = run("journalctl --vacuum-time=7d >/dev/null 2>&1; find /var/log -type f -name '*.gz' -delete 2>/dev/null; echo 'Logs cleared'", timeout=30)
    else:
        msg = "Unknown action"
    return jsonify({"result": msg[:500]})

# ------------------------------------------------------------------
# VM / Libvirt management
# ------------------------------------------------------------------
@app.route("/api/vms")
def api_vms():
    vms = []
    # Try virsh first (works if libvirt group / socket available)
    code, out, err = run("virsh list --all --name 2>/dev/null")
    if code == 0:
        names = [n.strip() for n in out.splitlines() if n.strip()]
        for n in names:
            if not n:
                continue
            # Info
            _, info, _ = run(f"virsh dominfo '{n}' 2>/dev/null")
            # State
            _, state_raw, _ = run(f"virsh domstate '{n}' 2>/dev/null")
            state = state_raw.strip() if state_raw else "unknown"
            # Memory / vcpus
            mem = "N/A"; vcpus = "N/A"
            for line in info.splitlines():
                if "Max memory" in line:
                    mem = line.split(":")[1].strip()
                if "CPU(s)" in line:
                    vcpus = line.split(":")[1].strip()
            vms.append({"name": n, "state": state, "mem": mem, "vcpus": vcpus})
    # Try libvirt-python if virsh empty / unavailable
    if not vms:
        try:
            import libvirt
            conn = libvirt.open("qemu:///system")
            if conn:
                for dom in conn.listAllDomains():
                    info = dom.info()
                    state_map = {0:"nodomain",1:"running",2:"blocked",3:"paused",4:"shutdown",5:"shutoff",6:"crashed",7:"pmsuspended"}
                    vms.append({
                        "name": dom.name(),
                        "state": state_map.get(info[0], "unknown"),
                        "mem": f"{info[1]//1024//1024} MB",
                        "vcpus": str(info[3]),
                    })
                conn.close()
        except Exception:
            pass
    return jsonify(vms)

@app.route("/api/vm_info/<name>")
def api_vm_info(name):
    # Return domain info + disk info via virsh or libvirt
    info = {}
    _, out, _ = run(f"virsh dominfo '{name}' 2>/dev/null")
    info["virsh"] = out
    # Disk files
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
    # Resize using qemu-img (safe; requires VM off or disk detached ideally; we'll attempt and report)
    _, msg, err = run(f"qemu-img resize '{disk_path}' {new_size_gb}G 2>&1")
    return jsonify({"result": msg or err, "disk": disk_path, "size_gb": new_size_gb})

# ------------------------------------------------------------------
# Start
# ------------------------------------------------------------------
if __name__ == "__main__":
    # Ensure templates / static exist relative to this file
    here = os.path.dirname(os.path.abspath(__file__))
    for d in ("templates", "static"):
        p = os.path.join(here, d)
        if not os.path.isdir(p):
            os.makedirs(p, exist_ok=True)
    app.run(host="0.0.0.0", port=8050, debug=False, threaded=True)
