# Payment Officer Role Implementation Plan (Phase 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split payment execution out of AP Clerk into a dedicated `payment_officer` additional role, with true segregation of duties enforced at finance-api's single payment gate, and reassign the existing backlog.

**Architecture:** `payment_officer` is an **additional** role (granted via `user_roles`, never a primary login role), seeded by an identity-api migration and registered in epms-api's Access Control Matrix. The `process_pa` task is redirected to it. Payment authority is enforced in exactly one place — `finance-api`'s `_PAY_ROLES` — because all three payment entry points (EPMS PA, OA Direct PA, Finance batches) funnel through `execute_payment`; `ap_clerk` is removed there.

**Tech Stack:** FastAPI + SQLAlchemy async (epms-api, finance-api, identity-api), Alembic, React + TypeScript (portal, epms), pytest.

**Spec:** `docs/superpowers/specs/2026-08-13-ap-payment-officer-and-match-visibility-design.md`

## Global Constraints

- Base branch: `feature/ap-payment-officer-and-match-visibility`, worktree `C:/Project/uniops-payofficer`, based on `origin/main` = `2cbf817`.
- Role code: `payment_officer`. Display label: `Payment Officer`.
- **Additional role, not a primary role.** It goes in epms-api's `BUILT_IN_ROLES` (Access Control Matrix) but **must NOT** go in `app/schemas/user.py`'s `VALID_ROLES`, which validates primary login roles only. The existing `erp_pa_officer` follows exactly this split — copy it, not the primary roles.
- **All user-facing UI copy is English.** Code comments may be Chinese.
- **Deployment ordering is a hard constraint:** the identity migration must run **before** the new images start.
- Do **not** add `payment_officer` to `epms-api/app/api/v1/invoices.py:49`'s `_AP_ROLES`. That set governs invoice *matching*, not payment; adding it would widen access in the opposite direction to this plan's purpose.

### Running tests

epms-api — see the identical section in `2026-08-13-invoice-match-visibility.md`. Same host-run requirement, same local-DB assertion, same serialisation rule.

finance-api and identity-api run their own suites; check each service's README or existing CI invocation for the exact command rather than assuming it matches epms-api.

---

### Task 1: Seed the role in identity

**Files:**
- Create: `identity-api/alembic/versions/0008_payment_officer_role.py`
- Test: `identity-api` migration check (command in Step 4)

**Interfaces:**
- Consumes: nothing
- Produces: `role_defs` row `payment_officer`; `role_permissions` grants for `view_pa`, `view_invoice`, `view_po`; `role_permission_locks` on `view_pa`

- [ ] **Step 1: Confirm the real chain tip**

```bash
cd /c/Project/uniops-payofficer/identity-api
ls alembic/versions/
grep -rn "down_revision" alembic/versions/ | sort
```

Following the `down_revision` links on 2026-08-13 the chain is 0001→0002→0003→0004→0005→0006→0007 with a single head at `0007_receipt_write_perm`, which is the value written below. **Re-verify anyway** — another session may have landed a migration since; do not assume filename order equals chain order. The new migration's `down_revision` must be the actual head. A wrong link creates a second head, and a downgrade will then roll back a sibling migration.

- [ ] **Step 2: Write the migration**

Create `identity-api/alembic/versions/0008_payment_officer_role.py`, structured exactly like `0004_erp_pa_officer_role.py` (read that file first):

