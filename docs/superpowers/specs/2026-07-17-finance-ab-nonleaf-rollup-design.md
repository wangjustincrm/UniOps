# Account Balance non-leaf (header) account subtree rollup — Design

**Date:** 2026-07-17
**Branch:** `feature/finance-nc-coa-sync` (worktree `c:/Project/uniops/.worktrees/nc-coa-sync`)
**Status:** design approved, pending spec review

## Problem

The Account Balance report (科目余额表) and its consumers filter posted JV lines
by an **exact** `account_code`. NC posts detail on末级 (leaf) accounts and rolls
those up under header (non-leaf) accounts. Our report does not roll up, so a
header account like **6601 "Selling expenses"** shows ~its own direct lines
only — for June 2026 that reads as near-empty, and expanding it by department
or drilling to vouchers returns nothing, while NC shows 6601 by department
(e.g. 0111 Marketing = 198,422.09 for the current period).

Confirmed against live NC: a direct query over the 6601 subtree by department
reproduces NC's per-department current-period figures exactly (0107 =
18,397.84, 0111 = 198,422.09 — both byte-for-byte with the NC科目余额表
screenshot). Root cause is therefore **missing non-leaf rollup**, not a data
error.

## Investigation facts (dev DB, 2026-07-17)

- `chart_of_accounts.parent_code` is the authoritative tree link: **0 dangling
  parent_codes**, and every child code is prefix-consistent with its parent.
  The rollup walks `parent_code`, never guesses by code-prefix.
- `is_postable` is the leaf marker. **"Top-level" ≠ "header":** 6602 (G&A) is a
  postable top-level leaf (`is_postable=t`, `parent_code=null`). 61 postable
  accounts have a null parent_code. The rollup rule must key on the tree, not
  on code length or top-level-ness.
- 6601: `is_postable=f`, `parent_code=null`; children 660101 "Selling
  expenses(fix)" and 660102 "Selling expenses(Variable)" are `is_postable=t`,
  `parent_code='6601'`.
- **Header accounts carry direct posted lines in our mirror.** 41,921 posted
  lines sit on non-postable codes (5101: 25,978; 6601: 2,454; 600101: 247;
  22250101: 12,745; …). Leaf accounts carry 272,518. A further **305 posted
  lines carry an account_code not present in COA at all** (orphans).
  → A header's rolled-up figure must be **its own direct lines + all
  descendants**, and the report's grand-total row must be computed from the
  direct partition (each line once) so rollup never double-counts.

## The rule

Every account's reported figure = sum over **{itself ∪ all descendant
accounts}**. A leaf has no descendants, so its figure is unchanged — there is
no `is_postable` branch in the arithmetic; headers simply aggregate. Because
`_net(d,c) = d − c` is linear, rolling up each component (opening / period
debit / period credit) then combining equals combining then rolling up, so
`closing = opening + period_debit − period_credit` holds at every level.

## Approach — Option C (chosen)

Considered:
- **A — subtree query per account.** Simple per call, but the main report would
  fire one query per account (hundreds). Rejected: query fan-out.
- **B — recursive SQL CTE.** Fast but opaque and hard to unit-test; duplicates
  tree logic. Rejected: testability.
- **C (chosen) — one grouped query + in-memory tree rollup for the main
  report; `account_code IN {subtree}` for the single-account surfaces.** The
  main report keeps its two grouped-by-`account_code` direct-sum queries, then
  propagates each code's direct figure to all ancestors in Python
  (cycle-guarded). Single-account surfaces (expand / drill / budget-actual)
  resolve `{self ∪ descendants}` once from the COA map and filter `IN`. Leaves
  behave exactly as today. The tree is small (~hundreds of nodes, depth ~4), so
  in-memory rollup is trivial.

## Components (all in `finance-api/app/crud/account_balance.py`)

### New shared helpers
- `_children_index(coa_map) -> dict[str, list[str]]` — invert `parent_code`
  into parent→children once per request.
