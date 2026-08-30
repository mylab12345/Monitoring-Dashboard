#!/usr/bin/env bash
# ============================================================================
#  Monitoring — Universal Linux Installer
# ----------------------------------------------------------------------------
#  Works on ANY Linux flavour: Debian/Ubuntu/Mint, Fedora/RHEL/Rocky/Alma,
#  openSUSE, Arch/Manjaro, Alpine, ...
#
#  Global one-liner (downloads latest from GitHub, then installs):
#      curl -fsSL https://raw.githubusercontent.com/mylab12345/Montoring/main/install.sh | sudo bash
#
#  Local (from a checkout of this repo):
#      sudo bash install.sh
#
#  Options:
#      --port N        port to listen on            (default 8050)
#      --home DIR      install location             (default /opt/monitoring)
#      --no-vm         skip libvirt/qemu tooling
#      --no-start      install but do not start the service
#      --repo R        GitHub repo for remote mode  (default mylab12345/Montoring)
#      --branch B      GitHub branch for remote mode (default main)
# ============================================================================
set -euo pipefail

# --- Defaults (overridable via flags / env) ---------------------------------
REPO="${REPO:-mylab12345/Montoring}"
BRANCH="${BRANCH:-main}"
MONITORING_HOME="${MONITORING_HOME:-/opt/monitoring}"
PORT="${MONITORING_PORT:-8050}"
WITH_VM=1
START_SERVICE=1
SELF_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/install.sh"
APP_NAME="monitoring"

log()  { printf '\033[1;32m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# --- Parse flags -------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --port)     PORT="$2"; shift 2 ;;
    --home)     MONITORING_HOME="$2"; shift 2 ;;
    --no-vm)    WITH_VM=0; shift ;;
    --no-start) START_SERVICE=0; shift ;;
    --repo)     REPO="$2"; shift 2 ;;
    --branch)   BRANCH="$2"; shift 2 ;;
    -h|--help)  sed -n '2,20p' "$0" 2>/dev/null || sed -n '2,20p' /dev/stdin; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

# --- Root check (re-exec with sudo when possible) ----------------------------
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || die "Not root and sudo not found. Re-run as root."
  if [ -f "$0" ] && [ -s "$0" ]; then
    log "Re-running with sudo…"
    exec sudo -E bash "$0" "$@"
  else
    log "Running from a pipe — re-fetching installer under sudo…"
    exec sudo bash -c "curl -fsSL '${SELF_URL}' | bash $([ "${PORT:-}" != 8050 ] && echo "--port ${PORT}") $([ "${MONITORING_HOME:-}" != /opt/monitoring ] && echo "--home ${MONITORING_HOME}")"
  fi
fi

# --- Locate app sources (local checkout OR download from GitHub) -------------
SRC=""
SCRIPT_DIR=""
if [ -f "$0" ] && [ -s "$0" ]; then
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  if [ -f "$SCRIPT_DIR/app.py" ] && [ -d "$SCRIPT_DIR/templates" ]; then
    SRC="$SCRIPT_DIR"
    log "Source: local checkout at $SRC"
  fi
fi
if [ -z "$SRC" ]; then
  TMP="$(mktemp -d /tmp/monitoring-install.XXXXXX)"
  log "Source: downloading ${REPO}@${BRANCH} from GitHub…"
  curl -fsSL "https://github.com/${REPO}/archive/refs/heads/${BRANCH}.tar.gz" | tar -xz -C "$TMP" \
    || die "Could not download sources. Check your internet or clone the repo and run install.sh locally."
  SRC="$(find "$TMP" -maxdepth 2 -name app.py -printf '%h\n' | head -1)"
  [ -n "$SRC" ] || die "Downloaded archive does not contain app.py"
  log "Source extracted: $SRC"
fi

# --- Detect OS / package manager --------------------------------------------
PKG=""
if command -v apt-get >/dev/null 2>&1; then PKG="apt";
elif command -v dnf >/dev/null 2>&1; then PKG="dnf";
elif command -v yum >/dev/null 2>&1; then PKG="yum";
elif command -v zypper >/dev/null 2>&1; then PKG="zypper";
elif command -v pacman >/dev/null 2>&1; then PKG="pacman";
elif command -v apk >/dev/null 2>&1; then PKG="apk";
fi
. /etc/os-release 2>/dev/null || true
DISTRO="${PRETTY_NAME:-unknown linux}"
log "Detected OS: $DISTRO (package manager: ${PKG:-none})"

