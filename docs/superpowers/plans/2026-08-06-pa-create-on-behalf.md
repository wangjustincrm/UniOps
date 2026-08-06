# Procurement Officer 代建 PA — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `procurement_officer` 能替任何人对任何 PO 创建 Payment Application,同时审批人仍由关联 PR 的 Requester 决定,且 officer 不会因此收到 create-PA 提醒邮件。

**Architecture:** 三层各改一小块。授权层:identity 迁移把 `epms.pa.write` 授给 `procurement_officer`。后端:`epms-api` 那段"基础角色为 requester 时校验 PO 归属"的 403 增加一条代建逃逸规则。前端:PA 列表的 New PA 按钮由硬编码角色数组改为读矩阵权限。审批路由与 `create_pa` 任务派发**刻意零改动**,用回归测试钉住。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 async / Alembic / pytest-asyncio;React 18 + TypeScript 5.9 + Vite(epms 前端)。

## Global Constraints

- **工作区**:`c:/Project/uniops-pa-onbehalf`,分支 `feature/pa-create-on-behalf`(基 `main=c323f64`)。所有改动只在这个 worktree 里做。
- **不 push、不合 main**:每个 Task 结束本地 commit 即可;push/合并由用户另行决定。
- **前端 user-facing 文案一律英文**;代码注释可中文。
- **epms-api 测试库禁止并发**:同一时刻只能跑一个 epms 套件(测试库 `epms_test` 是共享的)。
- **绝不在宿主机直接跑 alembic / 连库脚本**:宿主 `.env` 指向**生产库** `10.10.50.20`。所有迁移/SQL 校验必须 `docker exec` 进本地容器,或显式覆盖 `POSTGRES_*`。
- **验证要正面证据**:断言具体状态码/行数/计数,不把"没有输出"当通过;失败数与基线对比。
- 规格文档:`docs/superpowers/specs/2026-08-06-pa-create-on-behalf-design.md`。

---

### Task 1: identity — 把 `epms.pa.write` 授给 `procurement_officer`

**Files:**
- Create: `identity-api/alembic/versions/0005_procurement_officer_pa.py`
- Modify: `identity-api/scripts/seed_phase2_keys.py:38`
- 不改:`identity-api/scripts/verify_gate_parity.py`(冻结快照,见 spec §3.1)

**Interfaces:**
- Produces: 数据库中存在 `role_permissions('procurement_officer','epms.pa.write')` 一行。Task 2 的后端门禁 `require_permission("epms.pa.write")` 依赖它;Task 5 的前端按钮依赖它经 `GET /config/me/permissions` 暴露出来。
- Consumes: 现有 alembic 链尾 `0004_erp_pa_officer_role`(已核实是 identity 唯一 head)。

- [x] **Step 1: 确认 identity 的 alembic head 仍是 0004**

```bash
cd /c/Project/uniops-pa-onbehalf
grep -h "^revision\|^down_revision" identity-api/alembic/versions/*.py
```

Expected: 输出四对 revision/down_revision,链为 `0001 → 0002 → 0003 → 0004_erp_pa_officer_role`,没有第二个 head(即没有任何两个文件的 `down_revision` 相同)。若不是,停下来先问用户 —— 挂错 `down_revision` 会造成双 head。

- [x] **Step 2: 写迁移文件**

Create `identity-api/alembic/versions/0005_procurement_officer_pa.py` (revision id kept to 32 chars — `alembic_version_identity.version_num` is `varchar(32)` and rejects longer ids):

