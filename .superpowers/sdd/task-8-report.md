# Task 8 Report: Routing parity verifier + phase 3 release notes

## Status: DONE (with one flagged, investigated, non-blocking finding — see Concerns)

## Commit
- `d0c0381` — "test(approval): routing parity verifier + phase 3 release notes"
  - `approval-api/scripts/verify_routing_parity.py` (new)
  - `docs/superpowers/plans/2026-07-15-approval-routing-phase3-release.md` (new)

## Parity verifier output (dev)

```
docker exec uniops_approval_api python -m scripts.verify_routing_parity
```

```
DIFF dept=0100 doc=pa step=finance_bp old=['907147d2-aabb-4fba-83e0-9bee9d01a33e'] new=['907147d2-aabb-4fba-83e0-9bee9d01a33e', 'f933fd52-bdfa-433d-b4c1-9226075bc7b0']
... (identical shape, one DIFF per active department — 12 total)
PARITY FAILED: 12 divergence(s) across 12 depts x 3 doc types   [exit 1]
```

**Root-caused, not papered over.** All 12 diffs are the same single cause: user
Yuping Huang (`f933fd52-...`) has **primary** `users.role = 'finance_bp'` (set
2026-06-30, unrelated to this branch), but was never in the old
`company_config.role_management.finance_bp_user_ids` curated list. The new
`_post_holders()` (approval-api/app/crud/workflow.py:33-46) unions
`users.role` ∪ `user_roles` for **every** post code including `finance_bp` —
this is Task 4's **deliberate, spec'd, reviewed-and-approved** design (spec
line 713 `codes = list(_POST_CODES) + ["finance_bp"]`; docstring "A post can
be held as a PRIMARY role or an ADDITIONAL role — both count"; existing
tests are load-bearing on this union per Task 4's own report). It is a
one-directional widening (adds an eligible approver, never removes one) and
was never covered by Task 3's "5 singleton posts match role_management"
parity check (finance_bp is explicitly non-singleton, list-type). This is
the first end-to-end check to exercise it. All other migrated getters
(`gm`/`opm`/`vendor_manager`/`finance_manager`/`procurement_manager`,
`gm_or_opm` dept resolution, `director`, `supervisor_enabled`) show **zero**
divergence across all 12 depts x 3 doc types.

Documented in the release notes' "验收阶段发现...一处行为变化" section with a
recommendation to cross-check prod's finance_bp-primary-role users against
the old curated list before go-live. Did not alter the verifier to mask this
— it is a real, reproducible, and now-permanent property of the new getters
given current dev data, not a seed/migration defect.

## Full regression

| Suite | Result | Expected |
|---|---|---|
| approval-api pytest | **37 passed** | 37+ |
| identity-api pytest | **20 passed** | 20 |
| expense-api pytest (docker exec) | **83 passed** | 83 |
| vms-api pytest (docker exec) | **175 passed** | 175 |
| portal tsc --noEmit | **0 errors** | 0 |
| epms tsc --noEmit (`grep -c "error TS"`) | **69** | 69 (baseline, unchanged) |

finance-api and epms-api pytest skipped per instructions (controller already
ran both on this exact code: finance 201 passed; epms failure set identical
to baseline minus the 4 deleted temp-assignment tests).

## Release notes

`docs/superpowers/plans/2026-07-15-approval-routing-phase3-release.md`,
modelled on the phase 1 release doc. Covers: 3 migrations (approval
`0001_approval_routing` first-ever + `migrate-prod.sh` confirmed updated,
identity `0003_post_role_singleton`, epms `z4_drop_temp_assignments`);
mandatory seed step with exact command, order (migrate → seed → parity →
up), and the "no-seed" failure mode stated plainly (every non-dept_manager
step fails to resolve an approver — worse than phase 1); seed's reassign
behavior + WARNING lines + when it is unsafe to re-run; zero new
infrastructure (verified: approval-api's prod compose block has no
`ALLOWED_ORIGINS`, `Caddyfile:52` confirms no subdomain, `APPROVAL_ENGINE_URL`
already present in both dev/prod compose for epms-api — verified line
numbers cited in the doc); rollback (four JSONB columns untouched;
temp_assignments downgrade recreates empty, table was 0 rows in prod); the
deliberate `reconstruct.py` exemption; concrete post-deploy verification
commands (epms-gateway script asserting departments=12 and
supervisor_on=0, plus a browser checklist).

## Concerns

- The finance_bp parity divergence above is real and will reproduce
  identically in prod if any user's primary role is `finance_bp` without
  being in the old curated list. It is not a code defect from this branch's
  work, but it is new information the user should have before running the
  prod seed — flagged prominently in the release notes; recommend a manual
  prod cross-check before go-live per the doc's wording.
- Everything else (migrations, seed order, gateway plumbing, rollback,
  regression) verified directly against the repo, not taken on faith.

## Follow-up fix (2026-07-15): the finance_bp diff above was a real over-grant, now fixed

The 12 DIFFs flagged above were re-triaged as an actual bug, not an
acceptable one-directional widening: `finance_bp` is exempt from
identity's singleton index specifically *because* it's a multi-holder job
function, not a company-unique position like the other five post codes.
Reading `users.role` for it conflates "holds the job function" with "is the
assigned approver" — Yuping Huang's primary role is `finance_bp` but she was
never added to `role_management.finance_bp_user_ids`, so she should never
have resolved as a PA approver.

### Status: DONE

### Root cause
`approval-api/app/crud/workflow.py::_post_holders` unioned `users.role` ∪
`user_roles` for **all six** post codes including `finance_bp`. Correct for
the five singleton posts (gm/opm/vendor_manager/finance_manager/
procurement_manager — identity enforces one holder each via
`0003_post_role_singleton` + the cross-table 409 in
`PUT /authz/users/{id}/roles`, so holding the primary role IS holding the
post). Wrong for `finance_bp`, which must resolve from the curated
ASSIGNMENT (`user_roles`) only.

### Changes
- `approval-api/app/crud/workflow.py` — `_post_holders()`: split the SQL so
  `users.role` is matched against `_POST_CODES` (five singletons) only,
  while `user_roles` continues to be matched against
  `_POST_CODES + ["finance_bp"]`. Added an explicit docstring explaining the
  singleton-position vs. multi-holder-job-function distinction and citing
  the real prod case (Yuping Huang) as the motivating example.
- `approval-api/scripts/seed_routing.py` — removed the
  `if primary == "finance_bp": continue` skip from the finance_bp loop. Since
  finance_bp is now resolved from `user_roles` only, skipping the insert
  when an assignee's primary role happens to also be `finance_bp` would make
  that assignee vanish as an approver entirely. The `ON CONFLICT (user_id,
  role_code) DO NOTHING` still makes repeat runs idempotent. The five
  singletons' skip-if-primary-matches logic and stale-holder
  reassignment were left untouched, per instructions.
- Tests added:
  - `test_routing_adapters.py::test_finance_bp_primary_role_alone_is_not_included`
    — user with `users.role='finance_bp'` and no `user_roles` row must NOT
    appear in `finance_bp_user_ids`. Reproduces the exact prod situation;
    fails against the pre-fix code.
  - `test_seed_routing.py::test_finance_bp_row_always_written_even_when_primary_role_matches`
    — an assigned finance_bp whose primary role is also `finance_bp` must
    still get a `user_roles` row written. Guards the seed script change.
  - Kept `test_post_from_primary_role_is_included` (gm) unchanged as the
    contrasting singleton-post case — the two tests together pin the
    distinction.

### Verification (foreground)

```
cd /c/Project/uniops/approval-api && TEST_PG_PASSWORD=*** ./.venv/Scripts/python -m pytest tests -q
```
```
39 passed in 18.78s
```
(37 pre-existing + 2 new; no regressions.)

```
docker exec uniops_approval_api python -m scripts.seed_routing
```
```
seed_routing done: {'user_roles': 0, 'dept_rows': 0, 'backups': 0, 'reassigned': 0}
```
No-op re-run, as expected — the live-mounted container picked up the source change without a rebuild.

```
docker exec uniops_approval_api python -m scripts.verify_routing_parity
```
```
PARITY OK (12 depts x 3 doc types)
```
Zero DIFFs — the 12 diffs from the original Task 8 run are gone. Yuping Huang
no longer resolves as a `finance_bp` approver; prod's only real assignee
(`907147d2-...`, PM test) is unaffected since she's read from `user_roles`
which already carries her via the original curated
`role_management.finance_bp_user_ids` seed.

### Commit
`fix(approval): finance_bp approvers come from the assignment only, not from holding the job function`

