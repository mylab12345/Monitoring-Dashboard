"""Package-manager detection, fix command maps, and updatable-package listing."""
import re
import threading
import time

from flask import Blueprint, jsonify

from .commands import run, which
from .security import rate_limit

bp = Blueprint("packages", __name__)


def _pkg_manager():
    for mgr, cmd in (("apt", "apt-get"), ("dnf", "dnf"), ("yum", "yum"),
                     ("zypper", "zypper"), ("pacman", "pacman"), ("apk", "apk")):
        if which(cmd):
            return mgr
    return None


# Internal argv lists for the monitoring-package privileged helper. These are
# never concatenated with user input and never go through a shell.
# Generated (not hand-written per manager) so all six managers stay in sync
# by construction — adding a manager or action is a one-line change.
PACKAGE_MANAGERS = ("apt", "dnf", "yum", "zypper", "pacman", "apk")
PACKAGE_ACTIONS = ("update", "upgrade", "full-upgrade", "autoremove",
                   "clean", "fix-broken")


def _helper_argv(manager, action):
    """Build the argv list for one monitoring-package helper invocation."""
    return ["monitoring-package", "--manager", manager, "--action", action]


FIX_COMMANDS = {
    manager: {action: _helper_argv(manager, action)
              for action in PACKAGE_ACTIONS}
    for manager in PACKAGE_MANAGERS
}

# Package managers keep global locks and must never be driven concurrently by
# two browser requests (for example, Upgrade and Fix Broken from two tabs).
# The lock is shared with the diagnostics fix-all endpoint.
PACKAGE_MUTATION_LOCK = threading.Lock()

# Every action /api/fix knows how to run. Anything outside this set is a 400 so
# the UI can never report success for an operation that did not execute.
FIX_ACTIONS = frozenset({"update", "upgrade", "full-upgrade", "autoremove",
                         "clean", "fix-broken", "clear-logs"})

# UI wording -> canonical action name.
FIX_ALIASES = {"vacuum-journal": "clear-logs", "clear-cache": "clean",
               "system-upgrade": "full-upgrade"}

_updatable_cache = {"data": None, "ts": 0}
_updatable_lock = threading.Lock()
_UPDATABLE_TTL = 300  # 5 minutes


def _dpkg_audit():
    """Return ``(available, healthy, detail)`` for Debian package state.

    ``dpkg --audit`` normally exits zero even when it prints packages that are
    unpacked, half-installed, or awaiting configuration. Looking only for the
    word ``error`` therefore misses exactly the state left by an interrupted
    unpack (including ``coreutils: half-installed``).
    """
    if not which("dpkg"):
        return False, True, ""
    code, out, err = run(["dpkg", "--audit"], timeout=30)
    detail = "\n".join(part.strip() for part in (out or "", err or "") if part and part.strip())
    return True, code == 0 and not detail, detail


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
                    # Skip header line
                    if line.startswith("Listing"):
                        continue
                    name = line.split("/")[0].strip()
                    # Validate name is sane
                    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._-]*", name):
                        continue
                    ver = line.split()[1] if len(line.split()) > 1 else ""
                    pkgs.append({"name": name, "version": ver})
        elif mgr in ("dnf", "yum"):
            _, out, _ = run([mgr, "check-update", "-q"], timeout=60)
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 3 and not line.startswith(("Last metadata", "Loaded plugins", "Security:", " ")):
                    # First token should be pkg name
                    if "." in parts[0] or "-" in parts[0]:
                        pkgs.append({"name": parts[0], "version": parts[1]})
        elif mgr == "pacman":
            _, out, _ = run(["pacman", "-Qu", "--quiet"], timeout=30)
            pkgs = [{"name": l.strip(), "version": ""} for l in out.splitlines() if l.strip() and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", l.strip())]
        elif mgr == "apk":
            _, out, _ = run(["apk", "version", "-l", "<"], timeout=30)
            for line in out.splitlines()[1:]:
                parts = line.split()
                if parts and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", parts[0]):
                    pkgs.append({"name": parts[0], "version": parts[1] if len(parts) > 1 else ""})
        elif mgr == "zypper":
            # zypper list-updates can output table or XML; try to parse robustly
            # Try with --xmlout first for structured parsing, fallback to text
            _, out_xml, _ = run(["zypper", "--xmlout", "list-updates"], timeout=60)
            if out_xml and "<update-list>" in out_xml:
                # XML parsing without external deps: regex for <update name=
                for m in re.finditer(r'name="([^"]+)"\s+.*?\s+edition="([^"]+)"', out_xml):
                    pkgs.append({"name": m.group(1), "version": m.group(2)})
            else:
                _, out, _ = run(["zypper", "-q", "list-updates"], timeout=60)
                for line in out.splitlines():
                    line = line.strip()
                    if not line or line.startswith(("S |", "--", "Repository")):
                        continue
                    # Format: v | repo | Name | Current Version | Available Version | Arch
                    # or without S column
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 5:
                        # parts[2] is Name when S column present, else parts[0]
                        name_idx = 2 if len(parts) >= 6 else 0
                        ver_idx = 4 if len(parts) >= 6 else 2
                        name = parts[name_idx] if len(parts) > name_idx else ""
                        ver = parts[ver_idx] if len(parts) > ver_idx else ""
                        if name and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", name):
                            pkgs.append({"name": name, "version": ver})
    except Exception:
        pass
    result = {"manager": mgr, "count": len(pkgs), "packages": pkgs[:200]}
    _updatable_cache["data"] = result
    _updatable_cache["ts"] = time.time()
    return result


def clear_updatable_cache():
    """Drop the cached upgradable-package list (after a successful mutation).

    Without this, "Pending Updates" stays stale for up to the 5-minute TTL
    after an upgrade/repair, and the operator cannot see that the system is
    actually current (or that new updates appeared).
    """
    with _updatable_lock:
        _updatable_cache["data"] = None
        _updatable_cache["ts"] = 0


@bp.route("/api/updates")
@rate_limit("60 per minute")
def api_updates():
    return jsonify(_updatable_packages())
