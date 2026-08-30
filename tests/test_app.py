#!/usr/bin/env python3
"""
Monitoring regression tests (stdlib unittest only — no extra dependencies).

Run:  python3 -m unittest discover -s tests -v
  or: bash tests/run_all.sh

Covers every important bug fixed during the audit:
  * rate limiter no longer throttles the dashboard's own polling (was 429)
  * optional token authentication (401 without, 200 with)
  * privileged endpoints are rate-limited and validated
  * command-injection / input-validation rejections
  * security headers + JSON error handlers
  * privileged helper argument validation
  * repo-name consistency (README/update.sh use "Montoring")
  * uninstaller removes the desktop launcher
"""
import json
import os
import re
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("MONITORING_HOME", REPO)

try:
    import app as app_mod
except Exception as exc:  # pragma: no cover
    app_mod = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


@unittest.skipIf(app_mod is None, f"flask/psutil not available: {IMPORT_ERROR}")
class ApiBaseline(unittest.TestCase):
    """All read endpoints must answer 200 (the dashboard depends on them)."""

    @classmethod
    def setUpClass(cls):
        cls.client = app_mod.app.test_client()

    def _get(self, path):
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200, f"{path} -> {r.status_code} {r.get_data()[:200]!r}")
        return r

    def test_index_page(self):
        html = self._get("/").get_data(as_text=True)
        self.assertIn("Monitoring", html)
        self.assertIn("/api/status", html)

    def test_read_endpoints(self):
        for path in ("/api/health", "/api/version", "/api/status", "/api/systeminfo",
                     "/api/history", "/api/processes", "/api/services", "/api/disks",
                     "/api/network", "/api/ports", "/api/logs", "/api/checks",
                     "/api/troubleshooting", "/api/privileges"):
            r = self._get(path)
            json.loads(r.get_data())

    def test_404_json(self):
        r = self.client.get("/api/definitely-not-a-route")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()["error"], "not found")

    def test_405_json(self):
        r = self.client.post("/api/status")
        self.assertEqual(r.status_code, 405)
        self.assertEqual(r.get_json()["error"], "method not allowed")

    def test_security_headers(self):
        r = self.client.get("/api/version")
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(r.headers.get("Referrer-Policy"), "no-referrer")


@unittest.skipIf(app_mod is None, f"flask/psutil not available: {IMPORT_ERROR}")
class RateLimitRegression(unittest.TestCase):
    """The dashboard polls /api/status every few seconds; the old global
    '50 per hour' default throttled it (reproduced: 70 calls -> 20x 429)."""

    @classmethod
    def setUpClass(cls):
        cls.client = app_mod.app.test_client()

    def test_status_survives_polling(self):
        codes = [self.client.get("/api/status").status_code for _ in range(70)]
        self.assertNotIn(429, codes, f"/api/status got throttled: {codes.count(429)}x 429")


