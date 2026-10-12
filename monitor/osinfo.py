"""OS capability facts and lifecycle awareness.

One place that answers "what kind of machine is this?" — distro family,
package manager, init system, virtualisation, MAC framework, secure boot,
release lifecycle (EOL) — so every feature module (and the UI's capability
report) behaves consistently across Linux flavours instead of each one
re-detecting the OS on its own.
"""
import os
from datetime import date, datetime

from flask import Blueprint, jsonify

from .commands import run, which
from .common import _cached
from .security import rate_limit

bp = Blueprint("osinfo", __name__)

_PKG_MANAGERS = (("apt", "apt-get"), ("dnf", "dnf"), ("yum", "yum"),
                 ("zypper", "zypper"), ("pacman", "pacman"), ("apk", "apk"))


def _os_release():
    """Parse /etc/os-release into a dict (empty when unavailable)."""
    data = {}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                data[key] = val.strip().strip('"')
    except Exception:  # noqa: BLE001
        pass
    return data


def _pkg_manager():
    for mgr, cmd in _PKG_MANAGERS:
        if which(cmd):
            return mgr
    return None


def _init_system():
    if which("systemctl") and os.path.isdir("/run/systemd/system"):
        return "systemd"
    if os.path.exists("/sbin/openrc") or which("rc-service") or \
            os.path.exists("/etc/init.d") and os.path.exists("/run/openrc"):
        return "openrc"
    if os.path.isdir("/etc/init.d"):
        return "sysvinit"
    return "unknown"


def _distro_family(release):
    distro_id = (release.get("ID") or "").lower()
    like = (release.get("ID_LIKE") or "").lower().split()
    if distro_id in ("debian", "ubuntu", "linuxmint", "pop", "raspbian", "kali") \
            or "debian" in like:
        return "debian"
    if distro_id in ("fedora", "rhel", "centos", "rocky", "almalinux", "nobara") \
            or "rhel" in like or "fedora" in like or "centos" in like:
        return "fedora"
    if distro_id in ("opensuse", "opensuse-leap", "opensuse-tumbleweed", "sles") \
            or "suse" in like:
        return "suse"
    if distro_id in ("arch", "manjaro", "endeavouros", "garuda") or "arch" in like:
        return "arch"
    if distro_id in ("alpine",) or "alpine" in like:
        return "alpine"
    return distro_id or "unknown"


def _immutable():
    """Detect rpm-ostree/atomic style systems where the base OS is read-only."""
    if which("rpm-ostree"):
        return True
    try:
        with open("/proc/1/root/run/ostree-booted") as _:
            return True
    except Exception:  # noqa: BLE001
        return False


def _mac_framework():
    try:
        if os.path.exists("/sys/fs/selinux") or which("getenforce"):
            code, out, _ = run(["getenforce"], timeout=5)
            mode = (out or "").strip().lower()
            if mode in ("enforcing", "permissive", "disabled"):
                return {"framework": "selinux", "mode": mode}
            return {"framework": "selinux", "mode": "unknown"}
    except Exception:  # noqa: BLE001
        pass
    try:
        with open("/sys/module/apparmor/parameters/enabled") as f:
            if f.read().strip().upper().startswith("Y"):
                return {"framework": "apparmor", "mode": "enabled"}
    except Exception:  # noqa: BLE001
        pass
    return {"framework": "none", "mode": "unknown"}


def _secure_boot():
    if which("mokutil"):
        code, out, err = run(["mokutil", "--sb-state"], timeout=5)
        text = (out or err or "").lower()
        if "enabled" in text:
            return True
        if "disabled" in text:
            return False
    try:  # EFI variable: first byte 1 = Secure Boot enabled
        import glob
        for path in glob.glob("/sys/firmware/efi/efivars/SecureBoot-*"):
            with open(path, "rb") as f:
                data = f.read()
            if data:
                return data[-1] == 1
    except Exception:  # noqa: BLE001
        pass
    return None


def _eol_status(release):
    """Best-effort release lifecycle from os-release SUPPORT_END."""
    support_end = release.get("SUPPORT_END") or ""
    if not support_end:
        # Debian/Ubuntu don't ship SUPPORT_END; approximate from VERSION_ID
        # codenames is out of scope — report unknown rather than guess wrong.
        return {"known": False, "support_end": None, "eol": None, "days_left": None}
    try:
        end_date = datetime.strptime(support_end, "%Y-%m-%d").date()
        days_left = (end_date - date.today()).days
        return {"known": True, "support_end": support_end,
                "eol": days_left < 0,
                "days_left": days_left}
    except Exception:  # noqa: BLE001
        return {"known": True, "support_end": support_end, "eol": None,
                "days_left": None}


def os_facts():
    """Full OS capability report (cached briefly — these facts barely change)."""
    release = _os_release()
    try:
        uname = os.uname()
    except Exception:  # noqa: BLE001
        class _U:  # pragma: no cover - uname always exists on Linux
            sysname, release, machine, nodename = "Linux", "?", "?", "?"
        uname = _U()
    mgr = _pkg_manager()
    facts = {
        "hostname": uname.nodename,
        "os_name": release.get("PRETTY_NAME") or f"{uname.sysname} {uname.release}",
        "os_id": release.get("ID") or "",
        "version_id": release.get("VERSION_ID") or "",
        "codename": release.get("VERSION_CODENAME") or "",
        "family": _distro_family(release),
        "kernel": uname.release,
        "arch": uname.machine,
        "package_manager": mgr,
        "package_managers_detected": [m for m, c in _PKG_MANAGERS if which(c)],
        "init_system": _init_system(),
        "virtualization": _virtualization(),
        "immutable": _immutable(),
        "mac": _mac_framework(),
        "secure_boot": _secure_boot(),
        "lifecycle": _eol_status(release),
        "capabilities": {},
    }
    # Capability matrix: what the dashboard can do on THIS machine.
    facts["capabilities"] = {
        "packages": mgr is not None,
        "services": facts["init_system"] in ("systemd", "openrc"),
        "service_control": facts["init_system"] in ("systemd", "openrc"),
        "journal": which("journalctl"),
        "kernel_repair": which("dracut") or which("update-initramfs")
                         or which("mkinitcpio") or which("mkinitfs"),
        "snapshots": which("timeshift"),
        "smart": which("smartctl"),
        "secure_boot_known": facts["secure_boot"] is not None,
        "firmware_updates": which("fwupdmgr"),
    }
    return facts


def _virtualization():
    if which("systemd-detect-virt"):
        code, out, _ = run(["systemd-detect-virt"], timeout=5)
        v = (out or "").strip()
        if code == 0 and v and v != "none":
            return v
    try:
        with open("/proc/1/cgroup") as f:
            text = f.read()
        for needle, name in (("docker", "docker"), ("lxc", "lxc"),
                             ("kubepods", "kubernetes"), ("containerd", "containerd")):
            if needle in text:
                return name
    except Exception:  # noqa: BLE001
        pass
    return "bare metal"


@bp.route("/api/osinfo")
@rate_limit("30 per minute")
def api_osinfo():
    try:
        data = _cached("osinfo", 300, os_facts)
        return jsonify(data)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500
