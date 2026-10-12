"""Automation: scheduled actions, maintenance windows, alert/auto-remediation
rules and the audit-visible run log.

A single daemon thread (started by create_app, idempotent) ticks every 30s:

  * due ``schedule`` rows execute their whitelisted action and record the
    outcome (so "upgrade tonight at 02:00" works),
  * a maintenance window runs its configured actions once per window/day,
  * ``runaway`` rules watch for processes that stay above a CPU/RAM threshold
    and alert (or gently auto-renice — nice >= 0 only, never boost),
  * ``service_watch`` rules restart failed units through the same whitelisted
    monitoring-systemctl helper the UI uses, with a per-hour restart budget,
  * old rows are pruned once a day.

Everything is opt-in: with no rules/schedules configured the tick does
almost nothing. Failures are logged and recorded, never raised into requests.
"""
import datetime
import threading
import time

from flask import Blueprint, jsonify, request

from .commands import run_privileged, validate_service_name
from .common import LOG, _audit, _int_or
from .security import rate_limit
from . import store

bp = Blueprint("automation", __name__)

_app = None
_started = False
_runaway_seen = {}      # pid -> (first_seen_high_ts, last_alert_ts, last_renice_ts)

SCHEDULE_ACTIONS = frozenset({
    "update", "upgrade", "full-upgrade", "autoremove", "clean", "fix-broken",
    "clear-logs", "snapshot",
})
RULE_TYPES = ("runaway", "service_watch")


def start_automation(app=None):
    """Start the background tick thread once (idempotent)."""
    global _started, _app
    if app is not None:
        _app = app
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True,
                     name="monitoring-automation").start()


def _loop():
    last_prune = 0.0
    while True:
        try:
            tick()
            if time.time() - last_prune > 86400:
                store.prune()
                last_prune = time.time()
        except Exception as exc:  # noqa: BLE001 — the tick must never die
            LOG.debug("automation tick failed: %s", exc)
        time.sleep(30)


# ------------------------------------------------------------- execution

def _execute_action(action, params=None):
    """Run one whitelisted action outside a request; returns (ok, detail).

    Package/log actions reuse fixes.run_fix_action inside an app context (it
    returns a Flask response); 'snapshot' drives the monitoring-snap helper.
    """
    params = params or {}
    if action == "snapshot":
        label = str(params.get("label", "scheduled"))[:60]
        safe = "".join(c for c in label if c.isalnum() or c in " ._-") or "auto"
        code, out, err = run_privileged("monitoring-snap",
                                        ["create", "--label", safe],
                                        timeout=300)
        return code == 0, ((out or err) or "").strip()[:400]
    if _app is None:
        return False, "app context unavailable"
    try:
        with _app.app_context():
            from .fixes import run_fix_action
            payload, status = run_fix_action(action)
            data = payload.get_json() if hasattr(payload, "get_json") else {}
        ok = int(status) == 200
        detail = (data.get("result") or data.get("error") or "")[:400]
        return ok, detail
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:400]


def _run_schedule_items():
    for item in store.due_schedules():
        store.set_schedule_status(item["id"], "running")
        ok, detail = _execute_action(item["action"], item.get("params"))
        store.set_schedule_status(item["id"], "done" if ok else "failed",
                                 result=detail)
        store.add_run("schedule", item["action"],
                      "success" if ok else "failed", ref=str(item["id"]),
                      detail=detail)
        _audit("scheduled-action", action=item["action"],
               outcome="success" if ok else "failed")
        try:
            from . import notify
            notify.send_notification(
                f"Monitoring: scheduled {item['action']} "
                f"{'finished' if ok else 'failed'}",
                detail or item["action"])
        except Exception:  # noqa: BLE001
            pass


