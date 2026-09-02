"""Maintenance / fix actions (safe, non-destructive, package-manager aware).

All mutating operations use the same validated privilege helpers as the
troubleshooting centre.  A command's exit status is part of the API contract:
failed maintenance must never be reported to the browser as a successful
"Done" response.
"""
from flask import Blueprint, jsonify, request
from markupsafe import escape

from .commands import run_privileged
from .common import _audit, _cache_clear
from .packages import (FIX_ACTIONS, FIX_ALIASES, FIX_COMMANDS,
                       PACKAGE_MUTATION_LOCK, _pkg_manager,
                       clear_updatable_cache)
from .security import rate_limit

bp = Blueprint("fixes", __name__)

_PACKAGE_ACTIONS = frozenset({"update", "upgrade", "full-upgrade",
                              "autoremove", "clean", "fix-broken"})

# Managers whose "upgrade" already refreshes metadata and installs new
# kernels, so full-upgrade is the same operation.
_FULL_UPGRADE_EQUALS_UPGRADE = frozenset({"dnf", "yum", "pacman", "apk"})

# Managers whose metadata must be refreshed before the upgrade transaction
# (apt and zypper do not refresh automatically on upgrade).
_REFRESH_BEFORE_FULL_UPGRADE = frozenset({"apt", "zypper"})


def _output(stdout, stderr, fallback=""):
    """Combine command output without losing a useful error message."""
    text = "\n".join(part.strip() for part in (stdout or "", stderr or "")
                   if part and part.strip())
    return text[-500:] if text else fallback


def _clear_stale_caches():
    """Drop cached state that mutations invalidate (updates, checks, diag).

    The dashboard polls /api/checks, /api/troubleshooting and /api/updates
    with server-side TTLs; after a package change the operator should see the
    fresh state immediately instead of up to minutes of stale data.
    """
    clear_updatable_cache()
    _cache_clear("_checks")
    _cache_clear("_diag")
    _cache_clear("maintain")


def _result(action, manager, code, stdout, stderr):
    """Create a truthful API response for a completed helper invocation."""
    text = _output(stdout, stderr, "Done")
    if code == 0:
        _clear_stale_caches()
        _audit("fix", action=action, manager=manager, outcome="success")
        return jsonify({"result": text, "action": action, "manager": manager}), 200

    # Include the exit status so the UI/operator can distinguish a missing
    # helper, a package-manager lock, a read-only mount, and a package error.
    message = f"{action} failed (exit {code}): {text}"
    _audit("fix", action=action, manager=manager, outcome="failed",
           exit_code=code)
    return jsonify({"error": message, "action": action, "manager": manager,
                    "exit_code": code}), 500


def _unsupported(action, manager, message):
    _audit("fix", action=action, manager=manager, outcome="unavailable")
    return jsonify({"error": message, "action": action, "manager": manager}), 503


def run_fix_action(requested_action, manager=None):
    """Execute one fix action and return (payload_dict, http_status).

    Shared by POST /api/fix and the System & Kernel tool endpoints so the
    dashboard only has one implementation of each privileged operation.
    """
    action = FIX_ALIASES.get(requested_action, requested_action)
    if action not in FIX_ACTIONS:
        return (jsonify({"error": f"Unknown action: "
                                   f"{escape(str(action))[:60]}"}), 400)

    manager = manager or _pkg_manager()

    if action in _PACKAGE_ACTIONS:
        if not manager:
            return _unsupported(action, manager,
                                "No supported package manager found")
        if action == "full-upgrade" and manager in _FULL_UPGRADE_EQUALS_UPGRADE:
            action = "upgrade"
        cmd = FIX_COMMANDS.get(manager, {}).get(action)
        if not cmd:
            return _unsupported(action, manager,
                                f"No command for package manager {manager}")
        # apt/dpkg, dnf/rpm, etc. maintain a global database lock. Serialize
        # dashboard, maintain-tool and diagnostics requests before invoking
        # the helper.
        with PACKAGE_MUTATION_LOCK:
            if (requested_action in ("full-upgrade", "system-upgrade")
                    and manager in _REFRESH_BEFORE_FULL_UPGRADE):
                # apt needs `update` before full-upgrade (new package lists);
                # zypper needs a refresh before dist-upgrade. Run both inside
                # the package lock so no other transaction can interleave.
                refresh = FIX_COMMANDS.get(manager, {}).get("update")
                code, stdout, stderr = run_privileged(
                    refresh[0], refresh[1:], timeout=300)
                if code != 0:
                    return _result("full-upgrade", manager, code, stdout,
                                   stderr)
                code2, out2, err2 = run_privileged(cmd[0], cmd[1:], timeout=300)
                return _result("full-upgrade", manager, code2,
                               (stdout or "") + (out2 or ""),
                               (stderr or "") + (err2 or ""))
            code, stdout, stderr = run_privileged(
                cmd[0], cmd[1:], timeout=300)
        return _result(action, manager, code, stdout, stderr)

    if action == "clear-logs":
        # Try both independent cleanup operations, then fail if either one did
        # not complete. This preserves partial progress while remaining honest
        # about the result shown by the dashboard.
        code_journal, out_journal, err_journal = run_privileged(
            "monitoring-journal-vacuum", timeout=60)
        code_logs, out_logs, err_logs = run_privileged(
            "monitoring-clean-old-logs", ["--min-age-days", "7"], timeout=60)
        code = 0 if code_journal == 0 and code_logs == 0 else (
            code_journal if code_journal != 0 else code_logs)
        stdout = (out_journal or "") + (out_logs or "")
        stderr = (err_journal or "") + (err_logs or "")
        return _result(action, manager, code, stdout, stderr)

    # FIX_ACTIONS and the aliases are deliberately exhaustive. Keep this guard
    # in case a future action is added without its implementation above.
    return _unsupported(action, manager, "Maintenance action is not implemented")


@bp.route("/api/fix", methods=["POST"])
@rate_limit("10 per minute")
def api_fix():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("action"), str):
        return jsonify({"error": "action is required"}), 400
    payload, status = run_fix_action(data["action"].strip())
    return payload, status
