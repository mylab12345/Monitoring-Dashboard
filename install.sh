#!/usr/bin/env bash
# ============================================================================
#  Monitoring — Universal Linux Installer
# ----------------------------------------------------------------------------
#  Works on ANY Linux flavour: Debian/Ubuntu/Mint, Fedora/RHEL/Rocky/Alma,
#  openSUSE, Arch/Manjaro, Alpine, ...
#
#  Global one-liner (downloads latest from GitHub, then installs):
#      curl -fsSL https://raw.githubusercontent.com/mylab12345/Monitoring-Dashboard/main/install.sh | sudo bash
#
#  Local (from a checkout of this repo):
#      sudo bash install.sh
#
#  Options:
#      --port N        port to listen on            (default 8050)
#      --bind ADDR     address to bind              (default 0.0.0.0)
#      --token TOKEN   optional API access token (recommended on a network;
#                      all /api requests then require it). Pass "auto" to
#                      generate a random one.
#      --home DIR      install location             (default /opt/monitoring)
#      --no-vm         skip libvirt/qemu tooling
#      --no-start      install but do not start the service
#      --repo R        GitHub repo for remote mode  (default mylab12345/Monitoring-Dashboard)
#      --branch B      GitHub branch for remote mode (default main)
# ============================================================================
set -euo pipefail

# --- Defaults (overridable via flags / env) ---------------------------------
REPO="${REPO:-mylab12345/Monitoring-Dashboard}"
BRANCH="${BRANCH:-main}"
MONITORING_HOME="${MONITORING_HOME:-/opt/monitoring}"
PORT="${MONITORING_PORT:-8050}"
BIND="${MONITORING_BIND:-0.0.0.0}"
TOKEN="${MONITORING_TOKEN:-}"
WITH_VM=1
START_SERVICE=1
SELF_URL="https://raw.githubusercontent.com/${REPO}/${BRANCH}/install.sh"
APP_NAME="monitoring"
MONITORING_USER="monitoring"
MONITORING_GROUP="monitoring"
PRIVILEGE_DIR="/usr/local/lib/monitoring"
SUDOERS_FILE="/etc/sudoers.d/monitoring"
LOG_DIR="/var/log/monitoring"

