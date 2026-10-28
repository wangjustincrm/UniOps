#!/usr/bin/env bash
# Scope check for Order Bridge work (design §14.3).
#
#   tools/check-scope.sh [BASE]        BASE defaults to HEAD
#
# Looks at everything that differs from BASE: commits BASE..HEAD, staged and
# unstaged changes, and untracked files. Fails when
#   1. a path is outside the code allow-list (§14.1) and the configuration
#      allow-list (§14.2);
#   2. an allow-listed configuration file loses or changes an existing line
#      (additions only);
#   3. any lock file other than order-bridge/package-lock.json changes —
#      including the root package-lock.json;
#   4. a new folder appears under packages/;
#   5. a platform folder imports business code (the two one-way-dependency
#      checks of §13: orders-api/app/platform and order-bridge/src/platform-ui).
# For order-bridge it also confirms that a deliberate type error inside
# src/platform-ui is reported by tsc (the TS5101 "diagnostics skipped" trap)
# and that the library's classes reach the built CSS (§13.15).
#
# Steps 5+ run in throwaway containers (python:3.12-slim, node:20-alpine) on a
# temporary copy; nothing is installed into the working tree.
#   SKIP_FRONTEND=1   skip the order-bridge install/typecheck/build checks
set -uo pipefail

ROOT=$(git rev-parse --show-toplevel)
BASE=${1:-HEAD}
cd "$ROOT"
git rev-parse --verify -q "$BASE^{commit}" >/dev/null || { echo "unknown base: $BASE"; exit 2; }

FAIL=0
fail() { echo "FAIL: $*"; FAIL=1; }
ok() { echo "ok:   $*"; }

# ── changed paths ────────────────────────────────────────────────────────────
mapfile -t CHANGED < <({
  git diff --name-only "$BASE"            # committed since BASE + working tree
  git ls-files --others --exclude-standard # untracked
} | sort -u)
echo "changed paths vs $BASE: ${#CHANGED[@]}"

# §14.1 code allow-list
CODE_RE='^(orders-api/|order-bridge/|tools/new-module/|tools/check-scope\.sh$)'
# Order Bridge documentation (not code; listed so it is visible)
DOCS_RE='^(docs/prd/ORDER-BRIDGE\.md|docs/superpowers/specs/2026-10-23-order-bridge-design\.md)$'
# §14.2 configuration allow-list — additions only
CONFIG_FILES=(
  docker-compose.dev.yml docker-compose.prod.yml Caddyfile
  .env.prod.example .env.lan.example epms-api/.env.example
  make-stack-env.sh migrate-prod.sh check-health.sh reset-db.sh run_tests.sh DEPLOYMENT.md
  portal/src/components/layout/navConfig.tsx portal/src/pages/PortalHome.tsx
  portal/src/lib/api.ts portal/src/pages/admin/AdminPanel.tsx
  identity-api/alembic/versions/0023_order_bridge_perms.py
  epms/src/lib/taskTypes.ts epms/src/services/tasks.ts epms-api/app/crud/task.py
  epms-api/app/knowledge/modules.yaml epms-api/tests/test_module_guide.py
)
SERVICE_CONFIG_RE='^(approval-api|booking-api|budget-api|epms-api|expense-api|file-api|finance-api|identity-api|inventory-api|mdm-api|mrp-api|vms-api)/app/core/config\.py$'
LOCK_RE='(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|npm-shrinkwrap\.json)$'

is_config() {
  local p=$1 f
  [[ $p =~ $SERVICE_CONFIG_RE ]] && return 0
  for f in "${CONFIG_FILES[@]}"; do [[ $p == "$f" ]] && return 0; done
  return 1
}

declare -a CONFIG_TOUCHED=()
for p in "${CHANGED[@]}"; do
  if [[ $p =~ $LOCK_RE && $p != order-bridge/package-lock.json ]]; then
    fail "lock file changed: $p"
  elif [[ $p =~ ^packages/ ]]; then
    top=$(echo "$p" | cut -d/ -f1-2)
    if ! git cat-file -e "$BASE:$top" 2>/dev/null; then
      fail "new folder under packages/: $top"
    else
      fail "change inside packages/: $p"
    fi
  elif [[ $p =~ $CODE_RE || $p =~ $DOCS_RE ]]; then
    :
  elif is_config "$p"; then
    CONFIG_TOUCHED+=("$p")
  else
    fail "outside the allow-lists: $p"
  fi
done

for p in "${CONFIG_TOUCHED[@]}"; do
  if git cat-file -e "$BASE:$p" 2>/dev/null; then
    removed=$(git diff -U0 "$BASE" -- "$p" | grep -E '^-' | grep -vE '^--- ' || true)
    if [[ -n $removed ]]; then
      fail "$p: existing lines removed or changed (additions only):"
      echo "$removed" | head -5 | sed 's/^/        /'
    else
      ok "$p: additions only"
    fi
  else
    ok "$p: new file"
  fi