def _window_due(cfg, now):
    """True when 'now' is inside the window and it has not run today."""
    if not cfg.get("enabled"):
        return False
    try:
        hh, mm = str(cfg.get("start", "02:00")).split(":")
        start_min = int(hh) * 60 + int(mm)
        length = min(max(int(cfg.get("length_min", 120)), 15), 720)
    except Exception:  # noqa: BLE001
        return False
    local = datetime.datetime.now()
    now_min = local.hour * 60 + local.minute
    if not (start_min <= now_min < start_min + length):
        return False
    days = cfg.get("days") or [0, 1, 2, 3, 4, 5, 6]
    if local.weekday() not in [int(d) for d in days if str(d).isdigit()]:
        return False
    ref = f"window-{local.strftime('%Y-%m-%d')}"
    return store.count_runs("maintenance_window", ref, 0) == 0


def _run_window():
    cfg = store.kv_get("maintenance_window", {}) or {}
    if not _window_due(cfg, time.time()):
        return
    ref = f"window-{datetime.datetime.now().strftime('%Y-%m-%d')}"
    for action in cfg.get("actions") or []:
        if action not in SCHEDULE_ACTIONS:
            continue
        ok, detail = _execute_action(action)
        store.add_run("maintenance_window", action,
                      "success" if ok else "failed", ref=ref, detail=detail)
        _audit("maintenance-window", action=item_id(action),
               outcome="success" if ok else "failed")


def item_id(action):
    return str(action)[:80]


def _check_runaway_rules():
    from .processes import top_processes_snapshot
    rules = store.list_rules("runaway")
    if not rules:
        _runaway_seen.clear()
        return
    procs = top_processes_snapshot(limit=25)
    now = time.time()
    live_pids = set()
    for proc in procs:
        pid = proc["pid"]
        live_pids.add(pid)
        for rule in rules:
            if not rule.get("enabled"):
                continue
            cfg = rule.get("config") or {}
            cpu_pct = float(cfg.get("cpu_pct", 90))
            mem_pct = float(cfg.get("mem_pct", 0))
            duration = float(cfg.get("duration_s", 300))
            high = (proc.get("cpu") or 0) >= cpu_pct or \
                   (mem_pct and (proc.get("mem") or 0) >= mem_pct)
            key = (pid, rule["id"])
            seen = _runaway_seen.get(key)
            if not high:
                _runaway_seen.pop(key, None)
                continue
            if seen is None:
                _runaway_seen[key] = [now, 0.0, 0.0]
                continue
            if now - seen[0] < duration:
                continue
            label = f"Runaway process: {proc['name']}"
            if now - seen[1] > 900:  # alert cooldown 15 min
                seen[1] = now
                msg = (f"pid {pid} ({proc['name']}) has stayed above its "
                       f"threshold for {int(duration)}s "
                       f"(cpu={proc.get('cpu')}%, mem={proc.get('mem')}%)")
                store.add_alert("warning", label, msg)
                store.add_run("runaway", rule["name"], "alerted",
                              ref=str(pid), detail=msg)
                try:
                    from . import notify
                    notify.send_notification(f"Monitoring: {label}", msg)
                except Exception:  # noqa: BLE001
                    pass
            if cfg.get("action") == "renice" and now - seen[2] > 3600:
                seen[2] = now
                nice = int(cfg.get("nice", 10))
                nice = min(max(nice, 0), 19)  # never boost priority
                code, out, err = run_privileged(
                    "monitoring-nice", ["renice", str(pid), str(nice)],
                    timeout=15)
                store.add_run("runaway", rule["name"],
                              "reniced" if code == 0 else "renice-failed",
                              ref=str(pid), detail=f"nice={nice}")
                _audit("runaway-renice", pid=pid, nice=nice,
                       outcome="success" if code == 0 else "failed")
    # Forget entries for pids that exited (bounded memory).
    for key in list(_runaway_seen):
        if key[0] not in live_pids:
            _runaway_seen.pop(key, None)


