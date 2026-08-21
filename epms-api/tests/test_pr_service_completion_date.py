"""PR 的 Service/Project Expected Completion Date 真正落库。

回归背景:PrCreatePage 从一开始就渲染了这个字段并打了必填星号,但 zod 规则是
`.optional()`、两个 payload map 都没带它、schema 和模型里也没有这一列 —— 用户填
的日期从来没离开过浏览器。下面这些用例锁住"填了就存得住、服务/项目 PR 不填不
让提交"。
"""
import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.cost_center import CostCenter
from app.models.department import Department

pytestmark = pytest.mark.asyncio

COMPLETION = "2026-09-30"


async def _cost_center_id(test_engine) -> str:
    """Seed a cost center through the ORM — departments are mdm-owned, so there
    is no HTTP path to create one."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        dept = Department(code=f"D-{uuid.uuid4().hex[:6]}", name="Service Date Dept")
        db.add(dept)
        await db.flush()
        cc = CostCenter(code=f"CC-{uuid.uuid4().hex[:6]}", name="Service Date CC",
                        department_id=dept.id)
        db.add(cc)
        await db.commit()
        return str(cc.id)


async def _submittable(client, test_engine, **over):
    """PR 上挂齐 pr_action 里排在完成日之前的两道闸门要的字段(vendor + 预算账户),
    这样断言才落在完成日那条上,而不是被前面的闸门抢答。"""
    return await _create(
        client,
        vendor_id=await _vendor_id(client),
        cost_center_id=await _cost_center_id(test_engine),
        budget_code="6100-01",
        **over,
    )


async def _vendor_id(client):
    """提交闸门要求 PR 挂了 vendor(api/v1/pr.py 的既有规则),否则先被那条拦下。"""
    resp = await client.post("/api/v1/vendors", json={
        "code": f"V-SVC-{uuid.uuid4().hex[:6]}", "name": "Acme Services",
        "category": "supplier", "contact_name": "AP", "contact_email": "ap@acme.example",
        "payment_terms": "net30", "currency": "CAD"})
    resp.raise_for_status()
    return resp.json()["id"]


def _payload(**over):
    body = {
        "title": "Annual duct cleaning",
        "type": 4,
        "currency": "CAD",
        "required_by": "2026-09-01",
        "service_completion_date": COMPLETION,
        "line_items": [{
            "description": "Duct cleaning — Plant 1",
            "qty": "1", "unit": "job",
            "unit_price": "1000.00", "line_total": "1000.00",
        }],
    }
    body.update(over)
    return body


async def _create(client, **over):
    resp = await client.post("/api/v1/pr", json=_payload(**over))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── 落库 ──────────────────────────────────────────────────────────────────────

async def test_create_persists_the_completion_date(admin_client):
    pr = await _create(admin_client)
    assert pr["service_completion_date"] == COMPLETION

    # 重新 GET —— 证明是真的写进去了,而不是回声了一遍请求体
    fetched = (await admin_client.get(f"/api/v1/pr/{pr['id']}")).json()
    assert fetched["service_completion_date"] == COMPLETION


async def test_project_type_6_persists_it_too(admin_client):
    pr = await _create(admin_client, type=6)
    assert pr["service_completion_date"] == COMPLETION


async def test_edit_updates_the_completion_date(admin_client):
    pr = await _create(admin_client)
    resp = await admin_client.patch(
        f"/api/v1/pr/{pr['id']}", json={"service_completion_date": "2026-10-15"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["service_completion_date"] == "2026-10-15"

    fetched = (await admin_client.get(f"/api/v1/pr/{pr['id']}")).json()
    assert fetched["service_completion_date"] == "2026-10-15", \
        "PATCH 走的是 crud.update 的硬编码白名单元组;字段不在里面就静默不保存"


async def test_a_physical_pr_may_omit_it(admin_client):
    pr = await _create(admin_client, type=2, service_completion_date=None)
    assert pr["service_completion_date"] is None


# ── 提交闸门 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pr_type", [4, 6])
async def test_service_pr_cannot_be_submitted_without_it(admin_client, test_engine, pr_type):
    pr = await _submittable(admin_client, test_engine,
                            type=pr_type, service_completion_date=None)
    resp = await admin_client.post(
        f"/api/v1/pr/{pr['id']}/action", json={"action": "submit"})
    assert resp.status_code == 409, resp.text
    assert "Completion Date" in resp.json()["detail"]


async def test_a_physical_pr_submits_fine_without_it(admin_client, test_engine):
    """闸门必须只卡服务/项目两类 —— 卡到物理采购上会挡住全公司提单。"""
    pr = await _submittable(admin_client, test_engine,
                            type=2, service_completion_date=None)
    resp = await admin_client.post(
        f"/api/v1/pr/{pr['id']}/action", json={"action": "submit"})
    # 提交本身可能因为没有活的 approval-api 而失败,但**不能**是被这个闸门挡的。
    if resp.status_code == 409:
        assert "Completion Date" not in resp.json().get("detail", "")
