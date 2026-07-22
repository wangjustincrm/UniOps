# Task 7 Report — Budget Dashboard consumes server-side `/actuals/scope`

(Note: this file previously held a report for an unrelated task — "EPMS Role
Management 页签下线 + 代班功能删除" on branch `feature/approval-routing-phase3`.
That content has been replaced below with the report for the actual Task 7 of
this 7-task spec: frontend consumption of `/actuals/scope` on branch
`feature/budget-dashboard-dept-scoping`.)

## Status
DONE

## Commits
- `7963a62` on `feature/budget-dashboard-dept-scoping` —
  "feat(epms): Budget Dashboard consumes server-side /actuals/scope"
  (3 files changed, 36 insertions(+), 46 deletions(-))
- (follow-up fix) — "fix(epms): guard budget-scope empty warning on load/error"
  (SHA below in the Follow-up section)

## Follow-up fix (coordinator review) — guard empty-scope warning on load/error

**Problem raised in review:** while `useActualsScope()` is loading (or if it
errors), `scope` is `undefined`, so `isFullAccess` was `false` and
`scope?.cost_centers?.length ?? 0` was `0` — meaning EVERY user, including a
full-access admin, briefly saw the "No budget is visible for your account…"
warning on every cold load, and it PERSISTED on a scope query error even though
the backend still authoritatively returns their data.

**Fix (one file, `BudgetDashboard.tsx`):**
- `const { data: scope } = useActualsScope()` →
  `const { data: scope, isLoading: scopeLoading, isError: scopeError } = useActualsScope()`.
- Empty-scope warning condition changed from
  `!isFullAccess && (scope?.cost_centers?.length ?? 0) === 0` to
  `!scopeLoading && !scopeError && scope && !scope.full_access && scope.cost_centers.length === 0`
  — only renders once the query has settled successfully AND the viewer
  genuinely has zero CCs and is not full-access. No warning while loading or on
  error.
- Dropdown gate left as-is per the review note (empty `visibleCCs` during
  loading simply renders no dropdown, which is acceptable — only the alarming
  text needed guarding).

**Typecheck re-verify** (same working command, TS 5.9.3 via the node_modules
junction, `node_modules/.bin/tsc -p tsconfig.app.json --noEmit`):
- Total errors: `59` (still ≤ the 59 baseline).
- `grep -E "BudgetDashboard\.tsx" fix_tsc.txt` → no output (target file clean).

## What changed

### `epms/src/services/budget.ts`
- Added `export interface ApiActualsScope { full_access: boolean; cost_centers: {id, code, name, department_id}[] }` next to `ApiMonthlyActualsSummary`.
- Added `getActualsScope: () => budgetApi.get<ApiActualsScope>('/actuals/scope')` next to `getMonthlyActualsSummary` in the `budgetService` object.

