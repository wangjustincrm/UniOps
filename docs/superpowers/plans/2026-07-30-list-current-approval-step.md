# Current Approval Step in List Views — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the current approval step (role, approver name, days waiting) directly in each `in_review` row of the PR / PO / PA List views, and make the Status column sort by status → step role → approver name.

**Architecture:** Backend enriches each list item with a `current_step` object sourced from the open `approve_{doctype}` task's `assigned_role` (the resync-authoritative "real current step"), not from the fragile `approval_step_idx`. A single batched query per list call — no N+1. Frontend renders a muted subtext under the status badge and adds a shared multi-key comparator for the Status column.

**Tech Stack:** epms-api (FastAPI + SQLAlchemy async + Pydantic v2), epms frontend (React + TypeScript 5.9.3 + Vite).

## Global Constraints

- Zero DB migration — display-only; reads the existing `tasks` mirror table.
- UI copy is English only (role labels, `d` unit). Approver name is data, rendered as-is.
- epms frontend baseline is **59** tsc errors; gate `cd epms && npx tsc -p tsconfig.app.json --noEmit` must stay at 59 (no `--ignoreDeprecations` flag — TS 5.9.3). The epms frontend has **no** JS test runner; frontend verification = tsc baseline + manual smoke.
- Authoritative current step = open (`is_completed == False`) `approve_{doctype}` task's `assigned_role`; never index a static chain by `approval_step_idx` (over-budget PRs inject extra front steps; idx can be stale after routing changes).
- Multi-session discipline: work only on branch `feature/list-current-approval-step` (worktree `C:/Project/uniops-liststep`); commit per task; do not touch `main`.

---

### Task 1: Backend — role maps + `CurrentStep` schema + `enrich_current_step` helper

The core, independently testable unit: given a list of items and a doc_type, attach `current_step` to each `in_review` item that has an open approval task.

**Files:**
- Create: `epms-api/app/crud/current_step.py`
- Create: `epms-api/app/schemas/current_step.py`
- Test: `epms-api/tests/test_current_step_enrich.py`

**Interfaces:**
- Produces: `ROLE_LABELS: dict[str, str]`, `ROLE_ORDER: dict[str, int]`, and `async def enrich_current_step(db: AsyncSession, doc_type: str, items: list) -> None` (mutates each item in place, setting `.current_step` to a dict `{role, label, approver_name, since}` or `None`). `doc_type` is one of `"pr" | "po" | "pa"`.
- Produces: `CurrentStep` Pydantic model (`role: str`, `label: str`, `approver_name: str | None = None`, `since: datetime`).

- [ ] **Step 1: Write the `CurrentStep` schema**

Create `epms-api/app/schemas/current_step.py`:

```python
"""Shared schema for the current approval step surfaced in list views."""
from datetime import datetime

from pydantic import BaseModel


class CurrentStep(BaseModel):
    """The approval step an in-review document is currently waiting on.

    Sourced from the open approve_{doctype} task, not approval_step_idx.
    """
    role: str
    label: str
    approver_name: str | None = None
    since: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 2: Write the failing test**

Create `epms-api/tests/test_current_step_enrich.py`:

```python
"""enrich_current_step attaches the open approval task's role/approver/since
to in_review list items, sourced from the tasks mirror (not approval_step_idx)."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.crud.current_step import ROLE_LABELS, ROLE_ORDER, enrich_current_step
from app.models.task import Task
from app.models.user import User

TAG = uuid.uuid4().hex[:8]


def _task(doc_id, *, role, user_id=None):
    return Task(
        id=uuid.uuid4(), type="approve_pr", priority="normal",
        document_type="pr", document_id=doc_id, document_number="PR-X",
        assigned_role=role, assigned_user_id=user_id,
        title="Approve", is_completed=False,
    )


async def test_enrich_attaches_current_step_with_approver(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    doc_id = uuid.uuid4()
    approver = uuid.uuid4()
    async with sf() as db:
        db.add(User(id=approver, email=f"gm-{TAG}@x.com",
                    hashed_password=hash_password("x"), full_name="Zhang San",
                    role="gm_or_opm", is_active=True))
        db.add(_task(doc_id, role="gm_or_opm", user_id=approver))
        await db.commit()

        items = [SimpleNamespace(id=doc_id, status="in_review")]
        await enrich_current_step(db, "pr", items)

    cs = items[0].current_step
    assert cs is not None
    assert cs["role"] == "gm_or_opm"
    assert cs["label"] == "GM / OPM"
    assert cs["approver_name"] == "Zhang San"
    assert isinstance(cs["since"], datetime)


async def test_enrich_pool_task_has_null_approver_and_non_in_review_is_none(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    pool_doc = uuid.uuid4()
    other_doc = uuid.uuid4()
    async with sf() as db:
        db.add(_task(pool_doc, role="finance_bp", user_id=None))  # role pool, no assignee
        await db.commit()

        items = [
            SimpleNamespace(id=pool_doc, status="in_review"),
            SimpleNamespace(id=other_doc, status="draft"),   # not in_review
            SimpleNamespace(id=uuid.uuid4(), status="in_review"),  # in_review, no task
        ]
        await enrich_current_step(db, "pr", items)

    assert items[0].current_step["label"] == "Finance BP"
    assert items[0].current_step["approver_name"] is None
    assert items[1].current_step is None   # not in_review
    assert items[2].current_step is None   # in_review but no open task


def test_role_maps_cover_default_workflow_roles():
    for role in ("supervisor", "dept_manager", "director", "gm_or_opm",
                 "procurement_manager", "finance_bp", "finance_manager"):
        assert role in ROLE_LABELS
        assert role in ROLE_ORDER
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd epms-api && python -m pytest tests/test_current_step_enrich.py -v`
Expected: FAIL / collection error — `app.crud.current_step` does not exist.

> **Test DB env (per project convention):** the epms-api suite runs against a local docker Postgres `epms_test`, NOT the dev `epms` DB. Confirm `POSTGRES_*` env points at the test DB before running (see `feedback_uniops_admin_test_db_env`). Only one epms suite may run at a time (suites `drop_all` each other).

- [ ] **Step 4: Write the helper**

Create `epms-api/app/crud/current_step.py`:

```python
"""Enrich list items with their current approval step, sourced from the open
approve_{doctype} task (the resync-authoritative 'real current step') rather
than approval_step_idx, which over-budget injection and stale routing can skew.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task
from app.models.user import User

# English labels matching the default workflow node labels.
ROLE_LABELS: dict[str, str] = {
    "supervisor": "Supervisor",
    "dept_manager": "Dept Manager",
    "director": "Director",
    "procurement_manager": "Procurement Manager",
    "gm_or_opm": "GM / OPM",
    "finance_bp": "Finance BP",
    "finance_manager": "Finance Manager",
    "vendor_manager": "Vendor Manager",
}

# Chain order for grouping/sorting — independent of per-doc approval_step_idx so
# over-budget step injection cannot distort the grouping.
ROLE_ORDER: dict[str, int] = {
    "supervisor": 10,
    "dept_manager": 20,
    "director": 30,
    "procurement_manager": 35,
    "gm_or_opm": 40,
    "finance_bp": 50,
    "finance_manager": 60,
    "vendor_manager": 70,
}


def _label(role: str) -> str:
    return ROLE_LABELS.get(role) or role.replace("_", " ").title()