def _check_service_watch():
    from .commands import run, which
    rules = store.list_rules("service_watch")
    if not rules or not which("systemctl"):
        return
    now = time.time()
    for rule in rules:
        if not rule.get("enabled"):
            continue
        cfg = rule.get("config") or {}
        unit = cfg.get("unit", "")
        if not validate_service_name(unit):
            continue
        max_per_hour = min(max(int(cfg.get("max_per_hour", 3)), 1), 10)
        code, out, _ = run(["systemctl", "is-failed", unit], timeout=8)
        if (out or "").strip() != "failed":
            continue
        if store.count_runs("service_watch", unit, now - 3600) >= max_per_hour:
            continue
        code, out, err = run_privileged("monitoring-systemctl",
                                        ["restart", unit], timeout=60)
        ok = code == 0
        store.add_run("service_watch", rule["name"],
                      "restarted" if ok else "restart-failed", ref=unit,
                      detail=(err or out or "")[:300])
        store.add_alert("warning", f"Auto-recovery: {unit}",
                        f"{unit} was failed and has been "
                        f"{'restarted' if ok else 'restart attempted (failed)'}")
        _audit("service-watch", unit=unit,
               outcome="restarted" if ok else "restart-failed")
        try:
            from . import notify
            notify.send_notification(
                f"Monitoring: auto-recovery {unit}",
                f"{unit} was in a failed state; restart "
                f"{'succeeded' if ok else 'failed'}.")
        except Exception:  # noqa: BLE001
            pass


def tick():
    """One automation cycle (exposed for tests)."""
    _run_schedule_items()
    _run_window()
    _check_runaway_rules()
    _check_service_watch()


# ------------------------------------------------------------------ API

def _valid_hhmm(value):
    try:
        hh, mm = str(value).split(":")
        return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
    except Exception:  # noqa: BLE001
        return False


def _parse_run_at(value):
    """Accept epoch seconds or 'YYYY-MM-DD HH:MM' local time."""
    if isinstance(value, (int, float)) and value > 1000000000:
        return int(value)
    try:
        dt = datetime.datetime.strptime(str(value).strip(),
                                        "%Y-%m-%d %H:%M")
        return int(dt.timestamp())
    except Exception:  # noqa: BLE001
        return None


@bp.route("/api/schedule", methods=["GET", "POST"])
@rate_limit("30 per minute")
def api_schedule():
    if request.method == "GET":
        include_done = request.args.get("all") == "1"
        return jsonify({"items": store.list_schedule(include_done),
                        "actions": sorted(SCHEDULE_ACTIONS),
                        "available": store.available()})
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "")).strip()
    if action not in SCHEDULE_ACTIONS:
        return jsonify({"error": "action must be one of: "
                                 + ", ".join(sorted(SCHEDULE_ACTIONS))}), 400
    run_at = _parse_run_at(data.get("run_at"))
    if run_at is None:
        return jsonify({"error": "run_at must be epoch seconds or "
                                 "'YYYY-MM-DD HH:MM'"}), 400
    if run_at < time.time() - 60:
        return jsonify({"error": "run_at is in the past"}), 400
    params = data.get("params") or {}
    if not isinstance(params, dict):
        return jsonify({"error": "params must be an object"}), 400
    sid = store.add_schedule(action, params, run_at)
    _audit("schedule-add", action=action, run_at=run_at, id=sid)
    return jsonify({"ok": True, "id": sid})


@bp.route("/api/schedule/<int:schedule_id>", methods=["DELETE"])
@rate_limit("30 per minute")
def api_schedule_delete(schedule_id):
    store.delete_schedule(schedule_id)
    _audit("schedule-delete", id=schedule_id)
    return jsonify({"ok": True})


