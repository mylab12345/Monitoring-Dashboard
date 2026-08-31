"""Self-repair for the service mount namespace blocking package operations.

Older monitoring.service units lacked ``ReadWritePaths=/usr /etc /boot /efi``,
so apt/dnf/pacman run inside a read-only namespace and monitoring-package
refuses transactions with "required filesystem is read-only". This module
exposes the remediation - patch the unit, ``systemctl daemon-reload`` and
restart monitoring - as dashboard actions performed by the whitelisted
``monitoring-self-repair`` helper through the service account's passwordless
sudo, the same trust boundary as every other privileged operation.
"""
import json
import os

from flask import Blueprint, jsonify

from .commands import (PASSWORDLESS_SUDO, mount_status, privileged_tool,
                       run_privileged)
from .common import _audit, _cached
from .security import rate_limit

bp = Blueprint("selfrepair", __name__)

HELPER = "monitoring-self-repair"
REQUIRED_PATHS = ("/usr", "/etc", "/boot", "/efi")


def _helper_path():
    try:
        return privileged_tool(HELPER)
    except ValueError:
        return ""


def _repair_check():
    """Run the helper's --check and merge app-side mount facts.

    Never raises: a broken helper must degrade to a clear status payload, not
    a 500 that hides the read-only condition from the operator.
    """
    # Stable contract even when the helper is not installed yet: the UI only
    # shows a repair offer when the helper is present AND something is broken.
    facts = {
        "helper": "missing",
        "helper_path": _helper_path(),
        "systemd": None,
        "unit_path": "/etc/systemd/system/monitoring.service",
        "unit_exists": None,
        "unit_recognized": None,
        "unit_needs_patch": None,
        "service_active": None,
        "service_stale": None,
        "namespace_read_only": [],
        "in_service_context": None,
        "writable": None,
        "blocked": False,
        "actionable": False,
        "message": "",
    }
    path = _helper_path()
    if path and os.path.isfile(path):
        facts["helper"] = ("root" if os.geteuid() == 0
                           else "sudo" if PASSWORDLESS_SUDO else "no-sudo")
        code, out, err = run_privileged(HELPER, ["--check"], timeout=20)
        if code == 0:
            try:
                facts.update(json.loads(out or "{}"))
            except (ValueError, TypeError):
                facts["helper_error"] = (out or err or "")[:300]
        else:
            facts["helper_error"] = ((err or "") + (out or "")).strip()[:300]
            facts["helper"] = "denied"
    facts["app_read_only_paths"] = [
        {"path": p, "read_only": m["read_only"], "known": m["known"],
         "options": m["options"]}
        for p in REQUIRED_PATHS for m in [mount_status(p)]
    ]
    facts["blocked"] = bool(facts.get("blocked") or any(
        m["read_only"] for m in facts["app_read_only_paths"] if m["known"]))
    facts["actionable"] = bool(facts.get("actionable") or facts["blocked"])
    return facts


@bp.route("/api/self-repair")
def api_self_repair_status():
    try:
        return jsonify(_cached("self-repair", 30, _repair_check))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc), "actionable": False}), 500


@bp.route("/api/self-repair", methods=["POST"])
@rate_limit("5 per minute")
def api_self_repair_apply():
    """Apply the repair: patch unit + daemon-reload + restart monitoring.

    The restart is scheduled through systemd so this response completes
    before the dashboard process is replaced; the UI then waits for the API
    to come back and retries the failed package action.
    """
    if not os.path.isfile(_helper_path()):
        return jsonify({"error": "monitoring-self-repair helper is not "
                                 "installed — run install.sh or update.sh "
                                 "(sudo) and retry"}), 503
    if os.geteuid() != 0 and not PASSWORDLESS_SUDO:
        return jsonify({"error": "the monitoring service account has no "
                                 "passwordless sudo for this repair"}), 503

    code, out, err = run_privileged(HELPER, ["--apply"], timeout=120)
    message = ((out or "") + (err or "")).strip()
    if code == 0:
        _audit("self-repair", outcome="ok" if "restart" not in message
               else "restart-scheduled")
        return jsonify({"result": message[:600] or "Done", "exit_code": code})
    _audit("self-repair", outcome="failed", exit_code=code)
    return jsonify({"error": message[:600] or "self-repair failed",
                    "exit_code": code}), 500
