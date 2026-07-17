# Account Balance non-leaf rollup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Account Balance report and its consumers report every account as `{itself ∪ all descendant accounts}`, so header (non-leaf) accounts aggregate their末级 children and align with NC.

**Architecture:** All backend logic lives in one file, `finance-api/app/crud/account_balance.py`. New pure tree helpers walk `chart_of_accounts.parent_code`. The main report keeps its two grouped-by-`account_code` direct-sum queries and rolls up in memory; the single-account surfaces (expand / drill / budget-actual) filter `account_code IN {subtree}`. Totals stay computed from the direct partition (each line once) so they never double-count. The frontend indents/bolds header rows and adds an Account column to the drill modal.

**Tech Stack:** Python 3.11, SQLAlchemy async, FastAPI, pytest; React + TypeScript (finance frontend), TanStack Query.

## Global Constraints

- Tree source is `chart_of_accounts.parent_code` ONLY — never infer hierarchy from code-prefix.
- Rollup rule is uniform: every account's figure = sum over `{self ∪ descendants}`; a leaf has no descendants so its figure is unchanged (no `is_postable` branch in the arithmetic).
- The main report's `totals` and `balanced` flag MUST be computed from the DIRECT sums (the natural per-line partition), NOT from rolled rows — this is the anti-double-count guarantee and must stay byte-for-byte identical to pre-rollup output.
- All descendant/ancestor walks MUST be cycle-guarded with a `visited`/`seen` set.
- All frontend user-facing strings in English.
- Branch: `feature/finance-nc-coa-sync`, worktree `c:/Project/uniops/.worktrees/nc-coa-sync`. No DB migration.
- Run pytest inside the finance-api container/venv, from `finance-api/`. Only ONE finance/epms pytest suite runs at a time (shared test DB).

---

### Task 1: Tree helpers (`_children_index`, `_descendants`, `_level`, `_subtree_codes`)

**Files:**
- Modify: `finance-api/app/crud/account_balance.py` (add helpers near `_coa_map`, after line 25)
- Test: `finance-api/tests/test_account_balance.py` (append)

**Interfaces:**
- Consumes: `ChartOfAccount` (already imported at top of the crud file), `_coa_map(db) -> dict[str, ChartOfAccount]` (existing, line 23).
- Produces:
  - `_children_index(coa_map: dict[str, ChartOfAccount]) -> dict[str, list[str]]`
  - `_descendants(children_idx: dict[str, list[str]], code: str) -> set[str]` (includes `code` itself)
  - `_level(coa_map: dict[str, ChartOfAccount], code: str) -> int` (0 for root/orphan)
  - `async _subtree_codes(db, account_code: str) -> set[str]` (loads coa_map, returns `_descendants`)

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_account_balance.py`:

```python
# ── non-leaf rollup: tree helpers (Task 1) ────────────────────────────────────
from types import SimpleNamespace
from app.crud.account_balance import (
    _children_index, _descendants, _level, _subtree_codes)


def _tree(*pairs):
    # pairs of (code, parent_code) -> {code: obj with .parent_code}
    return {code: SimpleNamespace(parent_code=parent) for code, parent in pairs}


def test_children_index_inverts_parent_code():
    coa = _tree(("6601", None), ("660101", "6601"), ("660102", "6601"))
    idx = _children_index(coa)
    assert sorted(idx["6601"]) == ["660101", "660102"]
    assert "660101" not in idx           # leaves have no children entry


def test_descendants_includes_self_and_all_levels():
    coa = _tree(("A", None), ("A1", "A"), ("A1a", "A1"), ("B", None))
    idx = _children_index(coa)
    assert _descendants(idx, "A") == {"A", "A1", "A1a"}
    assert _descendants(idx, "A1") == {"A1", "A1a"}
    assert _descendants(idx, "A1a") == {"A1a"}   # leaf -> just self
    assert _descendants(idx, "Z") == {"Z"}       # orphan not in tree -> just self


def test_descendants_cycle_guard_terminates():
    # a self/looping parent_code must not infinite-loop
    coa = _tree(("X", "Y"), ("Y", "X"))
    idx = _children_index(coa)
    assert _descendants(idx, "X") == {"X", "Y"}


def test_level_counts_depth_from_root():
    coa = _tree(("6601", None), ("660101", "6601"), ("66010101", "660101"))
    assert _level(coa, "6601") == 0
    assert _level(coa, "660101") == 1
    assert _level(coa, "66010101") == 2
    assert _level(coa, "orphan") == 0            # not in coa -> root level