async def enrich_current_step(db: AsyncSession, doc_type: str, items: list) -> None:
    """Set `.current_step` on each item: a dict for in_review items with an open
    approval task, else None. Mutates items in place. One batched task query +
    one batched user-name query (no N+1)."""
    for it in items:
        it.current_step = None

    in_review = [it for it in items if getattr(it, "status", None) == "in_review"]
    if not in_review:
        return

    ids = [it.id for it in in_review]
    task_type = f"approve_{doc_type}"
    rows = (await db.execute(
        select(Task).where(
            Task.document_type == doc_type,
            Task.type == task_type,
            Task.is_completed == False,  # noqa: E712
            Task.document_id.in_(ids),
        ).order_by(Task.created_at.asc())
    )).scalars().all()

    task_by_doc: dict = {}
    for t in rows:
        task_by_doc.setdefault(t.document_id, t)  # earliest open task wins

    user_ids = {t.assigned_user_id for t in task_by_doc.values() if t.assigned_user_id}
    name_by_user: dict = {}
    if user_ids:
        urows = (await db.execute(
            select(User.id, User.full_name).where(User.id.in_(user_ids))
        )).all()
        name_by_user = {uid: name for uid, name in urows}

    for it in in_review:
        t = task_by_doc.get(it.id)
        if t is None:
            continue
        it.current_step = {
            "role": t.assigned_role,
            "label": _label(t.assigned_role),
            "approver_name": name_by_user.get(t.assigned_user_id) if t.assigned_user_id else None,
            "since": t.created_at,
        }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd epms-api && python -m pytest tests/test_current_step_enrich.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add epms-api/app/crud/current_step.py epms-api/app/schemas/current_step.py epms-api/tests/test_current_step_enrich.py
git commit -m "feat(epms-api): enrich_current_step helper + CurrentStep schema"
```

---

### Task 2: Backend — expose `current_step` on responses + wire into the three list endpoints

**Files:**
- Modify: `epms-api/app/schemas/pr.py` (`PrResponse`), `epms-api/app/schemas/po.py` (`PoResponse`), `epms-api/app/schemas/pa.py` (`PaResponse`)
- Modify: `epms-api/app/api/v1/pr.py:54-67` (`list_prs`), and the analogous `list_pos` in `epms-api/app/api/v1/po.py`, `list_pas` in `epms-api/app/api/v1/pa.py`
- Test: `epms-api/tests/test_current_step_enrich.py` (append)

**Interfaces:**
- Consumes: `enrich_current_step`, `CurrentStep` from Task 1.
- Produces: each list item JSON now carries `current_step: {role, label, approver_name, since} | null`.

- [ ] **Step 1: Add the response field (PR first)**

In `epms-api/app/schemas/pr.py`, add the import near the top and the field on `PrResponse` (after `line_items`, line ~190):

```python
from app.schemas.current_step import CurrentStep
```
```python
    line_items: list[PrLineItemResponse]
    current_step: CurrentStep | None = None

    model_config = {"from_attributes": True}
```

Do the same on `PoResponse` in `epms-api/app/schemas/po.py` and `PaResponse` in `epms-api/app/schemas/pa.py` (add the import + `current_step: CurrentStep | None = None` field before each `model_config`).

- [ ] **Step 2: Wire the enrichment into `list_prs`**

In `epms-api/app/api/v1/pr.py`, add the import (near line 22) and call the helper right before the return (line 67):

```python
from app.crud.current_step import enrich_current_step
```
```python
    await enrich_current_step(db, "pr", items)
    return PrListResponse(items=items, total=total)
```

Apply the identical pattern in `po.py` `list_pos` (`enrich_current_step(db, "po", items)`) and `pa.py` `list_pas` (`enrich_current_step(db, "pa", items)`), each immediately before their `...ListResponse(...)` return. Confirm each list endpoint returns ORM items validated via `from_attributes` (same shape as `list_prs`); the helper sets a transient `.current_step` attr, exactly like `created_by_name`.

- [ ] **Step 3: Write the schema/endpoint test**

Append to `epms-api/tests/test_current_step_enrich.py`:

```python
def test_pr_response_exposes_optional_current_step():
    from app.schemas.pr import PrResponse
    from app.schemas.current_step import CurrentStep
    field = PrResponse.model_fields["current_step"]
    assert field.default is None                    # optional, defaults null
    cs = CurrentStep.model_validate({
        "role": "gm_or_opm", "label": "GM / OPM",
        "approver_name": "Zhang San", "since": datetime.now(timezone.utc),
    })
    assert cs.label == "GM / OPM" and cs.approver_name == "Zhang San"