```python
"""Grant epms.pa.write to procurement_officer (create PA on behalf of anyone).

A Procurement Officer must be able to raise a Payment Application against ANY
purchase order, not just POs linked to a requisition they raised. The write
gate is the Access Control Matrix key epms.pa.write, so the whole change is one
matrix cell.

Approval routing is deliberately untouched: approval-api resolves a PA's
approvers from the linked PR's requester/department, never from PA.created_by
(see approval-api/app/crud/engine.py::_routing_user_id), so paying on someone
else's behalf cannot redirect the approval chain.

Granting here (rather than leaving it for an admin to tick in Portal -> Access
Control) is deliberate: the Create-GR cutover shipped a matrix-driven gate with
no seeded grant and 403'd everyone who previously had the button.

Idempotent (ON CONFLICT DO NOTHING) so it is safe on a DB the seed scripts
already touched, and self-sufficient on a fresh DB (it seeds the permission_defs
row its grant references).
"""
from alembic import op

revision = "0005_procurement_officer_pa"
down_revision = "0004_erp_pa_officer_role"
branch_labels = None
depends_on = None

_ROLE = "procurement_officer"
_KEY = "epms.pa.write"


def upgrade() -> None:
    op.execute(
        "INSERT INTO permission_defs(key,module,label,sort) "
        f"VALUES ('{_KEY}','epms','Create / Edit PAs',102) "
        "ON CONFLICT (key) DO NOTHING")
    op.execute(
        "INSERT INTO role_permissions(role_code,permission_key) "
        f"VALUES ('{_ROLE}','{_KEY}') ON CONFLICT DO NOTHING")


def downgrade() -> None:
    # Only the grant this migration added. permission_defs / role_defs are
    # shared with the seed scripts and other roles' grants — leave them alone.
    op.execute(
        f"DELETE FROM role_permissions WHERE role_code = '{_ROLE}' "
        f"AND permission_key = '{_KEY}'")
```

- [x] **Step 3: 同步 seed 常量**

Modify `identity-api/scripts/seed_phase2_keys.py:38` — 在元组末尾追加 `"procurement_officer"`:

```python
    "epms.pa.write":        ("system_admin", "finance_bp", "finance_manager", "ap_clerk", "requester", "erp_pa_officer", "procurement_officer"),
```

(这行是新库首次 seed 的来源,必须与迁移一致,否则新环境和老环境的矩阵会漂移。)

- [x] **Step 4: 在本地 identity 容器里跑 upgrade + downgrade + upgrade,验证幂等与可回滚**

```bash
docker ps --format '{{.Names}}' | grep identity     # 确认容器名(下面按 uniops_identity_api)
docker exec uniops_identity_api alembic heads
docker exec uniops_identity_api alembic upgrade head
docker exec uniops_identity_api python -c "
import asyncio, sqlalchemy as sa
from app.db.base import AsyncSessionLocal
async def main():
    async with AsyncSessionLocal() as s:
        n = (await s.execute(sa.text(
            \"SELECT count(*) FROM role_permissions WHERE role_code='procurement_officer' AND permission_key='epms.pa.write'\"))).scalar_one()
        print('GRANT_ROWS', n)
asyncio.run(main())"
```

Expected:`alembic heads` 只列出 `0005_procurement_officer_pa (head)`;`upgrade` 成功;最后打印 `GRANT_ROWS 1`。

再验回滚与重跑幂等:

```bash
docker exec uniops_identity_api alembic downgrade -1
docker exec uniops_identity_api python -c "
import asyncio, sqlalchemy as sa
from app.db.base import AsyncSessionLocal
async def main():
    async with AsyncSessionLocal() as s:
        n = (await s.execute(sa.text(
            \"SELECT count(*) FROM role_permissions WHERE role_code='procurement_officer' AND permission_key='epms.pa.write'\"))).scalar_one()
        print('GRANT_ROWS_AFTER_DOWN', n)
asyncio.run(main())"
docker exec uniops_identity_api alembic upgrade head
docker exec uniops_identity_api alembic upgrade head
```

Expected:`GRANT_ROWS_AFTER_DOWN 0`;两次 `upgrade head` 都成功(第二次是 no-op,不报唯一约束错)。

> 若本地没有跑着的 identity 容器,不要退回宿主机执行(宿主 `.env` 打生产库)。改为在 Task 2 的 epms 测试里通过 shadow 表验证授权效果,并在本 Task 的 commit message 里注明"迁移未在本地实跑,待部署前在 dev 环境验证"。

- [x] **Step 5: Commit**

```bash
cd /c/Project/uniops-pa-onbehalf
git add identity-api/alembic/versions/0005_procurement_officer_pa.py identity-api/scripts/seed_phase2_keys.py
git commit -m "feat(identity): grant epms.pa.write to procurement_officer

Lets a Procurement Officer raise a Payment Application against any PO.
Seeded in the migration rather than left for a manual Access Control tick,
so the capability is live the moment the release lands."
```

