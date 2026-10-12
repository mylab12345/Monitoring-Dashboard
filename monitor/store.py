"""SQLite persistence for history, alerts, audit, rules and schedules.

The dashboard used to keep every historical sample in RAM (lost on restart)
and every alert/audit entry in browser localStorage. This module adds a
small, dependency-free SQLite store so the tool can answer "what happened
yesterday", keep a server-side audit trail and drive scheduled automation.

Design rules:
  * stdlib ``sqlite3`` only — no new package requirements.
  * every public function is best-effort: a broken/unwritable DB must never
    break a monitoring request (the store degrades to no-op and reports
    ``available: False``).
  * one connection per operation plus a module lock — simple, safe with the
    Flask threaded server and the background automation thread.
  * bounded retention prunes old rows so the DB cannot grow without limit.
"""
import json
import os
import sqlite3
import threading
import time

from .common import APP_HOME, LOG

# Explicit env override wins; otherwise the DB lives in APP_HOME/data so a
# repo checkout works in development and /opt/monitoring works when installed.
DB_PATH = os.environ.get("MONITORING_DB") or os.path.join(APP_HOME, "data",
                                                          "monitoring.db")

_lock = threading.Lock()
_state = {"enabled": None, "error": None}

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv(
    key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS metrics_min(
    ts INTEGER PRIMARY KEY,
    cpu REAL, ram REAL, swap REAL, disk REAL,
    load1 REAL, rx REAL, tx REAL, temp REAL);
CREATE TABLE IF NOT EXISTS alerts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL, level TEXT NOT NULL, name TEXT NOT NULL,
    message TEXT NOT NULL, value REAL, threshold REAL,
    resolved_ts INTEGER);