```python
"""Seed the payment_officer role + its Access Control Matrix grants.

payment_officer is an ADDITIONAL role (grantable to many users, never a base
login role) that owns the Process-Payment task split out of ap_clerk. It gets
read visibility of the payables chain; payment authority itself is enforced in
finance-api's _PAY_ROLES, not by a matrix permission.

All inserts are ON CONFLICT DO NOTHING so this is safe to re-run, and
self-sufficient on a fresh DB (it seeds the permission_defs rows it references
so the role_permissions FKs resolve).
"""
from alembic import op

revision = "0008_payment_officer_role"
down_revision = "0007_receipt_write_perm"
branch_labels = None
depends_on = None

_ROLE = "payment_officer"
_LABEL = "Payment Officer"

_PERM_DEFS = {
    "view_po":      ("epms", "View Po", 1),
    "view_invoice": ("epms", "View Invoice", 3),
    "view_pa":      ("epms", "View Pa", 4),
}
_GRANTS = ["view_po", "view_invoice", "view_pa"]
_LOCKS = ["view_pa"]


def upgrade() -> None:
    for key, (module, label, sort) in _PERM_DEFS.items():
        op.execute(
            "INSERT INTO permission_defs(key,module,label,sort) "
            f"VALUES ('{key}','{module}','{label}',{sort}) "
            "ON CONFLICT (key) DO NOTHING")
    op.execute(
        "INSERT INTO role_defs(code,label,sort,is_active) "
        f"VALUES ('{_ROLE}','{_LABEL}',851,true) ON CONFLICT (code) DO NOTHING")
    for key in _GRANTS:
        op.execute(
            "INSERT INTO role_permissions(role_code,permission_key) "
            f"VALUES ('{_ROLE}','{key}') ON CONFLICT DO NOTHING")
    for key in _LOCKS:
        op.execute(
            "INSERT INTO role_permission_locks(role_code,permission_key) "
            f"VALUES ('{_ROLE}','{key}') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    op.execute(f"DELETE FROM role_permission_locks WHERE role_code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_permissions WHERE role_code = '{_ROLE}'")
    op.execute(f"DELETE FROM role_defs WHERE code = '{_ROLE}'")
```

Keep `revision` at or under 32 characters — an over-long id has broken a migration in this repo before.

- [ ] **Step 3: Verify there is exactly one head**

```bash
cd /c/Project/uniops-payofficer/identity-api
alembic heads
```

Expected: exactly one line. Two lines means Step 1's `down_revision` was wrong — fix before continuing.

- [ ] **Step 4: Apply and verify against a dev database**

Apply the migration to the local dev DB, then assert the rows landed. **Do not run alembic from the host with the repo `.env` loaded — it points at the production database.** Run it inside the identity container, or with `POSTGRES_*` explicitly overridden to the local docker Postgres.

```sql
SELECT code, label, is_active FROM role_defs WHERE code = 'payment_officer';
SELECT permission_key FROM role_permissions WHERE role_code = 'payment_officer' ORDER BY 1;
SELECT permission_key FROM role_permission_locks WHERE role_code = 'payment_officer';
```

Expected: one role row; three grants; one lock. Then run `alembic downgrade -1` and re-query to confirm all three return zero rows, then `alembic upgrade head` again.

- [ ] **Step 5: Commit**

```bash
cd /c/Project/uniops-payofficer
git add identity-api/alembic/versions/0008_payment_officer_role.py
git commit -m "feat(identity): seed the payment_officer additional role"
```

---

### Task 2: Register the role in epms-api's matrix

**Files:**
- Modify: `epms-api/app/crud/config.py` — `BUILT_IN_ROLES` (line ~18), `LOCKED_PERMISSIONS` (line ~28), `_ROLE_DEFAULTS` (line ~262), `_BUILTIN_ROLE_NAMES` (line ~540)
- Modify: `epms-api/app/crud/current_step.py:14` (display-name map)
- Test: `epms-api/tests/test_payment_officer_role.py` (create)

**Interfaces:**
- Consumes: `payment_officer` from Task 1
- Produces: the role appears in the Access Control Matrix payload with `view_pa` locked

- [ ] **Step 1: Write the failing test**

Create `epms-api/tests/test_payment_officer_role.py` asserting that the config/matrix endpoint lists `payment_officer`, that its label renders as `Payment Officer`, and that `view_pa` is reported as locked. Find the matrix endpoint by reading `epms-api/app/api/v1/config.py` and model the request on an existing matrix test (grep `tests/` for `LOCKED_PERMISSIONS` or `BUILT_IN_ROLES` to find one).

Add one guard test that pins the primary/additional distinction:

```python
def test_payment_officer_is_not_a_primary_login_role():
    from app.schemas.user import VALID_ROLES
    assert "payment_officer" not in VALID_ROLES, (
        "payment_officer 是附加角色。VALID_ROLES 校验的是主登录角色 —— "
        "加进去会让它可被设为某人的主角色,复制 erp_pa_officer 的处理方式")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /c/Project/uniops-payofficer/epms-api
LP=$(docker exec uniops_postgres printenv POSTGRES_PASSWORD)
POSTGRES_HOST=localhost POSTGRES_PORT=5432 POSTGRES_USER=epms POSTGRES_PASSWORD="$LP" \
  POSTGRES_DB=epms JWT_SECRET_KEY=test-secret \
  python -m pytest tests/test_payment_officer_role.py -q
```

Expected: the matrix assertions FAIL; the `VALID_ROLES` guard already PASSES (nothing added it yet) — that is correct, it is a regression guard.

- [ ] **Step 3: Register the role**

In `epms-api/app/crud/config.py`:

```python
# BUILT_IN_ROLES — add to the frozenset:
    "cfo", "auditor", "erp_pa_officer", "payment_officer", "system_admin",

# LOCKED_PERMISSIONS — add an entry:
    "payment_officer":      {"view_pa"},

# _ROLE_DEFAULTS — add alongside erp_pa_officer.
# NOTE: deliberately NOT _VIEW_ALL. This set must match Task 1's migration
# _GRANTS exactly (view_po / view_invoice / view_pa), or "what the migration
# seeds" and "what the matrix default claims" disagree forever.
# Narrower than erp_pa_officer's _VIEW_ALL on purpose: payment_officer exists
# to SEGREGATE duties, and it acts on already-approved PAs — receipt and
# requisition were verified upstream, so view_pr / view_gr are not needed.
    "payment_officer":      _P(view_po=True, view_invoice=True, view_pa=True, **_BOOKING),

# _BUILTIN_ROLE_NAMES — add the label:
    "payment_officer": "Payment Officer",
```

While in `_BUILTIN_ROLE_NAMES`, also add the missing `"erp_pa_officer": "ERP PA Officer",` — that role is in `BUILT_IN_ROLES` but absent from the label map, so it currently renders as a raw code. It is a one-line pre-existing gap in the exact dict being edited; note it in the commit message rather than leaving it half-registered next to a new role that is fully registered.

In `epms-api/app/crud/current_step.py`, add to the display-name map at line 14:

```python
    "payment_officer": "Payment Officer",
```

**Do not touch `app/schemas/user.py`.**

- [ ] **Step 4: Run test to verify it passes**

Same command as Step 2. Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /c/Project/uniops-payofficer
git add epms-api/app/crud/config.py epms-api/app/crud/current_step.py \
        epms-api/tests/test_payment_officer_role.py
git commit -m "feat(epms): register payment_officer in the access control matrix"
```

---

### Task 3: Redirect the process-payment task

**Files:**
- **Modify: `approval-api/app/crud/engine.py` — `_post_approve_pa` AND `_post_approve_pa_dir`** (the REAL emitters; verified 2026-08-13)
- Delete: `epms-api/app/crud/pa.py`'s `_create_process_pa_task` — **dead code**, no callers anywhere in epms-api
- Modify: `epms-api/app/crud/dashboard.py` (add `build_payment_officer` + routing branch near line 766)
- Test: `epms-api/tests/test_process_pa_task_role.py` (create)

**Interfaces:**
- Consumes: `payment_officer` registered in Task 2
- Produces: `process_pa` tasks carry `assigned_role == "payment_officer"`

- [ ] **Step 1: Write the failing test**

Create `epms-api/tests/test_process_pa_task_role.py` that drives a PA to fully-approved and asserts the emitted `process_pa` task has `assigned_role == "payment_officer"` and `assigned_user_id is None`. Read `tests/` for an existing PA-approval helper and reuse it; if every such helper needs approval-api (which fails locally, see the Phase 1 plan's notes), instead call `pa_crud._create_process_pa_task` directly with a constructed `PaymentApplication` and assert on the `Task` added to the session — a narrower but locally-runnable test. State in the test docstring which approach was taken and why.

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — role is `ap_clerk`.

- [ ] **Step 3: Change the assignment**

**The plan originally named the wrong file.** `epms-api`'s `_create_process_pa_task` has **no callers** — grep of `epms-api/` finds only its own definition. The live `process_pa` task is emitted by `approval-api/app/crud/engine.py` at two sites, both hardcoding `assigned_role="ap_clerk"`:

- `_post_approve_pa` — the PA-PO route (EPMS purchase payments)
- `_post_approve_pa_dir` — the **PA-DIR route (OA Direct PA)**, which the plan never mentioned at all

Change **both**, and delete the dead epms-api function so it cannot mislead the next reader the way it misled this plan.

In `approval-api/app/crud/engine.py` (both sites):

```python
        # 付款执行已从 AP Clerk 拆出为专职附加角色(2026-08-13):AP 的 Task Inbox
        # 此前同时堆着 AP Review 与 Payment 两类任务。权限侧的隔离在
        # finance-api 的 _PAY_ROLES,这里只负责把任务派给对的人。
        assigned_role="payment_officer",
