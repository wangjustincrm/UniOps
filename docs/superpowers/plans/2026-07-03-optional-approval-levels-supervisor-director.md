# Optional Approval Levels (Supervisor & Director) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two optional, admin-configurable approval levels — Supervisor (主管, between Requester and Dept Manager, PR-only, per-requester) and Director (总监, between Dept Manager and GM/OPM, PR+PA, per-department) — and make the Approval Timeline render the effective workflow (also fixing invisible over-budget steps).

**Architecture:** Optional steps are real nodes in `workflow_defs`, auto-skipped per-document when they have no active assignee. Routing follows the originating PR creator's identity (`routing_uid`). The approval engine (approval-api) is the single owner of workflow assembly; epms-api proxies the effective step list to the Timeline. Supervisor identity = new `users.supervisor_id`; Director identity = new `company_config.dept_director_mapping`.

**Tech Stack:** FastAPI + SQLAlchemy (async) + Alembic (Python 3.12), PostgreSQL (shared DB), React + TypeScript + React Query (Vite), pytest.

**Spec:** `docs/superpowers/specs/2026-07-03-department-director-approval-level-design.md`

---

## Conventions & pre-flight

- Run backend tests against the local docker postgres, not prod. Per repo memory:
  override `POSTGRES_*` to the local `uniops_postgres` container (password via
  `docker exec`). See `feedback_uniops_admin_test_db_env`.
- All user-facing strings are **English only** (`feedback_uniops_ui_english_only`).
- New mirror columns must match the physical table exactly — verify against
  `information_schema` before trusting any model (`feedback_uniops_mirror_models_match_reality`).
- Commit after every task. Do NOT push (`feedback_uniops_strict_version_control` —
  pushing requires explicit user consent).
