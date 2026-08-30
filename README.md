# Montoring — Universal Linux System Dashboard

Monitor, fix and manage any Linux machine from one beautiful glassmorphic desktop app — **CPU/RAM/disk metrics, health checks, one-click fixes, processes, systemd services, live logs, Docker containers, libvirt VMs and network inspection.**

Works on **any Linux flavour** — Linux Mint, Ubuntu, Debian, Fedora, RHEL/Rocky/Alma, openSUSE, Arch/Manjaro, Alpine.

---

## 🚀 Install (global one-liner)

The installer **detects your OS**, **downloads & installs all required dependencies** (python3, venv/pip, flask, psutil, libvirt/qemu tooling), **installs the app** to `/opt/montoring`, registers it as a **system service**, installs the **desktop app** (menu shortcut + autostart) and adds a **`montoring` CLI**:

```bash
curl -fsSL https://raw.githubusercontent.com/mylab12345/Montoring/main/install.sh | sudo bash
```

Or from a local checkout:

```bash
sudo bash install.sh
```

Options: `--port N` (default 8050), `--home DIR` (default /opt/montoring), `--no-vm` (skip libvirt tooling), `--no-start`.

When done, open **http://localhost:8050** — or launch it like a normal app (next section).

## 🖥️ Standalone Desktop App

Montoring installs as a **real desktop application**:

- **Application-menu shortcut** — look for *Montoring* in your menu (icon included), or run `montoring-app`
- **Native window** — opens chromeless via pywebview / Chrome `--app` / Firefox kiosk (whatever is available)
- **Starts with your system** — autostart entry in `/etc/xdg/autostart` opens the window at login; the backend service is already running via systemd
- **Splash + auto-connect** — the launcher shows a splash, starts the backend if needed, then opens the window
- Inside the app: **Help tab → 🚀 Launch App Window** re-opens the native window any time

```bash
montoring-app          # launch the desktop window
montoring-app --check  # show how the window would be opened
```

## 🔄 Update after code changes (one file)

After **any** change to the code, just run the update file — it reinstalls the app with the new changes and restarts the service:

```bash
sudo ./update.sh              # from a checkout with your changes
sudo ./update.sh --remote    # pull the latest from GitHub instead
```

It automatically: finds the installed app → backs up the current version (last 5 kept) → copies new files → syncs Python deps → restarts the service → health-checks it. If no install exists yet, it simply runs the installer.

## ✨ Features

**Overview**
- Live CPU / RAM / Disk / Swap metrics with sparkline history charts, load average, temperature, battery, uptime
- System health checks: disk usage, pending updates, broken packages, failed services, kernel errors, zombie processes
- One-click safe fixes: update, upgrade, autoremove, clean cache, fix broken, clear logs, docker prune — package-manager aware (apt/dnf/yum/zypper/pacman/apk)
- Mounted disks overview + listening ports + system info

**Processes** — top processes by CPU/RAM with search, auto-refresh and kill button

**Services** — browse/filter systemd units, start / stop / restart / enable / disable with confirmation

**Docker** — list containers, start / stop / restart / remove, prune unused data (auto-detects Docker)

**VMs** — libvirt/QEMU VM list with state, start / shutdown / reboot / force-off and disk resize via `qemu-img`

**Network** — per-interface cards (IPv4/IPv6/MAC, up/down, traffic, speed) + full listening-ports table

**Logs** — live journal viewer with priority filter, text filter, line count and auto-refresh

## 🛠 CLI (`montoring`)

```bash
montoring status    # service status + URL
montoring logs      # tail service logs
montoring restart   # restart the dashboard
montoring update    # run the updater from the installed copy
montoring open      # open the dashboard in your browser
```

## 📦 Layout

```
app.py                 # Flask backend (all APIs)
templates/index.html   # Single-page UI (sidebar navigation, glass theme)
static/dashboard.css   # Glass theme + wallpaper
static/icon.png        # App icon (desktop + favicon)
install.sh             # Universal OS-detecting installer (the global link)
update.sh              # One-file update/reinstall after code changes
uninstall.sh           # Clean uninstaller (--purge for everything)
montoring              # CLI control tool          -> /usr/local/bin/montoring
montoring-app          # Desktop app launcher      -> /usr/local/bin/montoring-app
montoring-app.desktop  # Menu shortcut/autostart   -> /usr/share/applications + /etc/xdg/autostart
montoring.service      # systemd unit template
openrc/montoring       # OpenRC script (Alpine etc.)
VERSION                # App version (shown in the dashboard)
```

## 🔐 Notes

- The service runs as **root** so fixes, service control, VM and Docker actions work — it binds `0.0.0.0:8050`, so only expose it to networks you trust.
- For VM management as a regular user: `sudo usermod -aG libvirt $USER`, then re-login.
- Change the port any time: edit `/etc/montoring.env` then `montoring restart`, or reinstall with `install.sh --port N`.

## 🧹 Uninstall

```bash
sudo /opt/montoring/uninstall.sh          # keep backups/logs
sudo /opt/montoring/uninstall.sh --purge  # remove everything
```
