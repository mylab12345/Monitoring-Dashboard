#!/usr/bin/env python3
"""Tests for the tab/button UX hardening pass.

Covers the behaviour added on top of the baseline suite (tests/test_app.py):

* Post-action verification for service actions (state + verified fields).
* Post-action verification for process kills (verified field, real process).
* Audit logging includes the client address inside a request context.
* The dashboard template ships the new accessibility / UX affordances:
  updated-stamps, ARIA labels on toolbar buttons, alert severity filter,
  CSV export buttons, permission banners, settings import/export.
* The frontend modules define the shared helpers every tab relies on
  (duplicate-click prevention, CSV export, clipboard/download helpers).

Run:  python3 -m pytest tests/test_ui_improvements.py -q
  or: python3 -m unittest tests.test_ui_improvements -v
"""
import os
import re
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("MONITORING_HOME", REPO)

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


# ----------------------------------------------------------------------
# Service action verification
# ----------------------------------------------------------------------
@unittest.skipIf(not APP_AVAILABLE, SKIP_REASON)
class ServiceActionVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app_mod.app.test_client()

    def test_success_reports_verified_state(self):
        """A successful helper run is verified against the unit's real state."""
        from monitor import services as svc
        with patch.object(svc, "run_privileged", return_value=(0, "", "")), \
             patch.object(svc, "which", return_value=True), \
             patch.object(svc, "run", return_value=(0, "active\n", "")):
            r = self.client.post("/api/service/action",
                                 json={"name": "cron.service", "action": "restart"})
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertIn("result", j)
        self.assertEqual(j.get("state"), "active")
        self.assertTrue(j.get("verified"))

    def test_unexpected_state_flags_unverified(self):
        """Exit code 0 but a wrong post-state must be flagged, not hidden."""
        from monitor import services as svc
        with patch.object(svc, "run_privileged", return_value=(0, "", "")), \
             patch.object(svc, "which", return_value=True), \
             patch.object(svc, "run", return_value=(3, "failed\n", "")):
            r = self.client.post("/api/service/action",
                                 json={"name": "cron.service", "action": "start"})
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertEqual(j.get("state"), "failed")
        self.assertFalse(j.get("verified"))

    def test_stop_accepts_inactive(self):
        from monitor import services as svc
        with patch.object(svc, "run_privileged", return_value=(0, "", "")), \
             patch.object(svc, "which", return_value=True), \
             patch.object(svc, "run", return_value=(3, "inactive\n", "")):
            r = self.client.post("/api/service/action",
                                 json={"name": "cron.service", "action": "stop"})
        j = r.get_json()
        self.assertEqual(j.get("state"), "inactive")
        self.assertTrue(j.get("verified"))

    def test_enable_uses_is_enabled_probe(self):
        from monitor import services as svc
        calls = []

        def fake_run(argv, timeout=8, **kw):
            calls.append(list(argv))
            return (0, "enabled\n", "")

        with patch.object(svc, "run_privileged", return_value=(0, "", "")), \
             patch.object(svc, "which", return_value=True), \
             patch.object(svc, "run", side_effect=fake_run):
            r = self.client.post("/api/service/action",
                                 json={"name": "cron.service", "action": "enable"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any("is-enabled" in c for c in calls),
                        f"expected an is-enabled probe, got {calls}")
        self.assertTrue(r.get_json().get("verified"))

    def test_verification_never_breaks_success(self):
        """A crashing verifier must not turn a successful action into a 500."""
        from monitor import services as svc
        with patch.object(svc, "run_privileged", return_value=(0, "", "")), \
             patch.object(svc, "which", side_effect=RuntimeError("boom")):
            r = self.client.post("/api/service/action",
                                 json={"name": "cron.service", "action": "restart"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("result", r.get_json())

    def test_failed_helper_still_errors(self):
        from monitor import services as svc
        with patch.object(svc, "run_privileged", return_value=(1, "", "denied")):
            r = self.client.post("/api/service/action",
                                 json={"name": "cron.service", "action": "restart"})
        self.assertEqual(r.status_code, 500)
        self.assertIn("error", r.get_json())

    def test_validation_still_enforced(self):
        """The verification changes must not weaken input validation."""
        r = self.client.post("/api/service/action",
                             json={"name": "x;rm -rf /", "action": "restart"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/service/action",
                             json={"name": "cron.service", "action": "pwn"})
        self.assertEqual(r.status_code, 400)


# ----------------------------------------------------------------------
# Process kill verification
# ----------------------------------------------------------------------
@unittest.skipIf(not APP_AVAILABLE, SKIP_REASON)
class ProcessKillVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app_mod.app.test_client()

    def test_kill_own_child_is_verified(self):
        """Killing a process we own returns verified=True once it is gone."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            r = self.client.post("/api/process/kill", json={"pid": proc.pid})
            self.assertEqual(r.status_code, 200, r.get_data()[:200])
            j = r.get_json()
            self.assertIn("result", j)
            self.assertIn("verified", j)
            # Reap so the pid does not linger as a zombie in this process.
            proc.wait(timeout=10)
            self.assertTrue(j["verified"])
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_pid_gone_helper(self):
        from monitor.processes import _pid_gone
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        # After wait() the child is reaped: pid must be reported gone.
        self.assertTrue(_pid_gone(proc.pid))
        # Our own (alive) pid must not be reported gone.
        self.assertFalse(_pid_gone(os.getpid()))

    def test_guardrails_unchanged(self):
        for pid in (1, 0, -5, "abc", 2000001, None):
            r = self.client.post("/api/process/kill", json={"pid": pid})
            self.assertEqual(r.status_code, 400, f"pid={pid!r}")


# ----------------------------------------------------------------------
# Audit logging
# ----------------------------------------------------------------------
@unittest.skipIf(not APP_AVAILABLE, SKIP_REASON)
class AuditLogging(unittest.TestCase):
    def test_audit_includes_client_in_request_context(self):
        from monitor.common import _audit
        with app_mod.app.test_request_context("/api/x",
                                              environ_base={"REMOTE_ADDR": "10.9.8.7"}):
            with self.assertLogs("monitoring", level="INFO") as cm:
                _audit("unit-test", foo="bar")
        joined = "\n".join(cm.output)
        self.assertIn("action=unit-test", joined)
        self.assertIn("foo=bar", joined)
        self.assertIn("client=10.9.8.7", joined)

    def test_audit_safe_outside_request_context(self):
        from monitor.common import _audit
        with self.assertLogs("monitoring", level="INFO") as cm:
            _audit("unit-test-nocontext", foo="bar")
        self.assertIn("action=unit-test-nocontext", "\n".join(cm.output))


# ----------------------------------------------------------------------
# Template: accessibility & UX affordances
# ----------------------------------------------------------------------
@unittest.skipIf(not APP_AVAILABLE, SKIP_REASON)
class TemplateAffordances(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = app_mod.app.test_client().get("/").get_data(as_text=True)

    def test_updated_stamps_present(self):
        for stamp in ("procUpdated", "svcUpdated", "vmUpdated", "netUpdated", "logUpdated"):
            self.assertIn('id="%s"' % stamp, self.html, stamp)

    def test_permission_banners_present(self):
        self.assertIn('id="svcPermBanner"', self.html)
        self.assertIn('id="vmPermBanner"', self.html)

    def test_alert_severity_filter_present(self):
        self.assertIn('id="alertFilterSeg"', self.html)
        for f in ("crit", "warn", "ok"):
            self.assertIn('data-f="%s"' % f, self.html)

    def test_csv_export_buttons_present(self):
        for fn in ("exportProcessesCSV", "exportServicesCSV", "exportPortsCSV",
                   "exportAlertsCSV", "exportActivityCSV"):
            self.assertIn(fn + "()", self.html, fn)

    def test_settings_export_import_present(self):
        self.assertIn("exportSettings()", self.html)
        self.assertIn("importSettings()", self.html)

    def test_auto_refresh_toggles_present(self):
        for tid in ("procAuto", "svcAuto", "vmAuto", "netAuto", "logAuto"):
            self.assertIn('id="%s"' % tid, self.html, tid)

    def test_ports_filter_present(self):
        self.assertIn('id="portsSearch"', self.html)

    def test_toast_region_is_status(self):
        self.assertRegex(self.html, r'<div id="toasts"[^>]*role="status"')

    def test_icon_buttons_have_aria_labels(self):
        """Every icon-only button in the template must carry an aria-label."""
        # Find <button ... class="...icon-btn..." ...> tags and check each has
        # either an aria-label or visible text via aria-labelledby.
        buttons = re.findall(r"<button[^>]*class=\"[^\"]*icon-btn[^\"]*\"[^>]*>", self.html)
        self.assertTrue(buttons, "no icon buttons found — selector broken?")
        missing = [b for b in buttons
                   if "aria-label=" not in b and "aria-labelledby=" not in b]
        self.assertEqual(missing, [], "icon-only buttons without aria-label:\n"
                         + "\n".join(missing))

    def test_sortable_headers_in_services_table(self):
        self.assertIn('id="svcTable"', self.html)
        self.assertIsNotNone(
            re.search(r'id="svcTable">\s*<thead>.*?class="sortable" data-key="unit"',
                      self.html, re.S),
            "services table is missing sortable unit header")


# ----------------------------------------------------------------------
# Frontend modules: shared helpers exist and are wired
# ----------------------------------------------------------------------
class FrontendHelpers(unittest.TestCase):
    @staticmethod
    def _read(rel):
        with open(os.path.join(REPO, rel), encoding="utf-8") as f:
            return f.read()

    def test_core_defines_shared_helpers(self):
        core = self._read("static/js/core.js")
        for fn in ("function beginAction", "function endAction", "function setBtnBusy",
                   "async function copyText", "function downloadText", "function toCSV",
                   "function exportCSV", "function stampUpdated",
                   "async function getPrivileges", "async function updatePermBanner"):
            self.assertIn(fn, core, fn)

    def test_csv_formula_injection_guard(self):
        """toCSV must neutralise leading =, +, -, @ (spreadsheet formulas)."""
        core = self._read("static/js/core.js")
        m = re.search(r"function toCSV.*?^}", core, re.S | re.M)
        self.assertIsNotNone(m, "toCSV not found")
        self.assertRegex(m.group(0), r"\^\[=\+\\-@", "no formula-prefix guard in toCSV")

    def test_duplicate_click_prevention_wired(self):
        for rel, needle in (
            ("static/js/processes.js", "'kill:'+pid"),
            ("static/js/services.js", "beginAction(key)"),
            ("static/js/vms.js", "beginAction(key)"),
            ("static/js/checks.js", "beginAction('fix:'+action)"),
        ):
            src = self._read(rel)
            self.assertIn("beginAction", src, f"{rel} missing duplicate-click guard")
            self.assertIn(needle, src, f"{rel} missing duplicate-click guard key")

    def test_confirmations_for_destructive_actions(self):
        self.assertIn("SVC_CONFIRM", self._read("static/js/services.js"))
        vms = self._read("static/js/vms.js")
        self.assertIn("VM_CONFIRM", vms)
        for act in ("destroy", "shutdown", "reboot"):
            self.assertIn(act + ":", vms, f"no confirmation entry for VM {act}")
        # clear buttons now confirm before wiping local history
        self.assertIn("confirmDlg('Clear alert history'",
                      self._read("static/js/alerts.js").replace('"', "'"))
        self.assertIn("confirmDlg('Clear activity log'",
                      self._read("static/js/activity.js").replace('"', "'"))

    def test_modal_has_focus_trap(self):
        core = self._read("static/js/core.js")
        self.assertIn("Focus trap", core)
        self.assertIn("opener", core)

    def test_tabs_have_arrow_key_navigation(self):
        tabs = self._read("static/js/tabs.js")
        self.assertIn("ArrowDown", tabs)
        self.assertIn("ArrowUp", tabs)
        self.assertIn("Home", tabs)

    def test_js_modules_parse(self):
        """Every shipped JS module must be syntactically valid (node --check)."""
        import shutil
        if not shutil.which("node"):
            self.skipTest("node not installed")
        jsdir = os.path.join(REPO, "static", "js")
        for name in sorted(os.listdir(jsdir)):
            if not name.endswith(".js"):
                continue
            r = subprocess.run(["node", "--check", os.path.join(jsdir, name)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{name}: {r.stderr[:400]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