async def test_pr_list_endpoint_serializes_current_step_key(admin_client):
    r = await admin_client.get("/api/v1/pr")
    assert r.status_code == 200
    for item in r.json()["items"]:
        assert "current_step" in item          # field present on every row
```

- [ ] **Step 4: Run tests**

Run: `cd epms-api && python -m pytest tests/test_current_step_enrich.py -v`
Expected: all passed (5 tests).

- [ ] **Step 5: Guard against regressions in the touched list endpoints**

Run: `cd epms-api && python -m pytest tests/test_pr.py tests/test_po.py tests/test_pa.py -q`
Expected: no NEW failures vs the pre-change run of the same command (baseline may carry known failures — compare counts, per `feedback_verification_positive_evidence`). If a target file does not exist, skip it.

- [ ] **Step 6: Commit**

```bash
git add epms-api/app/schemas/pr.py epms-api/app/schemas/po.py epms-api/app/schemas/pa.py epms-api/app/api/v1/pr.py epms-api/app/api/v1/po.py epms-api/app/api/v1/pa.py epms-api/tests/test_current_step_enrich.py
git commit -m "feat(epms-api): expose current_step on PR/PO/PA list responses"
```

---

### Task 3: Frontend — `CurrentStep` type + `CurrentStepHint` component + render in three lists

**Files:**
- Modify: `epms/src/types/index.ts` (add `CurrentStep` type)
- Modify: `epms/src/services/pr.ts` (`ApiPr`), `epms/src/services/po.ts` (`ApiPo`), `epms/src/services/pa.ts` (`ApiPa`)
- Create: `epms/src/components/ui/CurrentStepHint.tsx`
- Modify: `epms/src/pages/pr/PrListPage.tsx:291`, `epms/src/pages/po/PoListPage.tsx:251`, `epms/src/pages/pa/PaListPage.tsx:198`

**Interfaces:**
- Consumes: backend `current_step` JSON from Task 2.
- Produces: `interface CurrentStep` (exported from `types`), `<CurrentStepHint current_step={...} />` component.

- [ ] **Step 1: Add the shared `CurrentStep` type**

In `epms/src/types/index.ts`, add (near the other shared types):

```ts
export interface CurrentStep {
  role: string
  label: string
  approver_name: string | null
  since: string
}
```

- [ ] **Step 2: Add `current_step` to the three Api types**

In `epms/src/services/pr.ts`, import the type and add the field to `ApiPr` (e.g. after `created_at`, line ~56):

```ts
import type { CurrentStep } from '@/types'
```
```ts
  current_step?: CurrentStep | null
```

Do the same in `epms/src/services/po.ts` (`ApiPo`) and `epms/src/services/pa.ts` (`ApiPa`). If a service already imports from `@/types`, add `CurrentStep` to the existing import instead of duplicating.

- [ ] **Step 3: Create the `CurrentStepHint` component**

Create `epms/src/components/ui/CurrentStepHint.tsx`:

```tsx
import type { CurrentStep } from '@/types'

function daysSince(iso: string): number {
  const ms = Date.now() - new Date(iso).getTime()
  return Math.max(0, Math.floor(ms / 86_400_000))
}

/** Muted subtext shown under the status badge for in-review rows:
 *  "GM / OPM · Zhang San · 3d"  or  "Finance BP · 3d" (role pool, no assignee). */