---

### Task 2: epms-api — 放开代建的归属 403(TDD)

**Files:**
- Create: `epms-api/tests/test_pa_on_behalf_authz.py`
- Modify: `epms-api/app/api/v1/pa.py:78-100`

**Interfaces:**
- Consumes: `app.core.access_scope._effective_role_codes(db, base_role, user_id) -> set[str]`(已存在,返回 JWT 基础角色 ∪ identity `user_roles` 附加角色)。
- Produces: 模块级函数 `_may_create_pa_on_behalf(roles: set[str], po: PurchaseOrder) -> bool`,供 `create_pa` 使用;Task 3 的测试复用本文件里的 helper `_pa_payload` / `_grant_procurement_officer_pa_write` / `_user`。

**测试环境(每次跑测都要):** 覆盖 `POSTGRES_*` 指向本地 docker `uniops_postgres`(user=`epms`,库 `epms_test`),`JWT_SECRET_KEY=test-secret`;密码用 `docker inspect uniops_postgres` 取。首次需 `python -m scripts.create_test_db`。**同一时刻只跑一个 epms 套件。**

- [x] **Step 1: 写失败的测试**

Create `epms-api/tests/test_pa_on_behalf_authz.py`:

```python
"""procurement_officer may raise a PA on behalf of anyone.

Creating a PA is gated by the epms.pa.write matrix key plus — for callers whose
JWT base role is 'requester' — an ownership check against the linked PR. A
Procurement Officer must be able to pay ANY PO, whether the role is their base
login role or an additional role layered on a requester account, while a plain
requester still may only pay their own requisitions.

Approval routing is NOT affected by who creates the PA (approval-api resolves
approvers from the linked PR) — that invariant is covered by
approval-api/tests/test_pa_on_behalf_routing.py.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

PA_URL = "/api/v1/pa"


def _pa_payload(po_id):
    return {
        "po_id": po_id, "title": "On-behalf Payment", "pa_type": "regular",
        "subtotal": "100.00", "tax_amount": "0.00", "currency": "CAD",
        "line_items": [{"description": "X", "qty": "1", "unit": "EA", "unit_price": "100.00"}],
    }


async def _grant_procurement_officer_pa_write(db):
    """Mirror identity migration 0005 in the shadow authz tables — conftest's
    default matrix seeds only the view_*/create_* keys, never the phase-2 ones."""
    await db.execute(text(
        "INSERT INTO permission_defs(key,module,label,sort) "
        "VALUES ('epms.pa.write','epms','Create / Edit PAs',102) ON CONFLICT (key) DO NOTHING"))
    await db.execute(text(
        "INSERT INTO role_permissions(role_code,permission_key) "
        "VALUES ('procurement_officer','epms.pa.write') ON CONFLICT DO NOTHING"))


async def _user(db, role: str, *, additional: str | None = None):
    u = await user_crud.create(db, RegisterRequest(
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name=role.replace("_", " ").title(), role=role))
    if additional:
        await db.execute(text(
            "INSERT INTO user_roles(user_id, role_code) VALUES (:u, :r) "
            "ON CONFLICT DO NOTHING"), {"u": str(u.id), "r": additional})
    return u


async def _three_way_po_owned_by(db, requester_id):
    """A PO linked to a PR raised by `requester_id`, carrying a GR + matched
    invoice so the 3-way receipt gate is satisfied. Returns the PO id."""
    v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
               contact_name="C", contact_email="c@x.com")
    db.add(v); await db.flush()
    pr = PurchaseRequest(number=f"PR-{uuid.uuid4().hex[:8]}", title="Someone else's PR",
                         type=1, status="approved", amount=Decimal("100"),
                         created_by=requester_id)
    db.add(pr); await db.flush()
    po = PurchaseOrder(number=f"PO-{uuid.uuid4().hex[:8]}", title="PO", type=1,
                       vendor_id=v.id, vendor_name="Acme", status="issued",
                       created_by=requester_id, pr_id=pr.id)
    db.add(po); await db.flush()
    gr = GoodsReceipt(number=f"GR-{uuid.uuid4().hex[:8]}", title="G", po_id=po.id,
                      po_number=po.number, vendor_id=v.id, vendor_name="Acme",
                      gr_type="physical", procurement_type=1, status="collected",
                      created_by=requester_id)
    db.add(gr); await db.flush()
    inv = Invoice(internal_ref=f"I-{uuid.uuid4().hex[:6]}", vendor_invoice_number="X",
                  vendor_id=v.id, vendor_name="Acme", amount=Decimal("100"),
                  tax_amount=Decimal("0"), total_amount=Decimal("100"),
                  invoice_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
                  status="matched", line_items=[], po_id=po.id, gr_id=gr.id,
                  uploaded_by=requester_id)
    db.add(inv); await db.flush()
    return po.id


def _client_for(user):
    token = create_access_token(str(user.id), user.role)
    return AsyncClient(transport=ASGITransport(app=create_app()),
                       base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_procurement_officer_creates_pa_for_another_requesters_po(test_engine):
    """Base-role Procurement Officer: the matrix grant alone must be enough —
    the ownership branch does not apply to a non-requester base role."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_procurement_officer_pa_write(db)
        requester = await _user(db, "requester")
        po_id = await _three_way_po_owned_by(db, requester.id)
        officer = await _user(db, "procurement_officer")
        await db.commit()
    async with _client_for(officer) as c:
        r = await c.post(PA_URL, json=_pa_payload(str(po_id)))
    assert r.status_code == 201, r.text
    assert r.json()["pa_number"].startswith("PA-")


@pytest.mark.asyncio
async def test_requester_with_additional_procurement_officer_role_creates_pa(test_engine):
    """Granting Procurement Officer as an ADDITIONAL role on a requester login
    must work identically — the matrix unions roles, so the ownership 403 has to
    honour the additional role too."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_procurement_officer_pa_write(db)
        owner = await _user(db, "requester")
        po_id = await _three_way_po_owned_by(db, owner.id)
        officer = await _user(db, "requester", additional="procurement_officer")
        await db.commit()
    async with _client_for(officer) as c:
        r = await c.post(PA_URL, json=_pa_payload(str(po_id)))
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_plain_requester_still_cannot_create_pa_for_someone_elses_po(test_engine):
    """Regression guard: relaxing the ownership check for officers must not open
    it for ordinary requesters."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_procurement_officer_pa_write(db)
        owner = await _user(db, "requester")
        po_id = await _three_way_po_owned_by(db, owner.id)
        stranger = await _user(db, "requester")
        await db.commit()
    async with _client_for(stranger) as c:
        r = await c.post(PA_URL, json=_pa_payload(str(po_id)))
    assert r.status_code == 403, r.text
```