- Frontend typecheck: `tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
  (`reference_uniops_frontend_tsc6`).

---

## Phase 1 — Schema foundation

### Task 1: Add `users.supervisor_id` + `company_config.dept_director_mapping` (epms-api owns schema)

**Files:**
- Create: `epms-api/alembic/versions/<rev>_supervisor_id_and_director_mapping.py`
- Modify: `epms-api/app/models/user.py` (add column)
- Modify: `epms-api/app/models/config.py:86` (add column near `dept_gm_opm_mapping`)

- [ ] **Step 1: Write the migration**

Generate a revision id and set `down_revision` to the current epms-api head
(find it: `cd epms-api && alembic heads`).

```python
"""supervisor_id on users + dept_director_mapping on company_config

Revision ID: <rev>
Revises: <current_head>
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "<rev>"
down_revision = "<current_head>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("supervisor_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_supervisor_id_users",
        "users", "users",
        ["supervisor_id"], ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_users_supervisor_id", "users", ["supervisor_id"])
    op.add_column(
        "company_config",
        sa.Column("dept_director_mapping", postgresql.JSONB(), nullable=False,
                  server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("company_config", "dept_director_mapping")
    op.drop_index("ix_users_supervisor_id", table_name="users")
    op.drop_constraint("fk_users_supervisor_id_users", "users", type_="foreignkey")
    op.drop_column("users", "supervisor_id")
```

- [ ] **Step 2: Add the model columns**

In `epms-api/app/models/user.py`, after `department_id`:

```python
    supervisor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
```

In `epms-api/app/models/config.py`, right after the `dept_gm_opm_mapping` line (86):

```python
    dept_director_mapping: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
```

- [ ] **Step 3: Apply the migration to the local test DB**

Run: `cd epms-api && alembic upgrade head`
Expected: no error; `\d users` shows `supervisor_id`, `\d company_config` shows
`dept_director_mapping`.

- [ ] **Step 4: Verify columns exist**

Run: `docker exec uniops_postgres psql -U postgres -d <db> -c "\d users" | grep supervisor_id`
Expected: one row showing `supervisor_id | uuid`.

- [ ] **Step 5: Commit**

```bash
git add epms-api/alembic/versions epms-api/app/models/user.py epms-api/app/models/config.py
git commit -m "feat(epms-api): add users.supervisor_id and company_config.dept_director_mapping"
```

---

### Task 2: Expose the new config field through epms-api schema + CRUD

**Files:**
- Modify: `epms-api/app/schemas/config.py:195` (update model) and `:249` (read model)
- Modify: `epms-api/app/crud/config.py:262` (create default) and `:309` (updatable allowlist)

- [ ] **Step 1: Write a failing test**

Create `epms-api/tests/test_config_director_mapping.py`:

```python
import pytest

@pytest.mark.asyncio
async def test_update_dept_director_mapping_roundtrips(client):
    dept = "11111111-1111-1111-1111-111111111111"
    user = "22222222-2222-2222-2222-222222222222"
    resp = await client.put("/api/v1/config", json={"dept_director_mapping": {dept: user}})
    assert resp.status_code == 200
    got = await client.get("/api/v1/config")
    assert got.json()["dept_director_mapping"] == {dept: user}
```

(Use the same `client` fixture the existing `tests/test_config.py` uses; copy its
auth/setup imports.)

- [ ] **Step 2: Run it, verify it fails**

Run: `cd epms-api && pytest tests/test_config_director_mapping.py -v`
Expected: FAIL — `dept_director_mapping` not accepted / not returned.

- [ ] **Step 3: Add the schema + CRUD wiring**

In `epms-api/app/schemas/config.py`, add to the update model (near line 195,
alongside `dept_supervisor_enabled`):

```python
    dept_director_mapping: dict[str, str] | None = None
```

and to the read model (near line 249):

```python
    dept_director_mapping: dict[str, Any]
```

In `epms-api/app/crud/config.py`, add to the create default (near line 262):

```python
        dept_director_mapping={},
```

and add `"dept_director_mapping"` to the updatable-fields allowlist (near line 309,
the tuple containing `"dept_gm_opm_mapping", "dept_supervisor_enabled", ...`).

- [ ] **Step 4: Run the test, verify it passes**

Run: `cd epms-api && pytest tests/test_config_director_mapping.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/schemas/config.py epms-api/app/crud/config.py epms-api/tests/test_config_director_mapping.py
git commit -m "feat(epms-api): expose dept_director_mapping via config API"
```

---

### Task 3: Mirror the new columns in approval-api

**Files:**
- Modify: `approval-api/app/models/user.py` (add `supervisor_id`)
- Modify: `approval-api/app/models/config.py:15` (add `dept_director_mapping` + `dept_supervisor_enabled`)

- [ ] **Step 1: Add mirror columns**

In `approval-api/app/models/user.py`, after `department_id`:

```python
    supervisor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
```

In `approval-api/app/models/config.py`, after `dept_gm_opm_mapping` (line 15):

```python
    dept_director_mapping: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dept_supervisor_enabled: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
```

(These physical columns exist — `dept_supervisor_enabled` from migration
`d4e5f6a7b8c9`, `dept_director_mapping` from Task 1. Mirror is read-only.)

- [ ] **Step 2: Add both models to the engine test tables**

In `approval-api/tests/conftest.py`, ensure `User` and `CompanyConfig` are in
`_ENGINE_TABLES` (PurchaseRequest already is). Add if missing so the test DB
builds these tables.

- [ ] **Step 3: Smoke-check the models import**

Run: `cd approval-api && python -c "from app.models.user import User; from app.models.config import CompanyConfig; print(User.supervisor_id, CompanyConfig.dept_director_mapping)"`
Expected: prints two column attributes, no ImportError.

- [ ] **Step 4: Commit**

```bash
git add approval-api/app/models/user.py approval-api/app/models/config.py approval-api/tests/conftest.py
git commit -m "feat(approval-api): mirror supervisor_id and dept_director/supervisor config"
```

---

## Phase 2 — Engine routing & skipping

### Task 4: Supervisor & Director resolvers (with active-user validation)

**Files:**
- Modify: `approval-api/app/crud/engine.py` (add two helpers near `_resolve_gm_or_opm`, ~line 334)
- Test: `approval-api/tests/test_engine_optional_levels.py` (new)

- [ ] **Step 1: Write failing tests**

Create `approval-api/tests/test_engine_optional_levels.py`. Use the existing
engine-test fixtures (copy setup from `test_engine_dept_manager_routing.py`:
`db` session, helpers to insert `User`, `CompanyConfig`, `PurchaseRequest`).

```python
import uuid
import pytest
from app.crud.engine import _resolve_director, _resolve_supervisor
from app.models.user import User
from app.models.config import CompanyConfig

@pytest.mark.asyncio
async def test_resolve_director_returns_active_mapped_user(db):
    dept = uuid.uuid4()
    director = User(id=uuid.uuid4(), email="d@x.com", hashed_password="x",
                    full_name="Dir", role="requester", department_id=None, is_active=True)
    requester = User(id=uuid.uuid4(), email="r@x.com", hashed_password="x",
                     full_name="Req", role="requester", department_id=dept, is_active=True)
    db.add_all([director, requester]); await db.flush()
    mapping = {str(dept): str(director.id)}
    assert await _resolve_director(db, requester.id, mapping) == director.id

@pytest.mark.asyncio
async def test_resolve_director_none_when_unmapped(db):
    dept = uuid.uuid4()
    requester = User(id=uuid.uuid4(), email="r2@x.com", hashed_password="x",
                     full_name="Req", role="requester", department_id=dept, is_active=True)
    db.add(requester); await db.flush()
    assert await _resolve_director(db, requester.id, {}) is None

@pytest.mark.asyncio
async def test_resolve_director_none_when_mapped_user_inactive(db):
    dept = uuid.uuid4()
    director = User(id=uuid.uuid4(), email="d3@x.com", hashed_password="x",
                    full_name="Dir", role="requester", is_active=False)
    requester = User(id=uuid.uuid4(), email="r3@x.com", hashed_password="x",
                     full_name="Req", role="requester", department_id=dept, is_active=True)
    db.add_all([director, requester]); await db.flush()
    assert await _resolve_director(db, requester.id, {str(dept): str(director.id)}) is None

@pytest.mark.asyncio
async def test_resolve_supervisor_requires_enabled_dept_and_active_user(db):
    dept = uuid.uuid4()
    sup = User(id=uuid.uuid4(), email="s@x.com", hashed_password="x",
               full_name="Sup", role="requester", is_active=True)
    req = User(id=uuid.uuid4(), email="rr@x.com", hashed_password="x",
               full_name="Req", role="requester", department_id=dept,
               is_active=True, supervisor_id=sup.id)
    db.add_all([sup, req]); await db.flush()
    # dept enabled → resolves
    assert await _resolve_supervisor(db, req.id, {str(dept): True}) == sup.id
    # dept not enabled → None
    assert await _resolve_supervisor(db, req.id, {str(dept): False}) is None
    assert await _resolve_supervisor(db, req.id, {}) is None
```

- [ ] **Step 2: Run, verify failure**

Run: `cd approval-api && pytest tests/test_engine_optional_levels.py -v`
Expected: FAIL — `_resolve_director` / `_resolve_supervisor` not defined.

- [ ] **Step 3: Implement the resolvers**

In `approval-api/app/crud/engine.py`, after `_resolve_gm_or_opm` (~line 346):

```python
async def _active_user_id(db: AsyncSession, user_id: uuid.UUID | None) -> uuid.UUID | None:
    """Return user_id iff it references an existing, active user; else None."""
    if user_id is None:
        return None
    row = (await db.execute(
        select(User.id).where(User.id == user_id, User.is_active.is_(True))
    )).scalar_one_or_none()
    return row


async def _resolve_director(
    db: AsyncSession, routing_uid: uuid.UUID, dept_director_mapping: dict,
) -> uuid.UUID | None:
    """Director for the requester's department, or None (unmapped / inactive)."""
    dept_id = (await db.execute(
        select(User.department_id).where(User.id == routing_uid)
    )).scalar_one_or_none()
    if not dept_id:
        return None
    uid_str = dept_director_mapping.get(str(dept_id))
    if not uid_str:
        return None
    return await _active_user_id(db, uuid.UUID(uid_str))


