"""Privilege reporting: which bounded operations the service account may do."""
import grp
import os
import pwd

from flask import Blueprint, jsonify

from .commands import (PASSWORDLESS_SUDO, PRIVILEGED_HELPERS, mount_status,
                       privileged_tool, run)
from .common import SUDOERS_FILE, _cached, PRIVILEGE_DIR
from .security import rate_limit

bp = Blueprint("privileges", __name__)

# Render the same allowlist privileged_tool() enforces, so the report can
# never silently omit a helper (previously monitoring-self-update and
# monitoring-privilege-check were missing here).
_PRIVILEGE_HELPERS = sorted(PRIVILEGED_HELPERS)


def _check_sudo_helper(helper):
    """Run the helper's --check through passwordless sudo."""
    tool = privileged_tool(helper)
    if not os.path.isfile(tool) or not os.access(tool, os.X_OK):
        return "missing"
    if os.geteuid() == 0:
        return "root-direct-ok"
    if not PASSWORDLESS_SUDO:
        return "sudo-not-available"
    code, out, err = run([tool, "--check"], timeout=8, sudo=True)
    return "ok" if code == 0 else "denied"


def _privilege_status():
    report = {
        "service_user": "monitoring",
        "uid": os.geteuid(),
        "user": pwd.getpwuid(os.geteuid()).pw_name if os.geteuid() > 0 else "root",
        "groups": [],
        "sudoers_file": SUDOERS_FILE,
        "sudoers_present": os.path.isfile(SUDOERS_FILE),
        "privilege_dir": PRIVILEGE_DIR,
        "helpers": [],
    }
    for gid in os.getgroups():
        try:
            name = grp.getgrgid(gid).gr_name
        except KeyError:
            name = str(gid)
        if name not in report["groups"]:
            report["groups"].append(name)
    for helper in _PRIVILEGE_HELPERS:
        report["helpers"].append({
            "name": helper,
            "path": privileged_tool(helper),
            "status": _check_sudo_helper(helper),
        })

    # A sudo rule can be correct while a systemd mount namespace still makes
    # apt/dpkg fail with "Read-only file system". Report that separately from
    # ordinary Unix permissions so the operator can tell which fix is needed.
    try:
        from .packages import _pkg_manager
        manager = _pkg_manager()
    except Exception:
        manager = None
    package_paths = [mount_status(path) for path in
                     ("/usr", "/etc", "/var", "/boot", "/efi")]
    known_paths = [item for item in package_paths if item["exists"] and item["known"]]
    package_helper = next((h for h in report["helpers"]
                           if h["name"] == "monitoring-package"), None)
    report["maintenance"] = {
        "package_manager": manager,
        "package_helper_status": package_helper["status"] if package_helper else "missing",
        "package_write_paths": package_paths,
        "package_write_ready": (
            False if any(item["read_only"] for item in known_paths)
            else True if len(known_paths) == sum(item["exists"] for item in package_paths)
            else None
        ),
        "package_write_note": (
            "A required package path is mounted read-only. This is a mount-policy "
            "issue, not a chmod issue; install the updated service unit and restart it."
            if any(item["read_only"] for item in known_paths) else ""
        ),
    }
    return report


@bp.route("/api/privileges")
@rate_limit("60 per minute")
def api_privileges():
    try:
        return jsonify(_cached("privileges", 30, _privilege_status))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