done
[[ $FAIL == 0 ]] && ok "paths, lock files and packages/ within scope"

# ── one-way dependency: back end ─────────────────────────────────────────────
if [[ -d orders-api/app/platform ]]; then
  # -i: the checker arrives on stdin; without it python reads nothing and exits 0.
  if docker run -i --rm --network none -e PYTHONDONTWRITEBYTECODE=1 \
      -v "$ROOT/orders-api:/w:ro" -w /w python:3.12-slim \
      python - <<'PY'
import ast, sys, pathlib
sys.path.insert(0, "tests/platform")
# Same checker as tests/platform/test_one_way_dependency.py, without pytest.
src = pathlib.Path("tests/platform/test_one_way_dependency.py").read_text()
ns = {"__file__": "/w/tests/platform/test_one_way_dependency.py", "__name__": "scope_check"}
exec(compile(src, "test_one_way_dependency.py", "exec"), ns)
failures = 0
for name in ("test_platform_imports_no_business_code",
             "test_checker_catches_absolute_and_relative_business_imports",
             "test_no_order_bridge_names_in_platform_code"):
    try:
        ns[name]()
        print(f"  {name}: passed")
    except AssertionError as e:
        failures += 1
        print(f"  {name}: FAILED {e}")
print(f"  checks run: 3, failed: {failures}")
sys.exit(1 if failures else 0)
PY
  then ok "orders-api/app/platform imports no business code"
  else fail "orders-api/app/platform one-way dependency"
  fi
fi

# ── order-bridge: import rule, TS5101 trap, CSS ──────────────────────────────
if [[ -d order-bridge/src/platform-ui && ${SKIP_FRONTEND:-0} != 1 ]]; then
  TMP=$(mktemp -d)
  trap 'docker run --rm -v "$TMP:/t" alpine rm -rf /t/repo >/dev/null 2>&1; rm -rf "$TMP"' EXIT
  mkdir -p "$TMP/repo/packages"
  cp -r packages/shell "$TMP/repo/packages/shell"
  rsync -a --exclude node_modules --exclude dist order-bridge/ "$TMP/repo/order-bridge/"
  # A deliberate type error inside the library, in a separate copy of src.
  cat > "$TMP/repo/order-bridge/src/platform-ui/__scope_probe__.ts" <<'TS'
// written by tools/check-scope.sh: tsc must report this
export const probe: number = 'not a number'
TS
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e npm_config_cache=/tmp/npmcache \
    -v "$TMP/repo:/repo" -w /repo/order-bridge node:20-alpine sh -c '
      npm ci --install-links --no-audit --no-fund >/tmp/i.log 2>&1 || { echo "install failed"; tail -5 /tmp/i.log; exit 10; }
      npx tsc -p tsconfig.app.json --noEmit >/tmp/t.log 2>&1
      if grep -qE "TS5101|TS5107|TS5108" /tmp/t.log; then echo "deprecation config error: diagnostics may be skipped"; exit 11; fi
      grep -q "__scope_probe__.ts.*error TS2322" /tmp/t.log || { echo "the deliberate type error was NOT reported"; cat /tmp/t.log | head; exit 12; }
      others=$(grep "error TS" /tmp/t.log | grep -vc "__scope_probe__")
      [ "$others" = 0 ] || { echo "$others other type errors"; grep "error TS" /tmp/t.log | head; exit 13; }
      rm src/platform-ui/__scope_probe__.ts
      npx vitest run src/platform-ui/importRule.test.ts >/tmp/v.log 2>&1 || { echo "import rule failed"; tail -20 /tmp/v.log; exit 14; }
      npx eslint src/platform-ui >/tmp/e.log 2>&1 || { echo "eslint failed"; tail -20 /tmp/e.log; exit 15; }
      npx vite build >/tmp/b.log 2>&1 || { echo "build failed"; tail -10 /tmp/b.log; exit 16; }
      grep -q "radix-popover-trigger-width" dist/assets/*.css || { echo "platform-ui classes missing from the built CSS"; exit 17; }
      echo "probe reported; 0 other type errors; import rule + eslint pass; library classes in CSS"
    '
  rc=$?
  if [[ $rc == 0 ]]; then ok "order-bridge: TS5101 trap, import rule, ESLint, CSS"; else fail "order-bridge checks (code $rc)"; fi
fi

echo
if [[ $FAIL == 0 ]]; then echo "SCOPE CHECK PASSED"; else echo "SCOPE CHECK FAILED"; fi
exit $FAIL
