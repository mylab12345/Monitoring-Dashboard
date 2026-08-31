"""Package-manager detection, fix command maps, and updatable-package listing."""
import re
import threading
import time

from flask import Blueprint, jsonify

from .commands import readonly_mounts, run, which

bp = Blueprint("packages", __name__)


def _pkg_manager():
    for mgr, cmd in (("apt", "apt-get"), ("dnf", "dnf"), ("yum", "yum"),
                     ("zypper", "zypper"), ("pacman", "pacman"), ("apk", "apk")):
        if which(cmd):
            return mgr
    return None


# Internal argv lists for the monitoring-package privileged helper. These are
# never concatenated with user input and never go through a shell.
FIX_COMMANDS = {
    "apt":    {"update": ["monitoring-package", "--manager", "apt", "--action", "update"],
               "upgrade": ["monitoring-package", "--manager", "apt", "--action", "upgrade"],
               "autoremove": ["monitoring-package", "--manager", "apt", "--action", "autoremove"],
               "clean": ["monitoring-package", "--manager", "apt", "--action", "clean"]},
    "dnf":    {"update": ["monitoring-package", "--manager", "dnf", "--action", "update"],
               "upgrade": ["monitoring-package", "--manager", "dnf", "--action", "upgrade"],
               "autoremove": ["monitoring-package", "--manager", "dnf", "--action", "autoremove"],
               "clean": ["monitoring-package", "--manager", "dnf", "--action", "clean"]},
    "yum":    {"update": ["monitoring-package", "--manager", "yum", "--action", "update"],
               "upgrade": ["monitoring-package", "--manager", "yum", "--action", "upgrade"],
               "autoremove": ["monitoring-package", "--manager", "yum", "--action", "autoremove"],
               "clean": ["monitoring-package", "--manager", "yum", "--action", "clean"]},
    "zypper": {"update": ["monitoring-package", "--manager", "zypper", "--action", "update"],
               "upgrade": ["monitoring-package", "--manager", "zypper", "--action", "upgrade"],
               "autoremove": ["monitoring-package", "--manager", "zypper", "--action", "autoremove"],
               "clean": ["monitoring-package", "--manager", "zypper", "--action", "clean"]},
    "pacman": {"update": ["monitoring-package", "--manager", "pacman", "--action", "update"],
               "upgrade": ["monitoring-package", "--manager", "pacman", "--action", "upgrade"],
               "autoremove": ["monitoring-package", "--manager", "pacman", "--action", "autoremove"],
               "clean": ["monitoring-package", "--manager", "pacman", "--action", "clean"]},
    "apk":    {"update": ["monitoring-package", "--manager", "apk", "--action", "update"],
               "upgrade": ["monitoring-package", "--manager", "apk", "--action", "upgrade"],
               "autoremove": ["monitoring-package", "--manager", "apk", "--action", "autoremove"],
               "clean": ["monitoring-package", "--manager", "apk", "--action", "clean"]},
}

# Every action /api/fix knows how to run. Anything outside this set is a 400 so
# the UI can never report success for an operation that did not execute.
FIX_ACTIONS = frozenset({"update", "upgrade", "autoremove", "clean",
                         "fix-broken", "clear-logs", "remount-rw"})

# UI wording -> canonical action name.
FIX_ALIASES = {"vacuum-journal": "clear-logs", "clear-cache": "clean",
               "fix-readonly-fs": "remount-rw"}

# Exit code the monitoring-package helper returns when its write preflight
# refused to run because required filesystem paths are read-only.
FIX_RC_READONLY = 3

_updatable_cache = {"data": None, "ts": 0}
_updatable_lock = threading.Lock()
_UPDATABLE_TTL = 300  # 5 minutes


def _updatable_packages():
    if _updatable_cache["data"] is not None and (time.time() - _updatable_cache["ts"]) < _UPDATABLE_TTL:
        return _updatable_cache["data"]
    # Double-checked locking: /api/checks, /api/troubleshooting and verify can
    # race; never run two package-manager queries at once.
    if not _updatable_lock.acquire(blocking=False):
        return _updatable_cache["data"] or {"manager": None, "count": 0, "packages": []}
    try:
        if _updatable_cache["data"] is not None and (time.time() - _updatable_cache["ts"]) < _UPDATABLE_TTL:
            return _updatable_cache["data"]
        return _updatable_packages_uncached()
    finally:
        _updatable_lock.release()


