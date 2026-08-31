"""Maintenance / fix actions (safe, non-destructive, package-manager aware)."""
from flask import Blueprint, jsonify, request
from markupsafe import escape

from .commands import run_privileged
from .common import _audit
from .packages import FIX_ACTIONS, FIX_ALIASES, FIX_COMMANDS, _pkg_manager
from .security import rate_limit

bp = Blueprint("fixes", __name__)


@bp.route("/api/fix", methods=["POST"])
@rate_limit("10 per minute")
def api_fix():
    action = (request.json or {}).get("action", "")
    # Accept a few friendly aliases so UI wording and API action names can
    # differ without silently no-opping.
    action = FIX_ALIASES.get(action, action)
    if action not in FIX_ACTIONS:
        return jsonify({"error": f"Unknown action: {escape(str(action))[:60]}"}), 400
    mgr = _pkg_manager()
    msg = ""
    _audit("fix", action=action, manager=mgr)

    def pkg(action_name):
        cmd = FIX_COMMANDS.get(mgr, {}).get(action_name, [])
        if not cmd:
            return None, "No command for package manager"
        return run_privileged(cmd[0], cmd[1:], timeout=300)

    if action in ("update", "upgrade", "autoremove", "clean"):
        if mgr:
            _, msg, err = pkg(action)
            if err:
                msg = (msg or "") + (err or "")
        else:
            msg = "No supported package manager found"
    elif action == "fix-broken":
        if mgr == "apt":
            code, msg, err = run_privileged("monitoring-package",
                                            ["--manager", "apt", "--action", "fix-broken"],
                                            timeout=300)
            if err:
                msg = (msg or "") + (err or "")
        else:
            msg = "fix-broken is only supported on apt-based systems"
    elif action == "clear-logs":
        c1, o1, e1 = run_privileged("monitoring-journal-vacuum")
        c2, o2, e2 = run_privileged("monitoring-clean-old-logs", ["--min-age-days", "7"])
        msg = ((o1 or e1 or "") + (o2 or e2 or "")).strip() or "Logs cleared"
    return jsonify({"result": (msg or "Done")[:500]})