async def _resolve_supervisor(
    db: AsyncSession, routing_uid: uuid.UUID, dept_supervisor_enabled: dict,
) -> uuid.UUID | None:
    """The requester's assigned supervisor, iff their department has the
    supervisor level enabled and the assignee is active; else None."""
    row = (await db.execute(
        select(User.department_id, User.supervisor_id).where(User.id == routing_uid)
    )).first()
    if row is None:
        return None
    dept_id, supervisor_id = row
    if not dept_id or not dept_supervisor_enabled.get(str(dept_id)):
        return None
    return await _active_user_id(db, supervisor_id)
```

- [ ] **Step 4: Run, verify pass**

Run: `cd approval-api && pytest tests/test_engine_optional_levels.py -v`
Expected: PASS (the 4 resolver tests).

- [ ] **Step 5: Commit**

```bash
git add approval-api/app/crud/engine.py approval-api/tests/test_engine_optional_levels.py
git commit -m "feat(approval-api): supervisor/director resolvers with active-user validation"
```

---

### Task 5: Add supervisor/director nodes to default workflows + build_effective_workflow

**Files:**
- Modify: `approval-api/app/crud/engine.py` — `_WORKFLOW_DEFAULTS` (lines 184-229) and extract `build_effective_workflow`
- Modify: `epms-api/app/crud/config.py` — the `workflow_defs` seed default (locate the constant seeding `workflow_defs`)
- Create: migration `epms-api/alembic/versions/<rev>_workflow_defs_optional_levels.py` (data migration for existing rows)

- [ ] **Step 1: Update engine defaults**

In `approval-api/app/crud/engine.py`, change `_WORKFLOW_DEFAULTS["pr"]` and
`["pa"]` to include the optional nodes:

```python
    "pr": [
        {"id": "supervisor",   "role": "supervisor",   "label": "Supervisor"},
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
        {"id": "director",     "role": "director",     "label": "Director"},
        {"id": "gm_or_opm",    "role": "gm_or_opm",    "label": "GM / OPM"},
    ],
    "pa": [
        {"id": "dept_manager", "role": "dept_manager",   "label": "Department Manager"},
        {"id": "director",     "role": "director",       "label": "Director"},
        {"id": "gm_or_opm",    "role": "gm_or_opm",      "label": "GM / OPM"},
        {"id": "finance_bp",   "role": "finance_bp",     "label": "Finance BP"},
        {"id": "finance_mgr",  "role": "finance_manager", "label": "Finance Manager"},
    ],
```

- [ ] **Step 2: Extract `build_effective_workflow`**

In `execute_action`, the over-budget injection block (lines 693-705) currently
mutates a local `workflow`. Extract the assembly into a standalone function and
call it from `execute_action`. Add near `_get_workflow`:

```python
async def build_effective_workflow(
    db: AsyncSession, doc_type: str, doc: Any, cfg: CompanyConfig | None,
) -> list[dict]:
    """The ordered step list actually walked for THIS document: base workflow_defs
    plus any runtime-injected over-budget steps. Optional supervisor/director nodes
    stay in the list (skip is decided per-step at execution/render time)."""
    workflow = await _get_workflow(db, doc_type)
    if doc_type == "pr" and getattr(doc, "over_budget", False):
        bac = (cfg.budget_admin_config if cfg else None) or {}
        mode = bac.get("over_budget_mode", "fm_gm_opm")
        if mode != "hard_block":
            prepend = [{"id": "ob_finance_manager", "role": "finance_manager",
                        "label": "Over-Budget — Finance Manager"}]
            if mode == "fm_gm_opm":
                prepend.append({"id": "ob_gm_or_opm", "role": "gm_or_opm",
                                "label": "Over-Budget — GM / OPM"})
            workflow = prepend + workflow
    return workflow
```

Then in `execute_action`, replace the inline `_get_workflow` + injection block
(lines 679, 689-705) with:

```python
    workflow = await build_effective_workflow(db, doc_type, doc, cfg)
    over_budget_mode = ""
    if doc_type == "pr" and getattr(doc, "over_budget", False):
        over_budget_mode = ((cfg.budget_admin_config if cfg else None) or {}).get(
            "over_budget_mode", "fm_gm_opm")