def _updatable_packages_uncached():
    mgr = _pkg_manager()
    pkgs = []
    try:
        if mgr == "apt":
            _, out, _ = run(["apt", "list", "--upgradable"], timeout=30)
            for line in out.splitlines():
                if "/" in line and " " in line:
                    name = line.split("/")[0].strip()
                    ver = line.split()[1] if len(line.split()) > 1 else ""
                    pkgs.append({"name": name, "version": ver})
        elif mgr in ("dnf", "yum"):
            _, out, _ = run([mgr, "check-update", "-q"], timeout=60)
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 3 and not line.startswith(("Last metadata", "Loaded plugins")):
                    pkgs.append({"name": parts[0], "version": parts[1]})
        elif mgr == "pacman":
            _, out, _ = run(["pacman", "-Qu", "--quiet"], timeout=30)
            pkgs = [{"name": l.strip(), "version": ""} for l in out.splitlines() if l.strip()]
        elif mgr == "apk":
            _, out, _ = run(["apk", "version", "-l", "<"], timeout=30)
            for line in out.splitlines()[1:]:
                parts = line.split()
                if parts:
                    pkgs.append({"name": parts[0], "version": parts[1] if len(parts) > 1 else ""})
        elif mgr == "zypper":
            _, out, _ = run(["zypper", "-q", "list-updates"], timeout=60)
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[0] in ("v",):
                    pkgs.append({"name": parts[2], "version": parts[3] if len(parts) > 3 else ""})
    except Exception:
        pass
    result = {"manager": mgr, "count": len(pkgs), "packages": pkgs[:200]}
    _updatable_cache["data"] = result
    _updatable_cache["ts"] = time.time()
    return result


def invalidate_updatable_cache():
    """Drop the cached updatable-package list so /api/updates, /api/checks and
    the troubleshooting scan re-query the package manager immediately after a
    maintenance action (update/upgrade/fix-broken) changed the state."""
    with _updatable_lock:
        _updatable_cache["data"] = None
        _updatable_cache["ts"] = 0


_PKG_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+:-]*")


def dpkg_broken_status():
    """Report dpkg database health: packages half-installed (failed unpack),
    packages unpacked (awaiting configuration) and the raw `dpkg --audit`
    text. Healthy = available, empty lists and empty audit output."""
    status = {"available": False, "audit": "", "half_installed": [], "unpacked": []}
    if not which("dpkg"):
        return status
    status["available"] = True
    try:
        _, out, err = run(["dpkg", "--audit"], timeout=30)
        status["audit"] = ((out or "") + (err or "")).strip()
    except Exception:
        status["audit"] = ""
    try:
        _, out, _ = run(["dpkg-query", "-W",
                         "-f=${db:Status-Status} ${binary:Package}\n"], timeout=30)
        for line in (out or "").splitlines():
            parts = line.split(None, 1)
            if len(parts) != 2 or not _PKG_NAME_RE.fullmatch(parts[1].strip()):
                continue
            if parts[0] == "half-installed":
                status["half_installed"].append(parts[1].strip())
            elif parts[0] == "unpacked":
                status["unpacked"].append(parts[1].strip())
    except Exception:
        pass
    # Fallback for older dpkg without db:Status-Status support.
    if not status["half_installed"] and not status["unpacked"]:
        try:
            _, out, _ = run(["dpkg", "-l"], timeout=30)
            for line in (out or "").splitlines():
                parts = line.split()
                if len(parts) >= 3 and line[:1] in ("i", "h") and line[1:2] == "H" \
                        and _PKG_NAME_RE.fullmatch(parts[1]):
                    status["half_installed"].append(parts[1])
        except Exception:
            pass
    return status


def describe_fix_rc(rc):
    """Human explanation of a monitoring-package / monitoring-remount-rw
    helper exit code (empty string when nothing extra needs saying)."""
    if rc == 0:
        return ""
    if rc == FIX_RC_READONLY:
        ro = readonly_mounts()
        mounts = ", ".join(m for _, m, _ in ro) or "/"
        if ", " in mounts:
            mount_cmd = "sudo mount -o remount,rw <mountpoint>  (for each listed)"
        else:
            mount_cmd = f"sudo mount -o remount,rw {mounts}"
        return ("Refused: required filesystem paths are read-only ({0}). "
                "This is a permission/mount problem, not a package problem — "
                "nothing was modified. Remount the filesystem read-write "
                "({1}) or use the 'Remount RW' maintenance action, then "
                "retry.").format(mounts, mount_cmd)
    if rc == 2:
        return "Invalid helper invocation (internal error — nothing was run)."
    if rc == 127:
        return "Required package-manager executable not found."
    return ""


@bp.route("/api/updates")
def api_updates():
    return jsonify(_updatable_packages())
