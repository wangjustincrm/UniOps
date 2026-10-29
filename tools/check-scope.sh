#!/usr/bin/env bash
# Scope check for Sales module work (Order Bridge is its first part) (design §14.3).
#
#   tools/check-scope.sh [BASE]
#
# BASE defaults to merge-base(origin/main, HEAD) (or merge-base(main, HEAD)
# when there is no origin/main); a BASE given is also reduced to its merge
# base with HEAD. The base used is printed first.
#
# Looks at everything that differs from the base: commits base..HEAD, the
# index, the working tree and untracked files — renames count as delete + add.
# Fails when
#   1. a path is outside the code allow-list (§14.1) and the configuration
#      allow-list (§14.2);
#   2. an allow-listed configuration file loses or changes an existing line
#      in any of HEAD, the index or the working tree (additions only) —
#      except the lines named in LINE_EDITS, each of which may gain exactly
#      its listed text and nothing else;
#   3. any lock file other than sales/package-lock.json changes —
#      including the root package-lock.json;
#   4. anything under packages/ changes or appears;
#   5. a platform folder is missing, or imports business code (the two
#      one-way-dependency checks of §13: sales-api/app/platform and
#      sales/src/platform-ui).
# For sales it also confirms that a deliberate type error inside
# src/platform-ui is reported by tsc (the TS5101 "diagnostics skipped" trap),
# that the ESLint import rule fires on a planted business import, and that
# both a library class and a shell-only class reach the built CSS (§13.15).
#
# Steps 5+ run in throwaway containers (python:3.12-slim, node:20-alpine) on a
# temporary copy; nothing is installed into the working tree.
#   SKIP_FRONTEND=1   skip the sales install/typecheck/build checks
#                     (printed as SKIPPED; the result says so)
set -uo pipefail

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
git() { command git -c core.quotePath=false "$@"; }