- [x] **Step 2: 跑测确认哪一条失败**

```bash
cd /c/Project/uniops-pa-onbehalf/epms-api
pytest tests/test_pa_on_behalf_authz.py -v
```

Expected(改代码前的正确基线,三条各有各的含义):

- `test_procurement_officer_creates_pa_for_another_requesters_po` → **PASS**(只靠 Task 1 的矩阵授权即可,基础角色不是 requester 所以不进归属分支)
- `test_requester_with_additional_procurement_officer_role_creates_pa` → **FAIL**,403 `"You can only create payments for purchase orders linked to your own requisitions."` ← 这是本 Task 要修的
- `test_plain_requester_still_cannot_create_pa_for_someone_elses_po` → **PASS**

若第一条也 FAIL,说明 shadow 表授权没生效,先查 `_grant_procurement_officer_pa_write` 是否 commit 了。

- [x] **Step 3: 实现最小改动**

Modify `epms-api/app/api/v1/pa.py` —— 在 `create_pa` 之前(紧跟 `_get_prepayment_config` 之后)加入判定函数:

```python
def _may_create_pa_on_behalf(roles: set[str], po) -> bool:
    """Whether these role codes let the caller raise a PA against `po` that is
    not linked to their own requisition.

    This only bypasses the requester-ownership rule — the epms.pa.write matrix
    gate still applies to every caller.
    """
    # Procurement Officer pays on anyone's behalf, on any PO. Approval routing is
    # unaffected: approval-api resolves a PA's approvers from the linked PR's
    # requester/department, never from PA.created_by.
    if "procurement_officer" in roles:
        return True
    # erp_pa_officer covers only PR-less NC-imported POs — those have no
    # requisitioner for ownership to apply to in the first place.
    return "erp_pa_officer" in roles and po.pr_id is None and po.source == "nc"
```

