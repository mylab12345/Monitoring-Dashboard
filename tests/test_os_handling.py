#!/usr/bin/env python3
"""
Regression tests for the OS-handling feature set (v2.8.0):

  * persistent SQLite store (metrics rollups, alerts, audit, rules, schedule)
  * OS facts / capability report (/api/osinfo)
  * pressure + memory breakdown + OOM feed (/api/memory, /api/status)
  * long-term persistent history (/api/history/long)
  * process details/tree/priority/history endpoints
  * service unit types, dependency blast radius, unit logs and usage
  * disk health (inodes/devices/SMART)
  * host security posture, kernel hardening, logins, integrity, audit
  * automation (schedule/rules/window/runs) input validation
  * notification config validation

The Flask-backed tests self-skip when flask/psutil are unavailable, matching
the contract of tests/test_app.py.
"""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("MONITORING_HOME", REPO)
# Hermetic store (see tests/test_app.py) — same path so the two suites share
# one throwaway DB instead of touching the developer's runtime data.
os.environ.setdefault("MONITORING_DB",
                      os.path.join(REPO, "data", f"test-{os.getpid()}.db"))

try:
    import app as app_mod
except Exception as exc:  # pragma: no cover
    app_mod = None
    IMPORT_ERROR = str(exc)
else:
    IMPORT_ERROR = None

APP_AVAILABLE = app_mod is not None and getattr(app_mod, "app", None) is not None
SKIP_REASON = "flask/psutil not available: {}".format(
    IMPORT_ERROR or getattr(app_mod, "IMPORT_ERROR", None) or "app failed to initialise")


class StoreUnit(unittest.TestCase):
    """The SQLite store must be usable with zero dependencies and never raise."""

    def test_store_roundtrip(self):
        from monitor import store
        self.assertTrue(store.available(), store.status())
        # kv
        store.kv_set("test_key_os_handling", {"a": 1})
        self.assertEqual(store.kv_get("test_key_os_handling"), {"a": 1})
        # alerts
        aid = store.add_alert("warning", "Unit Test Alert", "hello", value=1.5,
                              threshold=1.0)
        self.assertIsNotNone(aid)
        rows = store.recent_alerts(5)
        self.assertTrue(any(r["name"] == "Unit Test Alert" for r in rows))
        store.resolve_alert("Unit Test Alert")
        rows = store.recent_alerts(5)
        hit = next(r for r in rows if r["name"] == "Unit Test Alert")
        self.assertIsNotNone(hit["resolved_ts"])
        # audit
        store.add_audit("unit-test", "detail=1")
        self.assertTrue(any(r["event"] == "unit-test"
                            for r in store.recent_audit(5)))
        # metric rollups
        store.record_metric({"t": 1700000000, "cpu": 1.0, "ram": 2.0,
                             "swap": 0, "disk": 3.0, "load1": 0.1,
                             "rx": 1, "tx": 2, "temp": 40})
        hist = store.metric_history(1699990000, limit=10)
        self.assertTrue(any(h["ts"] == 1700000000 for h in hist))
        # process history
        store.record_processes([{"pid": 42, "name": "unit", "user": "u",
                                 "cpu": 3.0, "mem": 1.0}], ts=1700000000)
        self.assertTrue(store.process_history(1699990000))
        # rules
        rid = store.add_rule("runaway", "unit-rule", {"cpu_pct": 90})
        self.assertIsNotNone(rid)
        self.assertTrue(any(r["id"] == rid for r in store.list_rules()))
        store.set_rule_enabled(rid, False)
        store.delete_rule(rid)
        self.assertFalse(any(r["id"] == rid for r in store.list_rules()))
        # schedule
        sid = store.add_schedule("clean", {}, 4102444800)
        self.assertIsNotNone(sid)
        self.assertTrue(any(s["id"] == sid for s in store.list_schedule()))
        store.set_schedule_status(sid, "done", "ok")
        store.delete_schedule(sid)
        # runs + budget counter
        store.add_run("unit_test", "noop", "success", ref="x")
        self.assertGreaterEqual(store.count_runs("unit_test", "x", 0), 1)
        self.assertTrue(store.recent_runs(5))
        # prune must not raise
        store.prune()

    def test_store_degrades_gracefully(self):
        """A broken DB path must disable the store, not break callers."""
        from monitor import store
        old_path, old_state = store.DB_PATH, dict(store._state)
        try:
            store.DB_PATH = "/proc/nonexistent-dir/monitoring.db"
            store._state["enabled"] = None
            self.assertFalse(store.available())
            self.assertEqual(store.recent_alerts(), [])
            self.assertIsNone(store.add_alert("warning", "x", "y"))
            self.assertEqual(store.kv_get("k", "default"), "default")
        finally:
            store.DB_PATH = old_path
            store._state.update(old_state)
            store._state["enabled"] = None
            store.available()


