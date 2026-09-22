"""systemd service listing and bounded control."""
from flask import Blueprint, jsonify, request

from .commands import run, run_privileged, validate_service_name, validate_systemctl_action, which
from .common import _audit
from .security import rate_limit

bp = Blueprint("services", __name__)


@bp.route("/api/services")
@rate_limit("60 per minute")
def api_services():
    if not which("systemctl"):
        return jsonify({"available": False, "services": []})
    _, out, err = run(["systemctl", "list-units", "--type=service", "--all", "--plain",
                       "--no-pager", "--no-legend"], timeout=15)
    services = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 4:
            unit = parts[0]
            desc = parts[4].strip() if len(parts) > 4 else ""
            services.append({
                "unit": unit,
                "load": parts[1],
                "active": parts[2],
                "sub": parts[3],
                "description": desc,
            })
    return jsonify({"available": True, "services": services})


@bp.route("/api/service/action", methods=["POST"])
@rate_limit("20 per minute")
def api_service_action():
    data = request.json or {}
    name = data.get("name", "")
    action = data.get("action", "")

    if not validate_service_name(name):
        return jsonify({"error": "invalid unit name"}), 400
    if not validate_systemctl_action(action):
        return jsonify({"error": "invalid action"}), 400

    # Route systemd mutations through the whitelisted helper only.
    code, out, err = run_privileged("monitoring-systemctl", [action, name], timeout=30)
    if code != 0:
        detail = ((err or "") + (out or "")).strip()[:300] or "helper failed"
        _audit("service-action", action=action, unit=name,
               outcome="failed", exit_code=code)
        # No server-side HTML-escaping: the frontend esc()s every value it
        # renders (escaping here as well would double-escape the display).
        return jsonify({"error": detail, "exit_code": code}), 500

    # Post-action verification: report the unit's actual state so the UI can
    # confirm the action really took effect instead of trusting exit code 0.
    state, verified = _verify_service_state(name, action)
    _audit("service-action", action=action, unit=name, outcome="success",
           state=state or None)
    payload = {"result": f"{action} {name}: ok"}
    if state:
        payload["state"] = state
        payload["verified"] = verified
    return jsonify(payload)


# Expected post-action states used for verification. "reload" and
# "reset-failed" have no unambiguous target state, so they verify as long as
# systemctl can report anything at all.
_EXPECTED_STATE = {
    "start": ("active", "activating"),
    "restart": ("active", "activating"),
    "stop": ("inactive", "failed", "deactivating"),
    "enable": ("enabled", "enabled-runtime", "static", "alias"),
    "disable": ("disabled", "masked"),
}


def _verify_service_state(name, action):
    """Return (state, verified) for a unit after an action; never raises."""
    try:
        if not which("systemctl"):
            return "", True
        probe = "is-enabled" if action in ("enable", "disable") else "is-active"
        _, out, err = run(["systemctl", probe, name], timeout=8)
        state = (out or err or "").strip().splitlines()
        state = state[0].strip() if state else ""
        expected = _EXPECTED_STATE.get(action)
        if not state:
            return "", True
        if expected is None:
            return state, True
        return state, state in expected
    except Exception:
        return "", True
