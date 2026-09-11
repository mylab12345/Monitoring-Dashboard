# Monitoring — Universal Linux System Console

Monitor, fix and manage any Linux machine from one enterprise-grade console — **live metrics with history charts, threshold alerting, health checks, one-click fixes, processes, systemd services, live logs, libvirt VMs, network inspection and a dedicated System & Kernel tool for repair, full system upgrades and performance tuning.** 100% local-first: **no accounts, no cloud, no telemetry** — pro tooling for your own machine.

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

Options: `--port N` (default 8050), `--bind ADDR` (default 127.0.0.1), `--token TOKEN` (or `auto` to generate one — see Security below), `--home DIR` (default /opt/monitoring), `--no-vm` (skip libvirt tooling), `--no-start`.

When done, open **http://localhost:8050** — or launch it like a normal app (next section).

## 🔐 Security (read this before exposing the dashboard)

The dashboard can **control the whole machine** — kill processes, start/stop
services, reboot VMs, run package upgrades, vacuum logs. Treat it as a root
shell with a GUI:

- **Access token (recommended):** install with `sudo bash install.sh --token auto`
  (or set `MONITORING_TOKEN` in `/etc/monitoring.env`). Every `/api/*` request
  then requires `Authorization: Bearer <token>`; the UI asks for the token the
  first time it loads. The token survives re-installs and updates.
