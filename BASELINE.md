# Monitoring — Architecture Map & Audit Baseline

**Version:** 2.4.1 · **Date:** 2026-08-30 · **Repo:** mylab12345/Monitoring

This document records the complete architecture/dependency map and the
baseline test results captured **before** any modification, plus the results
**after** the audit-repair, so regressions are measurable.

---

## 1. Architecture map

```
Browser (templates/index.html, static/dashboard.css)
   │  fetch /api/* (JSON) — optional Bearer token (MONITORING_TOKEN)
   ▼
app.py — Flask application (monolithic backend, ~2,100 lines)
   ├── /             SPA shell (no data, always public)
   ├── /api/health, /api/version, /api/status, /api/systeminfo, /api/history
   │                read-only metrics (sampler thread, 2 s ring buffer, 60 min)
   ├── /api/checks, /api/troubleshooting, /api/troubleshooting/verify
   │                health/diagnostics (cached 10 s)
   ├── /api/processes, /api/process/kill
   ├── /api/services, /api/service/action
   ├── /api/logs (journalctl), /api/ports (ss/netstat), /api/disks, /api/network
   ├── /api/vms, /api/vm/action, /api/vm_info/<name>, /api/vm_resize
   ├── /api/fix, /api/troubleshooting/fix-all   (package/cleanup actions)
   ├── /api/privileges, /api/open_app
   └── subprocess layer: argv-only run()/run_privileged() — NEVER shell=True
            │
            ▼  passwordless sudo, absolute-path NOPASSWD rules
   /usr/local/lib/monitoring/monitoring-{systemctl,package,journal-vacuum,
   clean-old-logs,vm,qemu,kill,zombie-clean,privilege-check}
   (8 privileged helpers, each validates its own arguments, no shell)
```

**Deployment:** `install.sh` (universal, Debian→Alpine) → dedicated non-login
`monitoring` account → venv in `/opt/monitoring` → systemd unit (or OpenRC /
pidfile fallback) → `/etc/monitoring.env` config → `monitoring` CLI +
`monitoring-app` desktop launcher. `update.sh` backs up (last 5), syncs files
+ deps, restarts, health-checks. `uninstall.sh [--purge]` removes everything.

**Runtime data:** in-memory only (history ring buffer, response cache,
flask-limiter memory storage); browser localStorage holds settings, alert and
activity history. No database, no cloud, no telemetry.

## 2. Dependency map (requirements.txt)

| Package | Constraint | Used by | Notes |
|---|---|---|---|
| flask | >=3.0.3,<3.2.0 | app.py | web framework |
| psutil | >=5.9.8,<8.0.0 | app.py | metrics, processes |
| markupsafe | >=2.1.5,<4.0.0 | app.py | escaping |
| flask-limiter | >=3.8.0,<5.0.0 | app.py | rate limiting |
| python-dotenv | >=1.0.1,<2.0.0 | app.py | env file |
| werkzeug | >=3.1.6,<3.2.0 | flask | pinned ≥3.1.6 (CVEs) |
| waitress | >=3.0.0,<4.0.0 | app.py main | production WSGI server |

Removed as unused: aiohttp, requests, urllib3, authlib, tornado (the committed
`pip_audit_report.json` audited those — a *different* environment — and claimed
"269 vulnerabilities"; the real audit below is clean).

## 3. Baseline BEFORE changes (2026-08-30T14:06Z)

| Suite | Result |
|---|---|
| tests/security_migration.sh | **PASS=42 FAIL=0 SKIP=1** |
| py_compile app.py + helpers + monitoring-app | OK |
| bash -n install/update/uninstall/monitoring/openrc/tests | OK |
| Live API smoke (in-process client) | all endpoints 200 |
| **70× GET /api/status** | **50× 200 then 20× 429** ← rate limiter bug reproduced |
| Mutation validation (kill pid 1, vm_resize injection) | correctly 400 |
| /api/fix bogus action | 200 "Unknown action" |

**Known issues fixed in this audit** (each identified → verified → fixed → regression-tested):

1. **CRITICAL — rate limiter throttles the dashboard itself** (`app.py` default
   limits `"200 per day", "50 per hour"`). The UI polls /api/status every 3–5 s;
   verified 429s after ~50 requests → dashboard metrics die. Fix: generous
   per-route default (`600 per minute`) on read endpoints, strict per-route
   limits (`5–30/min`) on privileged/mutating endpoints only.
