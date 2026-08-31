# Monitoring — Universal Linux System Console

Monitor, fix and manage any Linux machine from one enterprise-grade console — **live metrics with history charts, threshold alerting, health checks, one-click fixes, processes, systemd services, live logs, libvirt VMs and network inspection.** 100% local-first: **no accounts, no cloud, no telemetry** — pro tooling for your own machine.

Works on **any Linux flavour** — Linux Mint, Ubuntu, Debian, Fedora, RHEL/Rocky/Alma, openSUSE, Arch/Manjaro, Alpine.

---

## 🚀 Install (global one-liner)

The installer **detects your OS**, **downloads & installs all required dependencies** (python3, venv/pip, flask, psutil, libvirt/qemu tooling), **creates a dedicated non-login system account `monitoring`**, installs only the explicitly whitelisted sudo helpers in `/etc/sudoers.d/monitoring` (validated with `visudo`), **installs the app** to `/opt/monitoring`, registers it as a **system service running as `monitoring`**, installs the **desktop app** (menu shortcut + autostart) and adds a **`monitoring` CLI**:

```bash
curl -fsSL https://raw.githubusercontent.com/mylab12345/Monitoring-Dashboard/main/install.sh | sudo bash
```

Or from a local checkout:

```bash
sudo bash install.sh
```

Options: `--port N` (default 8050), `--bind ADDR` (default 0.0.0.0), `--token TOKEN` (or `auto` to generate one — see Security below), `--home DIR` (default /opt/monitoring), `--no-vm` (skip libvirt tooling), `--no-start`.

When done, open **http://localhost:8050** — or launch it like a normal app (next section).

## 🔐 Security (read this before exposing the dashboard)

The dashboard can **control the whole machine** — kill processes, start/stop
services, reboot VMs, run package upgrades, vacuum logs. Treat it as a root
shell with a GUI:

- **Access token (recommended):** install with `sudo bash install.sh --token auto`
  (or set `MONITORING_TOKEN` in `/etc/monitoring.env`). Every `/api/*` request
  then requires `Authorization: Bearer <token>`; the UI asks for the token the
  first time it loads. The token survives re-installs and updates.
- **Bind address:** default is `0.0.0.0`. For a single-user machine use
  `sudo bash install.sh --bind 127.0.0.1` so the dashboard is only reachable
  from localhost. The installer only opens the firewall port when you bind to
  a non-loopback address, and warns loudly if you do so without a token.
- **Privilege model:** the service runs as a dedicated non-login `monitoring`
  account. Privileged operations (systemd, packages, journal vacuum, kill,
  libvirt/qemu) go through whitelisted, argument-validating helper commands
  granted via absolute-path `NOPASSWD` sudo entries — never `sudo ALL`, never
  a shell.
- **API calls with curl:** `curl -H "Authorization: Bearer $TOKEN" http://localhost:8050/api/status`

## 🖥️ Standalone Desktop App

Monitoring installs as a **real desktop application**:

- **Application-menu shortcut** — look for *Monitoring* in your menu (icon included), or run `monitoring-app`
- **Native window** — opens chromeless via pywebview / Chrome `--app` / Firefox kiosk (whatever is available)
- **Starts with your system** — autostart entry in `/etc/xdg/autostart` opens the window at login; the backend service is already running via systemd
- **Splash + auto-connect** — the launcher shows a splash, starts the backend if needed, then opens the window
- Inside the app: **Help tab → 🚀 Launch App Window** re-opens the native window any time

```bash
monitoring-app          # launch the desktop window
monitoring-app --check  # show how the window would be opened
```

## 🔄 Update after code changes (one file)

After **any** change to the code, just run the update file — it reinstalls the app with the new changes and restarts the service:

```bash
sudo ./update.sh              # from a checkout with your changes
sudo ./update.sh --remote    # pull the latest from GitHub instead
```

It automatically: finds the installed app → backs up the current version (last 5 kept) → copies new files → syncs Python deps → restarts the service → health-checks it. If no install exists yet, it simply runs the installer.

### ⚠️ One-time note when upgrading from v2.4.x (the old single-file layout)

