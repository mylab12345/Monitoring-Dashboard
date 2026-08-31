"""System info, health checks, disks, listening ports and network interfaces."""
import os
import socket

from flask import Blueprint, jsonify

from .commands import run, which
from .common import _cached, _int_or
from .metrics import _cpu_model, _pretty_distro
from .packages import _dpkg_audit, _updatable_packages

bp = Blueprint("system", __name__)


def _virtualization():
    if which("systemd-detect-virt"):
        code, out, _ = run(["systemd-detect-virt"])
        v = (out or "").strip()
        if code == 0 and v and v != "none":
            return v
    return "bare metal"


@bp.route("/api/systeminfo")
def api_systeminfo():
    try:
        import platform
        import psutil
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


def _build_checks():
    results = []
    code, out, err = run(["df", "-h", "/"])
    results.append({"name": "Disk Usage", "status": "ok" if code == 0 else "fail",
                    "detail": out.splitlines()[1].strip() if out and len(out.splitlines()) > 1 else (err or "ok")})
    updates = _updatable_packages()
    results.append({"name": "Pending Updates", "status": "warn" if updates["count"] > 0 else "ok",
                    "detail": f"{updates['count']} packages upgradable ({updates['manager'] or 'n/a'})"})
    dpkg_available, dpkg_healthy, dpkg_detail = _dpkg_audit()
    if dpkg_available:
        broken = "none" if dpkg_healthy else "incomplete package state"
        detail = broken if dpkg_healthy else (dpkg_detail.splitlines()[0][:240] if dpkg_detail else broken)
        results.append({"name": "Broken Packages", "status": "warn" if not dpkg_healthy else "ok", "detail": detail})
    failed = "0"
    if which("systemctl"):
        code, out, err = run(["systemctl", "--failed", "--no-pager", "--quiet"])
        failed = str(len([l for l in out.splitlines() if l.strip()]))
        results.append({"name": "Failed Services", "status": "warn" if _int_or(failed) > 0 else "ok",
                        "detail": f"{failed} failed"})
    code, out, err = run(["journalctl", "-k", "-b", "-p", "err..alert", "--no-pager", "-q"]) \
        if which("journalctl") else run(["dmesg", "--level=err,crit,alert,emerg"])
    dmsg = str(len([l for l in out.splitlines() if l.strip()])) if code == 0 and out else "0"
    results.append({"name": "Kernel Errors", "status": "warn" if _int_or(dmsg) > 0 else "ok",
                    "detail": f"{dmsg} current-boot error lines"})
    try:
        import psutil
        zombies = [p for p in psutil.process_iter(["status"]) if p.info["status"] == "zombie"]
        results.append({"name": "Zombie Processes", "status": "warn" if zombies else "ok",
                        "detail": f"{len(zombies)} zombies"})
    except Exception:
        pass
    return results


@bp.route("/api/checks")
def api_checks():
    return jsonify(_cached("_checks", 10, _build_checks))


@bp.route("/api/ports")
def api_ports():
    # Port listing is read-only and does not need sudo. Process info may be
    # omitted for other users; that is intentionally fine.
    out = ""
    for argv in (["ss", "-tulnpH"], ["ss", "-tulnH"], ["netstat", "-tulpn"]):
        code, out, _ = run(argv, timeout=10, sudo=False)
        if code == 0 and out:
            break
    ports = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        if parts[0] in ("tcp", "udp", "tcp6", "udp6"):
            local = parts[4]
            proc = parts[6] if len(parts) > 6 else ""
        elif parts[0] == "Proto" or parts[0] in ("Active", "Netid"):
            continue
        else:
            if parts[0] not in ("tcp", "udp", "tcp6", "udp6"):
                continue
            local = parts[3]
            proc = parts[6] if len(parts) > 6 else ""
        addr, _, port = local.rpartition(":")
        ports.append({"proto": parts[0], "addr": addr or "*", "port": port, "process": proc})
    seen, uniq = set(), []
    for p in ports:
        key = (p["proto"], p["addr"], p["port"])
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return jsonify({"ports": uniq[:100]})


@bp.route("/api/disks")
def api_disks():
    try:
        import psutil
        disks = []
        seen_mounts = set()
        seen_devices = set()
        for part in psutil.disk_partitions(all=False):
            mount = part.mountpoint
            device = part.device
            if not mount or mount in seen_mounts:
                continue
            if device and device in seen_devices and mount != "/":
                continue
            if mount.startswith(("/snap", "/boot/efi")) and "squashfs" in (part.fstype or ""):
                continue
            seen_mounts.add(mount)
            if device:
                seen_devices.add(device)
            try:
                u = psutil.disk_usage(mount)
                disks.append({
                    "device": device or "-",
                    "mount": mount,
                    "fstype": part.fstype or "-",
                    "total_gb": round(u.total / 1024**3, 1),
                    "used_gb": round(u.used / 1024**3, 1),
                    "free_gb": round(u.free / 1024**3, 1),
                    "percent": u.percent,
                })
            except (PermissionError, OSError):
                continue
        disks.sort(key=lambda d: (0 if d["mount"] == "/" else 1, d["mount"]))
        return jsonify({"disks": disks})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/network")
def api_network():
    try:
        import psutil
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        io = psutil.net_io_counters(pernic=True)
        nics = []
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
