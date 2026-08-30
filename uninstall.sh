#!/usr/bin/env bash
# ============================================================================
#  Monitoring — Uninstaller
#  Usage:  sudo /opt/monitoring/uninstall.sh [--purge]
#          --purge : also remove backups and logs, sudoers and the service account
# ============================================================================
set -euo pipefail
HOME_DIR="${MONITORING_HOME:-/opt/monitoring}"
[ -f /etc/monitoring.env ] && . /etc/monitoring.env 2>/dev/null && HOME_DIR="${MONITORING_HOME:-$HOME_DIR}"
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1
MONITORING_USER="monitoring"
MONITORING_GROUP="monitoring"
PRIVILEGE_DIR="/usr/local/lib/monitoring"
SUDOERS_FILE="/etc/sudoers.d/monitoring"
LOG_DIR="/var/log/monitoring"

log() { printf '\033[1;32m[uninstall]\033[0m %s\n' "$*"; }

# Stop & disable service
if command -v systemctl >/dev/null 2>&1 && ps -p 1 -o comm= 2>/dev/null | grep -q systemd && [ -f /etc/systemd/system/monitoring.service ]; then
  systemctl stop monitoring 2>/dev/null || true
  systemctl disable monitoring 2>/dev/null || true
  rm -f /etc/systemd/system/monitoring.service
  systemctl daemon-reload 2>/dev/null || true
  log "Removed systemd service"
elif command -v rc-service >/dev/null 2>&1 && [ -x /etc/init.d/monitoring ]; then
  rc-service monitoring stop 2>/dev/null || true
  rm -f /etc/init.d/monitoring
  log "Removed OpenRC service"
else
  [ -f /run/monitoring.pid ] && kill "$(cat /run/monitoring.pid)" 2>/dev/null || true
  rm -f /run/monitoring.pid
fi

# Privileged helper + sudoers (always; they are security-sensitive)
rm -rf "$PRIVILEGE_DIR"
rm -f "$SUDOERS_FILE"
log "Removed privileged helpers and $SUDOERS_FILE"

# CLI + env (both the control CLI and the desktop-app launcher)
rm -f /usr/local/bin/monitoring /usr/local/bin/monitoring-app /etc/monitoring.env \
      /usr/share/applications/monitoring.desktop /etc/xdg/autostart/monitoring.desktop \
      /usr/share/pixmaps/monitoring.png
log "Removed CLI, desktop launcher and /etc/monitoring.env"

# Application
if [ "$PURGE" -eq 1 ]; then
  rm -rf "$HOME_DIR" "$LOG_DIR"
  log "Purged $HOME_DIR completely (including backups)"
else
  find "$HOME_DIR" -mindepth 1 -maxdepth 1 \
    ! -name '.backups' ! -name 'monitoring.log' ! -name 'nohup.out' \
    -exec rm -rf {} + 2>/dev/null || rm -rf "$HOME_DIR"
  log "Removed application files from $HOME_DIR (backups/logs kept — use --purge to remove)"
fi

if [ "$PURGE" -eq 1 ] && id "$MONITORING_USER" >/dev/null 2>&1; then
  userdel "$MONITORING_USER" 2>/dev/null || true
  log "Removed service account $MONITORING_USER"
fi
if [ "$PURGE" -eq 1 ] && getent group "$MONITORING_GROUP" >/dev/null 2>&1; then
  groupdel "$MONITORING_GROUP" 2>/dev/null || true
fi
log "Monitoring uninstalled. 👋"
