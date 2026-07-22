# Task 2 Report: NC65 COA pure mapping functions

## Status: DONE

## What was done

Followed the brief's TDD steps exactly, using verbatim code blocks from
`.superpowers/sdd/task-2-brief.md`.

1. Appended the Step-1 test block to `finance-api/tests/test_nc_coa_sync.py`
   (kept Task 1's two existing tests intact).
2. Ran the suite to confirm the expected collection failure
   (`ModuleNotFoundError: No module named 'app.services.nc_coa_sync'`).
3. Created `finance-api/app/services/nc_coa_sync.py` with the mapping
   functions exactly as given in the brief: `NC_OWNED_FIELDS`,
   `NcMappingError`, `clean`, `map_normal_balance`, `map_account_type`,
   `map_aux_item`, `derive_party_dim`, `map_account`. It imports
   `nc_configured` from `app/services/nc_sync.py` unchanged (no
   reimplementation).
4. Replaced `DIM_LABELS` in `finance-api/app/crud/account_balance.py` with
   the brief's expanded dict — all 7 original keys preserved verbatim, plus
   the new keys (`partner`, `project`, `project_type`,
   `government_grant_project`, `item`, `item_category`, `asset_category`,
   `tax_code`, `bank`, `bank_account`, `bank_category`, `country_region`,
   `sales_type`, `credit_card`).
5. Ran `tests/test_nc_coa_sync.py` — all pass (29 total: 2 from Task 1 + 27
   new; the brief's "~22" estimate undercounted the parametrized cases).
6. Ran `tests/test_account_balance.py` — all 20 pass, same count as before
   the `DIM_LABELS` edit (no regression).
7. Committed all three files together.

Note: this exact report path (`task-2-report.md`) contained a stale report
from an unrelated earlier task (meeting-room booking data model). It has
been overwritten with this task's report below.

## Environment used

Per the mid-task correction: no venv exists in the worktree, so all commands
ran the worktree's test files through the **main repo's** interpreter
(`c:/Project/uniops/finance-api/.venv/Scripts/python`), with `DATABASE_URL`
and `JWT_SECRET_KEY` set to inline dummies (never pointing at the main
repo's `.env`, which targets the production DB). `TEST_PG_PASSWORD` was
still sourced from `/c/Project/uniops/.env`'s `DB_PASSWORD` per the brief, to
authenticate against the local docker postgres the test fixtures actually
use.

## Exact commands and full output

### Step 2 — confirm failing (module not found)

```
cd /c/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
DATABASE_URL="postgresql+asyncpg://dummy:dummy@localhost:5432/dummy" \
JWT_SECRET_KEY="dummy" \
  /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/test_nc_coa_sync.py -v
```

Output (tail):

```
collecting ... collected 0 items / 1 error

=================================== ERRORS ====================================
_________________ ERROR collecting tests/test_nc_coa_sync.py __________________
ImportError while importing test module 'C:\Project\uniops\.worktrees\nc-coa-sync\finance-api\tests\test_nc_coa_sync.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
C:\Program Files\Python312\Lib\importlib\__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
tests\test_nc_coa_sync.py:39: in <module>
    from app.services.nc_coa_sync import (
E   ModuleNotFoundError: No module named 'app.services.nc_coa_sync'
=========================== short test summary info ===========================
ERROR tests/test_nc_coa_sync.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
============================== 1 error in 0.65s ===============================
```

Matches the brief's expected failure exactly.

### Step 5 — new tests, after implementation

```
cd /c/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
DATABASE_URL="postgresql+asyncpg://dummy:dummy@localhost:5432/dummy" \
JWT_SECRET_KEY="dummy" \
  /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/test_nc_coa_sync.py -v
```

Output:

```
collecting ... collected 29 items

tests/test_nc_coa_sync.py::test_coa_sync_run_roundtrip PASSED            [  3%]
tests/test_nc_coa_sync.py::test_coa_aux_item_required_defaults_false PASSED [  6%]
tests/test_nc_coa_sync.py::test_clean_treats_tilde_as_empty PASSED       [ 10%]
tests/test_nc_coa_sync.py::test_normal_balance_from_balanorient PASSED   [ 13%]
tests/test_nc_coa_sync.py::test_normal_balance_rejects_unknown PASSED    [ 17%]
tests/test_nc_coa_sync.py::test_account_type_mapping[1-0-asset] PASSED   [ 20%]
tests/test_nc_coa_sync.py::test_account_type_mapping[1-1-asset] PASSED   [ 24%]
tests/test_nc_coa_sync.py::test_account_type_mapping[2-1-liability] PASSED [ 27%]
tests/test_nc_coa_sync.py::test_account_type_mapping[4-1-equity] PASSED  [ 31%]
tests/test_nc_coa_sync.py::test_account_type_mapping[5-0-expense] PASSED [ 34%]
tests/test_nc_coa_sync.py::test_account_type_mapping[6-1-revenue] PASSED [ 37%]
tests/test_nc_coa_sync.py::test_account_type_mapping[6-0-expense] PASSED [ 41%]
tests/test_nc_coa_sync.py::test_account_type_rejects_unregistered_code PASSED [ 44%]
tests/test_nc_coa_sync.py::test_aux_item_maps_supported_dims PASSED      [ 48%]
tests/test_nc_coa_sync.py::test_aux_item_regression_project_substring_false_positives PASSED [ 51%]
tests/test_nc_coa_sync.py::test_aux_item_rejects_unregistered_code PASSED [ 55%]
tests/test_nc_coa_sync.py::test_derive_party_dim[112201-asset-customer] PASSED [ 58%]
tests/test_nc_coa_sync.py::test_derive_party_dim[220202-liability-supplier] PASSED [ 62%]
tests/test_nc_coa_sync.py::test_derive_party_dim[640202-expense-supplier] PASSED [ 65%]
tests/test_nc_coa_sync.py::test_derive_party_dim[6002-revenue-customer] PASSED [ 68%]
tests/test_nc_coa_sync.py::test_derive_party_dim[4001-equity-partner] PASSED [ 72%]
tests/test_nc_coa_sync.py::test_derive_party_dim_exceptions_never_become_customer PASSED [ 75%]
tests/test_nc_coa_sync.py::test_map_account_reads_facts_and_joins_masters PASSED [ 79%]
tests/test_nc_coa_sync.py::test_map_account_contra_asset_is_credit PASSED [ 82%]
tests/test_nc_coa_sync.py::test_map_account_tilde_unit_means_no_quantity_accounting PASSED [ 86%]
tests/test_nc_coa_sync.py::test_map_account_name_fallback_chain PASSED   [ 89%]
tests/test_nc_coa_sync.py::test_map_account_top_level_pid_tilde PASSED   [ 93%]
tests/test_nc_coa_sync.py::test_map_account_rejects_unknown_uom_pk PASSED [ 96%]
tests/test_nc_coa_sync.py::test_map_account_rejects_missing_accasoa_row PASSED [100%]

============================= 29 passed in 7.17s ==============================
```

### Step 6 — account_balance regression suite

```
cd /c/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
DATABASE_URL="postgresql+asyncpg://dummy:dummy@localhost:5432/dummy" \
JWT_SECRET_KEY="dummy" \
  /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/test_account_balance.py -v
```

Output:

```
collecting ... collected 20 items

tests/test_account_balance.py::test_account_balance_opening_movement_closing PASSED [  5%]
tests/test_account_balance.py::test_account_balance_excludes_draft PASSED [ 10%]
tests/test_account_balance.py::test_account_balance_endpoint PASSED      [ 15%]
tests/test_account_balance.py::test_budget_actual_by_cost_center PASSED  [ 20%]
tests/test_account_balance.py::test_expand_single_dim_matches_old_behavior PASSED [ 25%]
tests/test_account_balance.py::test_account_vouchers_drilldown_by_dims PASSED [ 30%]
tests/test_account_balance.py::test_budget_actual_endpoint PASSED        [ 35%]
tests/test_account_balance.py::test_jv_line_income_expense_item_column PASSED [ 40%]
tests/test_account_balance.py::test_coa_aux_item_roundtrip_and_unique PASSED [ 45%]
tests/test_account_balance.py::test_expand_two_dims_and_none_group PASSED [ 50%]
tests/test_account_balance.py::test_expand_rejects_unknown_dim PASSED    [ 55%]
tests/test_account_balance.py::test_expand_rejects_duplicate_dims PASSED [ 60%]
tests/test_account_balance.py::test_vouchers_filter_none_and_combo PASSED [ 65%]
tests/test_account_balance.py::test_dims_endpoint_config_and_fallback PASSED [ 70%]
tests/test_account_balance.py::test_expand_endpoint_dims_param PASSED    [ 75%]
tests/test_account_balance.py::test_aux_item_name_mapping PASSED         [ 80%]
tests/test_account_balance.py::test_nc_customer_roundtrip_and_unique PASSED [ 85%]
tests/test_account_balance.py::test_erp_supplier_mirror_readable PASSED  [ 90%]
tests/test_account_balance.py::test_customer_pick_name PASSED            [ 95%]
tests/test_account_balance.py::test_expand_by_supplier_and_customer PASSED [100%]

================== 20 passed, 6 warnings in 68.67s (0:01:08) ==================
```

(The 6 warnings are pre-existing `datetime.utcnow()` deprecation warnings
from `jose/jwt.py`, unrelated to this change.) 20/20 pass — the same count
as before the `DIM_LABELS` edit, confirming no regression.

## Commit

```
f78e505 feat(finance): NC COA mapping reads facts instead of inferring
3 files changed, 310 insertions(+), 1 deletion(-)
create mode 100644 finance-api/app/services/nc_coa_sync.py
```

Files: `finance-api/app/services/nc_coa_sync.py` (new),
`finance-api/app/crud/account_balance.py` (DIM_LABELS only),
`finance-api/tests/test_nc_coa_sync.py` (append).

## Concerns

None. All code was used verbatim from the brief, both new-test and
regression suites pass in full, and `DIM_LABELS` only gained keys — none of
the original 7 were altered. Git warned about LF→CRLF normalization on the
two touched/created files when staging; this is the repo's existing
`.gitattributes`/core.autocrlf behavior, not a content change made here.

---

## Fix: two review findings (missing raise-condition tests + dead constant)

### Finding 1 (Important) — two required raise conditions had no test

`map_account` is required to raise `NcMappingError` on four conditions;
only "unknown uom pk" and "missing ACCASOA row" were tested. Added two
tests to `finance-api/tests/test_nc_coa_sync.py`, placed next to
`test_map_account_rejects_unknown_uom_pk`, following its exact style:

```python
def test_map_account_rejects_unknown_acctype_pk():
    with pytest.raises(NcMappingError):
        map_account(_row(acctype_pk="NOSUCH"), **_lookups())

def test_map_account_rejects_unknown_currency_pk():
    with pytest.raises(NcMappingError):
        map_account(_row(currency_pk="NOSUCH"), **_lookups())
```

### Finding 2 (Minor) — dead constant

Deleted `PARTY_ITEM = "0004"` from `finance-api/app/services/nc_coa_sync.py`.
It was defined but never referenced — `AUX_ITEM_MAP` uses the literal
`"0004"` as its key, which was left untouched, per instructions.

### Test command and full output

```
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  DATABASE_URL=postgresql+asyncpg://x:x@localhost/x JWT_SECRET_KEY=x \
  /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/test_nc_coa_sync.py -v
```

```
collecting ... collected 31 items

tests/test_nc_coa_sync.py::test_coa_sync_run_roundtrip PASSED            [  3%]
tests/test_nc_coa_sync.py::test_coa_aux_item_required_defaults_false PASSED [  6%]
tests/test_nc_coa_sync.py::test_clean_treats_tilde_as_empty PASSED       [  9%]
tests/test_nc_coa_sync.py::test_normal_balance_from_balanorient PASSED   [ 12%]
tests/test_nc_coa_sync.py::test_normal_balance_rejects_unknown PASSED    [ 16%]
tests/test_nc_coa_sync.py::test_account_type_mapping[1-0-asset] PASSED   [ 19%]
tests/test_nc_coa_sync.py::test_account_type_mapping[1-1-asset] PASSED   [ 22%]
tests/test_nc_coa_sync.py::test_account_type_mapping[2-1-liability] PASSED [ 25%]
tests/test_nc_coa_sync.py::test_account_type_mapping[4-1-equity] PASSED  [ 29%]
tests/test_nc_coa_sync.py::test_account_type_mapping[5-0-expense] PASSED [ 32%]
tests/test_nc_coa_sync.py::test_account_type_mapping[6-1-revenue] PASSED [ 35%]
tests/test_nc_coa_sync.py::test_account_type_mapping[6-0-expense] PASSED [ 38%]
tests/test_nc_coa_sync.py::test_account_type_rejects_unregistered_code PASSED [ 41%]
tests/test_nc_coa_sync.py::test_aux_item_maps_supported_dims PASSED      [ 45%]
tests/test_nc_coa_sync.py::test_aux_item_regression_project_substring_false_positives PASSED [ 48%]
tests/test_nc_coa_sync.py::test_aux_item_rejects_unregistered_code PASSED [ 51%]
tests/test_nc_coa_sync.py::test_derive_party_dim[112201-asset-customer] PASSED [ 54%]
tests/test_nc_coa_sync.py::test_derive_party_dim[220202-liability-supplier] PASSED [ 58%]
tests/test_nc_coa_sync.py::test_derive_party_dim[640202-expense-supplier] PASSED [ 61%]
tests/test_nc_coa_sync.py::test_derive_party_dim[6002-revenue-customer] PASSED [ 64%]
tests/test_nc_coa_sync.py::test_derive_party_dim[4001-equity-partner] PASSED [ 67%]
tests/test_nc_coa_sync.py::test_derive_party_dim_exceptions_never_become_customer PASSED [ 70%]
tests/test_nc_coa_sync.py::test_map_account_reads_facts_and_joins_masters PASSED [ 74%]
tests/test_nc_coa_sync.py::test_map_account_contra_asset_is_credit PASSED [ 77%]
tests/test_nc_coa_sync.py::test_map_account_tilde_unit_means_no_quantity_accounting PASSED [ 80%]
tests/test_nc_coa_sync.py::test_map_account_name_fallback_chain PASSED   [ 83%]
tests/test_nc_coa_sync.py::test_map_account_top_level_pid_tilde PASSED   [ 87%]
tests/test_nc_coa_sync.py::test_map_account_rejects_unknown_uom_pk PASSED [ 90%]
tests/test_nc_coa_sync.py::test_map_account_rejects_unknown_acctype_pk PASSED [ 93%]
tests/test_nc_coa_sync.py::test_map_account_rejects_unknown_currency_pk PASSED [ 96%]
tests/test_nc_coa_sync.py::test_map_account_rejects_missing_accasoa_row PASSED [100%]

============================= 31 passed in 7.43s ==============================
```

31 passed (29 existing + 2 new), consistent with the expected count.

### Vacuity check

For each new test, temporarily disabled the corresponding raise, ran that
one test to confirm it fails, then restored the raise and re-ran the full
suite to confirm all 31 pass again.

**`currency_pk` test** — straightforward: commented out the
`if default_currency is None: raise NcMappingError(...)` block. Re-ran
`test_map_account_rejects_unknown_currency_pk` alone:

```
FAILED tests/test_nc_coa_sync.py::test_map_account_rejects_unknown_currency_pk
E       Failed: DID NOT RAISE <class 'app.services.nc_coa_sync.NcMappingError'>
```

Confirms the test is not vacuous — it depends on that raise.

**`acctype_pk` test** — first pass was surprising: commenting out only the
`if atype_code is None: raise NcMappingError(...)` block in `map_account`
did **not** make `test_map_account_rejects_unknown_acctype_pk` fail. Root
cause: `map_account` still calls `map_account_type(atype_code, ...)`
immediately after, and when `atype_code` is `None`,
`map_account_type` computes `code = (None or "").strip() == ""`, which is
not a key in `ACCOUNT_TYPE_BY_NC` and is independently caught by
`map_account_type`'s own `except KeyError: raise NcMappingError(...)` —
i.e. the explicit check in `map_account` is behaviorally redundant with a
backstop one level down. This is legitimate double protection, not a test
flaw: the required behavior ("unmapped acctype_pk raises") genuinely holds.

To confirm the test isn't vacuous against the actual regression the
finding warns about (an unmapped code silently resolving to a wrong
default, per this module's own docstring: "the old `return 'asset'`
catch-all is how the wrong data got in"), disabled *both* layers at once —
the explicit `map_account` check and `map_account_type`'s
`except KeyError: raise ...` (replaced with `return "asset"`, mirroring
the exact old buggy pattern the docstring references). Re-ran the test
alone:

```
FAILED tests/test_nc_coa_sync.py::test_map_account_rejects_unknown_acctype_pk
E       Failed: DID NOT RAISE <class 'app.services.nc_coa_sync.NcMappingError'>
```

This confirms the test correctly catches the real regression class it
guards against. Both temporary changes were then reverted and the full
31-test suite re-run to confirm a clean pass (output above is the restored
state).

### Commit

`81d725a` — `test(finance): cover acctype/currency raise paths, drop dead PARTY_ITEM constant`
