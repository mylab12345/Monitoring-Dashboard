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
- No shell, no home, minimal privileges
- Supplementary groups (`systemd-journal`, `adm`, `libvirt`, `docker`, `kvm`) provide read-only access where possible instead of sudo

### Privilege Escalation

- Only **whitelisted helper scripts** in `/usr/local/lib/monitoring/` may be executed via passwordless sudo
- Each helper validates its own arguments (PID range, unit-name regex, VM-name regex, disk-path ownership via `virsh domblklist`)
- No `sudo ALL`, no `shell=True` anywhere
- Sudoers file validated with `visudo -cf` during install

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
5. **Check privileges**: `monitoring check-privileges` or `GET /api/privileges`
6. **Keep updated**: `sudo ./update.sh` after any code change

## Known Limitations

- Rate limiting uses `memory://` storage (per-process, reset on restart) — suitable for single-host, not for multi-tenant
- No built-in TLS — requires reverse proxy for encrypted transport
- Metrics history is in-memory only (60 min ring buffer) — lost on restart
- Alerts and activity log are browser localStorage only

## Audit

- `tests/security_migration.sh` — 42 checks for injection, sudo, shell, permissions
- `tests/test_app.py` — 67 regression tests including input validation, rate limiting, token auth
- Run: `bash tests/run_all.sh`
