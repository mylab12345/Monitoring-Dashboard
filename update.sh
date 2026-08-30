#!/usr/bin/env bash
# ============================================================================
#  Monitoring — One-file Updater / Reinstaller
# ----------------------------------------------------------------------------
#  After ANY code change, just run this file: it re-installs the app with the
#  new changes (files + dependencies) and restarts the service.
#
#  Usage:
#      sudo ./update.sh                # install changes from THIS checkout
#      sudo ./update.sh --remote      # pull latest from GitHub and install
#      curl -fsSL https://raw.githubusercontent.com/mylab12345/Monitoring/main/update.sh | sudo bash
#                                     # same as --remote
#
#  What it does:
#    1. Finds the installed app   (default /opt/monitoring, or $MONITORING_HOME)
#    2. Backs up the current version
#    3. Copies the new app files  (app.py, templates, static, VERSION, …)
#    4. Re-syncs Python dependencies (fast no-op when already satisfied)
#    5. Restarts the service (systemd / OpenRC / pidfile)
#    6. Health-checks the dashboard and prints old → new version
# ============================================================================
set -euo pipefail

REPO="${REPO:-mylab12345/Monitoring}"
BRANCH="${BRANCH:-main}"
MODE="local"
FORCE=0
SELF_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/update.sh"

log()  { printf '\033[1;32m[update]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[update]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[update]\033[0m %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --remote) MODE="remote"; shift ;;
    --force)  FORCE=1; shift ;;
    --repo)   REPO="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    -h|--help) sed -n '2,22p' "$0" 2>/dev/null; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

# --- Root check ---------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || die "Not root and sudo not found. Re-run as root."
  if [ -f "$0" ] && [ -s "$0" ]; then
    exec sudo -E bash "$0" "$@"
  else
    exec sudo bash -c "curl -fsSL '${SELF_URL}' | bash --remote"
  fi
fi

# --- Locate app sources ---------------------------------------------------------
SRC=""
if [ -f "$0" ] && [ -s "$0" ]; then
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  if [ -f "$SCRIPT_DIR/app.py" ] && [ -d "$SCRIPT_DIR/templates" ]; then
    SRC="$SCRIPT_DIR"
  fi
fi
if [ "$MODE" = "remote" ] || [ -z "$SRC" ]; then
  TMP="$(mktemp -d /tmp/monitoring-update.XXXXXX)"
  log "Downloading latest ${REPO}@${BRANCH}…"
  curl -fsSL "https://github.com/${REPO}/archive/refs/heads/${BRANCH}.tar.gz" | tar -xz -C "$TMP" \
    || die "Download failed. Check internet connectivity or run update.sh from a local checkout."
  SRC="$(find "$TMP" -maxdepth 2 -name app.py -printf '%h\n' | head -1)"
  [ -n "$SRC" ] || die "Archive does not contain app.py"
fi
NEW_VERSION="$(cat "$SRC/VERSION" 2>/dev/null || echo '?')"

# --- Locate installed app --------------------------------------------------------
TARGET="${MONITORING_HOME:-}"
if [ -z "$TARGET" ] && [ -f /etc/monitoring.env ]; then
  . /etc/monitoring.env 2>/dev/null || true
  TARGET="${MONITORING_HOME:-}"
fi
if [ -z "$TARGET" ] && [ -d /opt/monitoring ] && [ -f /opt/monitoring/app.py ]; then
  TARGET="/opt/monitoring"
fi
if [ -z "$TARGET" ]; then
  warn "No existing installation found."
  log "Delegating to install.sh for a fresh install…"
  if [ -f "$SRC/install.sh" ]; then exec bash "$SRC/install.sh" "$@";
  else exec bash -c "curl -fsSL https://raw.githubusercontent.com/${REPO}/${BRANCH}/install.sh | bash"; fi
fi
log "Installation found: $TARGET"

# If sources ARE the installed copy (e.g. `monitoring update`), there is nothing
# local to sync — pull the latest from GitHub instead.
if [ "$SRC" = "$TARGET" ]; then
  log "Running from the installed copy — pulling latest from GitHub…"
  TMP="$(mktemp -d /tmp/monitoring-update.XXXXXX)"
  curl -fsSL "https://github.com/${REPO}/archive/refs/heads/${BRANCH}.tar.gz" | tar -xz -C "$TMP" \
    || die "Download failed. Check internet connectivity or run update.sh from a local checkout."
  SRC="$(find "$TMP" -maxdepth 2 -name app.py -printf '%h\n' | head -1)"
  [ -n "$SRC" ] || die "Archive does not contain app.py"
  NEW_VERSION="$(cat "$SRC/VERSION" 2>/dev/null || echo '?')"
fi

OLD_VERSION="$(cat "$TARGET/VERSION" 2>/dev/null || echo '?')"
log "Updating version: v${OLD_VERSION} → v${NEW_VERSION}"

# Downgrade guard: never replace a versioned install with sources that lack a
# VERSION file (usually means the remote branch is OLDER than what's installed).
if [ ! -f "$SRC/VERSION" ] && [ -f "$TARGET/VERSION" ] && [ "$FORCE" -ne 1 ]; then
  die "Downloaded sources have no VERSION file (likely older than the installed v${OLD_VERSION}). Use --force to install anyway."
fi