if [[ $# -ge 1 ]]; then
  REF=$1
elif command git rev-parse --verify -q origin/main >/dev/null; then
  REF=origin/main
else
  REF=main
fi
git rev-parse --verify -q "$REF^{commit}" >/dev/null || { echo "unknown base: $REF"; exit 2; }
BASE=$(git merge-base "$REF" HEAD) || { echo "no merge base between $REF and HEAD"; exit 2; }
echo "base: $BASE (merge-base of $REF and HEAD; $(git rev-list --count "$BASE"..HEAD) commit(s) ahead)"

FAIL=0
SKIPPED=0
fail() { echo "FAIL: $*"; FAIL=1; }
ok() { echo "ok:   $*"; }
skip() { echo "SKIPPED: $*"; SKIPPED=1; }

# ── changed paths ────────────────────────────────────────────────────────────
# NUL-separated, so no file name is ever quoted or split.
mapfile -d '' -t CHANGED < <({
  git diff --no-renames --name-only -z "$BASE" HEAD   # committed since the base
  git diff --no-renames --name-only -z --cached "$BASE"   # the index
  git diff --no-renames --name-only -z "$BASE"        # the working tree
  git ls-files --others --exclude-standard -z         # untracked
} | sort -zu)
# A path the branch added after the base and has since removed again (e.g. a
# folder committed, then renamed) is no change against the base: it is in the
# committed diff but in neither the base, the index nor the working tree.
declare -a KEPT=()
NETZERO=0
for p in "${CHANGED[@]}"; do
  if ! git cat-file -e "$BASE:$p" 2>/dev/null && ! git ls-files --error-unmatch -- "$p" >/dev/null 2>&1 && [[ ! -e $p ]]; then
    NETZERO=$((NETZERO + 1))
  else
    KEPT+=("$p")
  fi
done
CHANGED=("${KEPT[@]}")
echo "changed paths vs base: ${#CHANGED[@]} (plus $NETZERO added and removed again since the base)"

# §14.1 code allow-list
CODE_RE='^(sales-api/|sales/|tools/new-module/|tools/check-scope\.sh$)'
# Sales / Order Bridge documentation (history file names kept; not code; listed so it is visible)
DOCS_RE='^(docs/prd/ORDER-BRIDGE\.md|docs/prd/README\.md|docs/superpowers/specs/2026-10-23-order-bridge-design\.md)$'
# §14.2 configuration allow-list — additions only
CONFIG_FILES=(
  docker-compose.dev.yml docker-compose.prod.yml Caddyfile
  .env.prod.example .env.lan.example epms-api/.env.example
  make-stack-env.sh migrate-prod.sh check-health.sh reset-db.sh run_tests.sh DEPLOYMENT.md
  portal/src/components/layout/navConfig.tsx portal/src/pages/PortalHome.tsx
  portal/src/lib/api.ts portal/src/pages/admin/AdminPanel.tsx
  identity-api/alembic/versions/0023_sales_perms.py
  epms/src/lib/taskTypes.ts epms/src/services/tasks.ts epms-api/app/crud/task.py
  epms-api/app/knowledge/modules.yaml epms-api/tests/test_module_guide.py
)
SERVICE_CONFIG_RE='^(approval-api|booking-api|budget-api|epms-api|expense-api|file-api|finance-api|identity-api|inventory-api|mdm-api|mrp-api|vms-api)/app/core/config\.py$'
# Listed line edits (P0, 2026-10-29). A few existing lines hold a whole list on
# one line (CORS origin strings, import lists); the Sales entry can only be
# appended to them. Each such line is named here with the exact text it may
# gain, inserted right before the LAST occurrence of the anchor in the base
# line. Any other change to these lines, and any change to any other existing
# line, still fails; an entry whose anchor no longer matches its base line
# fails too (the line moved — re-check it by hand).
#   path <TAB> line number in the base <TAB> anchor <TAB> inserted text
LINE_EDITS=$(cat <<'EOF'
docker-compose.dev.yml	111	]'	,"http://${LAN_HOST:-localhost}:${SALES_PORT:-5182}"
docker-compose.dev.yml	165	]'	,"http://${LAN_HOST:-localhost}:${SALES_PORT:-5182}"
docker-compose.dev.yml	193	]'	,"http://${LAN_HOST:-localhost}:${SALES_PORT:-5182}"
docker-compose.dev.yml	245	]'	,"http://${LAN_HOST:-localhost}:${SALES_PORT:-5182}"
docker-compose.dev.yml	284	]'	,"http://${LAN_HOST:-localhost}:${SALES_PORT:-5182}"
docker-compose.dev.yml	328	]'	,"http://${LAN_HOST:-localhost}:${SALES_PORT:-5182}"
docker-compose.dev.yml	360	]'	,"http://${LAN_HOST:-localhost}:${SALES_PORT:-5182}"
docker-compose.dev.yml	398	]'	,"http://${LAN_HOST:-localhost}:${SALES_PORT:-5182}"
docker-compose.dev.yml	432	]'	,"http://${LAN_HOST:-localhost}:${SALES_PORT:-5182}"
docker-compose.dev.yml	494	]'	,"http://${LAN_HOST:-localhost}:${SALES_PORT:-5182}"
docker-compose.dev.yml	547	]'	,"http://${LAN_HOST:-localhost}:${SALES_PORT:-5182}"
docker-compose.dev.yml	589	]'	,"http://${LAN_HOST:-localhost}:${SALES_PORT:-5182}"
.env.prod.example	52	]	,"https://sales.canadaroyalmilk.com"
.env.lan.example	38	]	,"http://10.10.50.65:5182"
epms-api/.env.example	10	]	,"http://localhost:5182"
epms-api/tests/test_module_guide.py	70	))	, "sales"
portal/src/components/layout/navConfig.tsx	26	 }	, SALES_URL
portal/src/lib/api.ts	391	]	, SALES_URL
portal/src/pages/PortalHome.tsx	9	, encodeSession }	, SALES_URL
portal/src/pages/PortalHome.tsx	15	, MRP_URL }	, SALES_ACCESS_PERMS
EOF
)
# Compares a base file with a new one: unchanged and added lines pass; a base
# line that is gone or changed passes only as its listed edit (stdlib only).
read -r -d '' LINE_EDIT_CHECK <<'PY'
import difflib, os, sys
path, base_f, new_f = sys.argv[1:4]
edits = {}
for row in os.environ["LINE_EDITS"].splitlines():
    if row.strip():
        p, n, anchor, ins = row.split("\t")
        if p == path:
            edits[int(n)] = (anchor, ins)
