"""Package-manager detection, fix command maps, and updatable-package listing."""
import threading
import time

from flask import Blueprint, jsonify

from .commands import run, which

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
                         "fix-broken", "clear-logs"})

# UI wording -> canonical action name.
FIX_ALIASES = {"vacuum-journal": "clear-logs", "clear-cache": "clean"}

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


@bp.route("/api/updates")
def api_updates():
    return jsonify(_updatable_packages())
