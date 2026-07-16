"""Chart of Accounts + mappings + executor account stamping (Phase a A1)."""
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
from app.models.coa import AccountMapping, ChartOfAccount


def _token(role: str = "finance_manager") -> str:
    payload = {"sub": str(uuid.uuid4()), "role": role,
               "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _h(role="finance_manager"):
    return {"Authorization": f"Bearer {_token(role)}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_seed_integrity(db_session):
    """Every seeded mapping targets an existing postable account; normal
    balances follow accounting convention for the major types."""
    accounts = {a.code: a for a in (await db_session.execute(
        select(ChartOfAccount))).scalars().all()}
    assert len(accounts) >= 40

    mappings = (await db_session.execute(select(AccountMapping))).scalars().all()
    assert {m.source_code for m in mappings if m.mapping_type == "line_role"} == {
        "accounts_payable", "bank", "employee_expense", "sales_tax", "purchase_expense",
        "accounts_receivable", "revenue", "output_tax"}
    for m in mappings:
        target = accounts[m.account_code]
        assert target.is_postable, f"{m.source_code} maps to header {m.account_code}"

    assert accounts["2000"].normal_balance == "credit"   # AP
    assert accounts["1010"].normal_balance == "debit"    # bank
    assert accounts["1400"].normal_balance == "debit"    # ITC receivable
    assert accounts["4000"].normal_balance == "credit"   # revenue
    assert accounts["5000"].normal_balance == "debit"    # COGS


async def test_list_accounts_filters(client):
    r = await client.get("/finance/v1/coa", params={"account_type": "asset", "postable_only": True},
                         headers=_h("ap_clerk"))
    assert r.status_code == 200
    body = r.json()
    assert all(a["account_type"] == "asset" and a["is_postable"] for a in body)
    assert any(a["code"] == "1010" for a in body)
    assert not any(a["code"] == "1000" for a in body)   # header excluded


async def test_mapping_upsert_and_validation(client):
    # budget_account bridge — empty by seed, finance fills it
    r = await client.put("/finance/v1/coa/mappings/budget_account/6100-LAB",
                         json={"account_code": "6300"}, headers=_h())
    assert r.status_code == 200
    assert r.json()["account_code"] == "6300"

    # re-upsert overwrites
    r = await client.put("/finance/v1/coa/mappings/budget_account/6100-LAB",
                         json={"account_code": "6200"}, headers=_h())
    assert r.status_code == 200
    assert r.json()["account_code"] == "6200"

    # header account rejected
    r = await client.put("/finance/v1/coa/mappings/budget_account/X",
                         json={"account_code": "1000"}, headers=_h())
    assert r.status_code == 422

    # unknown account rejected
    r = await client.put("/finance/v1/coa/mappings/budget_account/X",
                         json={"account_code": "9999"}, headers=_h())
    assert r.status_code == 404

    # role gate
    r = await client.put("/finance/v1/coa/mappings/budget_account/X",
                         json={"account_code": "6400"}, headers=_h("requester"))
    assert r.status_code == 403


async def test_coa_permissions_resolve_assignments(client, db_session):
    """can_manage = JWT manage roles ∪ ADDITIONAL roles held in identity's
    user_roles table (same physical DB) — the boss is a 'requester' with an
    additional finance_bp role, never gate on jwt.role alone. Phase 3: sourced
    from user_roles, not the retired company_config.role_management."""
    from sqlalchemy import text

    r = await client.get("/finance/v1/coa/permissions", headers=_h("finance_manager"))
    assert r.json() == {"can_manage": True}

    r = await client.get("/finance/v1/coa/permissions", headers=_h("requester"))
    assert r.json() == {"can_manage": False}

    boss_id = str(uuid.uuid4())
    await db_session.execute(text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'finance_bp')"),
        {"u": boss_id})
    await db_session.flush()
    token = jwt.encode({"sub": boss_id, "role": "requester",
                        "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                       settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    r = await client.get("/finance/v1/coa/permissions",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.json() == {"can_manage": True}


async def test_coa_primary_role_finance_bp_without_assignment_denied(client):
    """finance_bp is a job FUNCTION many people hold, not a singleton post —
    holding it as your PRIMARY role (jwt.role) is not the same as being the
    curated, assigned approver. A user whose primary role is finance_bp but
    who has NO user_roles assignment row must be denied COA management."""
    r = await client.get("/finance/v1/coa/permissions", headers=_h("finance_bp"))
    assert r.json() == {"can_manage": False}

    r = await client.put("/finance/v1/coa/mappings/budget_account/X",
                         json={"account_code": "6400"}, headers=_h("finance_bp"))
    assert r.status_code == 403


async def test_account_crud_and_aux_dimensions(client):
    """辅助核算项: create with dims, patch them, reject unknown dims."""
    r = await client.post("/finance/v1/coa", headers=_h(), json={
        "code": "6310", "name": "External Lab Testing", "account_type": "expense",
        "normal_balance": "debit", "parent_code": "6300",
        "aux_dimensions": ["cost_center", "partner"],   # bare codes ⇒ optional
    })
    assert r.status_code == 201, r.text
    assert r.json()["aux_dimensions"] == [
        {"code": "cost_center", "required": False},
        {"code": "partner", "required": False},
    ]

    # per-dimension required flag (必填) — objects accepted, mixed with bare codes
    r = await client.patch("/finance/v1/coa/6310", headers=_h(), json={
        "aux_dimensions": [{"code": "cost_center", "required": True}, "project"],
    })
    assert r.status_code == 200
    assert r.json()["aux_dimensions"] == [
        {"code": "cost_center", "required": True},
        {"code": "project", "required": False},
    ]

    r = await client.patch("/finance/v1/coa/6310", headers=_h(),
                           json={"aux_dimensions": ["not_a_real_dim"]})  # not in catalog
    assert r.status_code == 422

    r = await client.post("/finance/v1/coa", headers=_h("requester"), json={
        "code": "9998", "name": "X", "account_type": "expense", "normal_balance": "debit"})
    assert r.status_code == 403

    r = await client.post("/finance/v1/coa", headers=_h(), json={
        "code": "2000", "name": "dup", "account_type": "liability", "normal_balance": "credit"})
    assert r.status_code == 409


async def test_import_csv_upserts_existing_coa(client, db_session):
    """The in-use COA loads via CSV file import (system-wide convention):
    update by code, insert new, report bad rows without failing the batch."""
    csv_text = (
        "code,name,account_type,normal_balance,subtype,parent_code,is_postable,is_active,aux_dimensions\n"
        "2000,Accounts Payable (NC),liability,credit,,,true,true,partner*\n"
        "2105,Accrued Purchases,liability,credit,,2100,true,true,partner*|item\n"
        "9XX,Broken Row,not_a_type,credit,,,true,true,\n"
    )
    r = await client.post(
        "/finance/v1/coa/import", headers=_h(),
        files={"file": ("coa.csv", csv_text.encode("utf-8-sig"), "text/csv")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["inserted"] == 1 and body["updated"] == 1
    assert len(body["errors"]) == 1 and "Line 4" in body["errors"][0]

    got = {a.code: a for a in (await db_session.execute(
        select(ChartOfAccount))).scalars().all()}
    assert got["2000"].name == "Accounts Payable (NC)"
    assert got["2000"].aux_dimensions == [{"code": "partner", "required": True}]   # star = required
    assert got["2105"].aux_dimensions == [
        {"code": "partner", "required": True}, {"code": "item", "required": False}]
    assert "9XX" not in got


async def test_export_csv_roundtrip(client):
    """Export → import round-trips (same columns both ways)."""
    r = await client.get("/finance/v1/coa/export", headers=_h())
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "chart-of-accounts.csv" in r.headers["content-disposition"]
    text = r.text.lstrip("﻿")
    assert text.splitlines()[0].startswith(
        "code,name,account_type,normal_balance,subtype,parent_code,is_postable,is_active,aux_dimensions")
    assert any(line.startswith("2000,") for line in text.splitlines())

    r2 = await client.post(
        "/finance/v1/coa/import", headers=_h(),
        files={"file": ("coa.csv", text.encode("utf-8-sig"), "text/csv")},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["errors"] == []
    assert r2.json()["inserted"] == 0   # pure round-trip: everything updates


async def test_seed_aux_defaults(db_session):
    accounts = {a.code: a for a in (await db_session.execute(
        select(ChartOfAccount))).scalars().all()}

    def codes(a):
        return {d["code"] for d in a.aux_dimensions}

    assert codes(accounts["2000"]) == {"partner"}                   # AP carries vendor
    assert "cost_center" in codes(accounts["6500"])                 # opex by CC
    assert codes(accounts["1210"]) == {"item", "warehouse", "lot"}
    # migrated seed defaults to optional; finance flips required in the UI
    assert all(d["required"] is False for d in accounts["2000"].aux_dimensions)


async def test_delete_only_unreferenced_accounts(client, db_session):
    """Delete works only for never-referenced accounts; referenced ones 409
    with the reason (deactivate instead)."""
    from app.models.posting import PostingEvent, PostingLine
    from datetime import datetime, timezone

    # fresh, unreferenced → deletable
    await client.post("/finance/v1/coa", headers=_h(), json={
        "code": "7777", "name": "Scratch", "account_type": "expense",
        "normal_balance": "debit"})
    r = await client.delete("/finance/v1/coa/7777", headers=_h())
    assert r.status_code == 204
    r = await client.delete("/finance/v1/coa/7777", headers=_h())
    assert r.status_code == 404

    # referenced by a mapping (seed: 2000) → 409
    r = await client.delete("/finance/v1/coa/2000", headers=_h())
    assert r.status_code == 409 and "mapping" in r.json()["detail"]

    # parent with children (seed: 1000) → 409
    r = await client.delete("/finance/v1/coa/1000", headers=_h())
    assert r.status_code == 409 and "child" in r.json()["detail"]

    # referenced by a posting line → 409
    ev = PostingEvent(source_service="finance", source_doc_type="pa",
                      source_doc_id=uuid.uuid4(), source_doc_number="PA-X",
                      event_type="payment", occurred_at=datetime.now(timezone.utc))
    db_session.add(ev)
    await db_session.flush()
    db_session.add(PostingLine(event_id=ev.id, line_no=1, line_role="bank",
                               credit=Decimal("1.00"), account_code="1020"))
    await db_session.flush()
    r = await client.delete("/finance/v1/coa/1020", headers=_h())
    assert r.status_code == 409 and "posting" in r.json()["detail"]

    # delete is manage-gated
    r = await client.delete("/finance/v1/coa/1020", headers=_h("requester"))
    assert r.status_code == 403


async def test_executor_stamps_account_codes(client, db_session):
    """A1 payoff: posting lines now carry ledger accounts via the mappings."""
    from app.models.pa import PaymentApplication
    from app.models.posting import PostingEvent, PostingLine

    pa = PaymentApplication(
        pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="COA stamp test", pa_type="PA-DIR",
        status="approved", po_id=None, po_number=None,
        vendor_id=uuid.uuid4(), vendor_name="ACME", invoice_ids=[],
        payment_amount=Decimal("88.00"), currency="CAD", created_by=uuid.uuid4(),
    )
    db_session.add(pa)
    await db_session.flush()

    r = await client.post("/finance/v1/payments/execute",
                          json={"doc_kind": "pa_dir", "doc_id": str(pa.id)},
                          headers=_h("ap_clerk"))
    assert r.status_code == 200, r.text

    ev = (await db_session.execute(
        select(PostingEvent).where(PostingEvent.source_doc_id == pa.id)
    )).scalar_one()
    lines = (await db_session.execute(
        select(PostingLine).where(PostingLine.event_id == ev.id).order_by(PostingLine.line_no)
    )).scalars().all()
    assert lines[0].line_role == "accounts_payable" and lines[0].account_code == "2000"
    assert lines[1].line_role == "bank" and lines[1].account_code == "1010"


# ── A1.7: aux dimension catalog + NC-parity metadata ────────────────────────────

async def test_aux_catalog_seeded_with_nc_dimensions(client):
    r = await client.get("/finance/v1/coa/aux-types", headers=_h("ap_clerk"))
    assert r.status_code == 200
    codes = {t["code"] for t in r.json()}
    # the legacy chart's dimensions are all present
    assert {"partner", "cost_center", "department", "item", "project",
            "income_expense_item", "sales_type", "country_region",
            "bank_account"}.issubset(codes)
    by = {t["code"]: t for t in r.json()}
    assert by["department"]["storage"] == "column"
    assert by["sales_type"]["storage"] == "aux_table"


async def test_create_custom_aux_type_then_use_it(client):
    r = await client.post("/finance/v1/coa/aux-types", headers=_h(), json={
        "code": "grant_program", "name": "Grant Program"})
    assert r.status_code == 201
    assert r.json()["storage"] == "aux_table" and r.json()["builtin"] is False

    # now an account can subscribe to it
    r = await client.post("/finance/v1/coa", headers=_h(), json={
        "code": "6310", "name": "Subsidized Testing", "account_type": "expense",
        "normal_balance": "debit", "aux_dimensions": [
            {"code": "department", "required": True}, "grant_program"]})
    assert r.status_code == 201, r.text
    assert {d["code"] for d in r.json()["aux_dimensions"]} == {"department", "grant_program"}

    # duplicate code rejected
    r = await client.post("/finance/v1/coa/aux-types", headers=_h(), json={
        "code": "grant_program", "name": "dup"})
    assert r.status_code == 409

    # manage-gated
    r = await client.post("/finance/v1/coa/aux-types", headers=_h("requester"), json={
        "code": "x", "name": "x"})
    assert r.status_code == 403


async def test_account_nc_metadata_roundtrip(client, db_session):
    from datetime import date
    r = await client.post("/finance/v1/coa", headers=_h(), json={
        "code": "5099", "name": "产成品销售成本", "account_type": "expense",
        "normal_balance": "debit", "aux_dimensions": ["item"],
        "quantity_accounting": True, "default_uom": "kg",
        "default_currency": "CAD", "effective_from": "2026-01-01",
        "cash_flow_category": "operating", "mnemonic": "CXSPC",
        "is_off_balance": False})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["quantity_accounting"] is True and b["default_uom"] == "kg"
    assert b["cash_flow_category"] == "operating" and b["mnemonic"] == "CXSPC"

    row = (await db_session.execute(
        select(ChartOfAccount).where(ChartOfAccount.code == "5099"))).scalar_one()
    assert row.effective_from == date(2026, 1, 1)


async def test_csv_roundtrip_includes_metadata(client):
    """Export → import keeps quantity accounting + uom (NC parity)."""
    await client.post("/finance/v1/coa", headers=_h(), json={
        "code": "5098", "name": "COGS Powder", "account_type": "expense",
        "normal_balance": "debit", "aux_dimensions": [{"code": "item", "required": True}],
        "quantity_accounting": True, "default_uom": "kg"})
    text = (await client.get("/finance/v1/coa/export", headers=_h())).text.lstrip("﻿")
    row = next(l for l in text.splitlines() if l.startswith("5098,"))
    assert "kg" in row and "item*" in row and "true" in row

    r = await client.post("/finance/v1/coa/import", headers=_h(),
                          files={"file": ("c.csv", text.encode("utf-8-sig"), "text/csv")})
    assert r.status_code == 200 and r.json()["errors"] == []


async def test_emit_writes_long_tail_aux_to_side_table(db_session):
    """A1.7 hybrid: department on the spine column; sales_type/country in the
    posting_line_dimensions side table."""
    import uuid as _uuid
    from app.services.posting import emit_event
    from app.models.posting import PostingLine, PostingLineDimension

    dept = _uuid.uuid4()
    ev = await emit_event(
        db_session, source_service="finance", source_doc_type="sale",
        source_doc_id=_uuid.uuid4(), source_doc_number="SO-1", event_type="revenue",
        lines=[
            {"line_role": "accounts_receivable", "debit": Decimal("100.00"),
             "department_id": dept,
             "aux": {"sales_type": "export", "country_region": "US"}},
            {"line_role": "sales", "credit": Decimal("100.00")},
        ],
    )
    assert ev is not None
    line = (await db_session.execute(
        select(PostingLine).where(PostingLine.event_id == ev,
                                  PostingLine.line_role == "accounts_receivable"))).scalar_one()
    assert line.department_id == dept
    dims = (await db_session.execute(
        select(PostingLineDimension).where(PostingLineDimension.posting_line_id == line.id))).scalars().all()
    assert {(d.dim_code, d.value_text) for d in dims} == {
        ("sales_type", "export"), ("country_region", "US")}
