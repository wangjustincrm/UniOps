#!/usr/bin/env bash
# UniOps — Unified Health Check
# Usage:
#   ./check-health.sh                         (local dev, all services on localhost)
#   EPMS_HOST=app.internal ./check-health.sh  (remote server check)

HOST=${EPMS_HOST:-localhost}
PASS=0
FAIL=0

ok() {
  if curl -sf "http://${HOST}:$2/health" >/dev/null 2>&1; then
    echo "✅ $1  http://${HOST}:$2/health"
    PASS=$((PASS + 1))
  else
    echo "❌ $1  http://${HOST}:$2/health  NOT RESPONDING"
    FAIL=$((FAIL + 1))
  fi
}

dep() {
  local label="$1" url="$2" field="$3"
  local result
  result=$(curl -sf "$url" 2>/dev/null)
  if [ $? -ne 0 ]; then
    echo "❌ $label  $url  NOT RESPONDING"
    FAIL=$((FAIL + 1))
    return
  fi
  local status
  status=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('$field','?'))" 2>/dev/null)
  if [ "$status" = "ok" ]; then
    echo "✅ $label  ($field: connected)"
    PASS=$((PASS + 1))
  else
    local detail
    detail=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('detail','unknown'))" 2>/dev/null)
    echo "❌ $label  $field: $detail"
    FAIL=$((FAIL + 1))
  fi
}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  UniOps Health Check — host: ${HOST}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
echo "── Backend Services ──────────────────────"
ok_path() {
  if curl -sf "http://${HOST}:$2$3" >/dev/null 2>&1; then
    echo "✅ $1  http://${HOST}:$2$3"
    PASS=$((PASS + 1))
  else
    echo "❌ $1  http://${HOST}:$2$3  NOT RESPONDING"
    FAIL=$((FAIL + 1))
  fi
}
ok_path "epms-api      " 8000 "/api/v1/health"
ok "approval-api  " 8003
ok "mdm-api       " 8002
ok "finance-api   " 8004
ok "file-api      " 8005
ok "expense-api   " 8006
ok "budget-api    " 8007
ok "vms-api       " 8008
ok "mrp-api       " 8011

echo ""
echo "── Dependencies (via epms-api) ───────────"
dep "postgres" "http://${HOST}:8000/api/v1/health/db"    "db"
dep "redis   " "http://${HOST}:8000/api/v1/health/redis" "redis"

echo ""
echo "── Frontend ──────────────────────────────"
for fport in 5173 5174 5175 5176; do
  fname=""
  [ "$fport" = "5173" ] && fname="epms-frontend "
  [ "$fport" = "5174" ] && fname="portal        "
  [ "$fport" = "5175" ] && fname="oa-frontend   "
  [ "$fport" = "5176" ] && fname="vms-frontend  "
  if curl -sf "http://${HOST}:${fport}" >/dev/null 2>&1; then
    echo "✅ $fname http://${HOST}:${fport}"
  else
    echo "⚠️  $fname http://${HOST}:${fport}  (may not be running)"
  fi
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Result: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] && echo "  Status: ALL HEALTHY ✅" || echo "  Status: ISSUES DETECTED ❌"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

exit "$FAIL"
