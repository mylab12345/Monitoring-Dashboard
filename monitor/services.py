"""Service/unit control: systemd first-class, OpenRC/SysV where available."""
from flask import Blueprint, jsonify, request

from .commands import run, run_privileged, validate_service_name, validate_systemctl_action, which
from .common import _audit, _int_or
from .osinfo import _init_system
from .security import rate_limit

bp = Blueprint("services", __name__)

# Unit types the systemd listing understands (extended beyond services so
# timers, sockets and mounts — today's cron and inetd — are visible too).
_UNIT_TYPES = ("service", "socket", "timer", "mount", "target", "swap",
               "device", "path")
_OPENRC_ACTIONS = ("start", "stop", "restart", "enable", "disable")
_OPENRC_RUNLEVELS = ("sysinit", "boot", "default", "nonetwork", "shutdown")


def _openrc_list():
    """List OpenRC services via rc-status --all (best-effort parse)."""
    services = []
    code, out, err = run(["rc-status", "--all"], timeout=15)
    if code != 0:
        return services
    runlevel = ""
    for line in (out or "").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("runlevel:"):
            runlevel = stripped.split(":", 1)[1].strip()
            continue
        parts = stripped.split()
        # e.g. "sshd   [  started  ]" — name and bracketed state
        if len(parts) >= 2 and stripped.startswith((" ", "\t")):
            state = parts[-1].strip("[]")
            name = parts[0]
            if name.lower() in ("runlevel", "dynamic"):
                continue
            services.append({"unit": name, "load": "loaded",
                            "active": state, "sub": runlevel or "-",
                            "description": "", "type": "service"})
    return services


def _sysv_list():
    """List SysV services via service --status-all (read-only)."""
    services = []
    code, out, err = run(["service", "--status-all"], timeout=15)
    for line in (out or "").splitlines():
        parts = line.split()
        # "[ + ]  sshd" / "[ - ]  cron" / "[ ? ]  foo"
        if len(parts) >= 3 and parts[0] == "[" and parts[2] == "]":
            mark = parts[1]
            name = parts[3] if len(parts) > 3 else ""
            if not name:
                continue
            state = {"+": "running", "-": "stopped"}.get(mark, "unknown")
            services.append({"unit": name, "load": "loaded", "active": state,
                            "sub": "-", "description": "", "type": "service"})
    return services


@bp.route("/api/services")
@rate_limit("60 per minute")
def api_services():
    """List units. ?type=service|socket|timer|... and ?failed=1 filter (systemd).

    On OpenRC/SysV hosts the listing falls back to rc-status / service so the
    tab stays useful instead of reporting 'unavailable'.
    """
    init = _init_system()
    req_type = (request.args.get("type") or "").strip().lower()
    failed_only = request.args.get("failed") == "1"
    if init == "systemd":
        if not which("systemctl"):
            return jsonify({"available": False, "services": [], "init": init})
        types = req_type if req_type in _UNIT_TYPES else "service"
        argv = ["systemctl", "list-units", f"--type={types}", "--all", "--plain",
                "--no-pager", "--no-legend"]
        if failed_only:
            argv = ["systemctl", "list-units", f"--type={types}", "--failed",
                    "--plain", "--no-pager", "--no-legend"]
        _, out, err = run(argv, timeout=15)
        services = []
        for line in out.splitlines():
            parts = line.split(None, 4)
            if len(parts) >= 4:
                unit = parts[0]
                desc = parts[4].strip() if len(parts) > 4 else ""
                utype = "service"
                for suffix in _UNIT_TYPES:
                    if unit.endswith("." + suffix):
                        utype = suffix
                        break
                services.append({
                    "unit": unit,
                    "load": parts[1],
                    "active": parts[2],
                    "sub": parts[3],
                    "description": desc,
                    "type": utype,
                })
        return jsonify({"available": True, "services": services, "init": init,
                        "failed_only": failed_only})
    if init == "openrc":
        services = _openrc_list()
        if failed_only:
            services = [s for s in services
                        if s.get("active", "") not in ("started", "starting")]
        return jsonify({"available": bool(services), "services": services,
                        "init": init})
    if init == "sysvinit":
        services = _sysv_list()
        return jsonify({"available": bool(services), "services": services,
                        "init": init})
    return jsonify({"available": False, "services": [], "init": init})