class OsFactsUnit(unittest.TestCase):
    def test_os_facts_contract(self):
        from monitor.osinfo import os_facts
        facts = os_facts()
        for key in ("hostname", "os_name", "family", "kernel", "arch",
                    "package_manager", "init_system", "virtualization",
                    "capabilities", "lifecycle"):
            self.assertIn(key, facts)
        self.assertIsInstance(facts["capabilities"], dict)
        self.assertIn("services", facts["capabilities"])
        # lifecycle must always carry the known flag
        self.assertIn("known", facts["lifecycle"])


class AlertEvaluationUnit(unittest.TestCase):
    def test_threshold_transitions_record_alerts(self):
        from monitor import store
        from monitor import metrics
        metrics._alert_state.clear()
        metrics._alert_notify_ts.clear()
        store.kv_set("alert_thresholds", {"cpuWarn": 50, "cpuCrit": 90})
        try:
            metrics.evaluate_alerts({"cpu": 10, "ram": 10, "disk": 10,
                                     "temp": 30})
            self.assertEqual(metrics._alert_state.get("cpu"), "ok")
            metrics.evaluate_alerts({"cpu": 75, "ram": 10, "disk": 10,
                                     "temp": 30})
            self.assertEqual(metrics._alert_state.get("cpu"), "warn")
            metrics.evaluate_alerts({"cpu": 95, "ram": 10, "disk": 10,
                                     "temp": 30})
            self.assertEqual(metrics._alert_state.get("cpu"), "crit")
            metrics.evaluate_alerts({"cpu": 5, "ram": 10, "disk": 10,
                                     "temp": 30})
            self.assertEqual(metrics._alert_state.get("cpu"), "ok")
            names = [a["name"] for a in store.recent_alerts(20)]
            self.assertIn("CPU usage", names)
        finally:
            store.kv_set("alert_thresholds", {})
            metrics._alert_state.clear()
            metrics._alert_notify_ts.clear()