@bp.route("/api/rules", methods=["GET", "POST"])
@rate_limit("30 per minute")
def api_rules():
    if request.method == "GET":
        return jsonify({"rules": store.list_rules(),
                        "types": list(RULE_TYPES),
                        "available": store.available()})
    data = request.get_json(silent=True) or {}
    rule_type = str(data.get("type", "")).strip()
    name = str(data.get("name", "")).strip()[:120]
    config = data.get("config") or {}
    if rule_type not in RULE_TYPES:
        return jsonify({"error": "type must be runaway or service_watch"}), 400
    if not isinstance(config, dict):
        return jsonify({"error": "config must be an object"}), 400
    if rule_type == "runaway":
        clean = {}
        for key, lo, hi in (("cpu_pct", 1, 100), ("mem_pct", 0, 100),
                            ("duration_s", 30, 3600), ("nice", 0, 19)):
            if key in config:
                val = _int_or(config.get(key), lo)
                clean[key] = min(max(val, lo), hi)
        if "action" in config and config["action"] not in ("alert", "renice"):
            return jsonify({"error": "action must be alert or renice"}), 400
        clean["action"] = config.get("action", "alert")
        clean.setdefault("cpu_pct", 90)
        clean.setdefault("duration_s", 300)
        config = clean
    else:
        unit = str(config.get("unit", "")).strip()
        if not validate_service_name(unit):
            return jsonify({"error": "config.unit must be a valid unit name"}), 400
        config = {"unit": unit,
                  "max_per_hour": min(max(_int_or(config.get("max_per_hour"), 3),
                                          1), 10)}
        if not name:
            name = f"watch {unit}"
    rid = store.add_rule(rule_type, name or rule_type, config)
    _audit("rule-add", type=rule_type, name=name, id=rid)
    return jsonify({"ok": True, "id": rid})


@bp.route("/api/rules/<int:rule_id>", methods=["POST", "DELETE"])
@rate_limit("30 per minute")
def api_rule_item(rule_id):
    if request.method == "DELETE":
        store.delete_rule(rule_id)
        _audit("rule-delete", id=rule_id)
        return jsonify({"ok": True})
    data = request.get_json(silent=True) or {}
    store.set_rule_enabled(rule_id, bool(data.get("enabled", True)))
    _audit("rule-toggle", id=rule_id, enabled=bool(data.get("enabled", True)))
    return jsonify({"ok": True})


@bp.route("/api/runs")
@rate_limit("60 per minute")
def api_runs():
    limit = min(max(_int_or(request.args.get("limit", 50), 50), 1), 200)
    kind = (request.args.get("kind") or "").strip() or None
    return jsonify({"runs": store.recent_runs(limit, kind),
                    "available": store.available()})


@bp.route("/api/maintenance/window", methods=["GET", "POST"])
@rate_limit("30 per minute")
def api_maintenance_window():
    if request.method == "GET":
        cfg = store.kv_get("maintenance_window", {}) or {}
        # Normalised contract: always expose every field with defaults so the
        # UI (and tests) never have to guess what "unset" looks like.
        defaults = {"enabled": False, "start": "02:00", "length_min": 120,
                    "days": [0, 1, 2, 3, 4, 5, 6], "actions": []}
        defaults.update({k: v for k, v in cfg.items() if k in defaults})
        return jsonify(defaults)
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get("enabled"))
    start = str(data.get("start", "02:00"))
    if not _valid_hhmm(start):
        return jsonify({"error": "start must be HH:MM"}), 400
    length_min = min(max(_int_or(data.get("length_min"), 120), 15), 720)
    days = data.get("days", [0, 1, 2, 3, 4, 5, 6])
    if not isinstance(days, list) or not all(
            isinstance(d, int) and 0 <= d <= 6 for d in days):
        return jsonify({"error": "days must be a list of 0..6 (Mon..Sun)"}), 400
    actions = data.get("actions", [])
    if not isinstance(actions, list) or any(a not in SCHEDULE_ACTIONS
                                            for a in actions):
        return jsonify({"error": "actions must be a subset of: "
                                 + ", ".join(sorted(SCHEDULE_ACTIONS))}), 400
    cfg = {"enabled": enabled, "start": start, "length_min": length_min,
           "days": days, "actions": actions}
    store.kv_set("maintenance_window", cfg)
    _audit("maintenance-window-config", enabled=enabled, start=start,
           actions=",".join(actions))
    return jsonify({"ok": True, "config": cfg})