def test_level_cycle_guard_terminates():
    coa = _tree(("X", "Y"), ("Y", "X"))
    assert _level(coa, "X") <= 2                 # returns, doesn't hang


async def test_subtree_codes_from_db(db_session):
    from app.models.coa import ChartOfAccount
    db_session.add_all([
        ChartOfAccount(code="6601", name="Selling", account_type="expense",
                       normal_balance="debit", is_postable=False, parent_code=None),
        ChartOfAccount(code="660101", name="Selling(fix)", account_type="expense",
                       normal_balance="debit", is_postable=True, parent_code="6601"),
        ChartOfAccount(code="660102", name="Selling(var)", account_type="expense",
                       normal_balance="debit", is_postable=True, parent_code="6601"),
    ])
    await db_session.flush()
    assert await _subtree_codes(db_session, "6601") == {"6601", "660101", "660102"}
    assert await _subtree_codes(db_session, "660101") == {"660101"}   # leaf
    assert await _subtree_codes(db_session, "9999") == {"9999"}       # orphan
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_account_balance.py -k "children_index or descendants or _level or subtree_codes or level_" -v`
Expected: FAIL with `ImportError: cannot import name '_children_index'` (helpers not defined yet).

- [ ] **Step 3: Implement the helpers**

In `finance-api/app/crud/account_balance.py`, insert immediately after `_coa_map` (after line 25):

```python
def _children_index(coa_map: dict) -> dict:
    """parent_code -> [child code] (leaves absent). The COA tree link is
    parent_code only — never inferred from code-prefix."""
    idx: dict[str, list[str]] = {}
    for code, acct in coa_map.items():
        if acct.parent_code:
            idx.setdefault(acct.parent_code, []).append(code)
    return idx


def _descendants(children_idx: dict, code: str) -> set:
    """{code} ∪ all transitive children. Cycle-guarded so a self/looping
    parent_code cannot infinite-loop."""
    seen: set[str] = set()
    stack = [code]
    while stack:
        c = stack.pop()
        if c in seen:
            continue
        seen.add(c)
        stack.extend(children_idx.get(c, ()))
    return seen


def _level(coa_map: dict, code: str) -> int:
    """Depth from the tree root (0 = root or orphan). Walks parent_code up,
    cycle-guarded."""
    depth = 0
    seen: set[str] = set()
    cur = code
    while True:
        acct = coa_map.get(cur)
        if acct is None or not acct.parent_code or cur in seen:
            return depth
        seen.add(cur)
        cur = acct.parent_code
        depth += 1


async def _subtree_codes(db: AsyncSession, account_code: str) -> set:
    """{account_code} ∪ all descendant account codes. Leaf/orphan -> {self}."""
    coa = await _coa_map(db)
    return _descendants(_children_index(coa), account_code)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_account_balance.py -k "children_index or descendants or _level or subtree_codes or level_" -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/account_balance.py finance-api/tests/test_account_balance.py
git commit -m "feat(finance): COA tree helpers for non-leaf rollup (descendants/level/subtree)"
```

---

### Task 2: Main report rollup (`account_balance`)

**Files:**
- Modify: `finance-api/app/crud/account_balance.py:28-69` (rewrite `account_balance`)
- Test: `finance-api/tests/test_account_balance.py` (append)

**Interfaces:**
- Consumes: `_children_index`, `_descendants`, `_level` (Task 1); existing `_coa_map`, `_s`, `_ZERO`, `POSTED`, `JournalVoucher`, `JournalVoucherLine`.
- Produces: `account_balance(db, period)` returns the same dict shape, with each row additionally carrying `"is_postable": bool` and `"level": int`; every row's figures are rolled over its subtree; `totals`/`balanced` unchanged (from direct sums).

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_account_balance.py`:

```python
# ── non-leaf rollup: main report (Task 2) ─────────────────────────────────────
async def _coa_selling_tree(db):
    from app.models.coa import ChartOfAccount
    db.add_all([
        ChartOfAccount(code="6601", name="Selling expenses", account_type="expense",
                       normal_balance="debit", is_postable=False, parent_code=None),
        ChartOfAccount(code="660101", name="Selling(fix)", account_type="expense",
                       normal_balance="debit", is_postable=True, parent_code="6601"),
        ChartOfAccount(code="660102", name="Selling(var)", account_type="expense",
                       normal_balance="debit", is_postable=True, parent_code="6601"),
    ])
    await db.flush()


async def test_account_balance_rolls_up_header(db_session):
    await _coa_selling_tree(db_session)
    await _posted_dim_event(db_session, "660101", "100.00", period="2026-07")
    await _posted_dim_event(db_session, "660102", "40.00", period="2026-07")
    await jv_crud.backfill_posted_jvs(db_session)

    bal = await ab.account_balance(db_session, "2026-07")
    by = {r["account_code"]: r for r in bal["rows"]}
    # header 6601 aggregates its two children (100 + 40)
    assert by["6601"]["period_debit"] == "140.00"
    assert by["6601"]["closing"] == "140.00"
    assert by["6601"]["is_postable"] is False and by["6601"]["level"] == 0
    # leaves unchanged, one level deeper
    assert by["660101"]["period_debit"] == "100.00" and by["660101"]["level"] == 1
    assert by["660102"]["period_debit"] == "40.00"


async def test_account_balance_totals_not_double_counted(db_session):
    # THE anti-double-count guarantee: the header row shows 140, but the grand
    # total counts each posted line once (140), not header+children (280).
    await _coa_selling_tree(db_session)
    await _posted_dim_event(db_session, "660101", "100.00", period="2026-07")
    await _posted_dim_event(db_session, "660102", "40.00", period="2026-07")
    await jv_crud.backfill_posted_jvs(db_session)

    bal = await ab.account_balance(db_session, "2026-07")
    assert bal["totals"]["period_debit"] == "140.00"     # not 280.00
    assert bal["balanced"] is True                       # 140 dr (children) == 140 cr (2000)


async def test_account_balance_leaf_only_unchanged(db_session):
    # orphan codes (not in COA) behave as leaves exactly as before rollup
    await _posted_event(db_session, "2026-06", amount="100.00")
    await _posted_event(db_session, "2026-07", amount="40.00")
    await jv_crud.backfill_posted_jvs(db_session)
    bal = await ab.account_balance(db_session, "2026-07")
    by = {r["account_code"]: r for r in bal["rows"]}
    assert by["5000"]["opening"] == "100.00"
    assert by["5000"]["closing"] == "140.00"
    assert by["5000"]["is_postable"] is True and by["5000"]["level"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_account_balance.py -k "rolls_up_header or totals_not_double or leaf_only_unchanged" -v`
Expected: FAIL — `test_account_balance_rolls_up_header` KeyErrors on `by["6601"]` (header has no direct line so no row today) / missing `is_postable`.

- [ ] **Step 3: Rewrite `account_balance`**

Replace `finance-api/app/crud/account_balance.py:28-69` (the whole `account_balance` function) with:

```python
async def account_balance(db: AsyncSession, period: str) -> dict:
    """Opening (cumulative posted before `period`) + period movement + closing,
    per account, in local (CAD) amounts. Non-leaf (header) accounts aggregate
    their whole {self ∪ descendants} subtree (NC科目余额表 parity); leaves are
    unchanged. Totals stay from the DIRECT per-line partition so rollup never
    double-counts."""
    coa = await _coa_map(db)
    children = _children_index(coa)

    async def sums(where):
        q = (select(JournalVoucherLine.account_code,
                    func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                    func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
             .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
             .where(JournalVoucher.status == POSTED).where(where)
             .group_by(JournalVoucherLine.account_code))
        return {code: (Decimal(d), Decimal(c)) for code, d, c in (await db.execute(q)).all()}

    opening = await sums(JournalVoucher.fiscal_period < period)
    movement = await sums(JournalVoucher.fiscal_period == period)

    active = set(opening) | set(movement)               # codes with a direct line
    all_codes = set(coa) | active
    subtree = {code: _descendants(children, code) for code in all_codes}

    def roll(direct, code):
        d = c = _ZERO
        for s in subtree[code]:
            sd, sc = direct.get(s, (_ZERO, _ZERO))
            d += sd; c += sc
        return d, c

    # show an account iff its subtree contains any direct line (self or descendant)
    shown = [c for c in all_codes if subtree[c] & active]
    shown.sort(key=lambda c: (c is None, c or ""))

    rows = []
    for code in shown:
        od, oc = roll(opening, code)
        md, mc = roll(movement, code)
        open_bal = od - oc
        acct = coa.get(code)
        rows.append({
            "account_code": code or "(unmapped)",
            "account_name": acct.name if acct else "(unmapped)",
            "account_type": acct.account_type if acct else None,
            "is_postable": acct.is_postable if acct else True,
            "level": _level(coa, code) if code else 0,
            "opening": _s(open_bal),
            "period_debit": _s(md), "period_credit": _s(mc),
            "closing": _s(open_bal + md - mc),
        })

    # totals from the DIRECT partition (each line once) — NOT from rolled rows
    tot_dr = tot_cr = tot_close = _ZERO
    for code in active:
        od, oc = opening.get(code, (_ZERO, _ZERO))
        md, mc = movement.get(code, (_ZERO, _ZERO))
        tot_dr += md; tot_cr += mc
        tot_close += (od - oc) + (md - mc)
    return {
        "period": period, "rows": rows,
        "totals": {"period_debit": _s(tot_dr), "period_credit": _s(tot_cr),
                   "closing": _s(tot_close)},
        "balanced": tot_dr == tot_cr and tot_close == _ZERO,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_account_balance.py -k "rolls_up_header or totals_not_double or leaf_only_unchanged or opening_movement_closing or excludes_draft or account_balance_endpoint" -v`