# --- Install OS dependencies -------------------------------------------------
install_pkgs() {  # install_pkgs "pkg1" "pkg2" ...  (best-effort, per-manager)
  case "$PKG" in
    apt)    DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" >/dev/null 2>&1 || true ;;
    dnf)    dnf install -y -q "$@" >/dev/null 2>&1 || true ;;
    yum)    yum install -y -q "$@" >/dev/null 2>&1 || true ;;
    zypper) zypper --non-interactive install "$@" >/dev/null 2>&1 || true ;;
    pacman) pacman -S --noconfirm --needed "$@" >/dev/null 2>&1 || true ;;
    apk)    apk add --quiet "$@" >/dev/null 2>&1 || true ;;
    *)      warn "No supported package manager — skipping OS package install" ;;
  esac
}

log "Installing base dependencies (python3, pip, venv, curl)…"
case "$PKG" in
  apt)    install_pkgs python3 python3-venv python3-pip curl ca-certificates ;;
  dnf)    install_pkgs python3 python3-pip curl ca-certificates ;;
  yum)    install_pkgs python3 python3-pip curl ca-certificates ;;
  zypper) install_pkgs python3 python3-pip python3-venv curl ca-certificates ;;
  pacman) install_pkgs python python-pip curl ca-certificates ;;
  apk)    install_pkgs python3 py3-pip curl ca-certificates libffi openssl ;;
  *)      : ;;
esac

command -v python3 >/dev/null 2>&1 || die "python3 is required but could not be installed. Install it manually and re-run."

# VM tooling (libvirt CLI + qemu-img) — optional, app degrades gracefully
if [ "$WITH_VM" -eq 1 ]; then
  log "Installing VM tooling (libvirt client, qemu-img — best effort)…"
  case "$PKG" in
    apt)    install_pkgs libvirt-clients qemu-utils ;;
    dnf)    install_pkgs libvirt qemu-img ;;
    yum)    install_pkgs libvirt qemu-img ;;
    zypper) install_pkgs libvirt-client qemu-img ;;
    pacman) install_pkgs libvirt qemu-desktop ;;
    apk)    install_pkgs libvirt qemu-img ;;
    *)      : ;;
  esac
fi

# --- Install application files ------------------------------------------------
if [ ! -f "$SRC/VERSION" ] && [ -f "$MONITORING_HOME/VERSION" ]; then
  warn "Downloaded sources have no VERSION file — they look OLDER than the installed $(cat "$MONITORING_HOME/VERSION"). Continuing anyway (use a newer branch for a real upgrade)."
fi
log "Installing application to $MONITORING_HOME…"
mkdir -p "$MONITORING_HOME"
cp -f "$SRC/app.py" "$MONITORING_HOME/app.py"
cp -f "$SRC/requirements.txt" "$MONITORING_HOME/requirements.txt" 2>/dev/null || true
cp -f "$SRC/VERSION" "$MONITORING_HOME/VERSION" 2>/dev/null || true
cp -f "$SRC/update.sh" "$MONITORING_HOME/update.sh" 2>/dev/null || true
cp -f "$SRC/uninstall.sh" "$MONITORING_HOME/uninstall.sh" 2>/dev/null || true
mkdir -p "$MONITORING_HOME/templates" "$MONITORING_HOME/static"
cp -f "$SRC/templates/"* "$MONITORING_HOME/templates/" 2>/dev/null || true
cp -f "$SRC/static/"* "$MONITORING_HOME/static/" 2>/dev/null || true

# --- Python environment (venv preferred, system pip fallback) ------------------
PYBIN=""
if python3 -m venv "$MONITORING_HOME/venv" >/dev/null 2>&1 && [ -x "$MONITORING_HOME/venv/bin/python" ]; then
  PYBIN="$MONITORING_HOME/venv/bin/python"
  log "Created virtualenv: $MONITORING_HOME/venv"
  log "Installing Python packages (flask, psutil)…"
  "$MONITORING_HOME/venv/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
  "$MONITORING_HOME/venv/bin/pip" install --quiet -r "$MONITORING_HOME/requirements.txt" \
    || die "pip install failed inside venv"
else
  warn "venv unavailable — falling back to system pip"
  if python3 -m pip install --quiet -r "$MONITORING_HOME/requirements.txt" --break-system-packages >/dev/null 2>&1; then
    :
  else
    python3 -m pip install --quiet -r "$MONITORING_HOME/requirements.txt" >/dev/null 2>&1 \
      || die "Could not install flask/psutil with system pip"
  fi
  PYBIN="$(command -v python3)"
fi

# --- Environment file -----------------------------------------------------------
cat > /etc/monitoring.env <<EOF
# Monitoring configuration (generated by install.sh)
MONITORING_HOME="$MONITORING_HOME"
MONITORING_PORT="$PORT"
PYBIN="$PYBIN"
EOF
chmod 644 /etc/monitoring.env