```

- [ ] **Step 4: Run test to verify it passes**

Expected: PASS.

- [ ] **Step 5: Add the dashboard branch**

In `epms-api/app/crud/dashboard.py`, add `build_payment_officer(db)` modelled on `build_ap_clerk` (line 529) but scoped to payment work — approved-awaiting-payment counts, not unmatched/exception invoice counts — and register it in the role routing chain near line 766:

```python
    if role == "payment_officer":
        return await build_payment_officer(db)
```

Place the branch before the `ap_clerk` branch is irrelevant (codes are distinct), but keep the ordering style consistent with its neighbours.

- [ ] **Step 6: Verify the dashboard branch returns without error**

Add a test that calls the dashboard builder for `role="payment_officer"` and asserts it returns a `DashboardResponse` rather than falling through to a default or raising. A branch that is never exercised is a 500 waiting for the first user to hold the role.

- [ ] **Step 7: Commit**

```bash
cd /c/Project/uniops-payofficer
git add epms-api/app/crud/pa.py epms-api/app/crud/dashboard.py \
        epms-api/tests/test_process_pa_task_role.py
git commit -m "feat(epms): route process-payment tasks to payment_officer"
```

---

### Task 4: Enforce segregation of duties in finance-api

This is the single authoritative gate. EPMS's `process` action does no permission check of its own — it forwards to `execute_payment` (`epms-api/app/api/v1/pa.py:524`), and OA Direct PA and Finance batches arrive the same way. Changing `_PAY_ROLES` therefore closes all three entry points at once.

**Files:**
- Modify: `finance-api/app/crud/payment_execute.py:36` (`_PAY_ROLES`)
- Modify: `finance-api/app/core/deps.py:9` (`_FINANCE_ROLES`)
- Test: `finance-api/tests/test_payment_authority.py` (create or extend — check for an existing payment-permission test first)

**Interfaces:**
- Consumes: `payment_officer` from Task 1
- Produces: `ap_clerk` can no longer execute payments; `payment_officer` can

- [ ] **Step 1: Write the failing test**

Assert all four cases explicitly — the removal is as important as the addition, and the fallback roles are what keep payments possible when the new role holder is away:

```python
@pytest.mark.asyncio
async def test_ap_clerk_can_no_longer_execute_payments(db):
    with pytest.raises(PaymentPermissionError):
        await payment_execute._check_can_pay(db, _user(role="ap_clerk"))


@pytest.mark.asyncio
async def test_payment_officer_can_execute_payments(db):
    await payment_execute._check_can_pay(db, _user(role="payment_officer"))


@pytest.mark.asyncio
async def test_payment_officer_works_as_an_additional_role(db):
    # 附加角色走 user_roles,_user_role_codes 做主角色 ∪ 附加角色并集
    await payment_execute._check_can_pay(
        db, _user(role="requester", additional=["payment_officer"]))


