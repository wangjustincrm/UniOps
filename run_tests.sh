#!/usr/bin/env bash
# UniOps — Local Test Runner
# Usage: ./run_tests.sh [--smoke-only] [--unit-only]
# Note: Unit tests (pytest) do NOT require services to be running.
#       Smoke tests (test_all.py) require all services running.

set -e

CYAN="\033[96m"; GREEN="\033[92m"; RED="\033[91m"; YELLOW="\033[93m"; RESET="\033[0m"
ROOT="$(dirname "$0")"

section() { echo -e "\n${CYAN}══ $1 ══${RESET}"; }
pass()    { echo -e "${GREEN}✅ $1${RESET}"; }
warn()    { echo -e "${YELLOW}⚠️  $1${RESET}"; }
fail()    { echo -e "${RED}❌ $1${RESET}"; exit 1; }

# Detect Python from .venv (Windows and Unix compatible)
find_python() {
  local dir="$1"
  if [ -f "$dir/.venv/Scripts/python" ]; then
    echo "$dir/.venv/Scripts/python"
  elif [ -f "$dir/.venv/Scripts/python.exe" ]; then
    echo "$dir/.venv/Scripts/python.exe"
  elif [ -f "$dir/.venv/bin/python" ]; then
    echo "$dir/.venv/bin/python"
  else
    echo ""
  fi
}

SMOKE_ONLY=0
UNIT_ONLY=0
for arg in "$@"; do
  [ "$arg" = "--smoke-only" ] && SMOKE_ONLY=1
  [ "$arg" = "--unit-only" ]  && UNIT_ONLY=1
done

# ── Unit Tests (pytest, no services needed) ────────────────────────────────

if [ "$SMOKE_ONLY" -eq 0 ]; then
  section "EPMS API — pytest (unit + integration)"

  PYTHON=$(find_python "$ROOT/epms-api")
  if [ -z "$PYTHON" ]; then
    warn "No .venv found in epms-api/. Run: cd epms-api && python -m venv .venv && pip install -r requirements-dev.txt"
    warn "Skipping pytest."
  else
    cd "$ROOT/epms-api"
    "$PYTHON" -m pytest tests/ -q --tb=short 2>&1 \
      && pass "EPMS API pytest: all tests passed" \
      || fail "EPMS API pytest: tests failed"
    cd "$ROOT"
  fi

  # Posting spine + identity + mdm suites — conftests default to the local
  # docker postgres (localhost:5432), independent of each service's .env.
  export TEST_DATABASE_URL="postgresql+asyncpg://epms:epms_dev@localhost:5432/mdm_test"
  export REDIS_HOST=localhost
  for svc in finance-api approval-api identity-api mdm-api; do
    section "$svc — pytest"
    PYTHON=$(find_python "$ROOT/$svc")
    if [ -z "$PYTHON" ]; then
      warn "No .venv found in $svc/. Run: cd $svc && python -m venv .venv && pip install -r requirements.txt -r requirements-dev.txt"
      warn "Skipping pytest."
    else
      cd "$ROOT/$svc"
      "$PYTHON" -m pytest tests/ -q --tb=short 2>&1 \
        && pass "$svc pytest: all tests passed" \
        || fail "$svc pytest: tests failed"
      cd "$ROOT"
    fi
  done
fi

# ── Smoke Tests (requires all services running) ────────────────────────────

if [ "$UNIT_ONLY" -eq 0 ]; then
  section "UniOps — Smoke Tests (requires all services running)"

  if ! curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    warn "epms-api not reachable. Start services first:"
    warn "  docker compose -f docker-compose.dev.yml up"
    warn "Skipping smoke tests."
  else
    cd "$ROOT"
    python test_all.py \
      && pass "Smoke tests: all passed" \
      || warn "Smoke tests: some failed (check service logs)"
  fi
fi

echo ""
echo "Done."