# --- Service definition -----------------------------------------------------------
SYSTEMD_UNIT="/etc/systemd/system/monitoring.service"
OPENRC_SCRIPT="/etc/init.d/monitoring"
USE_SYSTEMD=0; USE_OPENRC=0
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ] && ps -p 1 -o comm= 2>/dev/null | grep -q systemd; then USE_SYSTEMD=1;
elif command -v rc-service >/dev/null 2>&1; then USE_OPENRC=1; fi

if [ "$USE_SYSTEMD" -eq 1 ]; then
  log "Installing systemd service…"
  sed -e "s|__HOME__|${MONITORING_HOME}|g" -e "s|__PYBIN__|${PYBIN}|g" \
      -e "s|__ENVFILE__|/etc/monitoring.env|g" "$SRC/monitoring.service" > "$SYSTEMD_UNIT"
  systemctl daemon-reload
  systemctl enable monitoring >/dev/null 2>&1 || true
elif [ "$USE_OPENRC" -eq 1 ]; then
  log "Installing OpenRC service…"
  sed -e "s|__HOME__|${MONITORING_HOME}|g" "$SRC/openrc/monitoring" > "$OPENRC_SCRIPT"
  chmod 755 "$OPENRC_SCRIPT"
else
  warn "No systemd/OpenRC detected — the CLI wrapper will manage the process directly."
fi

# --- CLI wrapper --------------------------------------------------------------------
log "Installing CLI: /usr/local/bin/monitoring"
mkdir -p /usr/local/bin
sed -e "s|__HOME__|${MONITORING_HOME}|g" "$SRC/monitoring" > /usr/local/bin/monitoring
chmod 755 /usr/local/bin/monitoring

# --- Desktop app (standalone window, menu shortcut, autostart) -----------------------
log "Installing desktop app: monitoring-app + menu shortcut + autostart"
install -m 755 "$SRC/monitoring-app" /usr/local/bin/monitoring-app 2>/dev/null \
  || { cp -f "$SRC/monitoring-app" /usr/local/bin/monitoring-app && chmod 755 /usr/local/bin/monitoring-app; }
if [ -f "$SRC/static/icon.png" ]; then
  mkdir -p /usr/share/pixmaps /usr/share/applications /etc/xdg/autostart
  cp -f "$SRC/static/icon.png" /usr/share/pixmaps/monitoring.png
  cp -f "$SRC/monitoring-app.desktop" /usr/share/applications/monitoring.desktop
  cp -f "$SRC/monitoring-app.desktop" /etc/xdg/autostart/monitoring.desktop
  log "Menu shortcut installed (starts with your session)"
fi

# --- Firewall (best effort) -----------------------------------------------------------
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  log "Opening port $PORT/tcp in ufw"
  ufw allow "$PORT/tcp" >/dev/null 2>&1 || true
elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  log "Opening port $PORT/tcp in firewalld"
  firewall-cmd --permanent --add-port="$PORT/tcp" >/dev/null 2>&1 && firewall-cmd --reload >/dev/null 2>&1 || true
fi

# --- Start & health check ---------------------------------------------------------------
if [ "$START_SERVICE" -eq 1 ]; then
  log "Starting monitoring service…"
  if [ "$USE_SYSTEMD" -eq 1 ]; then
    systemctl restart monitoring
  elif [ "$USE_OPENRC" -eq 1 ]; then
    rc-service monitoring restart || rc-service monitoring start
  else
    /usr/local/bin/monitoring start
  fi
  sleep 2
  if curl -fsS "http://127.0.0.1:${PORT}/api/version" >/dev/null 2>&1; then
    log "Health check passed ✓"
  else
    warn "Health check did not respond yet (may still be starting)."
    warn "Check:  monitoring status   |   monitoring logs"
  fi
fi

VER="$(cat "$MONITORING_HOME/VERSION" 2>/dev/null || echo '?')"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
log "════════════════════════════════════════════════════════════"
log " Monitoring v${VER} installed successfully!"
log "════════════════════════════════════════════════════════════"
log " Dashboard : http://localhost:${PORT}   (binds 0.0.0.0)"
[ -n "$IP" ] && log "             http://${IP}:${PORT}   (from other machines)"
log " CLI       : monitoring {start|stop|restart|status|logs|update|version}"
log " Home      : ${MONITORING_HOME}"
log " Update    : curl -fsSL https://raw.githubusercontent.com/${REPO}/${BRANCH}/update.sh | sudo bash"
log "             (or: sudo ./update.sh from a checkout with your changes)"
log " Uninstall : ${MONITORING_HOME}/uninstall.sh"
echo
