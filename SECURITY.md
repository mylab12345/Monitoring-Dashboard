# Security Policy

## Overview

Monitoring Dashboard is a **single-host, local-first administration console** that can control the entire machine (kill processes, manage systemd services, VMs, package upgrades, log vacuum). Treat it as a root shell with a GUI.

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 2.5.x   | :white_check_mark: |
| < 2.5   | :x:                |

## Reporting a Vulnerability

Please report security issues privately:

- Open a **private security advisory** on GitHub, or
- Email the maintainers (see GitHub profile)

Do **not** open a public issue for security vulnerabilities.

We aim to acknowledge within 48h and provide a fix or mitigation within 7 days.

## Security Model

### Service Account

- Runs as dedicated non-login system account `monitoring`
- No shell, no home, minimal base privileges
- Supplementary groups (`systemd-journal`, `adm`, `libvirt`, `docker`, `kvm`) provide read-only *data* access (journal, logs, libvirt, `/proc`, `/sys`) without needing sudo

### Privilege Escalation

- **Profile (operator-selected): FULL ACCESS.** `/etc/sudoers.d/monitoring` grants `monitoring ALL=(ALL:ALL) NOPASSWD: ALL` — unrestricted passwordless root. The account can create, modify and delete **any** resource on the host: services, packages, files, VMs, kernel state. Nothing is restricted at the sudo layer.
- **Risk you accept with this profile:** any code execution as `monitoring` — a compromised dashboard, a stolen bearer token, an injected request, a rogue cron job — is equivalent to full root on the host. Mitigate by keeping the dashboard bound to `127.0.0.1` (default) or, on a LAN, **always** token-protected. Never expose it to untrusted networks.
- The application itself still routes every operation through the helper scripts in `/usr/local/lib/monitoring/` (argv lists only — no `shell=True` anywhere — with per-helper argument validation: PID ranges, unit-name regex, VM-name regex, disk ownership via `virsh domblklist`). This is an application-level convention that keeps input validation and the UI's permission reporting intact; it is **not** a restriction on what the account may do.
- **Least-privilege alternative:** the previous per-helper whitelist (19 absolute-path `NOPASSWD` entries, no `sudo ALL`) is preserved as a commented block inside `sudoers/monitoring`. Restore it by commenting out the full-access line, uncommenting the block, running `visudo -cf /etc/sudoers.d/monitoring` and restarting the service. Recommended for multi-user or exposed deployments.
- Sudoers file validated with `visudo -cf` during install; the installer logs and verifies the active profile

### Authentication

- Optional bearer token (`MONITORING_TOKEN` / `--token auto`)
- When set, every `/api/*` request requires `Authorization: Bearer <token>`
- UI prompts for token on first 401
- `Cache-Control: no-store` on authenticated API responses
- Constant-time token comparison (`hmac.compare_digest`)

### Network Binding

- **Default since v2.5.0**: `127.0.0.1` (localhost only)
- Use `--bind 0.0.0.0` to expose on LAN — **requires token**
- Installer only opens firewall (ufw/firewalld) when binding non-loopback **and** token is set
- No TLS built-in — use reverse proxy (nginx/caddy) with TLS for remote access
- `ProxyFix` enabled for correct client IP behind proxy

### Input Validation

- All subprocesses are argv lists (`shell=False`)
- `run()` rejects string commands
- Strict regex validation for service names, VM names, disk paths
- PID bounds (2..1_000_000, never 1, never self)
- Log grep sanitized, size-bounded
- VM resize validates size 1..10000 GB and disk belongs to domain

### Frontend Security

- No inline `onclick` with user-controlled data — all actions bound via `data-*` attributes and `addEventListener`
- All dynamic content escaped via `esc()` (HTML entity encoding)
- Global error handler surfaces unexpected failures as toast, not silent freeze

## Secure Deployment Checklist

1. **Install with token**: `sudo bash install.sh --token auto --bind 127.0.0.1`
2. **For LAN access**: `sudo bash install.sh --bind 0.0.0.0 --token <strong-random>`
3. **Behind reverse proxy**: Configure TLS, forward `X-Forwarded-For`, keep token enabled
4. **Firewall**: Keep closed unless needed; installer handles ufw/firewalld only when token-protected
5. **Check privileges**: `monitoring check-privileges` or `GET /api/privileges` — confirms the active sudo profile (full access vs. helper whitelist)
6. **Keep updated**: `sudo ./update.sh` after any code change

## Known Limitations

- Rate limiting uses `memory://` storage (per-process, reset on restart) — suitable for single-host, not for multi-tenant
- No built-in TLS — requires reverse proxy for encrypted transport
- Metrics history is in-memory only (60 min ring buffer) — lost on restart
- Alerts and activity log are browser localStorage only

## Audit

- `tests/security_migration.sh` — 90 checks for injection, sudo profile, shell, permissions
- `tests/test_app.py` — 121 regression tests including input validation, rate limiting, token auth, sudo-profile detection
- Run: `bash tests/run_all.sh`
