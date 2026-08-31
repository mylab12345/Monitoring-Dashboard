"""Privilege reporting: which bounded operations the service account may do."""
import grp
import os
import pwd

from flask import Blueprint, jsonify

from .commands import PASSWORDLESS_SUDO, privileged_tool, run
from .common import SUDOERS_FILE, _cached, PRIVILEGE_DIR

bp = Blueprint("privileges", __name__)

_PRIVILEGE_HELPERS = [
    "monitoring-systemctl",
    "monitoring-package",
    "monitoring-journal-vacuum",
    "monitoring-clean-old-logs",
    "monitoring-vm",
    "monitoring-vm-config",
    "monitoring-qemu",
    "monitoring-kill",
    "monitoring-zombie-clean",
]


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
    return report


@bp.route("/api/privileges")
def api_privileges():
    try:
        return jsonify(_cached("privileges", 30, _privilege_status))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