export function CurrentStepHint({ current_step }: { current_step?: CurrentStep | null }) {
  if (!current_step) return null
  const { label, approver_name, since } = current_step
  const parts = [label]
  if (approver_name) parts.push(approver_name)
  parts.push(`${daysSince(since)}d`)
  return (
    <div
      className="mt-1 text-[11px] leading-tight text-neutral-500 whitespace-nowrap"
      title={`Awaiting ${label}${approver_name ? ` (${approver_name})` : ''} since ${new Date(since).toLocaleString()}`}
    >
      {parts.join(' · ')}
    </div>
  )
}
```

- [ ] **Step 4: Render it in the PR list status cell**

In `epms/src/pages/pr/PrListPage.tsx`, add the import (with the other `@/components/ui` imports) and update the status cell (line 291):

```tsx
import { CurrentStepHint } from '@/components/ui/CurrentStepHint'
```
```tsx
                  <td className="px-4 py-3">
                    <StatusBadge status={pr.status} />
                    <CurrentStepHint current_step={pr.current_step} />
                  </td>
```

- [ ] **Step 5: Render it in the PO and PA list status cells**

In `epms/src/pages/po/PoListPage.tsx` (line 251), wrap the existing `<StatusBadge status={po.status as DocumentStatus} />` cell the same way, adding `<CurrentStepHint current_step={po.current_step} />` below it, plus the import.

In `epms/src/pages/pa/PaListPage.tsx` (line 198), do the same below `<PaStatusBadge status={pa.status} />`, plus the import. The subtext sits under the local `PaStatusBadge` for visual parity.

- [ ] **Step 6: Typecheck (must stay at baseline 59)**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit 2>&1 | tail -1` and `... | grep -c "error TS"`
Expected: error count **59** (unchanged baseline). If higher, fix the new code until back to 59.

- [ ] **Step 7: Commit**

```bash
git add epms/src/types/index.ts epms/src/services/pr.ts epms/src/services/po.ts epms/src/services/pa.ts epms/src/components/ui/CurrentStepHint.tsx epms/src/pages/pr/PrListPage.tsx epms/src/pages/po/PoListPage.tsx epms/src/pages/pa/PaListPage.tsx
git commit -m "feat(epms): show current approval step under status badge in PR/PO/PA lists"
```

---

### Task 4: Frontend — shared Status comparator (status → role order → approver name)

**Files:**
- Create: `epms/src/lib/currentStepSort.ts`
- Modify: `epms/src/pages/pr/PrListPage.tsx:124-130`, `epms/src/pages/po/PoListPage.tsx` (its `.sort` block), `epms/src/pages/pa/PaListPage.tsx` (its `.sort` block)

**Interfaces:**
- Consumes: `CurrentStep` type; each row's `status` + `current_step`.
- Produces: `compareByStatusThenStep(a, b): number` — primary `status` (locale), secondary role chain-order (only when both are `in_review`), tertiary `approver_name` (null last).

- [ ] **Step 1: Create the comparator utility**

Create `epms/src/lib/currentStepSort.ts`:

```ts
import type { CurrentStep } from '@/types'

// Mirrors ROLE_ORDER in epms-api/app/crud/current_step.py — chain order, not alphabetical.
const ROLE_ORDER: Record<string, number> = {
  supervisor: 10, dept_manager: 20, director: 30, procurement_manager: 35,
  gm_or_opm: 40, finance_bp: 50, finance_manager: 60, vendor_manager: 70,
}

type Rowish = { status: string; current_step?: CurrentStep | null }

/** Status (locale) → step role (chain order, in_review only) → approver name (null last). */
export function compareByStatusThenStep(a: Rowish, b: Rowish): number {
  const s = String(a.status).localeCompare(String(b.status))
  if (s !== 0) return s
  if (a.status !== 'in_review') return 0   // secondary/tertiary apply only within in_review

  const ao = ROLE_ORDER[a.current_step?.role ?? ''] ?? 999
  const bo = ROLE_ORDER[b.current_step?.role ?? ''] ?? 999
  if (ao !== bo) return ao - bo

  const an = a.current_step?.approver_name
  const bn = b.current_step?.approver_name
  if (an && bn) return an.localeCompare(bn)
  if (an) return -1   // named approver before role-pool (null) within the same role
  if (bn) return 1
  return 0
}
```