# --- Backup current version --------------------------------------------------------
BACKUP_DIR="$TARGET/.backups"
TS="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/${OLD_VERSION}-${TS}"
mkdir -p "$BACKUP"
cp -a "$TARGET/app.py" "$TARGET/templates" "$TARGET/static" "$BACKUP/" 2>/dev/null || true
[ -f "$TARGET/VERSION" ] && cp -a "$TARGET/VERSION" "$BACKUP/" || true
ls -1dt "$BACKUP_DIR"/*/ 2>/dev/null | tail -n +6 | xargs -r rm -rf
log "Backup saved: $BACKUP (keeping last 5)"

# --- Stop service (if running via pidfile; systemd handles restart later) ------------
SYSTEMD_ACTIVE=0; OPENRC_ACTIVE=0
if command -v systemctl >/dev/null 2>&1 && ps -p 1 -o comm= 2>/dev/null | grep -q systemd && systemctl is-active monitoring >/dev/null 2>&1; then
  SYSTEMD_ACTIVE=1
elif command -v rc-service >/dev/null 2>&1 && rc-service monitoring status >/dev/null 2>&1; then
  OPENRC_ACTIVE=1
fi

# --- Sync new files --------------------------------------------------------------------
log "Copying new application files…"
cp -f "$SRC/app.py" "$TARGET/app.py"
cp -f "$SRC/requirements.txt" "$TARGET/requirements.txt" 2>/dev/null || true
cp -f "$SRC/VERSION" "$TARGET/VERSION" 2>/dev/null || true
cp -f "$SRC/update.sh" "$TARGET/update.sh" 2>/dev/null && chmod 755 "$TARGET/update.sh" || true
cp -f "$SRC/uninstall.sh" "$TARGET/uninstall.sh" 2>/dev/null && chmod 755 "$TARGET/uninstall.sh" || true
mkdir -p "$TARGET/templates" "$TARGET/static"
cp -f "$SRC/templates/"* "$TARGET/templates/" 2>/dev/null || true
cp -f "$SRC/static/"* "$TARGET/static/" 2>/dev/null || true

# Keep CLI + service definitions in sync (only if sources provide them)
if [ -f /usr/local/bin/monitoring ] && [ -f "$SRC/monitoring" ]; then
  sed -e "s|__HOME__|${TARGET}|g" "$SRC/monitoring" > /usr/local/bin/monitoring && chmod 755 /usr/local/bin/monitoring
fi
# Desktop app pieces
if [ -f "$SRC/monitoring-app" ]; then
  install -m 755 "$SRC/monitoring-app" /usr/local/bin/monitoring-app 2>/dev/null || true
  if [ -f "$SRC/monitoring-app.desktop" ]; then
    mkdir -p /usr/share/pixmaps /usr/share/applications /etc/xdg/autostart 2>/dev/null || true
    cp -f "$SRC/static/icon.png" /usr/share/pixmaps/monitoring.png 2>/dev/null || true
    cp -f "$SRC/monitoring-app.desktop" /usr/share/applications/monitoring.desktop 2>/dev/null || true
    cp -f "$SRC/monitoring-app.desktop" /etc/xdg/autostart/monitoring.desktop 2>/dev/null || true
  fi
fi
PYBIN_CURRENT=""
[ -f /etc/monitoring.env ] && . /etc/monitoring.env 2>/dev/null && PYBIN_CURRENT="${PYBIN:-}"
if [ "$SYSTEMD_ACTIVE" -eq 1 ] || [ -f /etc/systemd/system/monitoring.service ]; then
  if [ -f "$SRC/monitoring.service" ] && [ -f "$SRC/install.sh" ]; then
    PY="${PYBIN_CURRENT:-$TARGET/venv/bin/python}"
    [ -x "$PY" ] || PY="$(command -v python3)"
    sed -e "s|__HOME__|${TARGET}|g" -e "s|__PYBIN__|${PY}|g" -e "s|__ENVFILE__|/etc/monitoring.env|g" \
      "$SRC/monitoring.service" > /etc/systemd/system/monitoring.service
    systemctl daemon-reload
  fi
fi

# --- Python deps ------------------------------------------------------------------------
if [ -x "$TARGET/venv/bin/pip" ]; then
  log "Syncing Python dependencies (venv)…"
  "$TARGET/venv/bin/pip" install --quiet -r "$TARGET/requirements.txt" || warn "pip sync had warnings"
else
  log "Syncing Python dependencies (system)…"
  python3 -m pip install --quiet -r "$TARGET/requirements.txt" --break-system-packages 2>/dev/null \
    || python3 -m pip install --quiet -r "$TARGET/requirements.txt" 2>/dev/null \
    || warn "Could not sync python deps automatically"
fi

# --- Restart ------------------------------------------------------------------------------
log "Restarting service…"
if [ "$SYSTEMD_ACTIVE" -eq 1 ] || { command -v systemctl >/dev/null 2>&1 && ps -p 1 -o comm= 2>/dev/null | grep -q systemd && [ -f /etc/systemd/system/monitoring.service ]; }; then
  systemctl restart monitoring
elif [ "$OPENRC_ACTIVE" -eq 1 ]; then
  rc-service monitoring restart
elif [ -x /usr/local/bin/monitoring ]; then
  /usr/local/bin/monitoring restart
else
  warn "Restart manually: python3 $TARGET/app.py"
fi

# --- Health check ---------------------------------------------------------------------------
PORT="${MONITORING_PORT:-8050}"
sleep 2
if curl -fsS "http://127.0.0.1:${PORT}/api/version" >/dev/null 2>&1; then
  log "Health check passed ✓   Dashboard: http://localhost:${PORT}"
else
  warn "Health check did not respond yet. Try: monitoring status / monitoring logs"
  warn "To roll back: cp -a ${BACKUP}/* ${TARGET}/ and restart."
fi
log "Update complete: v${OLD_VERSION} → v${NEW_VERSION} 🎉"
