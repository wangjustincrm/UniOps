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
        {"sub": user_id, "role": role, "exp": datetime.utcnow() + timedelta(hours=8)},
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
        await session.execute(text("DELETE FROM approval_dept_routing"))
        await session.commit()
        yield session


async def _set_routing(db, rows: dict[uuid.UUID, str]) -> None:
    for dept_id, code in rows.items():
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
