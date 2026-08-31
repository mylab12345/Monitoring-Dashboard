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

# 3. Frontend parse check — a syntax error in the inline app script aborts the
#    entire dashboard at parse time (every tab renders as empty skeletons), and
#    no amount of static string matching catches it. Requires node; skipped
#    with a warning when unavailable.
NODE=""
for cand in node nodejs; do
  if command -v "$cand" >/dev/null 2>&1; then NODE="$cand"; break; fi
done
if [ -n "$NODE" ]; then
  echo
  echo "──────────────────────────────────────────────────────────────"
  echo "▶ Frontend JS parse check (templates/index.html)"
  echo "──────────────────────────────────────────────────────────────"
  TMPJS="$(mktemp -t monitoring-frontend-XXXXXX.js)"
  if "$PY" - "$ROOT" "$TMPJS" <<'PYEOF'
import re, sys
root, out = sys.argv[1], sys.argv[2]
with open(f"{root}/templates/index.html") as fh:
    html = fh.read()
scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
if not scripts:
    sys.exit("no <script> block found in templates/index.html")
with open(out, "w") as fh:
    fh.write(scripts[-1])
PYEOF
  then
    if "$NODE" --check "$TMPJS"; then
      echo "✓ inline app script parses"
    else
      echo "✗ templates/index.html inline JS has a syntax error"
      FAILED=1
    fi
  else
    echo "✗ could not extract the inline script"
    FAILED=1
  fi
  rm -f "$TMPJS"
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