# Line endings are normalised, as git diff does under core.autocrlf.
read = lambda f: open(f, encoding="utf-8", newline="").read().replace("\r\n", "\n").split("\n")
base, new = read(base_f), read(new_f)
bad, expect = [], {}
for n, (anchor, ins) in sorted(edits.items()):
    line = base[n - 1] if 0 < n <= len(base) else ""
    if anchor not in line:
        bad.append(f"listed line {n} no longer matches the base (anchor {anchor!r})")
        continue
    k = line.rindex(anchor)
    expect[n - 1] = line[:k] + ins + line[k:]
for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, base, new, autojunk=False).get_opcodes():
    if tag in ("equal", "insert"):
        continue
    rest = iter(new[j1:j2])
    for i in range(i1, i2):
        want = expect.get(i)
        if want is None:
            bad.append(f"line {i + 1} removed or changed: {base[i][:100]!r}")
        elif not any(x == want for x in rest):
            bad.append(f"line {i + 1} changed other than its listed edit: {base[i][:100]!r}")
for b in bad:
    print(b)
sys.exit(1 if bad else 0)
PY
export LINE_EDITS
has_line_edits() { grep -q "^$1	" <<<"$LINE_EDITS"; }
LOCK_RE='(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|npm-shrinkwrap\.json)$'

is_config() {
  local p=$1 f
  [[ $p =~ $SERVICE_CONFIG_RE ]] && return 0
  for f in "${CONFIG_FILES[@]}"; do [[ $p == "$f" ]] && return 0; done
  return 1
}

declare -a CONFIG_TOUCHED=()
for p in "${CHANGED[@]}"; do
  if [[ $p =~ $LOCK_RE && $p != sales/package-lock.json ]]; then
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

# Deleted lines counted with --numstat: a removed line that itself starts
# with "-- " (SQL, YAML comments) must not be mistaken for a diff header.
deleted_lines() {  # $@ = git diff arguments before "--"
  git diff --no-renames --numstat "$@" -- "$P" | awk '{ s += ($2 == "-") ? 1 : $2 } END { print s + 0 }'
}
line_edits_in() {  # $1 = label, $2 = file holding that state's content
  local out
  if ! out=$(python3 -I -c "$LINE_EDIT_CHECK" "$P" "$BASE_COPY" "$2"); then
    fail "$P ($1): $(head -1 <<<"$out")"
    tail -n +2 <<<"$out" | head -4 | sed 's/^/        /'
    return 1
  fi
}
SCRATCH=$(mktemp -d)
for P in "${CONFIG_TOUCHED[@]}"; do
  if git cat-file -e "$BASE:$P" 2>/dev/null && has_line_edits "$P"; then
    BASE_COPY=$SCRATCH/base; git show "$BASE:$P" > "$BASE_COPY"
    git show "HEAD:$P" > "$SCRATCH/head" 2>/dev/null || : > "$SCRATCH/head"
    git show ":$P" > "$SCRATCH/index" 2>/dev/null || : > "$SCRATCH/index"
    if [[ -e $P ]]; then cp "$P" "$SCRATCH/tree"; else : > "$SCRATCH/tree"; fi
    n=$(grep -c "^$P	" <<<"$LINE_EDITS")
    line_edits_in committed "$SCRATCH/head" && line_edits_in staged "$SCRATCH/index" \
      && line_edits_in "working tree" "$SCRATCH/tree" \
      && ok "$P: additions only, plus its $n listed line edit(s)"
  elif git cat-file -e "$BASE:$P" 2>/dev/null; then
    d_head=$(deleted_lines "$BASE" HEAD)
    d_index=$(deleted_lines --cached "$BASE")
    d_tree=$( [[ -e $P ]] && deleted_lines "$BASE" || wc -l < <(git show "$BASE:$P") )
    if [[ $d_head != 0 || $d_index != 0 || $d_tree != 0 ]]; then
      fail "$P: existing lines removed or changed (committed $d_head, staged $d_index, working tree $d_tree):"
      git diff --no-renames -U0 "$BASE" -- "$P" | sed -n '5,$p' | grep -E '^-' | head -5 | sed 's/^/        /'
    else
      ok "$P: additions only"
    fi
  else
    ok "$P: new file"
  fi
