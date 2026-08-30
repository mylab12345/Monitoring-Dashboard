#!/usr/bin/env bash
# ============================================================================
#  Monitoring — Uninstaller
#  Usage:  sudo /opt/monitoring/uninstall.sh [--purge]
#          --purge : also remove backups and logs
# ============================================================================
set -euo pipefail
HOME_DIR="${MONITORING_HOME:-/opt/monitoring}"
[ -f /etc/monitoring.env ] && . /etc/monitoring.env 2>/dev/null && HOME_DIR="${MONITORING_HOME:-$HOME_DIR}"
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

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

# CLI + env
rm -f /usr/local/bin/monitoring /etc/monitoring.env
log "Removed CLI and /etc/monitoring.env"

# Application
if [ "$PURGE" -eq 1 ]; then
  rm -rf "$HOME_DIR"
  log "Purged $HOME_DIR completely (including backups)"
else
  find "$HOME_DIR" -mindepth 1 -maxdepth 1 \
    ! -name '.backups' ! -name 'monitoring.log' ! -name 'nohup.out' \
    -exec rm -rf {} + 2>/dev/null || rm -rf "$HOME_DIR"
  log "Removed application files from $HOME_DIR (backups/logs kept — use --purge to remove)"
fi
log "Monitoring uninstalled. 👋"