- `_descendants(children_idx, code) -> set[str]` — `{code}` ∪ all transitive
  children, **cycle-guarded** with a `visited` set so a self/looping
  `parent_code` can never infinite-loop.
- `_rollup(direct: dict[str, tuple], children_idx, coa_map) -> dict[str, tuple]`
  — for the main report: given per-code direct `(debit, credit)` figures,
  return per-code figures summed over each code's subtree. Codes present in
  `direct` but absent from COA (the 305 orphans) remain their own leaf entry.

Tree source is `parent_code` only.

### 1. Main report — `account_balance(db, period)`
- Keep the two existing grouped-by-`account_code` direct-sum queries
  (`opening` = `fiscal_period < period`, `movement` = `== period`) untouched.
- Roll up opening and movement over `{self ∪ descendants}` in memory.
- **Rows:** one per account with self-or-descendant activity (rolled
  `opening ∪ movement`). Each row additionally carries `is_postable` and
  `level` (depth from the tree root, for frontend indent). Sort stays by
  `code`, so 6601 → 660101 → 660102 read as a tree once indented.
- **Totals + `balanced`:** computed from the **direct** sums (each line once,
  the natural partition) — NOT from rolled rows — so the grand total and the
  `balanced` flag are identical to today. This is the anti-double-count
  guarantee and the central regression test.

### 2. Single-account surfaces — `IN (subtree)`
`expand_by_dims`, `account_vouchers`, and `_by_cost_center` (feeding
`budget_actual`) currently filter `JournalVoucherLine.account_code ==
account_code`. Change each to `account_code IN {self ∪ descendants}`, resolved
once from the COA map. For a leaf the set is `{self}` → identical behavior.

The committed monthly-view `all_keys = set(movement)` in `expand_by_dims`
stays as-is and composes cleanly (movement now spans the subtree).

### 3. Drill rows gain account identity — `account_vouchers`
Each returned row gains `account_code` + `account_name`. When drilling a
parent, its lines come from several children; the user must see which child
(660101 vs 660102) each line belongs to.

### 4. Budget Actual — `budget_actual` / `_by_cost_center`
5101 / 5301 / 6601 are non-leaf; rolling up their subtrees makes Budget Actual
include descendant actual spend it currently misses. Same helper, same rule.

## Frontend — `finance/src/pages/finance/AccountBalancePage.tsx`
- Main table: indent each row by `level`; bold non-leaf rows (`is_postable=false`).
- Drill modal: add an Account column (`code — name`).
- Dimension expansion: unchanged (already renders the 4 columns
  opening / period_debit / period_credit / closing).

## Error handling / edge cases
- Cycle in `parent_code`: `visited` set stops the walk.
- Orphan account_code (not in COA): its own row/leaf; still counts once in
  totals; can't be anyone's descendant.
- Unknown/empty dims in expand/drill: unchanged `BadDims` behavior.

## Testing
- `_descendants`: multi-level tree returns full subtree; cycle guard
  terminates; leaf returns `{self}`; orphan code isolated.
- `account_balance`: header row = self + children; leaf row unchanged;
  **grand total equals the sum of direct lines (not inflated)** — the critical
  regression test, built on a small tree.
- `expand_by_dims`: parent aggregates children by dimension; leaf unchanged;
  reconciles column-for-column with the parent report row.
- `account_vouchers`: parent drill returns children's lines each tagged with
  its own `account_code`; leaf unchanged.
- `budget_actual`: 5101 / 6601 include descendant spend.

## Verification / honesty note
On **dev**, 6601's June subtree reads ≈2× the live NC figure — dev is a frozen
reload, NC is live; this is data staleness, not the rollup logic. Dev
acceptance verifies the **logic** by comparing the report's rolled figures
against an NC-direct query using the same `{self ∪ descendants}` definition.
Final numeric parity with NC comes only after a production full-reload.

## Scope
One backend file (`account_balance.py`) + one frontend page
(`AccountBalancePage.tsx`) + tests. Same branch `feature/finance-nc-coa-sync`.
No migration.