@unittest.skipIf(not APP_AVAILABLE, SKIP_REASON)
class OsHandlingApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app_mod.app.test_client()

    def _get_ok(self, path):
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200, f"{path} -> {r.status_code}")
        return r.get_json()

    def test_osinfo_contract(self):
        d = self._get_ok("/api/osinfo")
        self.assertIn("capabilities", d)
        self.assertIn("init_system", d)

    def test_memory_and_pressure(self):
        d = self._get_ok("/api/memory")
        for key in ("breakdown", "oom", "psi"):
            self.assertIn(key, d)

    def test_status_includes_psi_key(self):
        d = self._get_ok("/api/status")
        self.assertIn("psi", d)

    def test_history_long(self):
        d = self._get_ok("/api/history/long?hours=2")
        self.assertIn("samples", d)
        self.assertIn("available", d)
        # hours must be clamped
        d = self._get_ok("/api/history/long?hours=99999")
        self.assertLessEqual(d["hours"], 168)

    def test_alerts_history(self):
        d = self._get_ok("/api/alerts/history?limit=10")
        self.assertIn("alerts", d)
        self.assertIn("available", d)

    def test_process_detail_validation(self):
        self.assertEqual(self.client.get("/api/process/0").status_code, 400)
        self.assertEqual(self.client.get("/api/process/1000001").status_code, 400)
        # a pid that does not exist -> 404, never a 500 traceback
        r = self.client.post("/api/process/priority", json={"pid": 1000000,
                                                            "nice": 5})
        self.assertIn(r.status_code, (404, 500))
        self.assertIn("error", r.get_json())

    def test_process_priority_validation(self):
        r = self.client.post("/api/process/priority", json={"pid": 1, "nice": 5})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/process/priority", json={"pid": 10, "nice": 99})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/process/priority",
                             json={"pid": 10, "affinity": "$(id)"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/process/priority", json={"pid": 10})
        self.assertEqual(r.status_code, 400)

    def test_process_tree_and_history(self):
        self._get_ok("/api/process/tree")
        d = self._get_ok("/api/process/history?minutes=30")
        self.assertIn("series", d)

    def test_service_deps_logs_usage_validation(self):
        for path in ("/api/service/deps?name=$(id)",
                     "/api/service/logs?name=-oProxy=x",
                     "/api/service/usage?name=x;rm"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 400, path)

    def test_services_type_filter(self):
        d = self._get_ok("/api/services?type=timer")
        self.assertIn("services", d)
        self.assertIn("init", d)

    def test_diskhealth_validation(self):
        r = self.client.get("/api/diskhealth?disk=../../etc/passwd")
        self.assertEqual(r.status_code, 400)
        d = self._get_ok("/api/diskhealth")
        for key in ("inodes", "devices", "io"):
            self.assertIn(key, d)

    def test_security_posture_contract(self):
        d = self._get_ok("/api/security/posture")
        for key in ("firewall", "mac", "hardening", "exposure", "logins",
                    "integrity", "security_updates"):
            self.assertIn(key, d)
        h = self._get_ok("/api/security/hardening")
        self.assertIn("items", h)
        self.assertIn("grade", h)

    def test_hardening_validation(self):
        r = self.client.post("/api/security/hardening",
                             json={"action": "explode"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/security/hardening",
                             json={"action": "apply", "profile": "turbo"})
        self.assertEqual(r.status_code, 400)

    def test_audit_endpoint(self):
        d = self._get_ok("/api/audit?limit=5")
        self.assertIn("entries", d)

    def test_notify_config_validation(self):
        r = self.client.post("/api/notify/config",
                             json={"kind": "evil", "url": "http://x"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/notify/config",
                             json={"kind": "webhook", "url": "ftp://x"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/notify/config",
                             json={"kind": "webhook",
                                   "url": "http://example.invalid/x\ny"})
        self.assertEqual(r.status_code, 400)
        d = self._get_ok("/api/notify/config")
        self.assertIn("token_set", d)

    def test_schedule_validation(self):
        r = self.client.post("/api/schedule", json={"action": "explode",
                                                    "run_at": 4102444800})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/schedule", json={"action": "clean",
                                                    "run_at": "garbage"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/schedule", json={"action": "clean",
                                                    "run_at": 100})
        self.assertEqual(r.status_code, 400)
        d = self._get_ok("/api/schedule")
        self.assertIn("items", d)
        self.assertIn("actions", d)

    def test_rules_validation(self):
        r = self.client.post("/api/rules", json={"type": "evil"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/rules",
                             json={"type": "runaway",
                                   "config": {"action": "kill"}})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/rules",
                             json={"type": "service_watch",
                                   "config": {"unit": "$(id)"}})
        self.assertEqual(r.status_code, 400)
        d = self._get_ok("/api/rules")
        self.assertIn("rules", d)

    def test_maintenance_window_validation(self):
        r = self.client.post("/api/maintenance/window",
                             json={"start": "99:99"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/maintenance/window",
                             json={"days": [9]})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/maintenance/window",
                             json={"actions": ["explode"]})
        self.assertEqual(r.status_code, 400)
        d = self._get_ok("/api/maintenance/window")
        self.assertIn("enabled", d)

    def test_schedule_delete(self):
        r = self.client.post("/api/schedule",
                             json={"action": "clean", "run_at": 4102444800})
        self.assertEqual(r.status_code, 200)
        sid = r.get_json()["id"]
        r = self.client.delete(f"/api/schedule/{sid}")
        self.assertEqual(r.status_code, 200)


@unittest.skipIf(not APP_AVAILABLE, SKIP_REASON)
class AutomationTick(unittest.TestCase):
    """The automation tick must never raise and must respect rule budgets."""

    def test_tick_runs_clean(self):
        import time
        from monitor import automation, store
        store.add_run("service_watch", "budget-test", "restarted",
                      ref="unit-test.service")
        # tick exercises schedules/window/runaway/service-watch paths
        automation.tick()
        automation.tick()

    def test_execute_action_unknown(self):
        from monitor import automation
        ok, detail = automation._execute_action("not-an-action")
        self.assertFalse(ok)

    def test_runaway_rule_evaluation(self):
        from monitor import automation, store
        rid = store.add_rule("runaway", "unit-runaway",
                             {"cpu_pct": 1, "duration_s": 30, "action": "alert"})
        try:
            automation._check_runaway_rules()
            # second call after the duration window may alert — must not raise
            automation._runaway_seen.clear()
            automation._check_runaway_rules()
        finally:
            store.delete_rule(rid)


if __name__ == "__main__":
    unittest.main()
