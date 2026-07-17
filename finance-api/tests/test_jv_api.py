"""JV lifecycle endpoints — Plan 2."""
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.journal_voucher import JournalVoucher
from app.services.posting import emit_event


def _token(role="finance_manager", sub=None):
    return jwt.encode({"sub": str(sub or uuid.uuid4()), "role": role,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _h(role="finance_manager", sub=None):
    return {"Authorization": f"Bearer {_token(role, sub)}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _draft_jv(db):
    ev_id = await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
        prepared_by=uuid.uuid4(),
        lines=[{"line_role": "purchase_expense", "account_code": "5000",
                "debit": Decimal("100.00"), "currency": "CAD"},
               {"line_role": "accounts_payable", "account_code": "2000",
                "credit": Decimal("100.00"), "currency": "CAD"}])
    return (await db.execute(select(JournalVoucher).where(
        JournalVoucher.posting_event_id == ev_id))).scalar_one()


async def test_list_and_get_detail(client, db_session):
    jv = await _draft_jv(db_session)
    r = await client.get("/finance/v1/journal-vouchers", headers=_h())
    assert r.status_code == 200
    assert any(row["id"] == str(jv.id) for row in r.json()["items"])

    r2 = await client.get(f"/finance/v1/journal-vouchers/{jv.id}", headers=_h())
    assert r2.status_code == 200
    body = r2.json()
    assert body["voucher"]["jv_number"].startswith("JV-")
    assert len(body["lines"]) == 2


async def test_review_then_post_endpoints(client, db_session):
    jv = await _draft_jv(db_session)
    r = await client.post(f"/finance/v1/journal-vouchers/{jv.id}/review", headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "reviewed"
    r2 = await client.post(f"/finance/v1/journal-vouchers/{jv.id}/post", headers=_h())
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "posted"


async def test_review_endpoint_forbidden_for_non_finance(client, db_session):
    jv = await _draft_jv(db_session)
    r = await client.post(f"/finance/v1/journal-vouchers/{jv.id}/review",
                          headers=_h(role="requester"))
    assert r.status_code == 403


async def test_post_batch_endpoint(client, db_session):
    jv = await _draft_jv(db_session)
    await client.post(f"/finance/v1/journal-vouchers/{jv.id}/review", headers=_h())
    r = await client.post("/finance/v1/journal-vouchers/post-batch",
                          json={"ids": [str(jv.id)]}, headers=_h())
    assert r.status_code == 200
    assert r.json()[0]["ok"] is True


async def test_list_pagination_and_search(client, db_session):
    a = await _draft_jv(db_session)
    b = await _draft_jv(db_session)
    # paginated envelope
    r = await client.get("/finance/v1/journal-vouchers?limit=1&offset=0", headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 2
    assert len(body["items"]) == 1
    # offset walks the list
    r2 = await client.get("/finance/v1/journal-vouchers?limit=1&offset=1", headers=_h())
    assert r2.json()["items"][0]["id"] != body["items"][0]["id"]
    # q matches jv_number
    r3 = await client.get(f"/finance/v1/journal-vouchers?q={a.jv_number}", headers=_h())
    ids = [row["id"] for row in r3.json()["items"]]
    assert str(a.id) in ids and str(b.id) not in ids


async def test_list_sort_by_jv_number_asc(client, db_session):
    a = await _draft_jv(db_session)
    b = await _draft_jv(db_session)
    r = await client.get("/finance/v1/journal-vouchers?sort=jv_number&dir=asc", headers=_h())
    assert r.status_code == 200
    nums = [row["jv_number"] for row in r.json()["items"]]
    assert {a.jv_number, b.jv_number} <= set(nums)
    assert nums == sorted(nums)          # 升序


async def test_list_sort_rejects_unknown_column(client, db_session):
    r = await client.get("/finance/v1/journal-vouchers?sort=id;drop", headers=_h())
    assert r.status_code == 422          # 白名单外 -> 拒绝


async def test_list_default_sort_is_date_desc(client, db_session):
    r = await client.get("/finance/v1/journal-vouchers", headers=_h())
    assert r.status_code == 200          # 无 sort 参数仍按 voucher_date desc


async def test_jv_permissions_endpoint(client, db_session):
    r = await client.get("/finance/v1/journal-vouchers/permissions", headers=_h())
    assert r.status_code == 200          # not 422 — route must sit above /{jv_id}
    assert r.json()["can_act"] is True
    r2 = await client.get("/finance/v1/journal-vouchers/permissions",
                          headers=_h(role="requester"))
    assert r2.json()["can_act"] is False


async def test_detail_resolves_names_and_dims(client, db_session):
    from app.models.journal_voucher import JournalVoucherLine, JvLineDimension
    from app.models.mirrors import CostCenter, Department, User

    preparer = uuid.uuid4()
    db_session.add(User(id=preparer, email="fin@x.com", full_name="Fin Preparer"))
    cc = CostCenter(id=uuid.uuid4(), code="MOH-0104-P02", name="Processing", is_active=True)
    dept = Department(id=uuid.uuid4(), code="0104", name="Production", is_active=True)
    db_session.add_all([cc, dept])
    await db_session.flush()

    jv = await _draft_jv(db_session)
    jv.prepared_by = preparer
    line = (await db_session.execute(select(JournalVoucherLine).where(
        JournalVoucherLine.jv_id == jv.id).order_by(JournalVoucherLine.line_no))
        ).scalars().first()
    line.cost_center_id = cc.id
    line.department_id = dept.id
    db_session.add(JvLineDimension(jv_line_id=line.id, dim_code="income_expense_item",
                                   value_text="CRM004"))
    await db_session.flush()

    r = await client.get(f"/finance/v1/journal-vouchers/{jv.id}", headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["voucher"]["prepared_by_name"] == "Fin Preparer"
    ln = next(l for l in body["lines"] if l["line_no"] == line.line_no)
    assert ln["cost_center_code"] == "MOH-0104-P02"
    assert ln["department_name"] == "Production"
    assert {"dim_code": "income_expense_item", "value_text": "CRM004"} in [
        {"dim_code": d["dim_code"], "value_text": d["value_text"]} for d in ln["dims"]]
