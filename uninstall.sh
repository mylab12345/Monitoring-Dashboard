#!/usr/bin/env bash
# ============================================================================
#  Montoring — Uninstaller
#  Usage:  sudo /opt/montoring/uninstall.sh [--purge]
#          --purge : also remove backups and logs
# ============================================================================
set -euo pipefail
HOME_DIR="${MONTORING_HOME:-/opt/montoring}"
[ -f /etc/montoring.env ] && . /etc/montoring.env 2>/dev/null && HOME_DIR="${MONTORING_HOME:-$HOME_DIR}"
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

log() { printf '\033[1;32m[uninstall]\033[0m %s\n' "$*"; }

# Stop & disable service
if command -v systemctl >/dev/null 2>&1 && ps -p 1 -o comm= 2>/dev/null | grep -q systemd && [ -f /etc/systemd/system/montoring.service ]; then
  systemctl stop montoring 2>/dev/null || true
  systemctl disable montoring 2>/dev/null || true
  rm -f /etc/systemd/system/montoring.service
  systemctl daemon-reload 2>/dev/null || true
  log "Removed systemd service"
elif command -v rc-service >/dev/null 2>&1 && [ -x /etc/init.d/montoring ]; then
  rc-service montoring stop 2>/dev/null || true
  rm -f /etc/init.d/montoring
  log "Removed OpenRC service"
else
  [ -f /run/montoring.pid ] && kill "$(cat /run/montoring.pid)" 2>/dev/null || true
  rm -f /run/montoring.pid
fi

# CLI + env
rm -f /usr/local/bin/montoring /etc/montoring.env
log "Removed CLI and /etc/montoring.env"

# Application
if [ "$PURGE" -eq 1 ]; then
  rm -rf "$HOME_DIR"
  log "Purged $HOME_DIR completely (including backups)"
else
  find "$HOME_DIR" -mindepth 1 -maxdepth 1 \
    ! -name '.backups' ! -name 'montoring.log' ! -name 'nohup.out' \
    -exec rm -rf {} + 2>/dev/null || rm -rf "$HOME_DIR"
  log "Removed application files from $HOME_DIR (backups/logs kept — use --purge to remove)"
fi
log "Montoring uninstalled. 👋"