done
rm -rf "$SCRATCH"
[[ $FAIL == 0 ]] && ok "paths, lock files and packages/ within scope"

# ── one-way dependency: back end ─────────────────────────────────────────────
if [[ ! -d sales-api/app/platform ]]; then
  fail "sales-api/app/platform is missing: the one-way dependency check cannot run"
else
  # -i: the checker arrives on stdin; without it python reads nothing and exits 0.
  if docker run -i --rm --network none -e PYTHONDONTWRITEBYTECODE=1 \
      -v "$ROOT/sales-api:/w:ro" -w /w python:3.12-slim \
      python - <<'PY'
import ast, sys, pathlib
sys.path.insert(0, "tests/platform")
# Same checker as tests/platform/test_one_way_dependency.py, without pytest.
src = pathlib.Path("tests/platform/test_one_way_dependency.py").read_text()
ns = {"__file__": "/w/tests/platform/test_one_way_dependency.py", "__name__": "scope_check"}
exec(compile(src, "test_one_way_dependency.py", "exec"), ns)
failures = 0
NAMES = ("test_platform_imports_no_business_code",
         "test_checker_catches_absolute_and_relative_business_imports",
         "test_integrations_import_only_platform_and_themselves",
         "test_integration_checker_flags_upper_layers_and_allows_platform",
         "test_no_sales_names_in_platform_code")
for name in NAMES:
    try:
        ns[name]()
        print(f"  {name}: passed")
    except AssertionError as e:
        failures += 1
        print(f"  {name}: FAILED {e}")
print(f"  checks run: {len(NAMES)}, failed: {failures}")
sys.exit(1 if failures else 0)
PY
  then ok "sales-api layers: platform imports no business code; integrations import no domain/services/api"
  else fail "sales-api one-way dependency (platform / integrations)"
  fi
fi

# ── sales: import rule, TS5101 trap, CSS ──────────────────────────────
# A class that only packages/shell uses (the primary Button's hover): if it is
# in the built CSS, Tailwind scanned the shell source (§13.15).
SHELL_SENTINEL='hover:bg-primary-700'
if [[ ! -d sales/src/platform-ui ]]; then
  fail "sales/src/platform-ui is missing: the front-end checks cannot run"
elif [[ ${SKIP_FRONTEND:-0} == 1 ]]; then
  skip "sales install / tsc probe / import rule / ESLint probe / build / CSS (SKIP_FRONTEND=1)"
else
  if grep -rqF -- "$SHELL_SENTINEL" sales/src; then
    fail "shell CSS sentinel '$SHELL_SENTINEL' is used in sales/src; pick another shell-only class"
  fi
  TMP=$(mktemp -d)
  trap 'docker run --rm -v "$TMP:/t" alpine rm -rf /t/repo >/dev/null 2>&1; rm -rf "$TMP"' EXIT
  mkdir -p "$TMP/repo/packages"
  cp -r packages/shell "$TMP/repo/packages/shell"
  rsync -a --exclude node_modules --exclude dist sales/ "$TMP/repo/sales/"
  # A deliberate type error inside the library, in a separate copy of src.
  cat > "$TMP/repo/sales/src/platform-ui/__scope_probe__.ts" <<'TS'