Versions before this refactor kept the whole backend in `app.py` and the whole
frontend inline in `templates/index.html`. The app is now modular: the backend
lives in the **`monitor/`** package and the UI in **`static/js/`**. The updater
and installer now copy both directories and verify them before restarting the
service.

- **Always upgrade with the *new* `update.sh`** — e.g. `git pull` in your
  checkout, then `sudo ./update.sh` from that checkout. An *old* installed
  `update.sh` (v2.4.x) does not know about `monitor/` and would copy only the
  thin `app.py`, leaving the service unable to start.
- If the service ever fails to start after an update, `app.py` prints a clear
  error (see `monitoring logs`) and the fix is simply re-running
  `sudo ./update.sh` from an up-to-date checkout — no manual surgery needed.
- Make sure your checkout is clean (`git status`) before `git pull`, so the new
  `monitor/` and `static/js/` directories land without conflicts.

## ✨ Features

**Overview**
- Live CPU / RAM / Disk / Swap metrics with ring gauges, sparkline history, load average, temperature, battery, uptime
- **Resource timeline** — CPU/RAM/throughput area chart with hover tooltip and 5m/15m/30m/1h ranges, backed by a server-side metrics ring buffer (charts survive page reloads)
- System health checks: disk usage, pending updates, broken packages, failed services, kernel errors, zombie processes
- One-click safe fixes: update, upgrade, autoremove, clean cache, fix broken, clear logs — package-manager aware (apt/dnf/yum/zypper/pacman/apk)
- Mounted disks overview + listening ports + system info banner (CPU model, kernel, uptime, live network rate)

**Alerts & Activity** — local threshold alerting (CPU/RAM/disk/temperature with warn & critical levels), alert bell with live breach count, full alert history, and an audit trail of every action taken from the console (stored in your browser only)

**Processes** — top processes by CPU/RAM with search, adjustable fetch size (25/50/100), sortable columns and kill button

**Services** — browse/filter systemd units, start / stop / restart / enable / disable with confirmation and pagination

**VMs** — libvirt/QEMU VM list with state, start / shutdown / reboot / force-off and disk resize via `qemu-img`

**Network** — per-interface cards (IPv4/IPv6/MAC, up/down, **live RX/TX throughput rates**, totals, speed) + full listening-ports table

**Logs** — live journal viewer with priority filter, text filter, line count, wrap toggle, copy and download

**Diagnose (guided troubleshooting center)** — overall health score with grade, issues grouped by CPU / Memory / Disk / Services / Network / Packages / Kernel, and every problem presented as **Problem → Evidence → Impact → Recommended Fix → Verify** with expandable deep diagnostics (top processes, kernel samples, failed units). Safe fixes require confirmation and are followed by **automatic post-fix verification**; runs are kept as a troubleshooting timeline, and the full report can be copied or exported as Markdown.

**Console UX** — dark/light/system theme (persisted), collapsible sidebar, command palette (Ctrl K), keyboard shortcuts (press `?`), configurable refresh interval and thresholds in **Settings**, consistent loading / empty / error states with retry across every tab — all local, no login required

## 🛠 CLI (`monitoring`)

```bash
monitoring status          # service status + URL
monitoring logs            # tail service logs
monitoring restart         # restart the dashboard
monitoring update          # run the updater from the installed copy
monitoring open            # open the dashboard in your browser
monitoring check-privileges # report the service account, groups and sudo grants
```

## 📦 Layout