2. **CRITICAL — no authentication on a full-host-control API bound to 0.0.0.0.**
   Fix: optional token auth (`MONITORING_TOKEN` / `--token`, constant-time
   compare, `WWW-Authenticate`), `--bind` option, firewall opened only when
   exposed + loud warning without a token, `Cache-Control: no-store` on
   authenticated API responses, UI token prompt (first 401).
3. **HIGH — repo-name typo** (`Montoring` instead of `Monitoring`) in
   README.md + update.sh broke the documented one-liner install/update (raw
   URLs are case-sensitive → 404). Fixed by renaming the GitHub repository to
   `mylab12345/Monitoring` and updating every reference; covered by tests.
4. **HIGH — Flask dev server in production.** Fix: waitress (threaded WSGI)
   when installed, Flask fallback; `MONITORING_SERVER` override.
5. **HIGH — systemd hardening broke privileged helpers**: ProtectKernelTunables/
   Modules/ControlGroups imply `NoNewPrivileges=yes` → sudo fails
   ("no new privileges flag is set"). Verified live in a systemd unit; fixed to
   the safe subset (ProtectSystem=full, ProtectHome=true, PrivateTmp=true).
6. **MEDIUM — missing input validation**: `/api/logs` prio unbounded,
   `/api/vm_resize` silently clamped out-of-range sizes, `api_vm_action`
   checked `which("libvirt")` (a Python module, not a binary),
   `validate_systemctl_action` accepted "status" (helper rejects → 500).
   All fixed; boundary tests added.
7. **MEDIUM — unauthenticated mutation endpoints without rate limits**
   (/api/fix, /api/troubleshooting/fix-all, verify, /api/open_app). Now
   rate-limited and audited (structured `action=...` log lines).
8. **MEDIUM — installer robustness**: `visudo` not on PATH (/usr/sbin) aborts
   install; `systemctl` without an operational bus aborts install/update;
   reinstall/update silently dropped operator security settings; firewall
   opened unconditionally; no apt-get update. All fixed with graceful
   fallbacks (CLI-managed mode) and env preservation.
9. **MEDIUM — uninstaller left `/usr/local/bin/monitoring-app` behind.** Fixed.
10. **LOW — repo hygiene**: committed `__pycache__` bytecode + 456 KB stale
    `pip_audit_report.json` removed from git; `.gitignore` extended.
11. **LOW — unbounded `_updatable_packages` stampede** (checks + diagnose +
    verify can race apt/dnf). Fixed with double-checked lock.
12. **LOW — no security headers / JSON error handlers.** Added
    nosniff/SAMEORIGIN/no-referrer + JSON 404/405/500.
13. **LOW — deprecated dependency pins** (loose `>=` only) and unused heavy
    deps. Trimmed and upper-bounded; `pip-audit -r requirements.txt` → **0
    vulnerabilities**.
14. **LOW — `monitoring-app` health probe failed under token auth.** Fixed.
15. **LOW — UI postJSON could hang forever** on long privileged actions. Fixed
    with a 320 s timeout + 401 retry.

## 4. Results AFTER changes

| Suite | Result |
|---|---|
| tests/security_migration.sh (original project self-test) | **PASS=42 FAIL=0 SKIP=1** (unchanged) |
| tests/test_app.py (new regression suite, 37 tests) | **37/37 OK** |
| Live HTTP E2E (waitress, no token): 13 endpoints | all 200; 0/70 throttled; headers present |
| Live HTTP E2E (waitress, token): 401/401/200/200/200 | correct; no-store; UI shell public |
| Full installer E2E (sudo, --token, --bind 127.0.0.1) | install ✓, service ✓, health ✓, token ✓ |
| Privileged helper pipeline through service | all 8 helpers "ok"; real kill + service restart ✓; audit log ✓ |
| update.sh E2E (reinstall + restart + health) | ✓ (backup kept, env preserved) |
| pip-audit -r requirements.txt (fresh venv) | **No known vulnerabilities found** |
| uninstall.sh --purge E2E | see run log |

## 5. How to reproduce

```bash
bash tests/run_all.sh              # security suite + 37 regression tests + audit
sudo bash install.sh --port 8051 --bind 127.0.0.1 --token test123
monitoring status && monitoring version
curl -H "Authorization: Bearer test123" http://127.0.0.1:8051/api/version
sudo ./update.sh                   # from a checkout — upgrade path
sudo /opt/monitoring/uninstall.sh --purge
```