Expected: PASS (6 tests — the 3 new plus the 3 pre-existing main-report tests, confirming no regression).

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/account_balance.py finance-api/tests/test_account_balance.py
git commit -m "feat(finance): roll up non-leaf accounts in account_balance (totals from direct partition)"
```

---

### Task 3: Single-account surfaces — `IN (subtree)` for expand / drill / budget-actual

**Files:**
- Modify: `finance-api/app/crud/account_balance.py` — `_by_cost_center` (94-103), `expand_by_dims` (204-255), `account_vouchers` (297-324)
- Test: `finance-api/tests/test_account_balance.py` (append)

**Interfaces:**
- Consumes: `_subtree_codes` (Task 1), existing `_coa_map`.
- Produces: `expand_by_dims`, `account_vouchers`, `_by_cost_center` all filter `account_code IN {self ∪ descendants}`; `account_vouchers` rows gain `"account_code"` and `"account_name"`.

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_account_balance.py`:

```python
# ── non-leaf rollup: expand / drill / budget-actual (Task 3) ───────────────────
async def test_expand_rolls_up_header_by_dim(db_session):
    await _coa_selling_tree(db_session)
    cc = await _cc(db_session, "MKT-01", "Marketing")
    await _posted_cc_event(db_session, "660101", cc, "100.00")
    await _posted_cc_event(db_session, "660102", cc, "40.00")
    await jv_crud.backfill_posted_jvs(db_session)
    exp = await ab.expand_by_dims(db_session, "6601", "2026-07", ["cost_center"])
    by = {r["keys"][0]["code"]: r for r in exp["rows"]}
    assert by["MKT-01"]["closing"] == "140.00"        # both children aggregated
    # leaf still isolates its own
    exp_leaf = await ab.expand_by_dims(db_session, "660101", "2026-07", ["cost_center"])
    assert {r["keys"][0]["code"]: r["closing"] for r in exp_leaf["rows"]} == {"MKT-01": "100.00"}


async def test_vouchers_header_drill_tags_child_account(db_session):
    await _coa_selling_tree(db_session)
    await _posted_dim_event(db_session, "660101", "100.00", period="2026-07")
    await _posted_dim_event(db_session, "660102", "40.00", period="2026-07")
    await jv_crud.backfill_posted_jvs(db_session)
    v = await ab.account_vouchers(db_session, "6601", "2026-07")
    codes = sorted(r["account_code"] for r in v["rows"])
    assert codes == ["660101", "660102"]
    names = {r["account_code"]: r["account_name"] for r in v["rows"]}
    assert names["660101"] == "Selling(fix)"


async def test_budget_actual_rolls_up_header(db_session):
    await _coa_selling_tree(db_session)               # 6601 is a BUDGET_ACTUAL account
    cc = await _cc(db_session, "SELL-01", "Sales")
    await _posted_cc_event(db_session, "660101", cc, "70.00")
    await jv_crud.backfill_posted_jvs(db_session)
    ba = await ab.budget_actual(db_session, "2026-07")
    rows = [r for r in ba["rows"] if r["account_code"] == "6601"]
    assert len(rows) == 1
    assert rows[0]["category"] == "SELL" and rows[0]["actual"] == "70.00"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_account_balance.py -k "expand_rolls_up_header or vouchers_header_drill or budget_actual_rolls_up" -v`
Expected: FAIL — expand/budget only see the header's own (zero) direct lines; `account_vouchers` rows have no `account_code` key.