再把归属分支(现 `pa.py:88-100`)替换为:

```python
        if pr_requester_id != uuid.UUID(user["sub"]):
            roles = await _effective_role_codes(db, "requester", uuid.UUID(user["sub"]))
            if not _may_create_pa_on_behalf(roles, po):
                raise HTTPException(
                    status_code=403,
                    detail="You can only create payments for purchase orders linked to your own requisitions.",
                )
```

- [x] **Step 4: 跑测确认三条全绿 + 老的 erp_pa_officer 套件不回归**

```bash
cd /c/Project/uniops-pa-onbehalf/epms-api
pytest tests/test_pa_on_behalf_authz.py tests/test_pa_erp_officer_authz.py -v
```

Expected:5 passed(新 3 + 老 2)。老套件里的 `test_plain_requester_cannot_create_pa_for_nc_po` 必须仍是 403 —— 它证明重构没有把逃逸口开得过宽。

- [x] **Step 5: Commit**

```bash
cd /c/Project/uniops-pa-onbehalf
git add epms-api/app/api/v1/pa.py epms-api/tests/test_pa_on_behalf_authz.py
git commit -m "feat(epms): let procurement_officer create a PA on anyone's behalf

The requester-ownership 403 now consults a single _may_create_pa_on_behalf
predicate, so the capability works whether Procurement Officer is the base JWT
role or an additional role on a requester login. Plain requesters are unchanged."
```

---

### Task 3: epms-api — 钉住"officer 代建不产生指向自己的任务"

**Files:**
- Modify: `epms-api/tests/test_pa_on_behalf_authz.py`(追加一条测试 + 一个 helper)

**Interfaces:**
- Consumes: Task 2 建立的 `_pa_payload` / `_grant_procurement_officer_pa_write` / `_user` / `_three_way_po_owned_by` / `_client_for`。
- Produces: 无生产代码产物 —— 这是把"通知不改动"这个需求转成可执行断言。

- [x] **Step 1: 追加测试**

在 `epms-api/tests/test_pa_on_behalf_authz.py` 顶部 import 区补上:

```python
from sqlalchemy import select
from app.models.task import Task
```

文件末尾追加:

```python
async def _open_create_pa_task_for(db, po, requester_id):
    """The create_pa task the live flow raises when an invoice is matched —
    always anchored on the PO and assigned to the PR requester."""
    t = Task(type="create_pa", document_type="po", document_id=po.id,
             document_number=po.number, assigned_role="requester",
             assigned_user_id=requester_id,
             title=f"Create Payment Application: {po.number}")
    db.add(t); await db.flush()
    return t.id


@pytest.mark.asyncio
async def test_officer_created_pa_leaves_no_task_for_the_officer(test_engine):
    """The create-PA reminder belongs to the PR requester and must stay there:
    creating the PA on their behalf completes THEIR task and must not raise any
    task (hence any e-mail or daily follow-up) aimed at the officer."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _grant_procurement_officer_pa_write(db)
        requester = await _user(db, "requester")
        po_id = await _three_way_po_owned_by(db, requester.id)
        po = await db.get(PurchaseOrder, po_id)
        task_id = await _open_create_pa_task_for(db, po, requester.id)
        officer = await _user(db, "procurement_officer")
        await db.commit()

    async with _client_for(officer) as c:
        r = await c.post(PA_URL, json=_pa_payload(str(po_id)))
    assert r.status_code == 201, r.text

    async with factory() as db:
        # The requester's reminder is done — they must not keep being chased.
        done = (await db.execute(
            select(Task.is_completed).where(Task.id == task_id))).scalar_one()
        assert done is True, "creating the PA must complete the requester's create_pa task"

        # Nothing at all points at the officer: no per-user assignment and no
        # create_pa pool broadcast to their role.
        mine = (await db.execute(
            select(Task).where(Task.assigned_user_id == officer.id))).scalars().all()
        assert mine == [], f"officer must receive no task, got {[t.type for t in mine]}"
        pooled = (await db.execute(
            select(Task).where(Task.type == "create_pa",
                               Task.assigned_role == "procurement_officer"))).scalars().all()
        assert pooled == [], "create_pa must never be broadcast to the procurement_officer pool"
```