@pytest.mark.asyncio
async def test_finance_manager_remains_a_fallback(db):
    # SoD 的可用性兜底:新角色的人休假不能让付款卡死
    await payment_execute._check_can_pay(db, _user(role="finance_manager"))
```

`_user` must produce whatever shape `_check_can_pay` consumes, and the additional-role case must insert a `user_roles` row (that is what `_user_role_codes` reads). Read `finance-api/app/crud/payment_execute.py:43-49` for the exact query before writing the fixture.

- [ ] **Step 2: Run test to verify it fails**

Expected: `test_ap_clerk_can_no_longer_execute_payments` and both `payment_officer` tests FAIL; `finance_manager` already PASSES.

- [ ] **Step 3: Apply the change**

```python
# finance-api/app/crud/payment_execute.py
# 职责隔离(2026-08-13):付款执行从 AP Clerk 拆出。ap_clerk 被**移除**,不是并存。
# finance_manager / finance_bp / system_admin 保留 —— 它们是可用性兜底,
# 使得 payment_officer 的持有人休假/离职时付款不会整体卡死。
_PAY_ROLES = {"payment_officer", "finance_manager", "finance_bp", "system_admin"}
```

```python
# finance-api/app/core/deps.py — 读权限,不是付款权限:新角色要能读付款相关端点
_FINANCE_ROLES = {"system_admin", "finance_manager", "finance_bp", "ap_clerk",
                  "payment_officer", "service_account"}
```

`ap_clerk` **stays** in `_FINANCE_ROLES` — AP still needs to read finance data; only the ability to *execute* payment is withdrawn.

- [ ] **Step 4: Run test to verify it passes**

Expected: all four PASS.

- [ ] **Step 5: Check `budget_scope`**

```bash
cd /c/Project/uniops-payofficer
sed -n '18,30p' finance-api/app/core/budget_scope.py
```

Decide from the surrounding code whether `payment_officer` needs a budget scope entry for the payment views it will open. If it does not, record that decision in the commit message; do not add it speculatively.

- [ ] **Step 6: Run the finance-api suite**

Compare the **failure set** against the same suite on a clean `2cbf817`, not the totals.

- [ ] **Step 7: Commit**

```bash
cd /c/Project/uniops-payofficer
git add finance-api/app/crud/payment_execute.py finance-api/app/core/deps.py \
        finance-api/tests/test_payment_authority.py