@bp.route("/api/service/action", methods=["POST"])
@rate_limit("20 per minute")
def api_service_action():
    data = request.json or {}
    name = data.get("name", "")
    action = data.get("action", "")

    if not validate_service_name(name):
        return jsonify({"error": "invalid unit name"}), 400

    init = _init_system()
    if init in ("openrc", "sysvinit"):
        # Non-systemd hosts: OpenRC gets full control through the monitoring-rc
        # helper; SysV is read-only (listing only) and says so honestly.
        if init == "sysvinit":
            return jsonify({"error": "service control is not supported on "
                                     "SysV init hosts (read-only listing)"}), 501
        if action not in _OPENRC_ACTIONS:
            return jsonify({"error": f"action must be one of "
                                     f"{', '.join(_OPENRC_ACTIONS)}"}), 400
        if action in ("enable", "disable"):
            argv = ["add" if action == "enable" else "delete", name, "default"]
        else:
            argv = [action, name]
        code, out, err = run_privileged("monitoring-rc", argv, timeout=30)
        if code != 0:
            detail = ((err or out) + (out or "")).strip()[:300] or "helper failed"
            _audit("service-action", action=action, unit=name,
                   outcome="failed", exit_code=code, init=init)
            return jsonify({"error": detail, "exit_code": code}), 500
        _audit("service-action", action=action, unit=name, outcome="success",
               init=init)
        return jsonify({"result": f"{action} {name}: ok", "init": init})

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


@bp.route("/api/service/deps")
@rate_limit("30 per minute")
def api_service_deps():
    """Dependency blast radius: what this unit requires, and what depends on
    it (reverse deps) — shown before start/stop so cascades are visible."""
    name = (request.args.get("name") or "").strip()
    if not validate_service_name(name):
        return jsonify({"error": "invalid unit name"}), 400
    if _init_system() != "systemd" or not which("systemctl"):
        return jsonify({"available": False, "unit": name}), 503
    _, fwd, _ = run(["systemctl", "list-dependencies", "--no-pager", name],
                    timeout=10)
    _, rev, _ = run(["systemctl", "list-dependencies", "--reverse",
                     "--no-pager", name], timeout=10)
    return jsonify({"available": True, "unit": name,
                    "requires": (fwd or "").strip()[:6000],
                    "required_by": (rev or "").strip()[:6000]})


@bp.route("/api/service/logs")
@rate_limit("30 per minute")
def api_service_logs():
    """Recent journal lines for one unit (read-only, group access)."""
    name = (request.args.get("name") or "").strip()
    if not validate_service_name(name):
        return jsonify({"error": "invalid unit name"}), 400
    lines = min(max(_int_or(request.args.get("lines", 80), 80), 10), 500)
    if which("journalctl"):
        code, out, err = run(["journalctl", "-u", name, "--no-pager",
                              "-n", str(lines), "-o", "short"], timeout=15,
                             sudo=False)
        text = (out or "").strip() or (err or "").strip()
        return jsonify({"unit": name, "logs": text[-60000:],
                        "source": "journal"})
    return jsonify({"unit": name, "logs": "journalctl not available",
                    "source": "none"}), 503


@bp.route("/api/service/usage")
@rate_limit("30 per minute")
def api_service_usage():
    """Per-unit resource usage from cgroup accounting (when enabled)."""
    name = (request.args.get("name") or "").strip()
    if not validate_service_name(name):
        return jsonify({"error": "invalid unit name"}), 400
    if _init_system() != "systemd" or not which("systemctl"):
        return jsonify({"available": False, "unit": name}), 503
    code, out, err = run(["systemctl", "show", name, "--no-pager",
                          "--property=CPUUsageNSec,MemoryCurrent,TasksCurrent,"
                          "NRestarts,ActiveState,SubState"], timeout=10)
    if code != 0:
        return jsonify({"available": False, "unit": name,
                        "error": (err or out or "systemctl show failed")[:200]}), 502
    props = {}
    for line in (out or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k.strip()] = v.strip()

    def _num(key):
        val = props.get(key, "")
        try:
            num = int(val)
            return None if num < 0 else num
        except (TypeError, ValueError):
            return None
    cpu_ns = _num("CPUUsageNSec")
    return jsonify({
        "available": True, "unit": name,
        "cpu_seconds": round(cpu_ns / 1e9, 2) if cpu_ns is not None else None,
        "memory_bytes": _num("MemoryCurrent"),
        "tasks": _num("TasksCurrent"),
        "restarts": _num("NRestarts"),
        "active_state": props.get("ActiveState", ""),
        "sub_state": props.get("SubState", ""),
    })
