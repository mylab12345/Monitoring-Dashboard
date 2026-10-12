"""Safe command runners and input validators.

All subprocesses are argv lists (never shell strings). Privileged
operations go through the helper scripts under PRIVILEGE_DIR, which the
full-access sudoers profile (/etc/sudoers.d/monitoring grants the
"monitoring" account NOPASSWD: ALL) permits. The helper routing is an
application-level convention for input validation and permission
reporting, not a sudo-layer restriction.
"""
import os
import re
import shutil
import subprocess

from .common import PRIVILEGE_DIR

HAVE_SUDO = shutil.which("sudo") is not None


def _parse_sudo_list_output(out):
    """Detect a full-access grant in `sudo -l` output.

    The full-access profile is a `NOPASSWD: ALL` rule (any command, no
    password). Absolute-path listings from the least-privilege profile
    (e.g. `NOPASSWD: /usr/local/lib/monitoring/monitoring-kill`) and
    look-alike command names (e.g. `/usr/bin/allocate`) must NOT match.
    """
    return bool(re.search(r"NOPASSWD:\s*ALL(?![\w/.-])", out or ""))


def _sudo_probe():
    """Best-effort probe of the caller's passwordless sudo grant.

    `sudo -n -l` only reports what the caller may run; it does not grant
    or execute anything. Returns (passwordless, full_access):
      passwordless — some passwordless sudo rule applies (or we are root)
      full_access  — a NOPASSWD: ALL rule applies (full-access profile)
                    rather than a listing of specific commands
    """
    if os.geteuid() == 0:
        # Root needs no sudo; nothing is restricted for this process.
        return True, True
    if not HAVE_SUDO:
        return False, False
    try:
        r = subprocess.run(["sudo", "-n", "-l"], capture_output=True,
                           text=True, timeout=5)
    except Exception:
        return False, False
    if r.returncode != 0:
        return False, False
    return True, _parse_sudo_list_output(r.stdout)


PASSWORDLESS_SUDO, SUDO_FULL_ACCESS = _sudo_probe()


# Single source of truth for every privileged helper the dashboard may invoke.
# privileges.py renders this same list as the Settings → Privileges report.
PRIVILEGED_HELPERS = frozenset({
    "monitoring-systemctl",
    "monitoring-self-repair",
    "monitoring-package",
    "monitoring-maintain",
    "monitoring-perf",
    "monitoring-self-update",
    "monitoring-journal-vacuum",
    "monitoring-clean-old-logs",
    "monitoring-vm",
    "monitoring-vm-config",
    "monitoring-qemu",
    "monitoring-kill",
    "monitoring-zombie-clean",
    "monitoring-privilege-check",
    "monitoring-nice",
    "monitoring-smart",
    "monitoring-sysctl",
    "monitoring-rc",
    "monitoring-snap",
})


def privileged_tool(name):
    """Return the absolute path to an installed monitoring helper."""
    if os.sep in name or name.startswith("."):
        raise ValueError("invalid privileged helper name")
    if name not in PRIVILEGED_HELPERS:
        raise ValueError(f"privileged helper not whitelisted: {name}")
    return os.path.join(PRIVILEGE_DIR, name)


def run(cmd, timeout=10, sudo=False, check=False):
    """Run an argv list; optionally prefix passwordless sudo when not root.

    Strings are rejected to eliminate shell injection by construction.
    Returns (returncode, stdout, stderr).
    """
    if isinstance(cmd, (str, bytes)):
        raise ValueError("run() requires an argv list; shell strings are "
                         "not allowed (command injection prevention)")
    argv = list(cmd)
    if not argv or not all(isinstance(a, str) for a in argv):
        raise ValueError("run() requires a list of strings")
    # Prevent argument injection via leading dashes in untrusted input
    # (trusted internal callers use fixed argv, but defense in depth)
    if sudo and os.geteuid() != 0:
        if not HAVE_SUDO:
            return -1, "", "sudo is not available"
        argv = ["sudo", "-n", "--"] + argv
    try:
        r = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
            start_new_session=True,
        )
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except subprocess.CalledProcessError as e:
        return e.returncode, e.stdout or "", e.stderr or ""
    except Exception as e:
        return -1, "", str(e)


