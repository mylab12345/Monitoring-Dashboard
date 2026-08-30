# Montoring — Linux Mint OS Dashboard

Minimal single-page dashboard to monitor, fix, and manage your local Mint system — including libvirt VMs.

## Start

```bash
pip install flask psutil --break-system-packages  # or apt install python3-flask python3-psutil
python3 app.py
```

Open http://localhost:8050 (binds 0.0.0.0:8050).

## Features

- **Live metrics**: CPU, RAM, Disk, Uptime, Temperature, Network, Load
- **Health checks**: Disk usage, pending updates, broken packages, failed services, kernel errors
- **Quick fixes**: Update, upgrade, autoremove, clean, fix broken, clear logs
- **VM Manager**: List VMs via `virsh` / `libvirt-python`, view state/memory/vCPUs, resize disk via `qemu-img`

## Libvirt / VM Access

Your user must be in the `libvirt` group and have socket access (`qemu:///system`) for VM management to appear.

```bash
sudo usermod -aG libvirt $USER
# then re-login
```

## Structure (minimal)

```
app.py              # Flask backend
templates/
  index.html        # Single-page UI (Flask template)
static/
  dashboard.css     # Glass theme
  wallpaper.jpg     # Natural background
```