```

(The `over_budget_mode` local is still needed for the `hard_block` submit guard.)

- [ ] **Step 3: Run existing engine tests — verify no regression**

Run: `cd approval-api && pytest tests/ -v`
Expected: existing tests still PASS (over-budget behaviour unchanged; new default
workflows only affect PR/PA which are exercised in later tasks). Fix any test
that hard-codes the old PR/PA step count.

- [ ] **Step 4: Seed + data-migrate existing workflow_defs**

Update the epms-api seed constant that populates `company_config.workflow_defs`
for `pr`/`pa` (in `epms-api/app/crud/config.py`) to the same 4-node PR / 5-node PA
lists as Step 1.

Then create a data migration so already-seeded production rows gain the nodes:

```python
"""insert supervisor/director nodes into existing workflow_defs

Revision ID: <rev>
Revises: <task1_rev>
"""
from alembic import op
import sqlalchemy as sa

revision = "<rev>"
down_revision = "<task1_rev>"

PR = [
    {"id": "supervisor",   "role": "supervisor",   "label": "Supervisor"},
    {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
    {"id": "director",     "role": "director",     "label": "Director"},
    {"id": "gm_or_opm",    "role": "gm_or_opm",    "label": "GM / OPM"},
]
PA = [
    {"id": "dept_manager", "role": "dept_manager",    "label": "Department Manager"},
    {"id": "director",     "role": "director",        "label": "Director"},
    {"id": "gm_or_opm",    "role": "gm_or_opm",       "label": "GM / OPM"},
    {"id": "finance_bp",   "role": "finance_bp",      "label": "Finance BP"},
    {"id": "finance_mgr",  "role": "finance_manager", "label": "Finance Manager"},
]

def upgrade() -> None:
    import json
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE company_config
        SET workflow_defs = jsonb_set(
            jsonb_set(COALESCE(workflow_defs, '{}'::jsonb), '{pr}', :pr::jsonb, true),
            '{pa}', :pa::jsonb, true)
    """), {"pr": json.dumps(PR), "pa": json.dumps(PA)})

def downgrade() -> None:
    pass  # non-destructive; leaving the nodes is harmless
```

- [ ] **Step 5: Apply + verify**

Run: `cd epms-api && alembic upgrade head`
Then: `docker exec uniops_postgres psql -U postgres -d <db> -c "SELECT workflow_defs->'pr' FROM company_config;"`
Expected: JSON array containing `supervisor`, `dept_manager`, `director`, `gm_or_opm`.

- [ ] **Step 6: Commit**

```bash
git add approval-api/app/crud/engine.py epms-api/app/crud/config.py epms-api/alembic/versions
git commit -m "feat: add supervisor/director nodes to PR/PA workflows + effective-workflow builder"
```

---

### Task 6: Wire supervisor/director into task creation, authorization, and auto-skip

**Files:**
- Modify: `approval-api/app/crud/engine.py` — `_create_approve_task`, `_actor_can_approve`, `_build_role_map`, the `approve` branch's `_holds`

- [ ] **Step 1: Write failing integration test**

Append to `approval-api/tests/test_engine_optional_levels.py` a full-chain test
(reuse the module's PR-insert + `execute_action` helpers, mirroring
`test_engine_dept_manager_routing.py`):

```python
from app.crud.engine import execute_action

@pytest.mark.asyncio
async def test_director_step_routes_after_manager(db, seed_pr_with_manager):
    """PR in a director-mapped dept: after manager approve, task lands on director."""
    ctx = await seed_pr_with_manager(dept_has_director=True)  # helper builds users+cfg+PR
    await execute_action(db, "pr", ctx.pr_id, "submit", ctx.requester_id, "requester")
    await execute_action(db, "pr", ctx.pr_id, "approve", ctx.manager_id, "dept_manager")
    # next task must be assigned to the director
    task = await ctx.current_task(db)
    assert task.assigned_role == "director"
    assert task.assigned_user_id == ctx.director_id

@pytest.mark.asyncio
async def test_director_step_skipped_when_unmapped(db, seed_pr_with_manager):
    ctx = await seed_pr_with_manager(dept_has_director=False)
    await execute_action(db, "pr", ctx.pr_id, "submit", ctx.requester_id, "requester")
    await execute_action(db, "pr", ctx.pr_id, "approve", ctx.manager_id, "dept_manager")
    task = await ctx.current_task(db)
    assert task.assigned_role == "gm_or_opm"  # director auto-skipped
```

Write the `seed_pr_with_manager` fixture in the test module: it inserts a
requester (with department), a `dept_manager` user in that department, optionally a
director + `dept_director_mapping`, a `CompanyConfig` (with `role_management` GM
user + `dept_gm_opm_mapping`), and a `PurchaseRequest` in `draft`. Expose helpers
`current_task(db)` (latest incomplete Task for the PR) and the ids.

- [ ] **Step 2: Run, verify failure**

Run: `cd approval-api && pytest tests/test_engine_optional_levels.py -k director_step -v`
Expected: FAIL — director step currently has no assignee resolution (task
assigned_user_id is None / wrong role).

- [ ] **Step 3: Load the new config maps in execute_action**

In `execute_action`, where `dept_gm_opm` is read (line 682), add:

```python
    dept_director = cfg.dept_director_mapping if cfg else {}
    dept_supervisor = cfg.dept_supervisor_enabled if cfg else {}
```

Compute the resolved ids once (after `routing_uid` is set, ~line 687):

```python
    director_uid = await _resolve_director(db, routing_uid, dept_director)
    supervisor_uid = await _resolve_supervisor(db, routing_uid, dept_supervisor)
```

Thread `director_uid` / `supervisor_uid` (and the maps) into
`_create_approve_task` and the `approve`-branch helpers via new parameters.

- [ ] **Step 4: Handle the roles in `_create_approve_task`**

In `_create_approve_task`, after the `gm_or_opm` branch (line 442), add:

```python
    elif role == "director":
        assigned_user_id = director_uid          # passed in; may be None (skipped upstream)
    elif role == "supervisor":
        assigned_user_id = supervisor_uid
```

Add `director_uid` / `supervisor_uid` params to the function signature and pass
them at both call sites in `execute_action` (submit + approve).

- [ ] **Step 5: Handle the roles in `_actor_can_approve` and `_holds`**

In `_actor_can_approve` (after the `gm_or_opm` branch, line 309), add:

```python
    if step_role == "director":
        return director_uid is not None and actor_id == director_uid
    if step_role == "supervisor":
        return supervisor_uid is not None and actor_id == supervisor_uid
```

(Pass `director_uid`/`supervisor_uid` in; the caller at line 734 already computes
`routing_uid` — compute the two ids there too, or pass the ones from Step 3.)

In the `approve` branch's local `_holds` (line 756), add:

```python
        if role == "director":
            return director_uid is not None and actor_id == director_uid
        if role == "supervisor":
            return supervisor_uid is not None and actor_id == supervisor_uid
```

- [ ] **Step 6: Run, verify director routing test passes**

Run: `cd approval-api && pytest tests/test_engine_optional_levels.py -k director_step -v`
Expected: `test_director_step_routes_after_manager` PASSES.
(`test_director_step_skipped_when_unmapped` still fails — skip logic is Task 7.)

- [ ] **Step 7: Commit**

```bash
git add approval-api/app/crud/engine.py approval-api/tests/test_engine_optional_levels.py
git commit -m "feat(approval-api): resolve director/supervisor in task creation and authorization"
```

---

### Task 7: Conditional skip — mid-chain look-ahead + leading step at submit

**Files:**
- Modify: `approval-api/app/crud/engine.py` — `_should_skip_step`, the `approve` look-ahead loop, the `submit` branch

- [ ] **Step 1: Write failing tests**

Append to `test_engine_optional_levels.py`:

```python
@pytest.mark.asyncio
async def test_supervisor_skipped_at_submit_when_dept_disabled(db, seed_pr_with_manager):
    ctx = await seed_pr_with_manager(dept_has_supervisor=False)
    await execute_action(db, "pr", ctx.pr_id, "submit", ctx.requester_id, "requester")
    task = await ctx.current_task(db)
    assert task.assigned_role == "dept_manager"  # supervisor step skipped at step 0

@pytest.mark.asyncio
async def test_supervisor_active_lands_first(db, seed_pr_with_manager):
    ctx = await seed_pr_with_manager(dept_has_supervisor=True)
    await execute_action(db, "pr", ctx.pr_id, "submit", ctx.requester_id, "requester")
    task = await ctx.current_task(db)
    assert task.assigned_role == "supervisor"
    assert task.assigned_user_id == ctx.supervisor_id

@pytest.mark.asyncio
async def test_inactive_director_skips_with_warning_event(db, seed_pr_with_manager):
    ctx = await seed_pr_with_manager(dept_has_director=True, director_active=False)
    await execute_action(db, "pr", ctx.pr_id, "submit", ctx.requester_id, "requester")
    await execute_action(db, "pr", ctx.pr_id, "approve", ctx.manager_id, "dept_manager")
    task = await ctx.current_task(db)
    assert task.assigned_role == "gm_or_opm"
    # a warning-flavoured skip event was recorded for the director step
    events = await ctx.events(db)
    assert any("configured Director is inactive" in (e.comment or "") for e in events)
```

Extend `seed_pr_with_manager` with `dept_has_supervisor` / `director_active`
params.

- [ ] **Step 2: Run, verify failure**

Run: `cd approval-api && pytest tests/test_engine_optional_levels.py -k "skipped or active_lands or inactive" -v`
Expected: FAIL — supervisor step is created at submit; director inactive not skipped.

- [ ] **Step 3: Extend `_should_skip_step`**

Change its signature to accept the resolved ids and return a reason string:

```python
def _should_skip_step(role: str, doc_type: str, doc: Any,
                      director_uid, supervisor_uid,
                      dept_has_director: bool, dept_has_supervisor: bool) -> tuple[bool, str]:
    if role == "quality_manager" and doc_type == "vms_visit":
        if getattr(doc, "quality_approver_id", None) is None:
            return True, "Auto-skipped (access area does not require Quality Manager review)"
    if role == "director":
        if director_uid is None:
            reason = ("Auto-skipped ⚠ configured Director is inactive/missing"
                      if dept_has_director else "Auto-skipped (department has no Director)")
            return True, reason
    if role == "supervisor":
        if supervisor_uid is None:
            reason = ("Auto-skipped ⚠ configured Supervisor is inactive/missing"
                      if dept_has_supervisor else "Auto-skipped (no Supervisor assigned)")
            return True, reason
    return False, ""
```

`dept_has_director` = `str(requester_dept) in dept_director_mapping`;
`dept_has_supervisor` = `bool(dept_supervisor_enabled.get(str(requester_dept)))`.
Compute both once alongside `director_uid`/`supervisor_uid` and pass through. In
the `approve` look-ahead loop (line 784), update the `_should_skip_step(...)` call
to the new signature.

- [ ] **Step 4: Skip leading inactive steps at submit**

Replace the `submit` branch's unconditional step-0 task creation (lines 721-725)
with a first-active-step search:

```python
        _set_status(meta, doc, "submitted")
        if hasattr(doc, "submitted_at"):
            doc.submitted_at = now
        start = 0
        while start < len(workflow):
            role = workflow[start]["role"]
            skip, reason = _should_skip_step(role, doc_type, doc, director_uid,
                                             supervisor_uid, dept_has_director, dept_has_supervisor)
            if not skip:
                break
            db.add(ApprovalEvent(
                document_type=doc_type, document_id=doc.id, document_number=doc_number,
                step_idx=start, action="approve", actor_id=actor_id,
                actor_role=role, comment=reason,
            ))
            start += 1
        doc.approval_step_idx = start
        if start < len(workflow):
            await _create_approve_task(db, doc_type, doc, step=start, workflow=workflow,
                                       meta=meta, rm=rm, dept_gm_opm=dept_gm_opm,
                                       routing_uid=routing_uid, director_uid=director_uid,
                                       supervisor_uid=supervisor_uid)
        else:
            _set_status(meta, doc, "approved")  # degenerate: everything skipped
```

- [ ] **Step 5: Run, verify pass**

Run: `cd approval-api && pytest tests/test_engine_optional_levels.py -v`
Expected: all optional-level tests PASS.

- [ ] **Step 6: Run full engine suite — no regression**

Run: `cd approval-api && pytest tests/ -v`
Expected: PASS. Fix any pre-existing test asserting old step indices.

- [ ] **Step 7: Commit**

```bash
git add approval-api/app/crud/engine.py approval-api/tests/test_engine_optional_levels.py
git commit -m "feat(approval-api): auto-skip supervisor/director steps (mid-chain + at submit)"
```

---

## Phase 3 — Access control (epms-api)

### Task 8: Add `supervisor` and `director` permission roles

**Files:**
- Modify: `epms-api/app/crud/config.py:212` (`_DEFAULT_ROLE_PERMISSIONS`)
- Test: `epms-api/tests/test_config_roles.py` (new)

- [ ] **Step 1: Write failing test**

```python
import pytest
from app.crud.config import get_effective_role_permissions
from app.models.config import CompanyConfig

def test_supervisor_and_director_default_perms():
    cfg = CompanyConfig(role_permissions={}, custom_roles=[])
    matrix = get_effective_role_permissions(cfg)
    assert matrix["supervisor"]["view_pr"] is True
    assert matrix["director"]["view_pr"] is True
    assert matrix["director"]["view_pa"] is True
```

- [ ] **Step 2: Run, verify failure**

Run: `cd epms-api && pytest tests/test_config_roles.py -v`
Expected: FAIL — KeyError `supervisor`.

- [ ] **Step 3: Add the roles**

In `_DEFAULT_ROLE_PERMISSIONS` (line 212), add entries using the existing `_P`
helper (line 199):

```python
    "supervisor":           _P(view_pr=True),
    "director":             _P(view_pr=True, view_pa=True),
```

- [ ] **Step 4: Run, verify pass**

Run: `cd epms-api && pytest tests/test_config_roles.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/crud/config.py epms-api/tests/test_config_roles.py
git commit -m "feat(epms-api): add supervisor/director permission roles"
```

---

### Task 9: Visibility scope for supervisor & director

**Files:**
- Modify: `epms-api/app/core/access_scope.py` — `_RESTRICTED_ROLES` (30), `_effective_role_codes` (55), `visible_pr_subquery` (126), add `_director_dept_ids`
- Test: `epms-api/tests/test_pr_scoping.py` (extend)

- [ ] **Step 1: Write failing tests**

Extend `epms-api/tests/test_pr_scoping.py` (reuse its config-seeding + user
helpers):

```python
@pytest.mark.asyncio
async def test_director_sees_mapped_dept_prs(test_engine):
    # dept D mapped to director U; a PR created by a requester in D is visible to U
    ...  # arrange via existing helpers; assert is_pr_visible(...) is True

@pytest.mark.asyncio
async def test_supervisor_sees_only_direct_reports_prs(test_engine):
    # requester R has supervisor S; a PR by R visible to S, a PR by unrelated user not
    ...
```

Fill in using the same arrange/act helpers already in the file (seed config,
insert users/PRs, build scope via `build_scope`, assert `is_pr_visible`).

- [ ] **Step 2: Run, verify failure**

Run: `cd epms-api && pytest tests/test_pr_scoping.py -k "director or supervisor" -v`
Expected: FAIL — roles fall through to unrestricted / empty.

- [ ] **Step 3: Implement**

In `access_scope.py`:

Add to `_RESTRICTED_ROLES` (line 30): `"supervisor"`, `"director"`.

In `_effective_role_codes` (after the finance_bp block, line 83):

```python
    if uid_str in (cfg.dept_director_mapping or {}).values():
        codes.add("director")
    is_supervisor = (await db.execute(
        select(User.id).where(User.supervisor_id == user_id).limit(1)
    )).scalar_one_or_none()
    if is_supervisor is not None:
        codes.add("supervisor")
```

Add a helper next to `_mapped_dept_ids`:

```python
async def _director_dept_ids(db: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()
    if not cfg or not cfg.dept_director_mapping:
        return []
    return [uuid.UUID(d) for d, u in cfg.dept_director_mapping.items() if u == str(user_id)]
```

In `visible_pr_subquery`, before the final `return None`, add branches:

```python
    if role == "director":
        dept_ids = await _director_dept_ids(db, user_id)
        if not dept_ids:
            return select(PurchaseRequest.id).where(False)
        cc_subq = select(CostCenter.id).where(CostCenter.department_id.in_(dept_ids))
        creator_subq = select(User.id).where(User.department_id.in_(dept_ids))
        return select(PurchaseRequest.id).where(
            or_(PurchaseRequest.cost_center_id.in_(cc_subq),
                PurchaseRequest.created_by.in_(creator_subq))
        )

    if role == "supervisor":
        reports = select(User.id).where(User.supervisor_id == user_id)
        return select(PurchaseRequest.id).where(PurchaseRequest.created_by.in_(reports))
```

Note: because `director`/`supervisor` are in `_RESTRICTED_ROLES`,
`_has_unrestricted_special_role` will not grant them blanket access. A director's
PO/PA visibility flows through the existing non-requester `visible_po_subquery`
path (POs linked to visible PRs) — no change needed.

- [ ] **Step 4: Run, verify pass**

Run: `cd epms-api && pytest tests/test_pr_scoping.py -v`
Expected: PASS (new + existing scoping tests).

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/core/access_scope.py epms-api/tests/test_pr_scoping.py
git commit -m "feat(epms-api): PR visibility for supervisor (direct reports) and director (mapped depts)"
```

---

## Phase 4 — Timeline (effective workflow)

### Task 10: Expose effective workflow from approval-api

**Files:**
- Modify: `approval-api/app/api/v1/approvals.py` (add GET endpoint)
- Test: `approval-api/tests/test_effective_workflow_endpoint.py` (new)

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_workflow_steps_endpoint_returns_effective_nodes(client, seed_over_budget_pr):
    doc_id = seed_over_budget_pr  # PR flagged over_budget with fm_gm_opm mode
    resp = await client.get(f"/approval/v1/approvals/pr/{doc_id}/workflow-steps")
    assert resp.status_code == 200
    ids = [s["id"] for s in resp.json()]
    assert ids[:2] == ["ob_finance_manager", "ob_gm_or_opm"]  # injected steps present
    assert "director" in ids and "supervisor" in ids
```

- [ ] **Step 2: Run, verify failure**

Run: `cd approval-api && pytest tests/test_effective_workflow_endpoint.py -v`
Expected: FAIL — 404 (endpoint missing).

- [ ] **Step 3: Implement the endpoint**

In `approval-api/app/api/v1/approvals.py`:

```python
from app.crud.engine import build_effective_workflow, _resolve_meta

@router.get("/{doc_type}/{doc_id}/workflow-steps")
async def workflow_steps(doc_type: str, doc_id: uuid.UUID,
                         db: AsyncSession = Depends(get_db), _: CurrentUser = ...):
    meta = _resolve_meta(doc_type)
    doc = (await db.execute(select(meta["model"]).where(meta["model"].id == doc_id))).scalar_one_or_none()
    if doc is None:
        raise HTTPException(404, f"{doc_type.upper()} not found")
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()
    return await build_effective_workflow(db, doc_type, doc, cfg)
```

(Match the auth dependency + imports used by the existing action endpoint in the
same file.)

- [ ] **Step 4: Run, verify pass**

Run: `cd approval-api && pytest tests/test_effective_workflow_endpoint.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add approval-api/app/api/v1/approvals.py approval-api/tests/test_effective_workflow_endpoint.py
git commit -m "feat(approval-api): GET workflow-steps returns effective workflow for a doc"
```

---

### Task 11: Proxy effective workflow through epms-api

**Files:**
- Modify: `epms-api/app/services/approval_client.py` (add `get_workflow_steps`)
- Modify: `epms-api/app/api/v1/pr.py:188` (add `/pr/{id}/workflow-steps` endpoint); mirror in `po.py`, `pa.py`

- [ ] **Step 1: Add the client call**

In `approval_client.py`:

```python
async def get_workflow_steps(doc_type: str, doc_id: str, bearer_token: str) -> list[dict]:
    url = f"{settings.APPROVAL_ENGINE_URL}/approvals/{doc_type}/{doc_id}/workflow-steps"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {bearer_token}"})
    if resp.status_code == 404:
        raise LookupError(resp.json().get("detail", "Not found"))
    resp.raise_for_status()
    return resp.json()
```

- [ ] **Step 2: Add the epms-api proxy endpoints**

In `pr.py` (near the events endpoint, line 188), using the same auth dep as
`pr_action`:

```python
@router.get("/{pr_id}/workflow-steps")
async def pr_workflow_steps(pr_id: uuid.UUID, request: Request, user: CurrentUserPayload):
    token = request.headers["authorization"].split(" ", 1)[1]
    return await approval_client.get_workflow_steps("pr", str(pr_id), token)
```

Add the analogous endpoint to `po.py` (`doc_type="po"`) and `pa.py`
(`doc_type="pa"`, or `pa_dir` per that page's doc kind).

- [ ] **Step 3: Manual smoke test**

Run the stack; `curl` an existing submitted PR:
`curl -H "Authorization: Bearer <token>" localhost:8000/api/v1/pr/<id>/workflow-steps`
Expected: JSON array of `{id, role, label}` including injected/optional nodes.

- [ ] **Step 4: Commit**

```bash
git add epms-api/app/services/approval_client.py epms-api/app/api/v1/pr.py epms-api/app/api/v1/po.py epms-api/app/api/v1/pa.py
git commit -m "feat(epms-api): proxy effective workflow-steps for PR/PO/PA timelines"
```

---

### Task 12: Render the Timeline from effective workflow

**Files:**
- Modify: `epms/src/services/pr.ts` (add `workflowSteps` fetch), `po.ts`, `pa.ts`
- Modify: `epms/src/hooks/usePrs.ts` (add `usePrWorkflowSteps`)
- Modify: `epms/src/pages/pr/PrDetailPage.tsx:264`, `po/PoDetailPage.tsx:593`, `pa/PaDetailPage.tsx` (feed workflow steps into `buildWorkflowSteps`)

- [ ] **Step 1: Add service + hook**

In `pr.ts`, next to `events` (line 144):

```typescript
  workflowSteps: (id: string) =>
    api.get<WorkflowNodeDef[]>(`/pr/${id}/workflow-steps`),
```

In `usePrs.ts`, next to `usePrEvents`:

```typescript
export function usePrWorkflowSteps(id: string) {
  return useQuery({
    queryKey: ['pr', id, 'workflow-steps'],
    queryFn: () => prService.workflowSteps(id),
    enabled: !!id,
  })
}
```

- [ ] **Step 2: Use it in the detail page**

In `PrDetailPage.tsx`, replace the static nodes passed to `buildWorkflowSteps`
(line ~264) with the fetched effective steps:

```tsx
const { data: workflowSteps } = usePrWorkflowSteps(id ?? '')
...
const approvalSteps = buildWorkflowSteps(
  workflowSteps ?? [],   // was: config?.workflow_defs?.pr ?? []
  pr.status,
  pr.approval_step_idx ?? 0,
  events ?? [],
  pr.created_by_name,
)
```

Do the same in `PoDetailPage.tsx` (line 593) and `PaDetailPage.tsx` with their
respective hooks (`usePoWorkflowSteps`, `usePaWorkflowSteps` — add these mirroring
Step 1).

- [ ] **Step 3: Typecheck**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors.

- [ ] **Step 4: Manual verification**

Open an over-budget PR detail page: the Timeline now shows the over-budget
Finance Manager / GM steps (previously invisible). Open a Marketing PR: Supervisor
and Director nodes appear; departments without them show the nodes as skipped.

- [ ] **Step 5: Commit**

```bash
git add epms/src/services epms/src/hooks/usePrs.ts epms/src/pages/pr/PrDetailPage.tsx epms/src/pages/po/PoDetailPage.tsx epms/src/pages/pa/PaDetailPage.tsx
git commit -m "feat(epms): render approval Timeline from effective workflow (shows optional + over-budget steps)"
```

---

## Phase 5 — Admin configuration UI

### Task 13: Director department mapping + supervisor assignment UI

**Files:**
- Modify: `epms/src/pages/admin/AdminPanel.tsx` (director mapping section; supervisor picker on user edit)
- Modify: `epms/src/services/config.ts:164` (add `dept_director_mapping` type)
- Modify: `epms/src/services/users.ts` (support `supervisor_id` in user update)

- [ ] **Step 1: Add config type**

In `config.ts`, add to the config interface (near line 164):

```typescript
  dept_director_mapping: Record<string, string>   // deptId -> directorUserId
```

- [ ] **Step 2: Director mapping section**

In `AdminPanel.tsx`, beside the existing GM/OPM department mapping, add a
"Department Directors" table: one row per department, each with a user-directory
picker (reuse the same directory search component the GM/OPM mapping uses) writing
`{ [deptId]: directorUserId }`. Save via `updateConfig.mutate({ dept_director_mapping })`.
Copy: "Directors approve after the Department Manager. Leave blank to skip."

- [ ] **Step 3: Supervisor picker on user edit**

In the user edit form, add a "Supervisor" field: a user-directory picker writing
`supervisor_id` on the user. Extend `users.ts` update payload/type with
`supervisor_id: string | null`. Clarify the existing per-department Supervisor
toggle copy: "Requires each requester in this department to have a Supervisor
assigned; those without one route straight to the Manager."

- [ ] **Step 4: Typecheck**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: no errors.

- [ ] **Step 5: Manual verification**

Assign a director to Marketing and a supervisor to a Marketing requester; submit a
PR as that requester and confirm the chain is `Supervisor → Manager → Director →
GM/OPM` in the Timeline.

- [ ] **Step 6: Commit**

```bash
git add epms/src/pages/admin/AdminPanel.tsx epms/src/services/config.ts epms/src/services/users.ts
git commit -m "feat(epms): admin UI for director mapping and per-user supervisor assignment"
```

---

## Final verification

- [ ] `cd approval-api && pytest tests/ -v` — all PASS
- [ ] `cd epms-api && pytest tests/ -v` — all PASS
- [ ] `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` — clean
- [ ] Manual end-to-end: Marketing PR walks `Supervisor → Manager → Director → GM/OPM`; a non-configured department walks `Manager → GM/OPM` with both optional nodes shown as skipped; an over-budget PR shows its injected steps in the Timeline.
- [ ] Report status to the user. Do NOT push — pushing requires explicit consent.