```
app.py                 # Thin entrypoint: creates the app + starts the server
monitor/               # Modular backend package (one module per feature area)
  __init__.py          #   create_app(): registers each blueprint defensively —
                       #   a broken module degrades gracefully instead of crashing
  common.py            #   config, logging, cache, shared helpers
  commands.py          #   argv-only subprocess runner + input validators
  security.py          #   token auth, rate limiting, headers, error handlers
  web.py metrics.py processes.py services.py logs.py system.py packages.py
  fixes.py diagnostics.py desktop.py vms.py privileges.py
                       #   one Blueprint per area; a syntax error in any file
                       #   only takes down its own routes (reported by /api/health)
templates/index.html   # Page markup + navigation (no inline app logic)
static/dashboard.css   # Glass theme + wallpaper
static/js/             # Frontend split into 14 files (core, tabs, overview,
                       #   alerts, diagnostics, activity, checks, network,
                       #   processes, services, vms, logs, settings, bootstrap)
                       #   — separate parse units, so one broken file can't
                       #   abort the rest; tab loaders are lazy thunks with a
                       #   try/catch guard
static/icon.png        # App icon (desktop + favicon)
install.sh             # Universal OS-detecting installer (the global link)
update.sh              # One-file update/reinstall after code changes
uninstall.sh           # Clean uninstaller (--purge for everything)
monitoring              # CLI control tool          -> /usr/local/bin/monitoring
monitoring-app          # Desktop app launcher      -> /usr/local/bin/monitoring-app
monitoring-app.desktop  # Menu shortcut/autostart   -> /usr/share/applications + /etc/xdg/autostart
monitoring.service      # systemd unit template (runs as the monitoring account)
openrc/monitoring       # OpenRC script (Alpine etc.)
privileged/             # sudo helper scripts (validated argv, no shell, no sudo ALL)
sudoers/monitoring      # exact sudoers fragment for the service account
VERSION                # App version (shown in the dashboard)
```

### Why the split?

The backend was a single 2,300-line `app.py` and the UI a single 2,200-line
inline `<script>`. In both cases a single mistake (a syntax error, a bad
import, an undefined helper) aborted **everything** — this actually shipped on
`main` at one point. Now:

- **Backend:** each feature area is a module registered via `create_app()`. A
  broken module is caught, logged, and reported in `GET /api/health` (`modules`)
  while every other endpoint keeps working.
- **Frontend:** each file is a separate `<script>` parse unit, tab loaders are
  lazy thunks wrapped in `try/catch`, and a global `error` handler surfaces
  unexpected failures as a toast instead of silently freezing the UI.

## 🔐 Notes

- The service runs as the dedicated **non-login system account `monitoring`**, never as root. It binds `0.0.0.0:8050`, so only expose it to networks you trust. There is **no login by design**: this is a single-user, standalone console — keep it on localhost or a trusted LAN.
- Elevated operations are limited to specific helper scripts under `/usr/local/lib/monitoring` authorised by `/etc/sudoers.d/monitoring` with `NOPASSWD` and **absolute paths only**. There is **no `sudo ALL`**, and the helpers validate every argument and never use a shell.
- Read-only information (journals, logs, libvirt list/detail, `ss`, `df`, `/proc`/`/sys`) is obtained through group access (`systemd-journal`, `adm`, `libvirt`, `docker`, `kvm`) instead of root. The installer adds those groups only when they exist on the host.
- Package upgrades are the exception that needs controlled system writes. The service keeps `ProtectSystem=full`, but explicitly grants the root-only package helper `ReadWritePaths=/usr /etc /boot /efi`; `/var` remains writable for package databases and caches. This is a mount-namespace rule, **not** a request to chmod `/usr` or make the `monitoring` account root.
- If `upgrade-packages` reports `Read-only file system` or leaves a package `half-installed`, run `sudo ./update.sh` from this checkout (or `sudo /opt/monitoring/update.sh --remote`) so the unit is replaced, then run `sudo systemctl daemon-reload && sudo systemctl restart monitoring`. Confirm the diagnosis with `monitoring check-privileges` or the dashboard's `GET /api/privileges`; retry **Fix Broken** after the package filesystem is writable.
- Every maintenance endpoint returns the helper exit code and a failure response when a command did not complete. Package mutations are serialized so Update, Upgrade and Fix Broken cannot race the same package database.
- Check what the service is allowed to do with **`monitoring check-privileges`**; the dashboard also exposes `GET /api/privileges`, including package filesystem mount status.
- For VM management as a regular user: `sudo usermod -aG libvirt $USER`, then re-login.
- Change the port any time: edit `/etc/monitoring.env` then `monitoring restart`, or reinstall with `install.sh --port N`.

## 🧹 Uninstall

```bash
sudo /opt/monitoring/uninstall.sh          # keep backups/logs
sudo /opt/monitoring/uninstall.sh --purge  # remove everything
```