log()  { printf '\033[1;32m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# --- Dedicated non-login service account --------------------------------------
NOLOGIN="$(command -v nologin || echo /usr/sbin/nologin)"

create_service_account() {
  if ! getent group "$MONITORING_GROUP" >/dev/null 2>&1; then
    if command -v groupadd >/dev/null 2>&1; then
      groupadd --system "$MONITORING_GROUP" || die "could not create group $MONITORING_GROUP"
    else
      warn "groupadd not found; creating $MONITORING_GROUP via addgroup"
      addgroup --system "$MONITORING_GROUP" 2>/dev/null || addgroup -S "$MONITORING_GROUP" || die "could not create group $MONITORING_GROUP"
    fi
  fi
  if ! id "$MONITORING_USER" >/dev/null 2>&1; then
    if command -v useradd >/dev/null 2>&1; then
      useradd --system --no-create-home --home-dir "$MONITORING_HOME" \
        --shell "$NOLOGIN" --gid "$MONITORING_GROUP" "$MONITORING_USER" \
        || die "could not create user $MONITORING_USER"
    elif command -v adduser >/dev/null 2>&1; then
      adduser -S -D -H -h "$MONITORING_HOME" -s "$NOLOGIN" -G "$MONITORING_GROUP" "$MONITORING_USER" \
        || die "could not create user $MONITORING_USER"
    else
      die "neither useradd nor adduser is available to create $MONITORING_USER"
    fi
    log "Created system user $MONITORING_USER (non-login, no home)"
  else
    log "Service account $MONITORING_USER already exists"
  fi
}

add_supplementary_groups() {
  # Read-only system info and optional subsystems are delegated to group access
  # instead of sudo. Add any groups that exist on this host.
  SUPP=""
  for g in systemd-journal adm libvirt docker kvm; do
    if getent group "$g" >/dev/null 2>&1; then
      SUPP="${SUPP:+${SUPP},}$g"
    fi
  done
  if [ -n "$SUPP" ]; then
    if command -v usermod >/dev/null 2>&1; then
      usermod -a -G "$SUPP" "$MONITORING_USER" >/dev/null 2>&1 || warn "could not add supplementary groups $SUPP"
      log "Added supplementary groups: $SUPP"
    fi
  fi
  printf '%s\n' "${SUPP:-}"
}

# --- Privileged helper install -------------------------------------------------
install_privileges() {
  local src_helper_dir="$SRC/privileged"
  [ -d "$src_helper_dir" ] || die "privileged/ helper directory missing from sources"
  mkdir -p "$PRIVILEGE_DIR"
  install -m 755 "$src_helper_dir"/monitoring-* "$PRIVILEGE_DIR/" \
    || { cp -f "$src_helper_dir"/monitoring-* "$PRIVILEGE_DIR/" && chmod 755 "$PRIVILEGE_DIR"/monitoring-*; }
  chown -R root:root "$PRIVILEGE_DIR"

  local sudo_src="$SRC/sudoers/monitoring"
  [ -f "$sudo_src" ] || die "sudoers/monitoring template missing from sources"
  install -m 440 "$sudo_src" "$SUDOERS_FILE" \
    || { cp -f "$sudo_src" "$SUDOERS_FILE" && chmod 440 "$SUDOERS_FILE"; }
  chown root:root "$SUDOERS_FILE"

  # visudo lives in /usr/sbin on Debian-family systems, which is often not on
  # the caller's PATH — resolve it explicitly before giving up.
  VISUDO_BIN="$(command -v visudo 2>/dev/null || { [ -x /usr/sbin/visudo ] && echo /usr/sbin/visudo; } || true)"
  if [ -z "$VISUDO_BIN" ]; then
    die "visudo not found; cannot safely install $SUDOERS_FILE"
  fi
  if visudo -cf "$SUDOERS_FILE" >/dev/null 2>&1; then
    log "sudoers validated with visudo ✓"
  elif "$VISUDO_BIN" -cf "$SUDOERS_FILE" >/dev/null 2>&1; then
    log "sudoers validated with visudo ✓"
  else
    "$VISUDO_BIN" -cf "$SUDOERS_FILE" || die "sudoers validation failed: $SUDOERS_FILE"
  fi
  # Best-effort proof that the service account can use its NOPASSWD rules.
  if command -v runuser >/dev/null 2>&1 && id "$MONITORING_USER" >/dev/null 2>&1; then
    if runuser -u "$MONITORING_USER" -- sudo -n -l >/dev/null 2>&1; then
      log "Service account passwordless sudo check: OK"
    else
      warn "Service account could not list passwordless sudo privileges (run: monitoring check-privileges)"
    fi
  elif command -v su >/dev/null 2>&1 && id "$MONITORING_USER" >/dev/null 2>&1; then
    if su -s /bin/sh -c 'sudo -n -l' "$MONITORING_USER" >/dev/null 2>&1; then
      log "Service account passwordless sudo check: OK"
    else
      warn "Service account could not list passwordless sudo privileges (run: monitoring check-privileges)"
    fi
  fi
}

# --- Parse flags -------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --port)     PORT="$2"; shift 2 ;;
    --bind)     BIND="$2"; shift 2 ;;
    --token)    TOKEN="$2"; shift 2 ;;
    --home)     MONITORING_HOME="$2"; shift 2 ;;
    --no-vm)    WITH_VM=0; shift ;;
    --no-start) START_SERVICE=0; shift ;;
    --repo)     REPO="$2"; shift 2 ;;
    --branch)   BRANCH="$2"; shift 2 ;;
    -h|--help)  sed -n '2,22p' "$0" 2>/dev/null || sed -n '2,22p' /dev/stdin; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

# --- Validate flags -----------------------------------------------------------
case "$PORT" in
  ''|*[!0-9]*) die "Invalid --port value: '$PORT' (must be 1-65535)" ;;
esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  die "Invalid --port value: '$PORT' (must be 1-65535)"
fi
case "$BIND" in
  ''|*[!0-9A-Za-z_.:\-*]*) die "Invalid --bind value: '$BIND'" ;;
esac
if [ -n "$TOKEN" ] && [ "$TOKEN" = "auto" ]; then
  if command -v openssl >/dev/null 2>&1; then
    TOKEN="$(openssl rand -hex 24)"
  else
    TOKEN="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  fi
  log "Generated random access token: $TOKEN"
