"""GM/OPM 部门范围锁定测试 —— 统一发票列表 (/api/v1/invoices/all)。

背景(权限重构三期 Task 2c):OA 后端 invoice_list.py 的 gm/opm 分支此前读
`company_config.dept_gm_opm_mapping`(冻结 JSONB,唯一未迁移的消费者)。本文件
锁住迁移后的行为 —— 数据源改为 approval-api 拥有的 approval_dept_routing 表
(同物理库,只读;Portal → Approval Routing 是唯一写入者)。

approval_dept_routing 不归 expense-api 的 alembic 管 —— expense_test 默认没有
这张表。仿 epms-api/tests/test_access_scope_dept.py 的做法,用
CREATE TABLE IF NOT EXISTS 建表(schema 抄
approval-api/alembic/versions/0001_approval_routing.py),让本文件的测试可以
自行灌路由数据,不依赖 approval-api 的迁移跑在共享测试库上。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.main import create_app
from app.models.epms_mirrors import EpmsCostCenter, EpmsInvoice, EpmsPurchaseOrder, EpmsPurchaseRequest


def _client_for(role: str, user_id: str) -> AsyncClient:
    token = jwt.encode(
        {"sub": user_id, "role": role, "type": "access", "exp": datetime.utcnow() + timedelta(hours=8)},
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture
async def routing_db(test_engine):
    """Session pre-seeded with a local approval_dept_routing table.

    approval_dept_routing is owned by approval-api (its own alembic head) —
    expense_test has no such table by default. Create it here with
    CREATE TABLE IF NOT EXISTS (schema copied from
    approval-api/alembic/versions/0001_approval_routing.py).

    Also shadows epms-api's `departments` table (id + is_active only — all the
    dept_ids query needs): invoice_list.py's gm/opm branch now LEFT JOINs the
    real `departments` table (mirroring epms-api/app/core/access_scope.py's
    _mapped_dept_ids fix) so it can see departments with NO routing row at
    all. expense_test is its own database (not the shared epms physical DB),
    so it has no `departments` table unless we shadow it here too — same
    reasoning as the approval_dept_routing shadow above.
    """
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await session.execute(text(
            "CREATE TABLE IF NOT EXISTS approval_dept_routing ("
            " dept_id uuid PRIMARY KEY,"
            " gm_or_opm varchar(3) NOT NULL DEFAULT 'gm',"
            " director_user_id uuid NULL,"
            " supervisor_enabled boolean NOT NULL DEFAULT false,"
            " updated_by uuid NULL,"
            " updated_at timestamptz NOT NULL DEFAULT now()"
            ")"
        ))
        await session.execute(text(
            "CREATE TABLE IF NOT EXISTS departments ("
            " id uuid PRIMARY KEY,"
            " is_active boolean NOT NULL DEFAULT true"
            ")"
        ))
        await session.execute(text("DELETE FROM approval_dept_routing"))
        await session.execute(text("DELETE FROM departments"))
        await session.commit()
        yield session


async def _make_dept(db, dept_id: uuid.UUID, is_active: bool = True) -> None:
    """Ensure a real (shadow) departments row exists for dept_id.

    Needed because the gm/opm dept_ids query now drives FROM departments —
    a dept_id with no matching row there can never appear in the result,
    regardless of approval_dept_routing content.
    """
    await db.execute(text(
        "INSERT INTO departments (id, is_active) VALUES (:d, :a) "
        "ON CONFLICT (id) DO UPDATE SET is_active = EXCLUDED.is_active"),
        {"d": str(dept_id), "a": is_active})
    await db.commit()


async def _set_routing(db, rows: dict[uuid.UUID, str]) -> None:
    for dept_id, code in rows.items():
        await _make_dept(db, dept_id)   # dept_ids query now joins departments
        await db.execute(text(
            "INSERT INTO approval_dept_routing (dept_id, gm_or_opm) VALUES (:d, :g) "
            "ON CONFLICT (dept_id) DO UPDATE SET gm_or_opm = EXCLUDED.gm_or_opm"),
            {"d": str(dept_id), "g": code})
    await db.commit()


async def _make_epms_invoice_chain(db, dept_id: uuid.UUID) -> uuid.UUID:
    """Insert a minimal EPMS PR → PO → Invoice chain under the given department
    (via a cost center) and return the invoice id."""
    cc_id = uuid.uuid4()
    pr_id = uuid.uuid4()
    po_id = uuid.uuid4()
    inv_id = uuid.uuid4()
    db.add(EpmsCostCenter(id=cc_id, code=f"CC-{cc_id.hex[:6]}", name="Test CC",
                           department_id=dept_id, is_active=True))
    db.add(EpmsPurchaseRequest(id=pr_id, created_by=uuid.uuid4(), cost_center_id=cc_id))
    db.add(EpmsPurchaseOrder(id=po_id, pr_id=pr_id))
    db.add(EpmsInvoice(
        id=inv_id,
        vendor_id=uuid.uuid4(),
        vendor_name="Titan Power Ltd",
        vendor_invoice_number=f"INV-{inv_id.hex[:8]}",
        internal_ref=f"REF-{inv_id.hex[:8]}",
        total_amount="1000.00",
        currency="CAD",
        status="matched",
        po_id=po_id,
        uploaded_by=uuid.uuid4(),
        created_at=datetime.now(timezone.utc),
    ))
    await db.commit()
    return inv_id


def _ids(body: dict) -> set[str]:
    return {item["id"] for item in body["items"]}


@pytest.mark.asyncio
async def test_gm_sees_invoice_from_dept_mapped_to_gm(routing_db):
    """正向:部门在 approval_dept_routing 里 gm_or_opm='gm' → GM 的发票列表包含该部门的单。"""
    dept_id = uuid.uuid4()
    await _set_routing(routing_db, {dept_id: "gm"})
    inv_id = await _make_epms_invoice_chain(routing_db, dept_id)

    async with _client_for("gm", str(uuid.uuid4())) as gm:
        resp = await gm.get("/api/v1/invoices/all")
    assert resp.status_code == 200
    assert str(inv_id) in _ids(resp.json())


@pytest.mark.asyncio
async def test_gm_does_not_see_invoice_from_dept_mapped_to_opm(routing_db):
    """负向:部门是 'opm' → GM 的列表不含它(防止退化成"返回全部")。"""
    dept_id = uuid.uuid4()
    await _set_routing(routing_db, {dept_id: "opm"})
    inv_id = await _make_epms_invoice_chain(routing_db, dept_id)

    async with _client_for("gm", str(uuid.uuid4())) as gm:
        resp = await gm.get("/api/v1/invoices/all")
    assert resp.status_code == 200
    assert str(inv_id) not in _ids(resp.json())


@pytest.mark.asyncio
async def test_multi_role_dept_manager_plus_gm_sees_gm_dept_invoice(routing_db):
    """Multi-role bug (hanchenggang: Department Manager + GM) on the unified
    invoice list. A user whose JWT base role is dept_manager but who ALSO holds
    an ADDITIONAL gm role (identity user_roles) must see invoices from the
    departments their GM role covers — not only their own department's chain.

    Before the fix, `_build_invoice_scope` branched on the single JWT base role
    and never even read user_roles, so the gm scope was silently dropped: a
    dept_manager+gm saw only their own department (here: none, since the JWT
    carries no department_id) and the GM-department invoice was invisible.
    """
    gm_dept = uuid.uuid4()
    await _set_routing(routing_db, {gm_dept: "gm"})
    inv_id = await _make_epms_invoice_chain(routing_db, gm_dept)

    # Give the (dept_manager) user an ADDITIONAL gm role via user_roles.
    mgr_id = uuid.uuid4()
    await routing_db.execute(text(
        "INSERT INTO role_defs (code, is_active) VALUES ('gm', true) "
        "ON CONFLICT (code) DO NOTHING"))
    await routing_db.execute(text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'gm')"), {"u": str(mgr_id)})
    await routing_db.commit()

    async with _client_for("dept_manager", str(mgr_id)) as mgr:
        resp = await mgr.get("/api/v1/invoices/all")
    assert resp.status_code == 200
    assert str(inv_id) in _ids(resp.json()), (
        "dept_manager+gm user did not see an invoice from a department their GM "
        "role covers — multi-role scope was NOT unioned on the invoice list"
    )


@pytest.mark.asyncio
async def test_director_sees_invoices_from_all_directed_departments(routing_db):
    """Director spans multiple departments (approval_dept_routing.director_user_id)
    — the unified invoice list must show invoices from EVERY department they
    direct, not just own uploads. Before the fix the invoice list had no director
    branch at all, so a director saw nothing here.
    """
    director_id = uuid.uuid4()
    dept_x, dept_y, dept_z = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    # director directs X and Y; Z is directed by someone else.
    for d in (dept_x, dept_y, dept_z):
        await _make_dept(routing_db, d)
    await routing_db.execute(text(
        "INSERT INTO approval_dept_routing (dept_id, gm_or_opm, director_user_id) VALUES "
        "(:x, 'gm', :dir), (:y, 'gm', :dir), (:z, 'gm', :other)"),
        {"x": str(dept_x), "y": str(dept_y), "z": str(dept_z),
         "dir": str(director_id), "other": str(uuid.uuid4())})
    await routing_db.commit()

    inv_x = await _make_epms_invoice_chain(routing_db, dept_x)
    inv_y = await _make_epms_invoice_chain(routing_db, dept_y)
    inv_z = await _make_epms_invoice_chain(routing_db, dept_z)

    async with _client_for("director", str(director_id)) as director:
        resp = await director.get("/api/v1/invoices/all")
    assert resp.status_code == 200
    seen = _ids(resp.json())
    assert str(inv_x) in seen and str(inv_y) in seen, (
        "director did not see invoices from both departments they direct"
    )
    assert str(inv_z) not in seen, "director saw an invoice from a department they do NOT direct"


@pytest.mark.asyncio
async def test_gm_sees_invoice_from_dept_with_no_routing_row(routing_db):
    """★ 无 routing 行的部门(mdm-api 建的新部门就是这种状态 —— 没有人写
    approval_dept_routing)。COALESCE 默认 'gm' 生效,GM 应该看得见;OPM 不该。

    这锁住 2026-07-16 controller brief 指出的缺口:此前 dept_ids 只读
    approval_dept_routing 本身(无 departments 表),新部门没有行 → GM 完全看
    不到该部门的发票(比 epms 侧更严重 —— epms 还有 task-chain 兜底,expense
    这条路径没有)。
    """
    dept_id = uuid.uuid4()
    await _make_dept(routing_db, dept_id)   # active department, deliberately NO routing row
    inv_id = await _make_epms_invoice_chain(routing_db, dept_id)

    async with _client_for("gm", str(uuid.uuid4())) as gm:
        resp = await gm.get("/api/v1/invoices/all")
    assert resp.status_code == 200
    assert str(inv_id) in _ids(resp.json())

    async with _client_for("opm", str(uuid.uuid4())) as opm:
        resp2 = await opm.get("/api/v1/invoices/all")
    assert resp2.status_code == 200
    assert str(inv_id) not in _ids(resp2.json())