- [ ] **Step 2: Use it in the PR list sort**

In `epms/src/pages/pr/PrListPage.tsx`, add the import and change the `status` branch of the sort (lines 124-130):

```tsx
import { compareByStatusThenStep } from '@/lib/currentStepSort'
```
```tsx
  const sorted = [...prs].sort((a, b) => {
    let cmp = 0
    if (sortField === 'amount') cmp = a.amount - b.amount
    else if (sortField === 'submitted_at') cmp = (a.submitted_at ?? '').localeCompare(b.submitted_at ?? '')
    else if (sortField === 'status') cmp = compareByStatusThenStep(a, b)
    else cmp = String(a[sortField as keyof typeof a]).localeCompare(String(b[sortField as keyof typeof b]))
    return sortDir === 'asc' ? cmp : -cmp
  })
```

- [ ] **Step 3: Use it in the PO and PA list sorts**

In `epms/src/pages/po/PoListPage.tsx` and `epms/src/pages/pa/PaListPage.tsx`, locate their `.sort((a, b) => {...})` blocks (same shape as PR) and replace the status branch with `else if (sortField === 'status') cmp = compareByStatusThenStep(a, b)`, adding the import. If a page's sort does not currently special-case `status` (falls through to the generic `String(...)` branch), add the `else if (sortField === 'status')` branch before that generic branch. If PA's sort field union does not include `'status'`, add `'status'` to its `SortField` type so the header remains sortable.

- [ ] **Step 4: Typecheck (must stay at baseline 59)**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c "error TS"`
Expected: **59**.

- [ ] **Step 5: Manual smoke (frontend has no JS test runner)**

In a running epms dev instance: open PR List, click the **Status** column header. Verify `In Review` rows group together and, within them, order by step role (Supervisor → Dept Manager → Director → GM/OPM → Finance BP → Finance Manager) then approver name; role-pool rows (no name) sit after named ones in the same role. Confirm PO and PA lists behave the same. Confirm each `In Review` row shows the subtext `Label · Name · Nd` (or `Label · Nd` for pools), and non-in-review rows show no subtext.

- [ ] **Step 6: Commit**

```bash
git add epms/src/lib/currentStepSort.ts epms/src/pages/pr/PrListPage.tsx epms/src/pages/po/PoListPage.tsx epms/src/pages/pa/PaListPage.tsx
git commit -m "feat(epms): sort Status column by status then approval step role and approver"
```

---

## Self-Review

**Spec coverage:**
- Backend enrichment via open task role → Task 1 (helper) + Task 2 (wiring). ✓
- `current_step = {role, label, approver_name, since}`, null when non-in_review / no task → Task 1 helper + tests. ✓
- Static role→label / role→order maps, over-budget-safe → Task 1 `ROLE_LABELS`/`ROLE_ORDER`. ✓
- No N+1 (one task query + one user query) → Task 1 helper. ✓
- Frontend subtext `Label · Name · Nd` / pool `Label · Nd` / none → Task 3 `CurrentStepHint`. ✓
- Three list pages, visual parity incl. PA local badge → Task 3 Steps 4-5. ✓
- Sort: status → role chain order (in_review only) → approver name, null last → Task 4 comparator + wiring. ✓
- Zero migration, English copy, 59 tsc baseline → Global Constraints + typecheck steps. ✓

**Placeholder scan:** No TBD/TODO; all code blocks are concrete; edge cases (pool, no task, non-in_review, unknown role) have real handling and assertions.

**Type consistency:** `enrich_current_step(db, doc_type, items)`, `current_step` dict keys `{role,label,approver_name,since}`, `CurrentStep` fields, and the TS `CurrentStep` interface all match across backend helper → backend schema → frontend type → component → comparator. `ROLE_ORDER` values identical in `current_step.py` and `currentStepSort.ts`.