fi

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
  # Download to a file first, then extract — piping straight into `tar` turns a
  # 404/network error into a misleading "gzip: unexpected end of file".
  TMP="$(mktemp -d /tmp/monitoring-install.XXXXXX)"
  url="https://github.com/${REPO}/archive/refs/heads/${BRANCH}.tar.gz"
  archive="$TMP/sources.tar.gz"
  log "Source: downloading ${REPO}@${BRANCH} from GitHub…"
  code="$(curl -fL -o "$archive" -w '%{http_code}' "$url" 2>/dev/null)" || {
    rm -rf "$TMP"
    die "Could not download sources (HTTP ${code:-no response}) from:
  $url
Check your internet connection, or verify REPO/BRANCH (got '${REPO}'/'${BRANCH}').
Alternatively clone the repo and run install.sh locally."
  }
  [ -s "$archive" ] || { rm -rf "$TMP"; die "Downloaded archive is empty (HTTP ${code:-?}) from: $url"; }
  tar -xzf "$archive" -C "$TMP" \
    || { rm -rf "$TMP"; die "Downloaded archive is not a valid tar.gz (corrupt download from: $url)."; }
  SRC="$(find "$TMP" -maxdepth 2 -name app.py -printf '%h\n' | head -1)"
  [ -n "$SRC" ] || { rm -rf "$TMP"; die "Downloaded archive does not contain app.py — is ${REPO}@${BRANCH} the monitoring repo?"; }
  log "Source extracted: $SRC"
fi

# --- Create dedicated service account -----------------------------------------
log "Setting up dedicated service account $MONITORING_USER…"
create_service_account
SUPP_GROUPS="$(add_supplementary_groups)"
install_privileges
mkdir -p "$LOG_DIR"
chown "$MONITORING_USER:$MONITORING_GROUP" "$LOG_DIR" 2>/dev/null || chown "$MONITORING_USER" "$LOG_DIR" 2>/dev/null || true
chmod 755 "$LOG_DIR"

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
  apt)    apt-get update -qq >/dev/null 2>&1 || warn "apt-get update failed — continuing with existing lists"
          install_pkgs python3 python3-venv python3-pip curl ca-certificates ;;
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
  # virsh needs a writable cache dir for the service account.
  mkdir -p "$MONITORING_HOME/.cache/libvirt"
  chown -R "$MONITORING_USER":"$MONITORING_GROUP" "$MONITORING_HOME/.cache"
fi

# Re-apply groups now that subsystem groups may have been created.
SUPP_GROUPS="$(add_supplementary_groups)"

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
mkdir -p "$MONITORING_HOME/templates" "$MONITORING_HOME/static" "$MONITORING_HOME/monitor"
cp -f "$SRC/templates/"* "$MONITORING_HOME/templates/" 2>/dev/null || true
# static/ and monitor/ are copied recursively: static holds the js/ subdir and
# monitor/ is the modular backend package (app.py is only the entrypoint).
cp -rf "$SRC/static/." "$MONITORING_HOME/static/" 2>/dev/null || true
cp -rf "$SRC/monitor/." "$MONITORING_HOME/monitor/" 2>/dev/null || true

# Sanity check: the new app.py depends on the `monitor/` package and the
# `static/js/` frontend. Fail loudly if the sources are incomplete rather than
# installing an app that cannot start.
if [ ! -f "$SRC/monitor/__init__.py" ]; then
  die "Sources are incomplete: '$SRC/monitor/__init__.py' not found. The checkout/archive is missing the modular backend — do a full \`git pull\` and re-run."
fi
if [ ! -f "$MONITORING_HOME/monitor/__init__.py" ]; then
  die "Install aborted: could not copy the 'monitor/' package into $MONITORING_HOME. Check permissions and re-run."
fi
if [ ! -f "$MONITORING_HOME/static/js/bootstrap.js" ]; then
  die "Install aborted: could not copy 'static/js/' into $MONITORING_HOME. Check permissions and re-run."
fi
# The code is owned root and is not writable by the service account.
chown -R root:"$MONITORING_GROUP" "$MONITORING_HOME" 2>/dev/null || chown -R root "$MONITORING_HOME" || true
chmod -R o+rX "$MONITORING_HOME" 2>/dev/null || true

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
# Preserve operator-set security settings from a previous install (re-runs of
# the installer/updater must never silently drop the access token or bind).
OLD_TOKEN=""; OLD_BIND=""; OLD_SERVER=""
if [ -f /etc/monitoring.env ]; then
  . /etc/monitoring.env 2>/dev/null || true
  OLD_TOKEN="${MONITORING_TOKEN:-}"; OLD_BIND="${MONITORING_BIND:-}"; OLD_SERVER="${MONITORING_SERVER:-}"
fi
[ -n "$TOKEN" ] || TOKEN="$OLD_TOKEN"
[ "$BIND" = "0.0.0.0" ] && [ -n "$OLD_BIND" ] && BIND="$OLD_BIND"
[ -z "${MONITORING_SERVER:-}" ] && MONITORING_SERVER="$OLD_SERVER"
cat > /etc/monitoring.env <<EOF
# Monitoring configuration (generated by install.sh)
MONITORING_HOME="$MONITORING_HOME"
MONITORING_PORT="$PORT"
MONITORING_BIND="$BIND"
MONITORING_TOKEN="$TOKEN"
MONITORING_SERVER="${MONITORING_SERVER:-waitress}"
PYBIN="$PYBIN"
MONITORING_PRIVILEGE_DIR="$PRIVILEGE_DIR"
MONITORING_USER="$MONITORING_USER"
MONITORING_LOG_FILE="$LOG_DIR/monitoring.log"
EOF
chmod 644 /etc/monitoring.env
chown root:root /etc/monitoring.env