def run_lines(cmd, timeout=10, sudo=False):
    """Run an argv command and return (code, non-empty stripped lines)."""
    code, out, err = run(cmd, timeout=timeout, sudo=sudo)
    lines = [line.strip() for line in (out or "").splitlines() if line.strip()]
    return code, lines


def run_privileged(helper, args=None, timeout=60):
    """Run one whitelisted monitoring privilege helper through sudo."""
    argv = [privileged_tool(helper)] + (list(args) if args else [])
    return run(argv, timeout=timeout, sudo=True)


def which(binary):
    return shutil.which(binary) is not None


def mount_status(path):
    """Report whether a filesystem target is mounted read-only.

    This is deliberately a mount check, not an ``os.access`` check: the
    service account is expected not to have ordinary write permission to
    system directories, while its validated sudo package helper must be able
    to write them inside the service mount namespace.
    """
    result = {"path": path, "exists": os.path.exists(path),
              "read_only": False, "known": True, "options": ""}
    if not result["exists"]:
        return result
    findmnt = shutil.which("findmnt")
    if not findmnt:
        result["known"] = False
        return result
    try:
        proc = subprocess.run(
            [findmnt, "--noheadings", "--output", "OPTIONS", "--target", path],
            shell=False, capture_output=True, text=True, timeout=5,
        )
        options = (proc.stdout or "").strip()
        result["options"] = options
        if proc.returncode != 0 or not options:
            result["known"] = False
        else:
            result["read_only"] = "ro" in {part.strip() for part in options.split(",")}
    except (OSError, subprocess.SubprocessError):
        result["known"] = False
    return result


def safe_name(name):
    """Legacy loose validator — use specific validators instead.

    Kept for backward compatibility but now stricter: no spaces, brackets.
    Prefer validate_service_name or validate_vm_name.
    """
    if not name or not isinstance(name, str):
        return False
    if len(name) > 120 or name.startswith('-'):
        return False
    # Strict: alphanumeric, dot, dash, underscore only
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\-]*", name))


def validate_systemctl_action(action):
    """Validate systemctl action against the whitelist enforced by the
    monitoring-systemctl privileged helper (must stay in sync with it)."""
    allowed_actions = ("start", "stop", "restart", "reload", "enable", "disable", "reset-failed")
    return action in allowed_actions


def validate_service_name(name):
    """Validate systemd service name strictly."""
    if not name or not isinstance(name, str):
        return False
    if len(name) > 256 or name.startswith('-'):
        return False
    # Must start with alphanumeric, allow @ . - _
    return bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9@.\-_]*", name))


def validate_vm_name(name):
    """Validate libvirt VM name strictly."""
    if not name or not isinstance(name, str):
        return False
    if len(name) > 120 or name.startswith('-'):
        return False
    return bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.\-]*", name))


def validate_disk_path(path):
    """Validate disk path - must be absolute with safe characters only."""
    if not path or not isinstance(path, str):
        return False
    if len(path) > 256 or not path.startswith('/'):
        return False
    if ".." in path.split("/"):
        return False
    # Normalized check
    if os.path.normpath(path) != path:
        return False
    return bool(re.fullmatch(r"/[a-zA-Z0-9./\-_]+", path))


def sanitize_int(value, default=0, min_val=None, max_val=None):
    """Safely convert to int with bounds checking."""
    try:
        result = int(str(value).strip())
        if min_val is not None and result < min_val:
            return min_val
        if max_val is not None and result > max_val:
            return max_val
        return result
    except (TypeError, ValueError):
        return default