CREATE TABLE IF NOT EXISTS audit(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL, event TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS proc_hist(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL, pid INTEGER NOT NULL, name TEXT,
    user TEXT, cpu REAL, mem REAL);
CREATE TABLE IF NOT EXISTS rules(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL, name TEXT NOT NULL,
    config TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
    created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS schedule(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL, params TEXT NOT NULL,
    run_at INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    created INTEGER NOT NULL, result TEXT);
CREATE TABLE IF NOT EXISTS runs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL, kind TEXT NOT NULL, ref TEXT,
    action TEXT NOT NULL, outcome TEXT NOT NULL, detail TEXT);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
CREATE INDEX IF NOT EXISTS idx_proc_ts ON proc_hist(ts);
CREATE INDEX IF NOT EXISTS idx_runs_ts ON runs(ts);
"""

# Retention (seconds). Keep a week of minute metrics, a day of process
# history, a month of automation runs and three months of audit/alerts.
RETENTION = {
    "metrics_min": 7 * 86400,
    "proc_hist": 86400,
    "runs": 30 * 86400,
    "audit": 90 * 86400,
    "alerts": 90 * 86400,
}


def _connect():
    """Open (and initialise) the DB, or disable the store on failure."""
    global _state
    if _state["enabled"] is False:
        return None
    try:
        if _state["enabled"] is None:
            parent = os.path.dirname(DB_PATH)
            if parent:
                os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        _state["enabled"] = True
        _state["error"] = None
        return conn
    except Exception as exc:  # noqa: BLE001 — degrade, never raise
        _state["enabled"] = False
        _state["error"] = str(exc)
        LOG.warning("persistent store unavailable (%s); continuing without "
                    "history persistence", exc)
        return None


def _run(fn, default=None):
    """Execute fn(conn) under the lock; return default on any failure."""
    with _lock:
        conn = _connect()
        if conn is None:
            return default
        try:
            result = fn(conn)
            conn.commit()
            return result
        except Exception as exc:  # noqa: BLE001
            LOG.debug("store operation failed: %s", exc)
            return default
        finally:
            conn.close()


def available():
    """True when the SQLite store is writable and initialised."""
    return _connect() is not None


def status():
    return {"available": bool(available()), "path": DB_PATH,
            "error": _state.get("error")}


# ---------------------------------------------------------------- kv config

def kv_get(key, default=None):
    def fn(conn):
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except Exception:  # noqa: BLE001 — tolerate hand-edited rows
            return row["value"]
    return _run(fn, default)


def kv_set(key, value):
    def fn(conn):
        conn.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)))
        return True
    return _run(fn, False) is True


# ------------------------------------------------------------ metric history

def record_metric(sample):
    """Persist one 1-minute rollup row (dict from the metrics sampler)."""
    def fn(conn):
        conn.execute(
            "INSERT OR REPLACE INTO metrics_min"
            "(ts, cpu, ram, swap, disk, load1, rx, tx, temp) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (int(sample.get("t", time.time())),
             sample.get("cpu"), sample.get("ram"), sample.get("swap"),
             sample.get("disk"), sample.get("load1"), sample.get("rx"),
             sample.get("tx"), sample.get("temp")))
        return True
    return _run(fn, False) is True


def metric_history(since_ts, limit=400):
    """Rollup samples since since_ts (ascending), downsampled to <= limit."""
    def fn(conn):
        rows = conn.execute(
            "SELECT ts, cpu, ram, swap, disk, load1, rx, tx, temp "
            "FROM metrics_min WHERE ts>=? ORDER BY ts ASC", (int(since_ts),)
        ).fetchall()
        data = [dict(r) for r in rows]
        if len(data) > limit > 0:
            step = max(1, -(-len(data) // limit))
            data = data[::step]
        return data
    return _run(fn, [])


# ------------------------------------------------------------------- alerts

def add_alert(level, name, message, value=None, threshold=None):
    def fn(conn):
        cur = conn.execute(
            "INSERT INTO alerts(ts, level, name, message, value, threshold) "
            "VALUES(?,?,?,?,?,?)",
            (int(time.time()), level, name, message[:500], value, threshold))
        return cur.lastrowid
    return _run(fn)


def resolve_alert(name):
    """Mark unresolved alerts with this name as resolved (now)."""
    def fn(conn):
        conn.execute(
            "UPDATE alerts SET resolved_ts=? WHERE name=? AND resolved_ts IS NULL",
            (int(time.time()), name))
        return True
    return _run(fn, False)


def recent_alerts(limit=100):
    def fn(conn):
        rows = conn.execute(
            "SELECT id, ts, level, name, message, value, threshold, resolved_ts "
            "FROM alerts ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 500),)
        ).fetchall()
        return [dict(r) for r in rows]
    return _run(fn, [])


# -------------------------------------------------------------------- audit

def add_audit(event, detail=None):
    def fn(conn):
        conn.execute("INSERT INTO audit(ts, event, detail) VALUES(?,?,?)",
                     (int(time.time()), str(event)[:120],
                      str(detail)[:2000] if detail else None))
        return True
    return _run(fn, False)


def recent_audit(limit=100):
    def fn(conn):
        rows = conn.execute(
            "SELECT id, ts, event, detail FROM audit "
            "ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 500),)
        ).fetchall()
        return [dict(r) for r in rows]
    return _run(fn, [])


# ------------------------------------------------------------- process hist

def record_processes(rows, ts=None):
    """Persist one top-consumers snapshot (list of process dicts)."""
    ts = int(ts or time.time())

    def fn(conn):
        for r in rows[:12]:
            conn.execute(
                "INSERT INTO proc_hist(ts, pid, name, user, cpu, mem) "
                "VALUES(?,?,?,?,?,?)",
                (ts, int(r.get("pid", 0)), str(r.get("name", ""))[:80],
                 str(r.get("user", ""))[:40], r.get("cpu"), r.get("mem")))
        return True
    return _run(fn, False)


def process_history(since_ts):
    def fn(conn):
        rows = conn.execute(
            "SELECT ts, pid, name, user, cpu, mem FROM proc_hist "
            "WHERE ts>=? ORDER BY ts ASC, cpu DESC", (int(since_ts),)
        ).fetchall()
        return [dict(r) for r in rows]
    return _run(fn, [])


# -------------------------------------------------------------------- rules

def add_rule(rule_type, name, config):
    def fn(conn):
        cur = conn.execute(
            "INSERT INTO rules(type, name, config, enabled, created) "
            "VALUES(?,?,?,1,?)",
            (str(rule_type)[:40], str(name)[:120], json.dumps(config),
             int(time.time())))
        return cur.lastrowid
    return _run(fn)


def list_rules(rule_type=None):
    def fn(conn):
        if rule_type:
            rows = conn.execute(
                "SELECT id, type, name, config, enabled, created FROM rules "
                "WHERE type=? ORDER BY id", (rule_type,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, type, name, config, enabled, created FROM rules "
                "ORDER BY id").fetchall()
        out = []
        for r in rows:
            item = dict(r)
            try:
                item["config"] = json.loads(item["config"])
            except Exception:  # noqa: BLE001
                item["config"] = {}
            out.append(item)
        return out
    return _run(fn, [])


def set_rule_enabled(rule_id, enabled):
    def fn(conn):
        conn.execute("UPDATE rules SET enabled=? WHERE id=?",
                     (1 if enabled else 0, int(rule_id)))
        return True
    return _run(fn, False)


def delete_rule(rule_id):
    def fn(conn):
        conn.execute("DELETE FROM rules WHERE id=?", (int(rule_id),))
        return True
    return _run(fn, False)


# ----------------------------------------------------------------- schedule

def add_schedule(action, params, run_at):
    def fn(conn):
        cur = conn.execute(
            "INSERT INTO schedule(action, params, run_at, status, created) "
            "VALUES(?,?,?, 'pending', ?)",
            (str(action)[:60], json.dumps(params or {}), int(run_at),
             int(time.time())))
        return cur.lastrowid
    return _run(fn)


def list_schedule(include_done=False):
    def fn(conn):
        sql = ("SELECT id, action, params, run_at, status, created, result "
               "FROM schedule")
        if not include_done:
            sql += " WHERE status IN ('pending','running')"
        sql += " ORDER BY run_at ASC LIMIT 200"
        out = []
        for r in conn.execute(sql).fetchall():
            item = dict(r)
            try:
                item["params"] = json.loads(item["params"])
            except Exception:  # noqa: BLE001
                item["params"] = {}
            out.append(item)
        return out
    return _run(fn, [])


def due_schedules(now=None):
    def fn(conn):
        rows = conn.execute(
            "SELECT id, action, params FROM schedule "
            "WHERE status='pending' AND run_at<=? ORDER BY run_at LIMIT 10",
            (int(now or time.time()),)).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            try:
                item["params"] = json.loads(item["params"])
            except Exception:  # noqa: BLE001
                item["params"] = {}
            out.append(item)
        return out
    return _run(fn, [])


def set_schedule_status(schedule_id, status, result=None):
    def fn(conn):
        conn.execute("UPDATE schedule SET status=?, result=? WHERE id=?",
                     (str(status)[:20], str(result)[:1000] if result else None,
                      int(schedule_id)))
        return True
    return _run(fn, False)


def delete_schedule(schedule_id):
    def fn(conn):
        conn.execute("DELETE FROM schedule WHERE id=?", (int(schedule_id),))
        return True
    return _run(fn, False)


# --------------------------------------------------------------------- runs

def add_run(kind, action, outcome, ref=None, detail=None):
    def fn(conn):
        conn.execute(
            "INSERT INTO runs(ts, kind, ref, action, outcome, detail) "
            "VALUES(?,?,?,?,?,?)",
            (int(time.time()), str(kind)[:40], str(ref)[:120] if ref else None,
             str(action)[:80], str(outcome)[:20],
             str(detail)[:1000] if detail else None))
        return True
    return _run(fn, False)


def recent_runs(limit=50, kind=None):
    def fn(conn):
        if kind:
            rows = conn.execute(
                "SELECT id, ts, kind, ref, action, outcome, detail FROM runs "
                "WHERE kind=? ORDER BY id DESC LIMIT ?",
                (str(kind)[:40], min(max(limit, 1), 200))).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, ts, kind, ref, action, outcome, detail FROM runs "
                "ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 200),)
            ).fetchall()
        return [dict(r) for r in rows]
    return _run(fn, [])


def count_runs(kind, ref, since_ts):
    """How many runs of (kind, ref) happened since since_ts — used by the
    auto-recovery budget (max N restarts per hour)."""
    def fn(conn):
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE kind=? AND ref=? AND ts>=?",
            (str(kind)[:40], str(ref)[:120], int(since_ts))).fetchone()
        return int(row["n"]) if row else 0
    return _run(fn, 0)


# ------------------------------------------------------------------ pruning

def prune():
    """Delete rows past their retention window; called by the automation tick."""
    now = int(time.time())

    def fn(conn):
        for table, max_age in RETENTION.items():
            conn.execute(f"DELETE FROM {table} WHERE ts<?",  # noqa: S608
                         (now - max_age,))
        conn.execute("DELETE FROM schedule WHERE status!='pending' "
                     "AND created<?", (now - 30 * 86400,))
        return True
    return _run(fn, False)
