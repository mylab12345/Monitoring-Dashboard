"""Disk & filesystem health: SMART, inodes, discard status, device I/O.

Catches the classic invisible OS failures — failing disks (SMART), inode
exhaustion ("disk not full but writes fail") and missing TRIM on SSDs — that
percent-used gauges cannot see. SMART runs through the read-only
monitoring-smart helper when installed, degrading to 'unknown' otherwise.
"""
import os

from flask import Blueprint, jsonify, request

from .commands import run, run_privileged, which
from .common import _cached
from .security import rate_limit

bp = Blueprint("diskhealth", __name__)

# Disk names the monitoring-smart helper accepts (kept in sync with it).
import re  # noqa: E402
DISK_RE = re.compile(r"^(sd[a-z]+|hd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)$")


def _inode_usage():
    """Inode usage per mounted filesystem (statvfs — always available)."""
    out = []
    mounts = []
    try:
        import psutil
        mounts = [(p.mountpoint, p.fstype) for p in
                  psutil.disk_partitions(all=False)]
    except Exception:  # noqa: BLE001
        mounts = [("/", "")]
    for mountpoint, fstype in mounts:
        if mountpoint.startswith(("/snap/", "/proc", "/sys", "/dev")):
            continue
        try:
            st = os.statvfs(mountpoint)
            total = st.f_files
            free = st.f_ffree
            if total <= 0:
                continue
            used = total - free
            out.append({"mount": mountpoint, "fstype": fstype or None,
                        "inodes_total": total, "inodes_used": used,
                        "percent": round(used * 100.0 / total, 1)})
        except Exception:  # noqa: BLE001
            continue
    return out


def _discard_status():
    """Per-device TRIM/discard support and mount option state."""
    out = []
    code, lsblk, _ = run(["lsblk", "-dn", "-o", "NAME,DISC-GRAN,DISC-MAX,ROTA"],
                         timeout=10)
    if code != 0:
        return out
    for line in (lsblk or "").splitlines():
        parts = line.split()
        if len(parts) >= 4:
            name, gran, dmax, rota = parts[0], parts[1], parts[2], parts[3]
            out.append({"device": name,
                        "discard_granularity": gran,
                        "discard_max": dmax,
                        "rotational": rota == "1",
                        "ssd": rota == "0"})
    return out


def _device_io():
    """Cumulative + rate-free per-device I/O counters (psutil)."""
    try:
        import psutil
        counters = psutil.disk_io_counters(perdisk=True) or {}
        out = []
        for name, c in counters.items():
            if name.startswith(("loop", "ram")):
                continue
            out.append({"device": name, "read_bytes": c.read_bytes,
                        "write_bytes": c.write_bytes,
                        "read_count": c.read_count,
                        "write_count": c.write_count,
                        "busy_ms": getattr(c, "busy_time", None)})
        return out
    except Exception:  # noqa: BLE001
        return []


def _smart_for(disk):
    """SMART health + highlights via the read-only helper (or direct
    smartctl for dev checkouts where the helper is not installed)."""
    result = {"disk": disk, "available": False, "healthy": None,
              "detail": "", "temperature_c": None, "attributes": []}
    code, out, err = run_privileged("monitoring-smart", ["health", disk],
                                    timeout=25)
    if code == -1 and which("smartctl"):  # dev fallback: direct read-only run
        code, out, err = run(["smartctl", "-H", "-A", f"/dev/{disk}"],
                             timeout=25, sudo=False)
    if code in (0, 1) and (out or err):
        text = (out or "") + "\n" + (err or "")
        result["available"] = True
        low = text.lower()
        if "passed" in low:
            result["healthy"] = True
        elif "failed" in low or "failing" in low:
            result["healthy"] = False
        result["detail"] = text.strip()[:2000]
        for line in text.splitlines():
            m = re.search(r"\b(\d+)\s+Temperature_Celsius\b.*?(\d+)\s*$", line)
            if m:
                result["temperature_c"] = int(m.group(2))
            # Reallocated / pending / uncorrectable — the scary three.
            m2 = re.match(r"\s*(\d+)\s+(Reallocated_Sector|Current_Pending|"
                          r"Offline_Uncorrectable)\S*\s+\S+\s+\S+\s+\S+\s+"
                          r"\S+\s+\S+\s+\S+\s+(\d+)", line)
            if m2:
                result["attributes"].append({"id": int(m2.group(1)),
                                             "name": m2.group(2),
                                             "raw": int(m2.group(3))})
    elif code == 2:
        result["detail"] = "invalid disk name"
    else:
        result["detail"] = (err or out or "smartctl unavailable").strip()[:300]
    return result


@bp.route("/api/diskhealth")
@rate_limit("30 per minute")
def api_diskhealth():
    """Filesystem hygiene + device list (SMART is fetched per-disk on demand
    via ?disk=, because a full SMART sweep is slow)."""
    try:
        data = _cached("diskhealth", 60, lambda: {
            "inodes": _inode_usage(),
            "devices": _discard_status(),
            "io": _device_io(),
        })
        disk = (request.args.get("disk") or "").strip()
        if disk:
            if not DISK_RE.match(disk):
                return jsonify({"error": "invalid disk name"}), 400
            data = dict(data)
            data["smart"] = _smart_for(disk)
        return jsonify(data)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500