# --- Service definition -----------------------------------------------------------
SYSTEMD_UNIT="/etc/systemd/system/monitoring.service"
OPENRC_SCRIPT="/etc/init.d/monitoring"
USE_SYSTEMD=0; USE_OPENRC=0
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ] && ps -p 1 -o comm= 2>/dev/null | grep -q systemd; then
  if systemctl daemon-reload >/dev/null 2>&1; then
    USE_SYSTEMD=1
  else
    warn "systemctl is present but the systemd bus is not operational (container/chroot?) — falling back to CLI-managed process"
  fi
elif command -v rc-service >/dev/null 2>&1; then USE_OPENRC=1; fi

if [ "$USE_SYSTEMD" -eq 1 ]; then
  log "Installing systemd service…"
  sed -e "s|__HOME__|${MONITORING_HOME}|g" -e "s|__PYBIN__|${PYBIN}|g" \
      -e "s|__ENVFILE__|/etc/monitoring.env|g" "$SRC/monitoring.service" > "$SYSTEMD_UNIT"
  chown root:root "$SYSTEMD_UNIT"
  chmod 644 "$SYSTEMD_UNIT"
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
# The dashboard can control the whole machine (kill processes, manage
# services/VMs, run package upgrades). Only expose it when the operator
# explicitly asked for a non-loopback bind, and warn loudly when it is
# exposed without an access token.
EXPOSED=0
if [ "$BIND" != "127.0.0.1" ] && [ "$BIND" != "::1" ]; then EXPOSED=1; fi
if [ "$EXPOSED" -eq 1 ] && [ -z "$TOKEN" ]; then
  warn "######################################################################"
  warn "# SECURITY: binding 0.0.0.0 with NO access token."
  warn "# Anyone who can reach port $PORT gets FULL CONTROL of this machine."
  warn "# Re-run with:  sudo bash install.sh --token <password>"
  warn "# (or set MONITORING_TOKEN in /etc/monitoring.env) to protect it."
  warn "######################################################################"
fi
if [ "$EXPOSED" -eq 1 ]; then
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    log "Opening port $PORT/tcp in ufw"
    ufw allow "$PORT/tcp" >/dev/null 2>&1 || true
  elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    log "Opening port $PORT/tcp in firewalld"
    firewall-cmd --permanent --add-port="$PORT/tcp" >/dev/null 2>&1 && firewall-cmd --reload >/dev/null 2>&1 || true
  fi
fi

# --- Start & health check ---------------------------------------------------------------
if [ "$START_SERVICE" -eq 1 ]; then
  log "Starting monitoring service…"
  if [ "$USE_SYSTEMD" -eq 1 ]; then
    systemctl restart monitoring \
      || { warn "systemctl restart failed — falling back to CLI-managed start"; /usr/local/bin/monitoring start; }
  elif [ "$USE_OPENRC" -eq 1 ]; then
    rc-service monitoring restart || rc-service monitoring start \
      || { warn "rc-service failed — falling back to CLI-managed start"; /usr/local/bin/monitoring start; }
  else
    /usr/local/bin/monitoring start
  fi
  sleep 2
  CURL_AUTH=()
  if [ -n "$TOKEN" ]; then CURL_AUTH=(-H "Authorization: Bearer $TOKEN"); fi
  if curl -fsS "${CURL_AUTH[@]}" "http://127.0.0.1:${PORT}/api/version" >/dev/null 2>&1; then
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
log " Dashboard : http://localhost:${PORT}   (binds ${BIND})"
[ -n "$IP" ] && [ "$EXPOSED" -eq 1 ] && log "             http://${IP}:${PORT}   (from other machines)"
[ -n "$TOKEN" ] && log " Access token: ${TOKEN}  (required by every API request)"
log " CLI       : monitoring {start|stop|restart|status|logs|update|version}"
log " Home      : ${MONITORING_HOME}"
log " Update    : curl -fsSL https://raw.githubusercontent.com/${REPO}/${BRANCH}/update.sh | sudo bash"
log "             (or: sudo ./update.sh from a checkout with your changes)"
log " Uninstall : ${MONITORING_HOME}/uninstall.sh"
echo