- [ ] **Step 3a: `_by_cost_center` → IN(subtree)**

Replace `finance-api/app/crud/account_balance.py:94-103` (the `_by_cost_center` function) with:

```python
async def _by_cost_center(db: AsyncSession, account_code: str, period: str):
    subtree = await _subtree_codes(db, account_code)
    q = (select(JournalVoucherLine.cost_center_id,
                func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .where(JournalVoucher.status == POSTED,
                JournalVoucher.fiscal_period == period,
                JournalVoucherLine.account_code.in_(subtree))
         .group_by(JournalVoucherLine.cost_center_id))
    return (await db.execute(q)).all()
```

- [ ] **Step 3b: `expand_by_dims` → IN(subtree)**

In `expand_by_dims`, after `cols = [reg[d][0] for d in dims]` (line 211), add the subtree resolution and change the `grouped` filter. Replace lines 211-220 (from `cols = ...` through the end of the `grouped` query's `.where(...)`) with:

```python
    cols = [reg[d][0] for d in dims]
    subtree = await _subtree_codes(db, account_code)

    async def grouped(where):
        q = (select(*cols,
                    func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                    func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
             .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
             .where(JournalVoucher.status == POSTED,
                    JournalVoucherLine.account_code.in_(subtree), where)
             .group_by(*cols))
```

(Leave the rest of `grouped` — the `return {tuple(...)}` — and everything below it unchanged.)

- [ ] **Step 3c: `account_vouchers` → IN(subtree) + account tag**

Replace `finance-api/app/crud/account_balance.py:297-324` (the whole `account_vouchers` function) with:

```python
async def account_vouchers(db: AsyncSession, account_code: str, period: str,
                           dims_values: dict | None = None) -> dict:
    """③ drill-down: posted JV lines for an account (rolled over its
    {self ∪ descendants} subtree), optionally filtered by a dimension-value
    combo ({dim_code: uuid | None}; None = IS NULL). Each row carries its own
    account_code/name so a header drill shows which child a line belongs to."""
    subtree = await _subtree_codes(db, account_code)
    coa = await _coa_map(db)
    q = (select(JournalVoucherLine, JournalVoucher)
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .where(JournalVoucher.status == POSTED,
                JournalVoucher.fiscal_period == period,
                JournalVoucherLine.account_code.in_(subtree))
         .order_by(JournalVoucher.voucher_date))
    if dims_values:
        reg = _check_dims(list(dims_values.keys()))
        for d, v in dims_values.items():
            col = reg[d][0]
            q = q.where(col.is_(None) if v is None else col == v)
    rows = []
    for ln, jv in (await db.execute(q)).all():
        acct = coa.get(ln.account_code)
        rows.append({
            "jv_id": str(jv.id), "jv_number": jv.jv_number,
            "voucher_date": jv.voucher_date.isoformat(),
            "account_code": ln.account_code,
            "account_name": acct.name if acct else None,
            "summary": ln.summary or jv.summary,
            "local_debit": str(ln.local_debit), "local_credit": str(ln.local_credit),
            "cost_center_id": str(ln.cost_center_id) if ln.cost_center_id else None,
            "source_doc_type": jv.source_doc_type,
            "source_doc_id": str(jv.source_doc_id) if jv.source_doc_id else None,
            "source_doc_number": jv.source_doc_number,
        })
    return {"account_code": account_code, "period": period, "rows": rows}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_account_balance.py -k "expand_rolls_up_header or vouchers_header_drill or budget_actual_rolls_up or expand_single_dim or account_vouchers_drilldown or vouchers_filter_none or budget_actual_by_cost_center" -v`
Expected: PASS — 3 new tests plus the pre-existing expand/drill/budget tests (leaf/orphan behavior unchanged).

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/account_balance.py finance-api/tests/test_account_balance.py
git commit -m "feat(finance): expand/drill/budget-actual aggregate non-leaf subtree; drill rows tagged with account"
```

---

### Task 4: Frontend — indent/bold header rows + drill Account column

**Files:**
- Modify: `finance/src/pages/finance/AccountBalancePage.tsx` (AbRow interface ~23-26; main row render ~123-145)
- Modify: `finance/src/pages/finance/AccountVouchersModal.tsx` (VoucherRow interface ~16-20; header ~52-59; body ~65-77)

**Interfaces:**
- Consumes: backend rows now include `is_postable: boolean`, `level: number` (main report) and `account_code`/`account_name` (drill rows) from Tasks 2-3.

- [ ] **Step 1: Capture the frontend typecheck baseline**

Run (from `finance/`): `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | grep -c "error TS"`
Record the number — this is the baseline. The task must not INCREASE it.

- [ ] **Step 2: Extend the AbRow interface**

In `finance/src/pages/finance/AccountBalancePage.tsx`, replace the `AbRow` interface (lines 23-26):

```tsx
interface AbRow {
  account_code: string; account_name: string; account_type: string | null
  is_postable: boolean; level: number
  opening: string; period_debit: string; period_credit: string; closing: string
}
```

- [ ] **Step 3: Indent + bold the main rows**

In the same file, replace the Code and Account `<td>` cells (lines 133-134) with level-based indent and header bolding:

```tsx
                      <td className={cn('px-3 py-2 font-mono text-xs', !r.is_postable && 'font-semibold')}>{r.account_code}</td>
                      <td className={cn('px-3 py-2', !r.is_postable && 'font-semibold')}
                          style={{ paddingLeft: `${12 + r.level * 18}px` }}>{r.account_name}</td>
```

- [ ] **Step 4: Add the Account column to the drill modal**

In `finance/src/pages/finance/AccountVouchersModal.tsx`:

Replace the `VoucherRow` interface (lines 16-20):

```tsx
interface VoucherRow {
  jv_id: string; jv_number: string; voucher_date: string; summary: string | null
  account_code: string; account_name: string | null
  local_debit: string; local_credit: string; cost_center_id: string | null
  source_doc_type: string | null; source_doc_id: string | null; source_doc_number: string | null
}
```

Replace the table header row (lines 53-59) to add an Account column:

```tsx
                <tr>
                  <th className="px-3 py-2 w-24">Date</th>
                  <th className="px-3 py-2 w-36">Voucher</th>
                  <th className="px-3 py-2 w-40">Account</th>
                  <th className="px-3 py-2">Summary</th>
                  <th className="px-3 py-2 w-28 text-right">Debit</th>
                  <th className="px-3 py-2 w-28 text-right">Credit</th>
                </tr>
```

Replace the empty-state colSpan (line 63) `colSpan={5}` with `colSpan={6}`, and replace the body row cells (lines 66-77) to render the Account cell:

```tsx
                  <tr key={`${r.jv_id}-${i}`} className={cn('border-t border-neutral-100', i % 2 && 'bg-neutral-50/40')}>
                    <td className="px-3 py-2 font-mono text-xs text-neutral-600">{r.voucher_date}</td>
                    <td className="px-3 py-2">
                      <button onClick={() => onOpenJv(r.jv_id)}
                              className="font-mono text-xs text-[#085E5E] hover:underline">
                        {r.jv_number}
                      </button>
                    </td>
                    <td className="px-3 py-2 font-mono text-xs text-neutral-600">
                      {r.account_code}{r.account_name ? ` · ${r.account_name}` : ''}
                    </td>
                    <td className="px-3 py-2 text-neutral-700">{r.summary || '—'}</td>
                    <td className="px-3 py-2 text-right font-mono">{money(r.local_debit)}</td>
                    <td className="px-3 py-2 text-right font-mono">{money(r.local_credit)}</td>
                  </tr>
```

- [ ] **Step 5: Run the typecheck and confirm no new errors**

Run (from `finance/`): `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | grep -c "error TS"`
Expected: the count equals the Step 1 baseline (no new errors). If it rose, read the new `error TS` lines and fix them in the files above before committing.

- [ ] **Step 6: Commit**

```bash
git add finance/src/pages/finance/AccountBalancePage.tsx finance/src/pages/finance/AccountVouchersModal.tsx
git commit -m "feat(finance): indent/bold header rows in Account Balance; add Account column to voucher drill"
```

---

## Verification (post-implementation, dev)

After all tasks pass, verify the LOGIC in dev (numeric parity with live NC only comes after a prod full-reload — dev is a frozen reload):

1. Restart finance-api so the container picks up the code: `docker compose -f docker-compose.dev.yml restart finance-api`.
2. In the Account Balance page, June 2026: confirm 6601 now shows a rolled figure, expands by Department with amounts, and its Vouchers drill lists lines tagged 660101/660102.
3. Cross-check the report's rolled 6601 figure against a direct NC query over the same `{self ∪ descendants}` subtree by department — the rollup logic is correct if the report equals that NC-direct query on the SAME dev data (any dev-vs-live gap is staleness, not logic).
