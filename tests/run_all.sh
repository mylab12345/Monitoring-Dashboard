#!/usr/bin/env bash
# ============================================================================
#  Monitoring — full regression suite
#  Runs: security migration self-test + API/validation unit tests + syntax
#  checks for every script + dependency audit (when pip-audit is available).
#  Exits non-zero on the first failing suite.
# ============================================================================
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAILED=0

# Prefer a Python that can actually import the app (venv, installed copy, or
# system python3); API tests self-skip when flask is unavailable.
PY=""
for cand in "$ROOT/venv/bin/python" "$ROOT/.venv/bin/python" /opt/monitoring/venv/bin/python python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import flask" >/dev/null 2>&1; then
    PY="$cand"; break
  fi
done
[ -n "$PY" ] || PY="$(command -v python3)"

run() {
  echo
  echo "──────────────────────────────────────────────────────────────"
  echo "▶ $*"
  echo "──────────────────────────────────────────────────────────────"
  if ! bash -c "$*"; then
    echo "✗ FAILED: $*"
    FAILED=1
  fi
}

# 1. The project's own security migration self-test
run "cd '$ROOT' && bash tests/security_migration.sh"

# 2. API / validation / helper / repo-consistency regression tests
run "cd '$ROOT' && '$PY' -m unittest discover -s tests -p 'test_*.py' -v"

# 3. Frontend parse check — the app script is split across static/js/*.js, each
#    of which is a separate parse unit, so a syntax error in one file can no
#    longer abort the whole dashboard. Every file must still parse cleanly.
#    Requires node; skipped with a warning when unavailable.
NODE=""
for cand in node nodejs; do
  if command -v "$cand" >/dev/null 2>&1; then NODE="$cand"; break; fi
done
if [ -n "$NODE" ]; then
  echo
  echo "──────────────────────────────────────────────────────────────"
  echo "▶ Frontend JS parse check (static/js/*.js)"
  echo "──────────────────────────────────────────────────────────────"
  FAIL_JS=0
  for f in "$ROOT"/static/js/*.js; do
    [ -f "$f" ] || continue
    if "$NODE" --check "$f"; then
      echo "✓ $(basename "$f") parses"
    else
      echo "✗ $(basename "$f") has a syntax error"
      FAIL_JS=1
    fi
  done
  if [ "$FAIL_JS" -eq 0 ]; then
    echo "✓ all frontend JS files parse"
  else
    FAILED=1
  fi
else
  echo
  echo "⚠ node not available — skipping frontend parse check"
fi

# 4. Dependency audit (best effort — requires pip-audit)
AUDIT=""
for cand in pip-audit "$ROOT/venv/bin/pip-audit" /opt/monitoring/venv/bin/pip-audit; do
  if command -v "$cand" >/dev/null 2>&1 || [ -x "$cand" ]; then AUDIT="$cand"; break; fi
done
if [ -n "$AUDIT" ]; then
  echo
  echo "──────────────────────────────────────────────────────────────"
  echo "▶ Dependency audit (pip-audit)"
  echo "──────────────────────────────────────────────────────────────"
  "$AUDIT" -r "$ROOT/requirements.txt" || { echo "✗ pip-audit found vulnerabilities"; FAILED=1; }
else
  echo
  echo "⚠ pip-audit not available — skipping dependency audit"
fi

if [ "$FAILED" -eq 0 ]; then
  echo
  echo "✅ ALL REGRESSION SUITES PASSED"
else
  echo
  echo "❌ ONE OR MORE SUITES FAILED"
fi
exit "$FAILED"
