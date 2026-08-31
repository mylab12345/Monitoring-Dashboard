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
  * repo-name consistency (README/install.sh/update.sh use the real
    "mylab12345/Monitoring-Dashboard", not "Montoring" or the 404-ing
    truncated "mylab12345/Monitoring")
  * frontend integrity (all inline handlers defined, spinRefresh present,
    every /api/ request goes through the authenticated fetch helpers)
  * uninstaller removes the desktop launcher
"""
import json
import os
import re
import subprocess
import sys
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

# app.py catches missing flask/psutil and leaves `app` as None (the import
# itself still succeeds). The Flask-backed tests must be skipped in BOTH cases
# so a dependency-less checkout reports clean skips instead of setUpClass
# errors / AttributeError: 'NoneType' has no attribute 'test_client'.
APP_AVAILABLE = app_mod is not None and getattr(app_mod, "app", None) is not None
SKIP_REASON = "flask/psutil not available: {}".format(
    IMPORT_ERROR or getattr(app_mod, "IMPORT_ERROR", None) or "app failed to initialise")


@unittest.skipIf(not APP_AVAILABLE, SKIP_REASON)
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
        # The app script is now served as separate static/js/*.js files rather
        # than an inline <script> block.
        self.assertIn("/static/js/core.js", html)
        self.assertIn("/static/js/bootstrap.js", html)

    def test_read_endpoints(self):
        for path in ("/api/health", "/api/version", "/api/status", "/api/systeminfo",
                     "/api/history", "/api/processes", "/api/services", "/api/disks",
                     "/api/network", "/api/ports", "/api/logs", "/api/checks",
                     "/api/troubleshooting", "/api/privileges", "/api/self-repair"):
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


@unittest.skipIf(not APP_AVAILABLE, SKIP_REASON)
class RateLimitRegression(unittest.TestCase):
    """The dashboard polls /api/status every few seconds; the old global
    '50 per hour' default throttled it (reproduced: 70 calls -> 20x 429)."""

    @classmethod
    def setUpClass(cls):
        cls.client = app_mod.app.test_client()

    def test_status_survives_polling(self):
        codes = [self.client.get("/api/status").status_code for _ in range(70)]
        self.assertNotIn(429, codes, f"/api/status got throttled: {codes.count(429)}x 429")


@unittest.skipIf(not APP_AVAILABLE, SKIP_REASON)
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

    def test_vm_config_validation(self):
        r = self.client.post("/api/vm_config", json={})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/vm_config", json={"name": ""})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/vm_config", json={"name": "$(id)"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/vm_config", json={"name": "vm1"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/vm_config", json={"name": "vm1", "vcpus": 0})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/vm_config", json={"name": "vm1", "vcpus": 300})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/vm_config", json={"name": "vm1", "ram_gb": 0})
        self.assertEqual(r.status_code, 400)

    def test_fix_unknown_action(self):
        """Unknown actions must be a hard 400 with an `error` key.

        Regression: this used to return 200 {"result": "Unknown action"}, and
        the UI only inspects `error` — so a privileged maintenance action that
        never ran was reported to the user as "Completed" and written to the
        audit trail as a success.
        """
        r = self.client.post("/api/fix", json={"action": "bogus"})
        self.assertEqual(r.status_code, 400)
        body = r.get_json()
        self.assertIn("error", body)
        self.assertNotIn("result", body)
        self.assertIn("Unknown action", body["error"])

    def test_fix_rejects_missing_action(self):
        r = self.client.post("/api/fix", json={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_fix_vacuum_journal_alias(self):
        """The Overview "Vacuum Journal" button must map to a real action.

        It posted `vacuum-journal`, which the server did not implement.
        """
        from monitor import packages as _a
        self.assertEqual(_a.FIX_ALIASES.get("vacuum-journal"), "clear-logs")
        self.assertIn("clear-logs", _a.FIX_ACTIONS)
        r = self.client.post("/api/fix", json={"action": "vacuum-journal"})
        self.assertNotEqual(r.status_code, 400)
        self.assertNotIn("Unknown action", json.dumps(r.get_json()))

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


@unittest.skipIf(not APP_AVAILABLE, SKIP_REASON)
class MaintenanceBackend(unittest.TestCase):
    """Maintenance must use the package helper and report real outcomes."""

    @classmethod
    def setUpClass(cls):
        cls.client = app_mod.app.test_client()

    def test_failed_package_helper_is_not_reported_as_success(self):
        from monitor import fixes
        with patch.object(fixes, "_pkg_manager", return_value="apt"), \
             patch.object(fixes, "run_privileged", return_value=(30, "", "read-only")):
            response = self.client.post("/api/fix", json={"action": "upgrade"})
        self.assertEqual(response.status_code, 500)
        body = response.get_json()
        self.assertEqual(body["exit_code"], 30)
        self.assertIn("read-only", body["error"])
        self.assertNotIn("result", body)

    def test_fix_broken_has_a_command_for_every_supported_manager(self):
        from monitor import packages
        for manager in ("apt", "dnf", "yum", "zypper", "pacman", "apk"):
            self.assertEqual(
                packages.FIX_COMMANDS[manager]["fix-broken"][0],
                "monitoring-package",
            )
            self.assertEqual(
                packages.FIX_COMMANDS[manager]["fix-broken"][2],
                manager,
            )

    def test_dpkg_audit_detects_half_installed_output_without_error_word(self):
        from monitor import packages
        with patch.object(packages, "which", return_value=True), \
             patch.object(packages, "run", return_value=(0, "coreutils\n  half-installed", "")):
            available, healthy, detail = packages._dpkg_audit()
        self.assertTrue(available)
        self.assertFalse(healthy)
        self.assertIn("half-installed", detail)


@unittest.skipIf(not APP_AVAILABLE, SKIP_REASON)
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

    def test_vm_config_validation(self):
        self.assertEqual(self._helper("monitoring-vm-config").returncode, 2)
        self.assertEqual(self._helper("monitoring-vm-config", "setvcpus").returncode, 2)
        self.assertEqual(self._helper("monitoring-vm-config", "setvcpus", "vm1").returncode, 2)
        self.assertEqual(self._helper("monitoring-vm-config", "bogus", "vm1", "2").returncode, 2)
        self.assertEqual(self._helper("monitoring-vm-config", "setvcpus", "$(id)", "2").returncode, 2)
        self.assertEqual(self._helper("monitoring-vm-config", "setvcpus", "vm1", "abc").returncode, 2)
        self.assertEqual(self._helper("monitoring-vm-config", "setvcpus", "vm1", "-1").returncode, 2)
        self.assertEqual(self._helper("monitoring-vm-config", "setvcpus", "vm1", "2", "--bogus").returncode, 2)

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

    def test_self_repair_validation(self):
        self.assertEqual(self._helper("monitoring-self-repair", "--apply", "extra").returncode, 2)
        self.assertEqual(self._helper("monitoring-self-repair", "--bogus").returncode, 2)
        self.assertEqual(self._helper("monitoring-self-repair").returncode, 2)

    def test_all_helpers_check(self):
        for name in ("monitoring-systemctl", "monitoring-self-repair", "monitoring-package", "monitoring-journal-vacuum",
                     "monitoring-clean-old-logs", "monitoring-vm", "monitoring-vm-config",
                     "monitoring-qemu", "monitoring-kill", "monitoring-zombie-clean",
                     "monitoring-privilege-check"):
            r = self._helper(name, "--check")
            self.assertEqual(r.returncode, 0, f"{name} --check failed: {r.stderr}")


class SelfRepairUnit(unittest.TestCase):
    """Pure unit-patching logic of the monitoring-self-repair helper.

    The helper must surgically add ReadWritePaths=/usr /etc /boot /efi to the
    installed unit without touching anything else, and its --check must answer
    with a stable JSON contract without mutating the system.
    """

    @classmethod
    def setUpClass(cls):
        import importlib.machinery
        import importlib.util
        path = os.path.join(REPO, "privileged", "monitoring-self-repair")
        loader = importlib.machinery.SourceFileLoader("monitoring_self_repair", path)
        spec = importlib.util.spec_from_loader("monitoring_self_repair", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        cls.mod = mod

    def test_has_rwp_detects_required_paths(self):
        m = self.mod
        good = "[Service]\nProtectSystem=full\nReadWritePaths=/usr /etc /boot /efi\n[Install]\nWantedBy=multi-user.target\n"
        self.assertTrue(m._unit_has_rwp(good))
        self.assertFalse(m._unit_has_rwp("[Service]\nReadWritePaths=/usr /etc\n"))
        # systemd accumulates repeated lines: the union must satisfy all paths.
        self.assertTrue(m._unit_has_rwp("[Service]\nReadWritePaths=/usr /etc\nReadWritePaths=/boot /efi\n"))
        # a commented-out directive does not count
        self.assertFalse(m._unit_has_rwp("[Service]\n# ReadWritePaths=/usr /etc /boot /efi\n"))
        # case-insensitive key, spaces around '='
        self.assertTrue(m._unit_has_rwp("[Service]\nReadWritePaths = /usr /etc /boot /efi\n"))

    def test_insert_rwp_places_directive_and_preserves_content(self):
        m = self.mod
        content = ("[Unit]\nDescription=Monitoring\n[Service]\nUser=monitoring\n"
                   "ProtectSystem=full\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n"
                   "[Install]\nWantedBy=multi-user.target\n")
        patched = m._insert_rwp(content)
        self.assertIsNotNone(patched)
        self.assertIn(m.RWP_DIRECTIVE, patched)
        self.assertTrue(m._unit_has_rwp(patched))
        # operator settings survive the patch untouched
        self.assertIn("User=monitoring", patched)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", patched)
        self.assertIn("WantedBy=multi-user.target", patched)
        # idempotent
        self.assertEqual(m._insert_rwp(patched), patched)

    def test_insert_rwp_without_service_section_refuses(self):
        self.assertIsNone(self.mod._insert_rwp("[Unit]\nDescription=x\n"))

    def test_check_returns_json_contract(self):
        r = subprocess.run(
            [sys.executable, os.path.join(REPO, "privileged", "monitoring-self-repair"), "--check"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        for key in ("systemd", "systemd_run", "unit_path", "unit_exists",
                    "unit_recognized", "unit_needs_patch", "service_active",
                    "service_stale", "namespace_read_only", "blocked",
                    "actionable", "message"):
            self.assertIn(key, data)
        self.assertIsInstance(data["namespace_read_only"], list)


@unittest.skipIf(not APP_AVAILABLE, SKIP_REASON)
class SelfRepairApi(unittest.TestCase):
    """Dashboard endpoints for the mount-namespace self-repair."""

    @classmethod
    def setUpClass(cls):
        cls.client = app_mod.app.test_client()

    def test_status_endpoint_contract(self):
        r = self.client.get("/api/self-repair")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        for key in ("helper", "helper_path", "unit_path", "app_read_only_paths",
                    "blocked", "actionable"):
            self.assertIn(key, body)

    def test_apply_requires_installed_helper(self):
        # In the test environment the helper is not installed in the privilege
        # directory, so the mutation must be refused safely, not half-run.
        # (Order-independent: the rate-limit test may already consume the POST
        # budget for this minute, so a 429 is equally acceptable.)
        r = self.client.post("/api/self-repair", json={})
        self.assertIn(r.status_code, (503, 429))
        if r.status_code == 503:
            self.assertIn("error", r.get_json())

    def test_apply_rate_limited(self):
        codes = [self.client.post("/api/self-repair", json={}).status_code
                 for _ in range(7)]
        self.assertGreaterEqual(codes.count(429), 2,
                                "api/self-repair POST should be rate-limited")


class RepoConsistency(unittest.TestCase):
    """Docs/scripts must reference the real repo name — the GitHub repository
    is `mylab12345/Monitoring-Dashboard`. Both the `Montoring` typo AND the
    truncated `mylab12345/Monitoring` 404 the documented one-liners and the
    updater's archive download (URLs are case-sensitive and must match the
    real repository name exactly)."""

    # "mylab12345/Monitoring" not immediately followed by "-Dashboard" — i.e.
    # the truncated repo name. The negative lookahead keeps the correct
    # "...Monitoring-Dashboard" reference from matching.
    WRONG_REPO = re.compile(r"mylab12345/Monitoring(?!-Dashboard)")

    def test_repo_name_in_update_sh(self):
        with open(os.path.join(REPO, "update.sh")) as fh:
            txt = fh.read()
        self.assertIn("mylab12345/Monitoring-Dashboard", txt)
        self.assertNotIn("Montoring", txt)
        self.assertNotRegex(txt, self.WRONG_REPO,
                           "update.sh references the wrong/truncated repo name (404s)")

    def test_repo_name_in_readme(self):
        with open(os.path.join(REPO, "README.md")) as fh:
            txt = fh.read()
        self.assertIn("raw.githubusercontent.com/mylab12345/Monitoring-Dashboard", txt)
        self.assertNotIn("Montoring", txt)
        self.assertNotRegex(txt, self.WRONG_REPO,
                           "README references the wrong/truncated repo name (404s)")

    def test_install_sh_repo_name(self):
        with open(os.path.join(REPO, "install.sh")) as fh:
            txt = fh.read()
        self.assertIn('REPO="${REPO:-mylab12345/Monitoring-Dashboard}"', txt)
        self.assertNotIn("Montoring", txt)
        self.assertNotRegex(txt, self.WRONG_REPO,
                           "install.sh references the wrong/truncated repo name (404s)")

    def test_monitoring_service_repo(self):
        with open(os.path.join(REPO, "monitoring.service")) as fh:
            txt = fh.read()
        self.assertIn("https://github.com/mylab12345/Monitoring-Dashboard", txt)
        self.assertNotIn("Montoring", txt)
        self.assertNotRegex(txt, self.WRONG_REPO,
                           "monitoring.service references the wrong/truncated repo name")

    def test_uninstaller_removes_launcher(self):
        with open(os.path.join(REPO, "uninstall.sh")) as fh:
            txt = fh.read()
        self.assertIn("monitoring-app", txt)

    def test_service_hardening_and_nonroot(self):
        with open(os.path.join(REPO, "monitoring.service")) as fh:
            txt = fh.read()
        self.assertIn("User=monitoring", txt)
        self.assertIn("ProtectSystem=full", txt)
        self.assertIn("ReadWritePaths=/usr /etc /boot /efi", txt)
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


class UpgradeSafety(unittest.TestCase):
    """A `git pull` / update from the old monolith must not silently break the
    installed app.

    The new `app.py` is a thin shim that imports the `monitor/` package and the
    frontend lives in `static/js/`. If the installer/updater does not ship those
    directories, the dashboard crashes on startup. These checks pin the copy
    steps and the defensive-import guard so that regression is caught."""

    def _text(self, name):
        with open(os.path.join(REPO, name)) as fh:
            return fh.read()

    def test_installer_copies_monitor_package(self):
        txt = self._text("install.sh")
        self.assertIn('cp -rf "$SRC/monitor/."', txt)
        self.assertIn("monitor/__init__.py", txt)

    def test_updater_copies_monitor_package(self):
        txt = self._text("update.sh")
        self.assertIn('cp -rf "$SRC/monitor/."', txt)
        self.assertIn("monitor/__init__.py", txt)

    def test_installer_and_updater_copy_static_js_recursively(self):
        for name in ("install.sh", "update.sh"):
            self.assertIn('cp -rf "$SRC/static/."', self._text(name))

    def test_updater_backs_up_monitor_for_rollback(self):
        self.assertIn('"$TARGET/monitor"', self._text("update.sh"))

    def test_installed_updater_downloads_sources_before_installing_helpers(self):
        txt = self._text("update.sh")
        self.assertLess(txt.index('if [ "$SRC" = "$TARGET" ]'),
                        txt.index("install_privileges\nmkdir"))

    def test_app_entrypoint_is_defensive(self):
        """If monitor/ is missing (e.g. an old update.sh ran), app.py must print
        a clear, actionable message and exit, not raise a cryptic ImportError."""
        txt = self._text("app.py")
        self.assertIn("IMPORT_ERROR", txt)
        self.assertIn("could not load the 'monitor/'", txt)
        self.assertIn("create_app() if create_app is not None else None", txt)

    def test_app_package_has_graceful_loader(self):
        txt = self._text(os.path.join("monitor", "__init__.py"))
        self.assertIn("LOAD_ERRORS", txt)
        self.assertIn("register_blueprint", txt)


class FrontendIntegrity(unittest.TestCase):
    """Static checks on the frontend (templates/index.html + static/js/*.js).

    A missing helper here kills the whole dashboard (a previous release had
    no `spinRefresh` definition: every fetchJSON/postJSON call threw a
    ReferenceError, so the UI never rendered any data), so every check below
    is a regression guard, not a style preference.

    The app script is now split across static/js/*.js (loaded in the order the
    index.html <script src> tags list them), so a syntax error in one file no
    longer aborts every other file — each is a separate parse unit."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(REPO, "templates", "index.html")) as fh:
            cls.html = fh.read()
        # Collect the JS files in the exact order the page loads them.
        names = re.findall(r"url_for\('static',\s*filename='js/([^']+)'\)", cls.html)
        cls.js_files = names
        parts = []
        for name in names:
            with open(os.path.join(REPO, "static", "js", name)) as fh:
                parts.append(fh.read())
        cls.js = "\n".join(parts)

    def test_each_script_file_parses(self):
        """Every individual JS file must be syntactically valid on its own.

        Regression: a duplicated tooltip loop left an orphaned `continue` and a
        stray brace in chartBoxEvents(). A syntax error aborts the whole script
        at parse time, so *every* tab rendered as empty skeletons with no data.
        Because the files are separate parse units now, one broken file must
        not invalidate the others — but each must still parse cleanly.
        """
        node = None
        for cand in ("node", "nodejs"):
            try:
                if subprocess.run([cand, "--version"], capture_output=True,
                                  timeout=15).returncode == 0:
                    node = cand
                    break
            except (OSError, subprocess.SubprocessError):
                continue
        if node is None:
            self.skipTest("node not available to parse-check the frontend")
        self.assertTrue(self.js_files, "no static/js files discovered")
        for name in self.js_files:
            path = os.path.join(REPO, "static", "js", name)
            r = subprocess.run([node, "--check", path],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0,
                             f"static/js/{name} has a syntax error:\n{r.stderr}")

    def test_spinrefresh_is_defined(self):
        self.assertRegex(self.js, r"function\s+spinRefresh\s*\(")
        # every call site must come after the definition
        def_i = self.js.find("function spinRefresh(")
        self.assertGreater(def_i, -1)
        for m in re.finditer(r"(?<!function\s)spinRefresh\s*\(", self.js):
            self.assertGreater(m.start(), def_i, "spinRefresh( called before definition")

    def test_spinrefresh_toggles_refresh_button(self):
        self.assertRegex(self.js, r"classList\.toggle\(['\"]spinning['\"],\s*busy>0\)")
        self.assertRegex(self.html, r'id="refreshBtn"')

    def test_all_inline_handlers_are_defined(self):
        handlers = re.findall(r'on(?:click|change|input|submit|keydown|keyup)\s*=\s*"([^"]*)"', self.html)
        self.assertTrue(handlers)
        defined = set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(", self.js))
        defined |= set(re.findall(
            r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|function)", self.js))
        called = set()
        for h in handlers:
            for m in re.finditer(r"([A-Za-z_$][\w$]*)\s*\(", h):
                # skip DOM methods invoked as obj.method(...) — e.g. event.preventDefault()
                pre = h[max(0, m.start() - 8):m.start()]
                if re.search(r"[\w$]\.\w*$", pre):
                    continue
                called.add(m.group(1))
        missing = sorted(c for c in called if c not in defined and c != "if")
        self.assertEqual(missing, [], f"inline handlers reference undefined functions: {missing}")

    def test_api_requests_use_auth_helpers(self):
        """No raw `fetch('/api/...')` outside fetchJSON/postJSON/apiFetch —
        raw fetches bypass the Bearer token and break token-protected installs."""
        # strip the wrapper bodies so their internal fetch calls don't count
        wrappers = r"async function (?:fetchJSON|postJSON|apiFetch)\b.*?(?=\nfunction |\nconst |\n// )"
        body = re.sub(wrappers, "", self.js, flags=re.S)
        bad = re.findall(r"\bfetch\s*\(\s*['\"`]/api/", body)
        self.assertEqual(bad, [], f"raw /api/ fetch without auth: {bad}")

    def test_no_missing_element_ids(self):
        # Static ids from the HTML plus ids the JS creates dynamically (e.g.
        # the command palette builds `id="palInput"`/`id="palList"` at runtime).
        ids = set(re.findall(r'id="([^"]+)"', self.html))
        ids |= set(re.findall(r'id="([^"]+)"', self.js))
        used = set(re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)", self.js))
        used |= set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", self.js))
        used |= set(re.findall(r"\$\$\(\s*'#([A-Za-z0-9_-]+)", self.js))
        missing = sorted(u for u in used if u not in ids)
        self.assertEqual(missing, [], f"JS references missing element ids: {missing}")


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
        modules = [os.path.join("monitor", p) for p in
                   os.listdir(os.path.join(REPO, "monitor")) if p.endswith(".py")]
        for f in ("app.py", "monitoring-app") + tuple(helpers) + tuple(modules):
            r = subprocess.run([sys.executable, "-m", "py_compile", os.path.join(REPO, f)],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, f"{f}: {r.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
