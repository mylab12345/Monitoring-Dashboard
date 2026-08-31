"""Maintenance / fix actions (safe, non-destructive, package-manager aware)."""
from flask import Blueprint, jsonify, request
from markupsafe import escape

from .commands import run_privileged
from .common import _audit
from .packages import (FIX_ACTIONS, FIX_ALIASES, FIX_COMMANDS, _pkg_manager,
                       describe_fix_rc, invalidate_updatable_cache)
from .security import rate_limit

bp = Blueprint("fixes", __name__)


def _fix_result(action, code, msg, err=""):
    """Build the /api/fix response body, translating privileged-helper exit
    codes so the UI can never show "Completed" for an action that failed or
    was refused (e.g. a read-only filesystem)."""
    combined = ((msg or "") + (err or "")).strip()
    if code == 0:
        return {"result": (combined or "Done")[:500]}
    detail = describe_fix_rc(code)
    if combined:
        text = combined
        if detail and detail not in combined:
            text = combined + " — " + detail
    else:
        text = detail or f"Action '{action}' failed (exit code {code})"
    return {"result": text[:500], "error": text[:500]}


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
            return 2, "", "No command for package manager"
        return run_privileged(cmd[0], cmd[1:], timeout=600)

    if action in ("update", "upgrade", "autoremove", "clean"):
        if mgr:
            code, msg, err = pkg(action)
            # Package state changed (or the attempt reported new errors):
            # drop the cached update list so the UI refreshes immediately.
            invalidate_updatable_cache()
            return jsonify(_fix_result(action, code, msg, err))
        return jsonify({"result": "No supported package manager found",
                        "error": "No supported package manager found"})
    elif action == "fix-broken":
        if mgr == "apt":
            code, msg, err = run_privileged("monitoring-package",
                                            ["--manager", "apt", "--action", "fix-broken"],
                                            timeout=600)
            invalidate_updatable_cache()
            return jsonify(_fix_result(action, code, msg, err))
        return jsonify({"result": "fix-broken is only supported on apt-based systems",
                        "error": "fix-broken is only supported on apt-based systems"})
    elif action == "remount-rw":
        code, msg, err = run_privileged("monitoring-remount-rw", timeout=120)
        return jsonify(_fix_result(action, code, msg, err))
    elif action == "clear-logs":
        c1, o1, e1 = run_privileged("monitoring-journal-vacuum")
        c2, o2, e2 = run_privileged("monitoring-clean-old-logs", ["--min-age-days", "7"])
        combined = ((o1 or e1 or "") + (o2 or e2 or "")).strip() or "Logs cleared"
        code = 0 if c1 == 0 and c2 == 0 else 1
        return jsonify(_fix_result(action, code, combined))
    return jsonify({"result": "Done"})
