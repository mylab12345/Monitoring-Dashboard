#!/usr/bin/env bash
# ============================================================================
#  Monitoring — security migration self-test
#  Validates that the app no longer runs as root, uses argv-only subprocesses,
#  installs a non-login service account, and only exposes whitelisted sudo
#  helper commands (validated with visudo). Run as a normal user; root-only
#  checks are reported as SKIP without failing.
# ============================================================================
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0; SKIP=0
ok()   { echo "  PASS: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
skip() { echo "  SKIP: $1"; SKIP=$((SKIP+1)); }

echo "== Monitoring security migration checks =="

# 1. Syntax of shell/python scripts
HELPERS=( "$ROOT"/privileged/monitoring-* )
for f in app.py monitoring-app "$ROOT"/privileged/*.py "$ROOT"/monitor/*.py \
         install.sh update.sh uninstall.sh monitoring "$ROOT"/openrc/monitoring \
         "$ROOT"/tests/security_migration.sh "${HELPERS[@]}"; do
  [ -f "$f" ] || continue
  case "$f" in
    *.py|monitoring-app|*/privileged/*|*/monitor/*)
      (command -v python3 >/dev/null 2>&1 && python3 -m py_compile "$f") >/dev/null 2>&1 && ok "py_compile $(basename "$f")" || bad "py_compile $(basename "$f")" ;;
    *.sh|monitoring|*/openrc/*|*/tests/*) (command -v bash >/dev/null 2>&1 && bash -n "$f") >/dev/null 2>&1 && ok "bash -n $(basename "$f")" || bad "bash -n $(basename "$f")" ;;
  esac
done

# 2. No shell=True / no string commands in the app (app.py + monitor/*.py)
SHELL_VIOLATIONS=""
for py in "$ROOT/app.py" "$ROOT"/monitor/*.py; do
  [ -f "$py" ] || continue
  if grep -nE 'shell\s*=\s*True|(^|[^a-zA-Z0-9_])run\("[^"]|(^|[^a-zA-Z0-9_])run\(f"' "$py" >/dev/null 2>&1; then
    SHELL_VIOLATIONS="$SHELL_VIOLATIONS $py"
  fi
done
if [ -n "$SHELL_VIOLATIONS" ]; then
  grep -nE 'shell\s*=\s*True|(^|[^a-zA-Z0-9_])run\("[^"]|(^|[^a-zA-Z0-9_])run\(f"' $SHELL_VIOLATIONS
  bad "app still contains shell strings or shell=True"
else
  ok "app uses argv-only subprocess commands"
fi

# 3. systemd service runs as monitoring, not root
if grep -Eq '^User=monitoring$' "$ROOT/monitoring.service" && ! grep -Eq '^User=root$' "$ROOT/monitoring.service"; then
  ok "systemd unit runs as monitoring"
else
  bad "systemd unit is not configured for User=monitoring"
fi

# 4. sudoers fragment: absolute paths, no sudo ALL, NOPASSWD only
if grep -Eq 'NOPASSWD:[[:space:]]*ALL|ALL[[:space:]]*=[[:space:]]*.*ALL' "$ROOT/sudoers/monitoring"; then
  bad "sudoers fragment contains ALL"
else
  ok "sudoers fragment has no ALL"
fi
if grep -qE '^[^#[:space:]].*ALL=.*NOPASSWD:[[:space:]]*[/]' "$ROOT/sudoers/monitoring"; then
  ok "sudoers fragment uses absolute NOPASSWD commands"
else
  bad "sudoers fragment lacks absolute-path NOPASSWD entries"
fi

# 5. visudo validation (skip if visudo unavailable)
if command -v visudo >/dev/null 2>&1; then
  if visudo -cf "$ROOT/sudoers/monitoring" >/dev/null 2>&1; then
    ok "visudo validates sudoers/monitoring"
  else
    bad "visudo rejected sudoers/monitoring"
  fi
else
  skip "visudo not installed"
fi

# 6. Each privileged helper supports --check without touching the system
for h in "$ROOT/privileged"/monitoring-*; do
  if [ -x "$h" ]; then
    if "$h" --check >/dev/null 2>&1; then ok "$(basename "$h") --check"; else bad "$(basename "$h") --check"; fi
  else
    bad "$(basename "$h") is not executable"
  fi
done

# 7. Privilege check command exists in CLI
if grep -q "check-privileges" "$ROOT/monitoring"; then
  ok "monitoring CLI exposes check-privileges"
else
  bad "monitoring CLI lacks check-privileges"
fi

# 8. Installer references the dedicated account and visudo
if grep -q "MONITORING_USER=\"monitoring\"" "$ROOT/install.sh" \
   && grep -q "visudo -cf" "$ROOT/install.sh" \
   && grep -q "useradd --system --no-create-home" "$ROOT/install.sh"; then
  ok "installer creates the service account and validates sudoers"
else
  bad "installer account/visudo setup is incomplete"
fi

# 9. Subsystem group access (journal/logs/libvirt/docker/kvm) is configured
if grep -q 'systemd-journal adm libvirt docker kvm' "$ROOT/install.sh"; then
  ok "installer grants subsystem groups for read access"
else
  bad "installer does not configure subsystem group access"
fi

# 10. Privileged operations route through whitelisted helpers (app.py + monitor/)
APP_SRC="$(find "$ROOT/monitor" -name '*.py' 2>/dev/null) $ROOT/app.py"
for pat in 'monitoring-systemctl' 'monitoring-self-repair' 'monitoring-package' 'monitoring-vm' \
           'monitoring-qemu' 'monitoring-journal-vacuum' \
           'monitoring-clean-old-logs' 'monitoring-kill'; do
  if grep -q "$pat" $APP_SRC; then ok "app uses $pat helper"; else bad "app does not use $pat helper"; fi
done

# 11. Process/VM/repair paths must not call sudo directly
if grep -qE 'sudo[- ]+-n[^ ]*[[:space:]]+systemctl|sudo[- ]+-n[^ ]*[[:space:]]+virsh|sudo[- ]+-n[^ ]*[[:space:]]+qemu-img|sudo[- ]+-n[^ ]*[[:space:]]+apt|sudo[- ]+-n[^ ]*[[:space:]]+journalctl' $APP_SRC; then
  bad "app still uses raw sudo for privileged operations"
else
  ok "app has no raw sudo calls for systemd/virsh/qemu/apt/journalctl"
fi

# 12. Docker group is mentioned for read access when docker exists
if grep -q 'docker' "$ROOT/install.sh" && grep -q 'docker' "$ROOT/update.sh"; then
  ok "installer/updater account groups include Docker"
else
  bad "installer/updater do not include Docker group"
fi

echo
echo "PASS=$PASS FAIL=$FAIL SKIP=$SKIP"
[ "$FAIL" -eq 0 ]