- **Bind address:** default is `127.0.0.1` (localhost only, secure by default). For LAN access use
  `sudo bash install.sh --bind 0.0.0.0 --token <strong-password>` so the dashboard is protected. The installer only opens the firewall port when you bind to
  a non-loopback address **and** a token is set, and warns loudly if you try to expose without a token.
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
./update.sh --check          # read-only: installed vs. latest version (no root)
```

It automatically: finds the installed app → backs up the current version (last 5 kept) → copies new files → syncs Python deps → restarts the service → health-checks it. If no install exists yet, it simply runs the installer. `--check` needs no root and prints `monitoring-update-check installed=… latest=… update_available=0|1` — the same data the Help → Dashboard Update card shows.

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
- One-click safe fixes: update, upgrade, **full-upgrade (incl. new kernels)**, autoremove, clean cache, fix broken, clear logs — package-manager aware (apt/dnf/yum/zypper/pacman/apk)
- **System & Kernel tool (sidebar → System & Kernel)** — one tool for system software health:
  - **Fix All System & Kernel Issues** — the one-click fixer for *every* detected system & kernel software issue: interrupted package transactions, broken dependencies, kernel module map, missing/stale initramfs images, the boot menu, firmware metadata — then refreshes package lists, runs the full system upgrade (new kernels included), removes obsolete packages and cleans caches. Each step is reported and verified individually (`[ok]/[skip]/[fail]`); advanced options force an initramfs rebuild or skip the upgrade.
  - **System Upgrade** — refresh package lists + full system upgrade including new kernels (`apt full-upgrade` / `dnf upgrade` / `zypper dist-upgrade` / `pacman -Syu` / `apk upgrade`), with pending-update list and reboot-required warning
  - **System & Kernel Repair** — finishes interrupted package transactions, repairs dependency state, regenerates the kernel module map (`depmod -a`) and rebuilds missing/stale initramfs images for every installed kernel (the classic "boot fails after a kernel update" fix) — each step reported individually
  - **Performance Tuning** — reversible CPU governor / `vm.swappiness` / I/O scheduler profiles (balanced, max performance, powersave), persisted across reboots, one-click revert to your exact original values — plus a read-only performance-health panel (load average, swap usage) with concrete tuning hints
  - **Kernel & Firmware** — running vs. newest installed kernel, reboot state, fwupd status, kernel error log link
- **Dashboard update (Help → Dashboard Update)** — Monitoring updates itself from the web UI: checks the installed version against the latest GitHub release and applies it via the installed `update.sh --remote` (the service restarts automatically). The read-only check is also available from the terminal: `./update.sh --check`.
- Self-repair: when a package fix hits the service's read-only mount namespace (`required filesystem is read-only: /usr, /etc, /boot`), the dashboard can fix it itself — patch `monitoring.service` with `ReadWritePaths=/usr /etc /boot /efi`, `daemon-reload` and restart — through the service account, no root shell
- Mounted disks overview + listening ports + system info banner (CPU model, kernel, uptime, live network rate)

**Alerts & Activity** — local threshold alerting (CPU/RAM/disk/temperature with warn & critical levels), alert bell with live breach count, full alert history with **severity filter (All / Critical / Warnings / Resolved)**, full-date timestamps, **CSV export**, and an audit trail of every action taken from the console (stored in your browser only, also CSV-exportable). Clearing either history asks for confirmation first.

**Processes** — top processes by CPU/RAM with search, adjustable fetch size (25/50/100), sortable columns (mouse **and keyboard** — headers are focusable, Enter/Space sorts, `aria-sort` announced), auto-refresh toggle, "Updated HH:MM:SS" stamp, **CSV export of the filtered view**, and a kill button with confirmation, duplicate-click protection and **post-kill verification** (the API confirms the PID is actually gone)

**Services** — browse/filter/search systemd units with state filter chips, **sortable Unit/State columns**, pagination, optional auto-refresh, **CSV export**, and start / stop / restart / enable / disable actions with action-specific confirmations, duplicate-click protection and **post-action verification** (`systemctl is-active` / `is-enabled` is checked after every mutation and surfaced in the UI). A **permission banner** warns up front when the privileged helper is missing or denied.

**VMs** — libvirt/QEMU VM list with state, start / shutdown / reboot / force-off (each disruptive action has its own confirmation dialog), disk resize via `qemu-img` and vCPU/RAM configuration — all with duplicate-click protection, optional auto-refresh and a permission banner when the VM helper is unavailable

**Network** — per-interface cards (IPv4/IPv6/MAC, up/down, **live RX/TX throughput rates**, totals, speed) + full listening-ports table with **text filter and CSV export**, optional auto-refresh and last-updated stamp

**Logs** — live journal viewer with priority filter, text filter, line count, follow (auto-refresh) and wrap toggles, copy, download, and a last-updated stamp

**Diagnose (guided troubleshooting center)** — overall health score with grade, issues grouped by CPU / Memory / Disk / Services / Network / Packages / Kernel, and every problem presented as **Problem → Evidence → Impact → Recommended Fix → Verify** with expandable deep diagnostics (top processes, kernel samples, failed units). Safe fixes require confirmation and are followed by **automatic post-fix verification**; runs are kept as a troubleshooting timeline, and the full report can be copied or exported as Markdown.

**Settings** — theme, refresh interval, chart range, danger-action confirmations, alert thresholds with **warn-below-crit validation**, plus **export/import of all preferences as JSON** and a confirmed reset-to-defaults

**Console UX & accessibility** — dark/light/system theme (persisted), collapsible sidebar, command palette (Ctrl K), keyboard shortcuts (press `?`), configurable refresh interval and thresholds in **Settings**, consistent loading / empty / error states with retry across every tab — all local, no login required. Accessibility is first-class:
- every icon-only button has a tooltip **and** an `aria-label`; segmented filters expose `aria-pressed`
- sidebar tabs support **Arrow/Home/End** keys; tables sort from the keyboard and announce order via `aria-sort`
- confirmation dialogs have a **focus trap**, Esc-to-close, and return focus to the button that opened them
- toasts render into a `role="status"` live region; per-tab "Updated HH:MM:SS" stamps show data freshness

### Safety model for system-changing buttons

Every mutating control (kill, stop, restart, disable, force-off, upgrade, repair, vacuum, clear) follows the same pipeline:

1. **Permission check** — tabs consult `GET /api/privileges` and show a banner when the required sudo helper is missing/denied, before you click.
2. **Confirmation dialog** — action-specific wording explains exactly what will happen (skippable via Settings → "Confirm dangerous actions").
3. **Duplicate-click prevention** — an in-flight registry blocks the same action from firing twice; buttons disable and show a spinner (`aria-busy`).
4. **Progress indication** — long operations stream into an inline terminal panel; the toolbar refresh icon spins while requests are in flight.
5. **Post-action verification** — the backend re-checks reality (`systemctl is-active`/`is-enabled`, PID existence) and the UI reports "done", "done but state unexpected", or "failed" accordingly.
6. **Audit logging** — every privileged action is logged server-side (`action=… client=… outcome=…`, plus `MONITORING_LOG_FILE` when set) and mirrored in the browser-local Activity log (CSV-exportable).

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

- The service runs as the dedicated **non-login system account `monitoring`**, never as root. It binds `127.0.0.1:8050` by default (localhost only), so only expose it to networks you trust with `--bind 0.0.0.0 --token`. There is **no login by design**: this is a single-user, standalone console — keep it on localhost or a trusted LAN.
- Elevated operations are limited to specific helper scripts under `/usr/local/lib/monitoring` authorised by `/etc/sudoers.d/monitoring` with `NOPASSWD` and **absolute paths only**. There is **no `sudo ALL`**, and the helpers validate every argument and never use a shell.
- Read-only information (journals, logs, libvirt list/detail, `ss`, `df`, `/proc`/`/sys`) is obtained through group access (`systemd-journal`, `adm`, `libvirt`, `docker`, `kvm`) instead of root. The installer adds those groups only when they exist on the host.
- Package upgrades are the exception that needs controlled system writes. The service keeps `ProtectSystem=full`, but explicitly grants the root-only package helper `ReadWritePaths=/usr /etc /boot /efi`; `/var` remains writable for package databases and caches. This is a mount-namespace rule, **not** a request to chmod `/usr` or make the `monitoring` account root.
- If `upgrade-packages` / **Fix Broken** / **Full Upgrade** / **Fix All** reports `Read-only file system` (exit 78 — "required filesystem is read-only"), the dashboard repairs it **automatically**: the fix endpoints detect exit 78, run the whitelisted `monitoring-self-repair` helper (patches the installed unit with `ReadWritePaths=/usr /etc /boot /efi`, `systemctl daemon-reload`, schedules the restart through systemd), then wait for the dashboard to come back and **retry the failed action once** — no root shell needed. The Maintenance card also still offers the manual **Repair service mount namespace** button. If the dashboard itself cannot reach writable filesystems (host genuinely mounted read-only), fall back to the manual path: `sudo ./update.sh` from this checkout (or `sudo /opt/monitoring/update.sh --remote`), then `sudo systemctl daemon-reload && sudo systemctl restart monitoring`. Confirm the diagnosis with `monitoring check-privileges`, `GET /api/privileges` or `GET /api/self-repair`; retry **Fix Broken** after the package filesystem is writable.
- Every maintenance endpoint returns the helper exit code and a failure response when a command did not complete. Package mutations are serialized so Update, Upgrade and Fix Broken cannot race the same package database.
- Check what the service is allowed to do with **`monitoring check-privileges`**; the dashboard also exposes `GET /api/privileges`, including package filesystem mount status.
- For VM management as a regular user: `sudo usermod -aG libvirt $USER`, then re-login.
- Change the port any time: edit `/etc/monitoring.env` then `monitoring restart`, or reinstall with `install.sh --port N`.

## 🧪 Tests

```bash
bash tests/run_all.sh                       # full regression suite (security
                                            # migration + unit tests + JS parse
                                            # check + optional pip-audit)
python3 -m pytest tests/ -q                 # just the Python test suites
python3 -m pytest tests/test_ui_improvements.py -q   # UI/UX hardening tests only
```

`tests/test_app.py` covers the API baseline, input validation and security
regressions; `tests/test_ui_improvements.py` covers post-action verification
(services + process kill), request-context audit logging, and the template's
accessibility/UX affordances (ARIA labels, confirmations, CSV exports,
duplicate-click guards, focus trap). Suites self-skip cleanly when flask or
psutil are not installed.

## 🧹 Uninstall

```bash
sudo /opt/monitoring/uninstall.sh          # keep backups/logs
sudo /opt/monitoring/uninstall.sh --purge  # remove everything
```