git commit -m "feat(finance): segregate payment execution from AP Clerk"
```

---

### Task 5: Expose the role in the admin UI

**Files:**
- Modify: Portal Admin's role-assignment UI (locate with the grep in Step 1)
- Modify: any epms component that hardcodes `ap_clerk` for payment affordances

**Interfaces:**
- Consumes: the role registered in Tasks 1-2
- Produces: an admin can grant `payment_officer` to a user

- [ ] **Step 1: Find every place a role list is hardcoded**

```bash
cd /c/Project/uniops-payofficer
grep -rn "erp_pa_officer" portal/src epms/src --include=*.ts --include=*.tsx
grep -rn "ap_clerk" portal/src epms/src --include=*.ts --include=*.tsx
```

The `erp_pa_officer` hits show exactly which files a previously-added additional role had to touch — that is the checklist. The `ap_clerk` hits must each be judged: a *payment* affordance gated on `ap_clerk` is now wrong; a *matching* or *invoice* affordance gated on `ap_clerk` is still correct and must be left alone.

- [ ] **Step 2: Add the role wherever `erp_pa_officer` appears**

Mirror it exactly, with the label `Payment Officer`.

- [ ] **Step 3: Fix payment affordances gated on `ap_clerk`**

For each payment-related hit from Step 1, switch it to the same authority the backend now enforces. Prefer asking the server (`GET /payments/can-pay` already exists and reflects `_check_can_pay`) over re-implementing the role set in the client — a client-side copy of `_PAY_ROLES` will drift.

- [ ] **Step 4: Verify the type gates**

```bash
cd /c/Project/uniops-payofficer/portal && npx tsc -p tsconfig.app.json 2>&1 | grep -c "error TS"
cd /c/Project/uniops-payofficer/epms   && npx tsc -p tsconfig.app.json 2>&1 | grep -c "error TS"
```

Compare each against a baseline measured on this branch before the edits. Note that Portal and epms are pinned to **different TypeScript versions** with different accepted flags — do not copy one project's command into the other.

- [ ] **Step 5: Verify reachability in a running app**

Grant the role to a test user in Portal Admin, confirm it persists, then confirm that user sees the Process Payment task and the payment controls, and that an `ap_clerk`-only user no longer sees the execute controls. **A type check is not evidence a user can reach the feature** — this branch's own history has three cases of "built correctly, user could not get there".

- [ ] **Step 6: Commit**

```bash
cd /c/Project/uniops-payofficer
git add portal/src epms/src
git commit -m "feat(portal): allow granting the Payment Officer role"
```

---

### Task 6: Reassign the existing backlog

**Files:**
- Create: `epms-api/scripts/reassign_process_pa_tasks.py`
- Create: `epms-api/tests/test_reassign_process_pa_tasks.py`

**Interfaces:**
- Consumes: `payment_officer` registered in Tasks 1-2
- Produces: a forward script and a reverse script, both idempotent

- [ ] **Step 1: Write the failing test**

Assert that the script moves only open `process_pa` tasks currently on `ap_clerk`, leaves completed ones untouched, leaves other task types untouched, is idempotent on a second run, and that the reverse function restores the original state.

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — module does not exist.

- [ ] **Step 3: Write the script**

It must support a **count-only mode that makes no writes**, because the release checklist requires a read-only inventory before the migration runs:

```sql
-- forward
UPDATE tasks SET assigned_role = 'payment_officer'
WHERE type = 'process_pa' AND is_completed = false AND assigned_role = 'ap_clerk';

-- reverse (rollback)
UPDATE tasks SET assigned_role = 'ap_clerk'
WHERE type = 'process_pa' AND is_completed = false AND assigned_role = 'payment_officer';
```

- [ ] **Step 4: Run test to verify it passes**

Expected: PASS.

- [ ] **Step 5: Take a read-only production inventory**

Run the count-only mode against production **read-only** and record the number. This number is the expected row count for the release, and without it the post-run verification has nothing to compare against.

- [ ] **Step 6: Commit**

```bash
cd /c/Project/uniops-payofficer
git add epms-api/scripts/reassign_process_pa_tasks.py \
        epms-api/tests/test_reassign_process_pa_tasks.py
git commit -m "chore(epms): script to reassign the process-payment backlog"
```

---

## Phase 2 exit criteria

- [ ] `alembic heads` in identity-api returns exactly one head
- [ ] Migration verified up **and** down against a dev DB
- [ ] `payment_officer` absent from `app/schemas/user.py`'s `VALID_ROLES` (guard test passes)
- [ ] finance-api tests cover all four authority cases, including `ap_clerk` being **denied**
- [ ] epms-api and finance-api failure **sets** compared against `2cbf817`
- [ ] Reachability confirmed in a running app: role grantable, task visible, payment controls correct on both sides of the split
- [ ] Backlog inventory number recorded before release

## Release notes for this phase

1. **Ordering is a hard constraint.** Run the identity `0008` migration **before** starting the new images — the new code queries the role's authorisation rows.
2. **Tell AP before deploying.** Removing `ap_clerk` from `_PAY_ROLES` makes `GET /payments/can-pay` return `false` for them, so the Create/Execute controls disappear from Payment Batches. This is the intended effect, but unannounced it will be reported as an outage.
3. **Grant the role to at least one person as part of the release**, or `process_pa` tasks land in a pool with no members.
4. **Run the reassignment script after the migration**, then verify positively: the new-role count equals the inventory from Task 6 Step 5, and the old-condition query returns zero rows. "No output" is not a pass.
5. Rollback: reverse script + revert `_PAY_ROLES` + un-grant the role in Portal Admin. The migration's `downgrade` removes the role rows.