// written by tools/check-scope.sh: tsc must report this
export const probe: number = 'not a number'
TS
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e npm_config_cache=/tmp/npmcache \
    -e SHELL_SENTINEL="$SHELL_SENTINEL" -e SHELL_SELECTOR="${SHELL_SENTINEL//:/\\:}" \
    -v "$TMP/repo:/repo" -w /repo/sales node:20-alpine sh -c '
      npm ci --install-links --no-audit --no-fund >/tmp/i.log 2>&1 || { echo "install failed"; tail -5 /tmp/i.log; exit 10; }
      npx tsc -p tsconfig.app.json --noEmit >/tmp/t.log 2>&1
      if grep -qE "TS5101|TS5107|TS5108" /tmp/t.log; then echo "deprecation config error: diagnostics may be skipped"; exit 11; fi
      grep -q "__scope_probe__.ts.*error TS2322" /tmp/t.log || { echo "the deliberate type error was NOT reported"; cat /tmp/t.log | head; exit 12; }
      others=$(grep "error TS" /tmp/t.log | grep -vc "__scope_probe__")
      [ "$others" = 0 ] || { echo "$others other type errors"; grep "error TS" /tmp/t.log | head; exit 13; }
      rm src/platform-ui/__scope_probe__.ts
      npx vitest run src/platform-ui/importRule.test.ts >/tmp/v.log 2>&1 || { echo "import rule failed"; tail -20 /tmp/v.log; exit 14; }
      npx eslint src/platform-ui >/tmp/e.log 2>&1 || { echo "eslint failed"; tail -20 /tmp/e.log; exit 15; }
      # Each ESLint layer rule must fire on a planted violation.
      probe() {  # file, import line
        mkdir -p "$(dirname "$1")"
        printf "%s\nexport const planted = 1\n" "$2" > "$1"
        npx eslint "$1" >/tmp/p.log 2>&1
        grep -q "no-restricted-imports" /tmp/p.log || { echo "ESLint did NOT fire: $1 <- $2"; tail -10 /tmp/p.log; exit 18; }
        rm "$1"
      }
      probe src/platform-ui/__eslint_probe__.ts "import { api } from \"@/lib/api\""
      probe src/platform-ui/primitives/__eslint_probe__.ts "import { useCan } from \"../patterns/permissions\""
      probe src/platform-ui/primitives/__eslint_probe__.ts "import { useQuery } from \"@tanstack/react-query\""
      probe src/platform-ui/resource/__eslint_probe__.ts "import { Dialog } from \"../primitives/Dialog\""
      probe src/platform-ui/patterns/__eslint_probe__.ts "import { Button } from \"@uniops/shell\""
      probe src/features/__eslint_probe__.ts "import { Button } from \"@uniops/shell\""
      probe src/pages/__eslint_probe__.ts "import { Badge } from \"@uniops/shell\""
      npx vite build >/tmp/b.log 2>&1 || { echo "build failed"; tail -10 /tmp/b.log; exit 16; }
      grep -q "radix-popover-trigger-width" dist/assets/*.css || { echo "platform-ui classes missing from the built CSS"; exit 17; }
      # As written in CSS: hover:x → .hover\:x
      grep -qF "$SHELL_SELECTOR" dist/assets/*.css || { echo "shell-only class $SHELL_SENTINEL missing from the built CSS"; exit 19; }
      echo "probe reported; 0 other type errors; import + layer rules pass; 7 planted ESLint violations caught; library and shell classes in CSS"
    '
  rc=$?
  if [[ $rc == 0 ]]; then ok "sales: TS5101 trap, import + layer rules, ESLint probes, library + shell CSS"; else fail "sales checks (code $rc)"; fi
fi

echo
if [[ $FAIL != 0 ]]; then
  echo "SCOPE CHECK FAILED"
elif [[ $SKIPPED != 0 ]]; then
  echo "SCOPE CHECK PASSED (with SKIPPED checks)"
else
  echo "SCOPE CHECK PASSED"
fi
exit $FAIL