@unittest.skipIf(app_mod is None, f"flask/psutil not available: {IMPORT_ERROR}")
class InputValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app_mod.app.test_client()

    def test_kill_rejects_init(self):
        r = self.client.post("/api/process/kill", json={"pid": 1})
        self.assertEqual(r.status_code, 400)

    def test_kill_rejects_garbage(self):
        for pid in ("abc", -5, 0, 2000001, None):
            r = self.client.post("/api/process/kill", json={"pid": pid})
            self.assertEqual(r.status_code, 400, f"pid={pid!r}")

    def test_service_action_validation(self):
        r = self.client.post("/api/service/action", json={"name": "x;rm -rf /", "action": "restart"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/service/action", json={"name": "sshd.service", "action": "pwn"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/service/action", json={"name": "-oProxyCommand=x", "action": "start"})
        self.assertEqual(r.status_code, 400)

    def test_vm_action_validation(self):
        r = self.client.post("/api/vm/action", json={"name": "$(touch /tmp/x)", "action": "start"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/vm/action", json={"name": "vm1", "action": "explode"})
        self.assertEqual(r.status_code, 400)

    def test_vm_resize_validation(self):
        base = {"name": "x;rm -rf /", "disk_path": "/etc/passwd", "new_size_gb": 5}
        r = self.client.post("/api/vm_resize", json=base)
        self.assertEqual(r.status_code, 400)
        good = {"name": "vm1", "disk_path": "/var/lib/libvirt/images/vm1.qcow2"}
        for size in (0, -3, 10001, "abc"):
            r = self.client.post("/api/vm_resize", json={**good, "new_size_gb": size})
            self.assertEqual(r.status_code, 400, f"size={size!r}")

    def test_fix_unknown_action(self):
        r = self.client.post("/api/fix", json={"action": "bogus"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Unknown action", r.get_json()["result"])

    def test_fix_all_empty_scan(self):
        r = self.client.post("/api/troubleshooting/fix-all", json={})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertIn("skipped", body)

    def test_verify_unknown_issue(self):
        r = self.client.post("/api/troubleshooting/verify", json={"issue_ids": ["does-not-exist"]})
        self.assertEqual(r.status_code, 200)
        self.assertIn("does-not-exist", r.get_json()["results"])

    def test_logs_params_bounded(self):
        r = self.client.get("/api/logs?lines=99999&prio=999&grep=../../etc/passwd")
        self.assertEqual(r.status_code, 200)


@unittest.skipIf(app_mod is None, f"flask/psutil not available: {IMPORT_ERROR}")
class TokenAuth(unittest.TestCase):
    """MONITORING_TOKEN gates every /api route (tested in a fresh interpreter
    because the token is read at import time)."""

    def _run(self, env_extra):
        code = (
            "import app; c = app.app.test_client();\n"
            "print('noauth', c.get('/api/status').status_code);\n"
            "print('withauth', c.get('/api/status', headers={'Authorization':'Bearer secret-token'}).status_code);\n"
            "print('wrong', c.get('/api/status', headers={'Authorization':'Bearer nope'}).status_code);\n"
            "print('header', c.get('/api/status', headers={'X-Monitoring-Token':'secret-token'}).status_code);\n"
            "print('page', c.get('/').status_code);\n"
        )
        env = dict(os.environ, MONITORING_HOME=REPO, **env_extra)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, timeout=60, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = dict(l.split() for l in out.stdout.splitlines())
        return lines

    def test_token_gates_api(self):
        lines = self._run({"MONITORING_TOKEN": "secret-token"})
        self.assertEqual(lines["noauth"], "401")
        self.assertEqual(lines["wrong"], "401")
        self.assertEqual(lines["withauth"], "200")
        self.assertEqual(lines["header"], "200")
        self.assertEqual(lines["page"], "200")  # UI shell stays loadable

    def test_no_token_no_auth(self):
        lines = self._run({})
        self.assertEqual(lines["noauth"], "200")

    def test_mutation_rate_limits(self):
        """Privileged endpoints keep strict per-route limits (fresh process)."""
        code = (
            "import app; c = app.app.test_client();\n"
            "codes = [c.post('/api/fix', json={'action':'bogus'}).status_code for _ in range(12)];\n"
            "print(codes.count(429), 'of', len(codes), 'throttled')\n"
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, timeout=60,
                             env=dict(os.environ, MONITORING_HOME=REPO))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("of 12 throttled", out.stdout)
        n = int(out.stdout.split()[0])
        self.assertGreaterEqual(n, 2, "api/fix should be rate-limited")


class HelperValidation(unittest.TestCase):
    """Each privileged helper must refuse bad input before touching anything."""

    def _helper(self, name, *args):
        path = os.path.join(REPO, "privileged", name)
        return subprocess.run([sys.executable, path] + list(args),
                              capture_output=True, text=True, timeout=30)

    def test_kill_bounds(self):
        self.assertEqual(self._helper("monitoring-kill", "1").returncode, 2)
        self.assertEqual(self._helper("monitoring-kill", "1", "--force").returncode, 2)
        self.assertEqual(self._helper("monitoring-kill", "abc").returncode, 2)
        self.assertEqual(self._helper("monitoring-kill", "5", "6").returncode, 2)

    def test_systemctl_validation(self):
        self.assertEqual(self._helper("monitoring-systemctl", "restart", "-oProxy=x").returncode, 2)
        self.assertEqual(self._helper("monitoring-systemctl", "hack", "sshd").returncode, 2)
        self.assertEqual(self._helper("monitoring-systemctl", "restart").returncode, 2)

    def test_vm_validation(self):
        self.assertEqual(self._helper("monitoring-vm", "start", "$(id)").returncode, 2)
        self.assertEqual(self._helper("monitoring-vm", "fly", "vm1").returncode, 2)

    def test_qemu_validation(self):
        # disk not attached to domain -> helper refuses without touching qemu
        r = self._helper("monitoring-qemu", "resize", "vm1", "/etc/passwd", "40")
        self.assertEqual(r.returncode, 2)
        r = self._helper("monitoring-qemu", "resize", "vm1", "/var/lib/libvirt/images/vm1.qcow2", "0")
        self.assertEqual(r.returncode, 2)

    def test_package_validation(self):
        self.assertEqual(self._helper("monitoring-package", "--manager", "evil", "--action", "update").returncode, 2)
        self.assertEqual(self._helper("monitoring-package", "--manager", "apt", "--action", "explode").returncode, 2)
        self.assertEqual(self._helper("monitoring-package", "apt", "update").returncode, 2)

    def test_clean_logs_range(self):
        self.assertEqual(self._helper("monitoring-clean-old-logs", "--min-age-days", "-5").returncode, 2)
        self.assertEqual(self._helper("monitoring-clean-old-logs", "--min-age-days", "99999").returncode, 2)
        self.assertEqual(self._helper("monitoring-clean-old-logs", "--bogus").returncode, 2)

    def test_journal_vacuum_no_args(self):
        self.assertEqual(self._helper("monitoring-journal-vacuum", "extra").returncode, 2)

    def test_zombie_clean_no_args(self):
        self.assertEqual(self._helper("monitoring-zombie-clean", "extra").returncode, 2)

    def test_all_helpers_check(self):
        for name in ("monitoring-systemctl", "monitoring-package", "monitoring-journal-vacuum",
                     "monitoring-clean-old-logs", "monitoring-vm", "monitoring-qemu",
                     "monitoring-kill", "monitoring-zombie-clean",
                     "monitoring-privilege-check"):
            r = self._helper(name, "--check")
            self.assertEqual(r.returncode, 0, f"{name} --check failed: {r.stderr}")


class RepoConsistency(unittest.TestCase):
    """Docs/scripts must reference the real repo name (raw URLs are
    case-sensitive; 'Monitoring' returns 404 and breaks the one-liners)."""

    def test_repo_name_in_update_sh(self):
        with open(os.path.join(REPO, "update.sh")) as fh:
            txt = fh.read()
        self.assertIn("Montoring", txt)
        self.assertNotIn("mylab12345/Monitoring", txt)

    def test_repo_name_in_readme(self):
        with open(os.path.join(REPO, "README.md")) as fh:
            txt = fh.read()
        self.assertIn("raw.githubusercontent.com/mylab12345/Montoring", txt)
        self.assertNotIn("raw.githubusercontent.com/mylab12345/Monitoring", txt)

    def test_install_sh_repo_name(self):
        with open(os.path.join(REPO, "install.sh")) as fh:
            txt = fh.read()
        self.assertIn("REPO=\"${REPO:-mylab12345/Montoring}\"", txt)

    def test_uninstaller_removes_launcher(self):
        with open(os.path.join(REPO, "uninstall.sh")) as fh:
            txt = fh.read()
        self.assertIn("monitoring-app", txt)

    def test_service_hardening_and_nonroot(self):
        with open(os.path.join(REPO, "monitoring.service")) as fh:
            txt = fh.read()
        self.assertIn("User=monitoring", txt)
        self.assertIn("ProtectSystem=full", txt)
        self.assertNotIn("NoNewPrivileges=true", txt)  # would break sudo helpers
        self.assertNotIn("RestrictSUIDSGID=true", txt)

    def test_requirements_audited_pins(self):
        with open(os.path.join(REPO, "requirements.txt")) as fh:
            txt = fh.read()
        for pkg in ("flask", "psutil", "markupsafe", "flask-limiter",
                    "python-dotenv", "werkzeug", "waitress"):
            self.assertIn(pkg, txt)
        for gone in ("aiohttp", "requests", "tornado", "authlib", "urllib3"):
            self.assertNotIn(gone, txt)

    def test_version_bumped(self):
        with open(os.path.join(REPO, "VERSION")) as fh:
            ver = fh.read().strip()
        self.assertRegex(ver, r"^\d+\.\d+\.\d+$")
        self.assertGreaterEqual(tuple(int(x) for x in ver.split(".")), (2, 4, 0))

    def test_no_bytecode_or_stale_audit_in_git(self):
        out = subprocess.run(["git", "-C", REPO, "ls-files"], capture_output=True,
                             text=True, timeout=30).stdout
        self.assertNotIn("__pycache__", out)
        self.assertNotIn("pip_audit_report.json", out)


class ScriptSyntax(unittest.TestCase):
    def test_bash_syntax(self):
        for f in ("install.sh", "update.sh", "uninstall.sh", "monitoring",
                  "tests/security_migration.sh", "openrc/monitoring"):
            r = subprocess.run(["bash", "-n", os.path.join(REPO, f)],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, f"{f}: {r.stderr}")

    def test_python_syntax(self):
        helpers = [os.path.join("privileged", p) for p in
                   os.listdir(os.path.join(REPO, "privileged")) if p.endswith(".py")]
        for f in ("app.py", "monitoring-app") + tuple(helpers):
            r = subprocess.run([sys.executable, "-m", "py_compile", os.path.join(REPO, f)],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, f"{f}: {r.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