- [x] **Step 2: 跑测**

```bash
cd /c/Project/uniops-pa-onbehalf/epms-api
pytest tests/test_pa_on_behalf_authz.py -v
```

Expected: 4 passed。这条应当**一次就过** —— 它是不变量测试,证明现有派发逻辑(`crud/pa.py::_complete_create_pa_tasks`)已经满足"officer 不收提醒"的需求,不需要写新代码。若它 FAIL,说明真的有代码把任务派给了创建人,那要停下来报告,不要改测试去迁就。

- [x] **Step 3: Commit**

```bash
cd /c/Project/uniops-pa-onbehalf
git add epms-api/tests/test_pa_on_behalf_authz.py
git commit -m "test(epms): pin that on-behalf PA creation raises no task for the officer

The create-PA reminder stays with the PR requester (and is completed by the
officer's PA), so the officer never gets the mail or the daily follow-up."
```

---

### Task 4: approval-api — 钉住"审批人仍由 PR Requester 决定"

**Files:**
- Create: `approval-api/tests/test_pa_on_behalf_routing.py`

**Interfaces:**
- Consumes: `app.crud.engine._routing_user_id(db, doc_type, doc)`、`app.crud.engine._routing_department_id(db, doc_type, doc, routing_uid)`;pytest fixture `engine_db_session`(见 `approval-api/tests/conftest.py`,同目录其他 engine 测试都用它)。
- Produces: 无生产代码产物。

- [x] **Step 1: 写测试**

Create `approval-api/tests/test_pa_on_behalf_routing.py`:

```python
"""A PA raised on someone else's behalf still routes by the linked PR.

Procurement Officers may create a Payment Application for any PO. Approval
routing must keep following the PR requester (and the PR's selected department)
— never the PA creator — or paying on someone's behalf would silently reroute
the approval chain to the officer's own manager.
"""
import uuid
from decimal import Decimal

from app.crud.engine import _routing_department_id, _routing_user_id
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.user import User


async def test_pa_created_by_officer_routes_by_pr_requester(engine_db_session):
    db = engine_db_session
    dept_requester = uuid.uuid4()
    dept_officer = uuid.uuid4()
    requester = User(id=uuid.uuid4(), role="requester",
                     department_id=dept_requester, is_active=True)
    officer = User(id=uuid.uuid4(), role="procurement_officer",
                   department_id=dept_officer, is_active=True)
    db.add_all([requester, officer])
    await db.flush()

    pr = PurchaseRequest(
        number=f"PR-TEST-{uuid.uuid4().hex[:4]}", title="On-behalf routing",
        status="approved", approval_step_idx=0, amount=Decimal("100.00"),
        created_by=requester.id,
    )
    db.add(pr)
    await db.flush()
    po = PurchaseOrder(
        number=f"PO-TEST-{uuid.uuid4().hex[:4]}", title="On-behalf routing",
        status="issued", approval_step_idx=0, total=Decimal("100.00"),
        vendor_name="Acme", pr_id=pr.id, created_by=officer.id,
    )
    db.add(po)
    await db.flush()
    pa = PaymentApplication(
        pa_number=f"PA-TEST-{uuid.uuid4().hex[:4]}", title="On-behalf routing",
        status="draft", approval_step_idx=0, payment_amount=Decimal("100.00"),
        vendor_name="Acme", po_id=po.id, po_number=po.number, invoice_ids=[],
        created_by=officer.id,          # <- the officer, NOT the requester
    )
    db.add(pa)
    await db.flush()

    routing_uid = await _routing_user_id(db, "pa", pa)
    assert routing_uid == requester.id, "PA routing must follow the linked PR's requester"

    dept = await _routing_department_id(db, "pa", pa, routing_uid)
    assert dept == dept_requester, "routing department must be the requester's, not the officer's"


async def test_pa_routing_follows_pr_selected_department(engine_db_session):
    """When the PR was filed under an explicitly selected department, an
    on-behalf PA must route to THAT department, not the requester's own."""
    db = engine_db_session
    dept_own = uuid.uuid4()
    dept_selected = uuid.uuid4()
    requester = User(id=uuid.uuid4(), role="requester",
                     department_id=dept_own, is_active=True)
    officer = User(id=uuid.uuid4(), role="procurement_officer",
                   department_id=uuid.uuid4(), is_active=True)
    db.add_all([requester, officer])
    await db.flush()

    pr = PurchaseRequest(
        number=f"PR-TEST-{uuid.uuid4().hex[:4]}", title="Cross-department",
        status="approved", approval_step_idx=0, amount=Decimal("100.00"),
        created_by=requester.id, department_id=dept_selected,
    )
    db.add(pr)
    await db.flush()
    po = PurchaseOrder(
        number=f"PO-TEST-{uuid.uuid4().hex[:4]}", title="Cross-department",
        status="issued", approval_step_idx=0, total=Decimal("100.00"),
        vendor_name="Acme", pr_id=pr.id, created_by=officer.id,
    )
    db.add(po)
    await db.flush()
    pa = PaymentApplication(
        pa_number=f"PA-TEST-{uuid.uuid4().hex[:4]}", title="Cross-department",
        status="draft", approval_step_idx=0, payment_amount=Decimal("100.00"),
        vendor_name="Acme", po_id=po.id, po_number=po.number, invoice_ids=[],
        created_by=officer.id,
    )
    db.add(pa)
    await db.flush()

    routing_uid = await _routing_user_id(db, "pa", pa)
    dept = await _routing_department_id(db, "pa", pa, routing_uid)
    assert dept == dept_selected, "PR.department_id must win over the requester's own department"
```

- [x] **Step 2: 跑测**

```bash
cd /c/Project/uniops-pa-onbehalf/approval-api
pytest tests/test_pa_on_behalf_routing.py -v
```

Expected: 2 passed(不变量测试,应一次就过 —— `_routing_user_id` 已经沿 `PA → PO → PR` 解析)。若 FAIL,说明"审批流不变"这个前提不成立,停下来报告,不要动引擎代码。

- [x] **Step 3: Commit**

```bash
cd /c/Project/uniops-pa-onbehalf
git add approval-api/tests/test_pa_on_behalf_routing.py
git commit -m "test(approval): pin PA routing to the linked PR when created on behalf

Guards the premise of the Procurement Officer on-behalf feature: PA approvers
come from the PR's requester/department, never from PA.created_by."
```

---

### Task 5: epms 前端 — New PA 按钮改矩阵驱动

**Files:**
- Modify: `epms/src/pages/pa/PaListPage.tsx:12`(import)、`epms/src/pages/pa/PaListPage.tsx:117`(`canCreate`)

**Interfaces:**
- Consumes: `useRolePermissions()` from `@/hooks/useConfig`,返回 `{ permissions: Record<string, boolean>, roles: string[] }`(`epms/src/services/config.ts::MyPermissions`)。后端 `GET /config/me/permissions` 返回 `permission_defs` 的全部 key(含 `epms.pa.write`),且已是基础角色 ∪ 附加角色的并集。
- Produces: 无下游消费者。

- [x] **Step 1: 加 import**

在 `epms/src/pages/pa/PaListPage.tsx` 第 12 行 `import { useAuthStore } from '@/stores/auth.store'` 之后插入:

```ts
import { useRolePermissions } from '@/hooks/useConfig'
```

- [x] **Step 2: 改 `canCreate`**

把 `epms/src/pages/pa/PaListPage.tsx:117`:

```ts
  const canCreate = ['ap_clerk', 'procurement_officer', 'procurement_manager', 'requester', 'system_admin'].includes(user?.role ?? '')
```

替换为:

```ts
  // Driven by the Access Control Matrix (epms.pa.write), not a hardcoded role
  // list: the same key gates POST /pa, so the button and the endpoint can no
  // longer disagree — and it picks up additional roles, which a base-role check
  // cannot see.
  const perms = useRolePermissions().data?.permissions
  const canCreate = user?.role === 'system_admin' || !!perms?.['epms.pa.write']
```

- [x] **Step 3: 类型检查对齐基线**

worktree 里没有 `node_modules`,先装依赖(用 `npm ci` 而不是 `npm install`,避免改动 lockfile):

```bash
cd /c/Project/uniops-pa-onbehalf/epms
npm ci
npx tsc -p tsconfig.app.json 2>&1 | tail -5
```

Expected:错误条数与基线 **58** 一致(基线是这个仓库既有的存量错误)。用下面的命令数一下,并确认没有一条错误指向 `PaListPage.tsx`:

```bash
npx tsc -p tsconfig.app.json 2>&1 | grep -c "error TS"
npx tsc -p tsconfig.app.json 2>&1 | grep "PaListPage"
```

Expected:第一条打印 `58`;第二条**无输出**(这里"无输出"是有效证据,因为同一次运行的第一条命令已经给出了正面的计数基线)。若计数 > 58,看新增的那几条是不是本次改动引入的。

- [x] **Step 4: Commit**

```bash
cd /c/Project/uniops-pa-onbehalf
git add epms/src/pages/pa/PaListPage.tsx
git commit -m "fix(epms): gate the New PA button on epms.pa.write instead of a role list

The hardcoded list showed the button to procurement_manager, whose POST /pa was
then rejected. Reading the matrix key makes the button match the endpoint and
picks up procurement_officer (base or additional role) automatically."
```

---

### Task 6: 收尾 — 全套回归 + 计划/规格归档

**Files:**
- Modify: `docs/superpowers/plans/2026-08-06-pa-create-on-behalf.md`(勾选完成项)

- [x] **Step 1: 跑 epms-api 全量套件,与基线对比**

```bash
cd /c/Project/uniops-pa-onbehalf/epms-api
pytest -q 2>&1 | tail -5
```

Expected:failed 数不高于本分支起点的基线。**注意**:epms 套件存在存量失败(历史基线约 72 failed,且与运行环境有关),所以不能只看"有没有红"。若有失败,逐条确认它是否出现在未改动的 `main` 上 —— 在 `c:/Project/uniops-release`(main 工作区)上跑同样的命令取基线对比。**跑之前确认没有别的会话在跑 epms 套件。**

- [x] **Step 2: 跑 approval-api 全量套件**

```bash
cd /c/Project/uniops-pa-onbehalf/approval-api
pytest -q 2>&1 | tail -5
```

Expected:与基线一致;本次新增 2 passed。

- [x] **Step 3: 勾选本计划中已完成的 checkbox 并提交**

```bash
cd /c/Project/uniops-pa-onbehalf
git add docs/superpowers/plans/2026-08-06-pa-create-on-behalf.md
git commit -m "docs: mark on-behalf PA plan tasks complete"
```

- [x] **Step 4: 汇报,不要自行 push 或合并**

向用户汇报:改动清单、测试证据(通过/失败计数 vs 基线)、以及部署须知 —— identity 需跑迁移 `0005`,其余按标准发布流程全 15 镜像同 sha;部署后无需人工在 Portal 勾权限。

---

## 部署须知(交付时一并转达)

1. identity-api 跑 `migrate`(alembic `0005_procurement_officer_pa`)**或**等价的 `seed_phase2_keys`。
2. 其余服务按标准发布流程发布(全 15 镜像同 sha)。
3. 无需人工勾权限;若日后要让 `procurement_manager` 也能代建,在 Portal → Access Control 勾 `epms.pa.write` 即可,无需改代码。
4. 部署后跑 `verify_gate_parity` 会出现 `DIFF role=procurement_officer key=epms.pa.write old=False new=True` —— 这是本次有意矩阵变更的**正确表现**,不是回归(该脚本的 `PHASE2_DEFAULTS` 是割接时的冻结快照,刻意不同步)。