### `epms/src/hooks/useBudget.ts`
- Added `ApiActualsScope` to the existing `import type { ... } from '@/services/budget'` block (rather than a separate import line — same module, avoids a duplicate import statement; behavior identical to the brief's snippet).
- Added `useActualsScope()` hook (react-query, `queryKey: ['budget', 'actuals-scope']`, `staleTime: 5 * 60_000`) right after `useMonthlyActualsSummary` in the Balance/Actuals section.

### `epms/src/pages/budget/BudgetDashboard.tsx`
- Removed `FULL_ACCESS_ROLES`, `SPECIAL_ROLE_CODES` module-level consts and their comment blocks.
- Removed `useRolePermissions`, `useMyAssignedRoles` imports from `@/hooks/useConfig`.
- Removed the `useAuthStore` import and the `user` destructure entirely (verified: `user` had no other use in the file — only appeared inside the removed role-gating block).
- Removed `myPermissions`, `myAssignedRoles`, `myRoles`, `isSpecialRoleAssignee`, `isFinanceBpAssigned`, `isFullAccess` (old), and the old `visibleCCs` filter derivation.
- Added `const { data: scope } = useActualsScope()`, `isFullAccess = scope?.full_access ?? false`, and the `visibleCCs` `useMemo` that maps scope cost centers onto the richer `useCostCenters` objects (falling back to the scope payload shape when not found in `costCenters`) — verbatim per brief.
- Dropdown render condition changed from `visibleCCs.length > 1` to `visibleCCs.length > 1 || (!isFullAccess && visibleCCs.length >= 1)` — verbatim per brief.
- Added the empty-scope note (`<p className="text-sm text-warning-700 mt-1">No budget is visible for your account...</p>`) directly after the `scopeLabel` span, inside the header `<div>`, gated on `!isFullAccess && (scope?.cost_centers?.length ?? 0) === 0` — verbatim per brief.
- `scopeLabel` (unchanged) still reads `isFullAccess`, now sourced from the scope query as required — no edit needed there.

## Typecheck — positive evidence

**Environment note (not in the brief, discovered while capturing baseline):**
`npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` failed outright
— the worktree's TypeScript is actually **5.9.3** (junctioned from the main
checkout's `epms/node_modules`), not 6.0.3, so `--ignoreDeprecations 6.0` errors
with `TS5103: Invalid value`. Separately, this worktree (`uniops-budget-scope`)
is an npm-workspaces monorepo checkout with **no root `node_modules`** of its
own — hoisted deps like `react`, `react-router-dom`, `@tanstack/react-query`,
`@uniops/shell` live in the *main* checkout's root `node_modules`
(`C:/Project/uniops/node_modules`), and Node's upward module resolution from
`uniops-budget-scope/epms/node_modules` never reaches that sibling tree.
Running tsc without a root `node_modules` produced ~1197 bogus "Cannot find
module 'react'" style errors — not a real baseline.

Fix applied (workspace-tooling only, no source files touched): created a
Windows junction `C:/Project/uniops-budget-scope/node_modules ->
C:/Project/uniops/node_modules`, mirroring the pre-existing
`epms/node_modules -> .../uniops/epms/node_modules` junction already in place.
Same pattern the repo already uses; only affects local module resolution, not
git-tracked content.

Actual working command (drop `--ignoreDeprecations 6.0`; invoke tsc directly
rather than via `npx`, which was also erroring for an unrelated reason —
`npx` printed "This is not the tsc command you are looking for" even with the
correct binary present):
```
cd epms && node_modules/.bin/tsc -p tsconfig.app.json --noEmit
```

- **Baseline (before edits):** `59` errors (`grep -c "error TS"`), **0**
  referencing `BudgetDashboard.tsx`, `services/budget.ts`, or
  `hooks/useBudget.ts`. (Close to, but not identical to, the memory-recorded
  "~69 存量错" baseline for the main epms checkout — plausible drift between
  the two checkouts; pre-existing and out of this task's scope, not
  investigated further.)
- **After edits:** `59` errors — same count.
- **Grep for my 3 files in post-edit output:**
  ```
  $ grep -E "BudgetDashboard\.tsx|services/budget\.ts|hooks/useBudget\.ts" postedit_tsc.txt
  (no output — zero matches)
  ```

Gate passed: post-edit count (59) ≤ baseline (59), and none of the 3 files
appear in the error list.

## Files changed
- `C:/Project/uniops-budget-scope/epms/src/services/budget.ts`
- `C:/Project/uniops-budget-scope/epms/src/hooks/useBudget.ts`
- `C:/Project/uniops-budget-scope/epms/src/pages/budget/BudgetDashboard.tsx`

## Self-review
- No leftover dead imports/vars: `useRolePermissions`, `useMyAssignedRoles`,
  `useAuthStore` all removed; `useConfig` import kept (still used for
  thresholds); `useCostCenters` kept (still used — richer CC objects for
  dropdown labels and the `scopeLabel` "My Department"/company-wide lookup
  fallback via `costCenters.find`).
- Whole-file grep for the removed identifiers (`FULL_ACCESS_ROLES`,
  `SPECIAL_ROLE_CODES`, `isFinanceBpAssigned`, `myPermissions`,
  `myAssignedRoles`, `myRoles`, `isSpecialRoleAssignee`) after editing —
  zero hits remain.
- Behavior matches spec:
  - Default (`ccId === 'all'`) still aggregates at the department/company
    scope server-side — frontend no longer computes who's full-access; it
    just reads `scope.full_access` and `scope.cost_centers`.
  - Dropdown is now shown whenever the viewer has ≥1 visible CC and is not
    (full-access AND exactly one CC) — i.e. always available for scoped
    viewers with any CC, per brief's condition.
  - Empty-scope note renders when a non-full-access viewer has zero visible
    cost centers.

## Concerns
1. **Environment gap in the task brief**: the documented typecheck command
   (`npx tsc ... --ignoreDeprecations 6.0`) does not work as-is in this
   worktree (wrong TS version, missing root `node_modules` junction). Fixed
   locally by adding a root `node_modules` junction (tooling-only, not a git
   change) and running `node_modules/.bin/tsc` directly without the invalid
   flag. Future tasks in this worktree touching the frontend should be aware
   the junction now exists (or should recreate it if the worktree is reset)
   — `C:/Project/uniops-budget-scope/node_modules` (junction →
   `C:/Project/uniops/node_modules`).
2. Not run/verified beyond typecheck: no unit tests exist for this component
   (per brief), and no manual browser verification was performed (explicitly
   deferred per the brief's "Post-implementation (do NOT run without user
   go)" section — dev-token manual verify, full test suites, and
   release/deploy are the user's call).
3. `visibleCCs` relies on a runtime cast (`as (typeof costCenters)[number]`,
   per the brief's own code) for scope-only cost centers not present in
   `useCostCenters`'s (active-only) result — e.g. an inactive CC still in
   scope. Intentional/per-spec, flagging only that it's a cast rather than a
   structurally verified shape match.
4. Found this same report file (`task-7-report.md`) already existed on disk
   with content for a different, unrelated task ("EPMS Role Management 页签
   下线") on a different branch (`feature/approval-routing-phase3`). Since it
   was committed and my brief explicitly said to write my report to this
   exact path, I overwrote it. Flagging in case the old content needs to be
   recovered from git history (it's in this branch's history prior to my
   commit) or the filename collision indicates a task-numbering mixup across
   branches/sessions.
