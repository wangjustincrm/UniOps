"""EPMS Direct PA: an invoice matched to no PO and no agreement (Match → Direct
PA), paid by a PA that names its own department / cost centre / budget account
and walks the `pa_dir` workflow. Moved here from OA (retired 2026-08-07).
"""
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.models.cost_center import CostCenter
from app.models.department import Department
from app.schemas.auth import RegisterRequest


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """finance_sync / budget-api are fail-open HTTP calls; keep tests offline."""
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr("app.api.v1.invoices.finance_sync.sync_ap_invoice", _noop)
    monkeypatch.setattr("app.services.budget_client.ensure_known_budget_code", _noop)


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _user_client(test_engine, role: str, department_id=None):
    """(client, user_id) for a fresh user of `role`, optionally in a department."""
    async with _factory(test_engine)() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"{role}-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name=f"Test {role}", role=role))
        if department_id is not None:
            user.department_id = department_id
        await db.commit()
        uid = user.id
    token = create_access_token(str(uid), role)
    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"}), uid


@pytest.fixture
async def world(test_engine, admin_client):
    """Two departments with a cost centre each, two vendors."""
    tag = uuid.uuid4().hex[:6].upper()
    async with _factory(test_engine)() as db:
        ops = Department(code=f"OPS{tag}", name=f"Operations {tag}")
        qa = Department(code=f"QA{tag}", name=f"Quality {tag}")
        db.add_all([ops, qa])
        await db.flush()
        ops_cc = CostCenter(code=f"GA-{tag}", name="Ops G&A", department_id=ops.id)
        qa_cc = CostCenter(code=f"LAB-{tag}", name="QA Lab", department_id=qa.id)
        db.add_all([ops_cc, qa_cc])
        await db.commit()
        ids = {"ops": ops.id, "qa": qa.id, "ops_cc": ops_cc.id, "qa_cc": qa_cc.id}
    for key in ("v1", "v2"):
        r = await admin_client.post("/api/v1/vendors", json={
            "code": f"V-{key}-{tag}", "name": f"Direct Vendor {key} {tag}", "category": "Services",
            "contact_name": "X", "contact_email": "x@x.com",
            "payment_terms": "net30", "currency": "CAD"})
        assert r.status_code == 201, r.text
        ids[key] = r.json()["id"]
    return ids


async def _invoice(client, vendor_id, number=None, amount="1000.00", tax="130.00",
                   due="2026-11-15"):
    r = await client.post("/api/v1/invoices", json={
        "vendor_id": vendor_id,
        "vendor_invoice_number": number or f"D-{uuid.uuid4().hex[:6]}",
        "amount": amount, "tax_amount": tax, "currency": "CAD",
        "invoice_date": "2026-10-15", "due_date": due,
        "line_items": [{"description": "Consulting", "quantity": "1",
                        "unit_price": amount, "line_total": amount}]})
    assert r.status_code == 201, r.text
    return r.json()


async def _direct_invoice(client, vendor_id, **kw):
    inv = await _invoice(client, vendor_id, **kw)
    r = await client.post(f"/api/v1/invoices/{inv['id']}/mark-direct")
    assert r.status_code == 200, r.text
    return r.json()


def _pa_body(w, invoice_ids, **over):
    body = {"invoice_ids": invoice_ids, "department_id": str(w["ops"]),
            "cost_center_id": str(w["ops_cc"]), "budget_account_code": "CRM0150101",
            "notes": "Annual calibration — vendor works without a PO"}
    body.update(over)
    return body


# ── Match → Direct PA ─────────────────────────────────────────────────────────

async def test_mark_direct_matches_the_invoice_to_nothing(admin_client, world):
    inv = await _invoice(admin_client, world["v1"])
    r = await admin_client.post(f"/api/v1/invoices/{inv['id']}/mark-direct")
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["status"], out["match_route"]) == ("matched", "direct")
    assert out["po_id"] is None and out["agreement_id"] is None
    assert out["matched_by_name"]
    # Already matched: a second time is refused, not silently re-stamped.
    again = await admin_client.post(f"/api/v1/invoices/{inv['id']}/mark-direct")
    assert again.status_code == 409
    # The Invoices page filter finds it on its own route, and not under PO.
    by_route = await admin_client.get("/api/v1/invoices", params={"route": "direct"})
    assert inv["id"] in {i["id"] for i in by_route.json()["items"]}
    po_route = await admin_client.get("/api/v1/invoices", params={"route": "po", "page_size": 200})
    assert inv["id"] not in {i["id"] for i in po_route.json()["items"]}


async def test_mark_direct_follows_the_match_right(test_engine, admin_client, world):
    """The uploader may (as for a PO match); an unrelated requester may not."""
    inv = await _invoice(admin_client, world["v1"])
    stranger, _ = await _user_client(test_engine, "requester")
    uploader, uploader_id = await _user_client(test_engine, "requester")
    async with _factory(test_engine)() as db:
        await db.execute(text("UPDATE invoices SET uploaded_by = :u WHERE id = :i"),
                         {"u": uploader_id, "i": inv["id"]})
        await db.commit()
    async with stranger, uploader:
        denied = await stranger.post(f"/api/v1/invoices/{inv['id']}/mark-direct")
        assert denied.status_code == 403
        ok = await uploader.post(f"/api/v1/invoices/{inv['id']}/mark-direct")
        assert ok.status_code == 200, ok.text


# ── Create Direct PA ──────────────────────────────────────────────────────────

async def test_create_direct_pa_from_two_invoices(admin_client, world):
    a = await _direct_invoice(admin_client, world["v1"], amount="1000.00", tax="130.00")
    b = await _direct_invoice(admin_client, world["v1"], amount="200.00", tax="26.00")
    r = await admin_client.post("/api/v1/pa/direct", json=_pa_body(world, [a["id"], b["id"]]))
    assert r.status_code == 201, r.text
    pa = r.json()
    assert pa["source"] == "direct" and pa["status"] == "draft" and pa["pa_type"] == "regular"
    assert pa["po_id"] is None and pa["agreement_id"] is None
    assert Decimal(pa["subtotal"]) == Decimal("1200.00")
    assert Decimal(pa["tax_amount"]) == Decimal("156.00")
    assert Decimal(pa["payment_amount"]) == Decimal("1356.00")
    assert set(pa["invoice_ids"]) == {a["id"], b["id"]}
    assert len(pa["line_items"]) == 2
    assert pa["department_id"] == str(world["ops"])
    assert pa["department_name"].startswith("Operations")
    assert pa["cost_center_id"] == str(world["ops_cc"]) and pa["cost_center_code"].startswith("GA-")
    assert pa["budget_account_code"] == "CRM0150101"
    assert pa["notes"].startswith("Annual calibration")
    assert pa["title"].startswith("Direct Payment — ")

    # Listed and readable in EPMS (OA's own Direct PAs are not, see below).
    listed = await admin_client.get("/api/v1/pa", params={"page_size": 200})
    assert pa["id"] in {p["id"] for p in listed.json()["items"]}
    by_dept = await admin_client.get("/api/v1/pa", params={"department_id": str(world["ops"])})
    assert pa["id"] in {p["id"] for p in by_dept.json()["items"]}
    got = await admin_client.get(f"/api/v1/pa/{pa['id']}")
    assert got.status_code == 200 and got.json()["cost_center_code"] == pa["cost_center_code"]


async def test_create_direct_pa_refusals(admin_client, world):
    plain = await _invoice(admin_client, world["v1"])
    r = await admin_client.post("/api/v1/pa/direct", json=_pa_body(world, [plain["id"]]))
    assert r.status_code == 422 and "Direct PA" in r.json()["detail"]

    a = await _direct_invoice(admin_client, world["v1"])
    other_vendor = await _direct_invoice(admin_client, world["v2"])
    r = await admin_client.post("/api/v1/pa/direct", json=_pa_body(world, [a["id"], other_vendor["id"]]))
    assert r.status_code == 422 and "same vendor" in r.json()["detail"]

    # A cost centre of another department is refused.
    r = await admin_client.post("/api/v1/pa/direct", json=_pa_body(
        world, [a["id"]], cost_center_id=str(world["qa_cc"])))
    assert r.status_code == 422 and "does not belong" in r.json()["detail"]

    # The reason is required — blank after trimming is no reason.
    for blank in ("", "   "):
        r = await admin_client.post("/api/v1/pa/direct", json=_pa_body(world, [a["id"]], notes=blank))
        assert r.status_code == 422, blank

    ok = await admin_client.post("/api/v1/pa/direct", json=_pa_body(world, [a["id"]]))
    assert ok.status_code == 201, ok.text
    # One invoice, one live PA.
    twice = await admin_client.post("/api/v1/pa/direct", json=_pa_body(world, [a["id"]]))
    assert twice.status_code == 409 and ok.json()["pa_number"] in twice.json()["detail"]


async def test_invoices_due_far_apart_need_separate_direct_pas(admin_client, world):
    a = await _direct_invoice(admin_client, world["v1"], due="2026-11-01")
    b = await _direct_invoice(admin_client, world["v1"], due="2026-12-20")
    r = await admin_client.post("/api/v1/pa/direct", json=_pa_body(world, [a["id"], b["id"]]))
    assert r.status_code == 422 and "fall due within" in r.json()["detail"]


async def test_requester_pays_only_invoices_they_can_open(test_engine, admin_client, world):
    """Ordinary PA right (epms.pa.write) + the invoice read gate: a requester can
    raise a Direct PA on an invoice they uploaded, not on someone else's."""
    theirs = await _direct_invoice(admin_client, world["v1"])
    mine = await _direct_invoice(admin_client, world["v1"])
    req, req_id = await _user_client(test_engine, "requester", world["ops"])
    async with _factory(test_engine)() as db:
        await db.execute(text("UPDATE invoices SET uploaded_by = :u WHERE id = :i"),
                         {"u": req_id, "i": mine["id"]})
        # Production grants requester epms.pa.write (identity seed_phase2_keys);
        # the test matrix seeds phase-1 keys only.
        await db.execute(text(
            "INSERT INTO permission_defs(key,module,label,sort) "
            "VALUES ('epms.pa.write','epms','Create / Edit PAs',102) ON CONFLICT (key) DO NOTHING"))
        await db.execute(text(
            "INSERT INTO role_permissions(role_code,permission_key) "
            "VALUES ('requester','epms.pa.write') ON CONFLICT DO NOTHING"))
        await db.commit()
    async with req:
        denied = await req.post("/api/v1/pa/direct", json=_pa_body(world, [theirs["id"]]))
        assert denied.status_code == 404
        ok = await req.post("/api/v1/pa/direct", json=_pa_body(world, [mine["id"]]))
        assert ok.status_code == 201, ok.text
        # …and sees it in their own list.
        listed = await req.get("/api/v1/pa", params={"page_size": 200})
        assert ok.json()["id"] in {p["id"] for p in listed.json()["items"]}


# ── Edit / submit ─────────────────────────────────────────────────────────────

async def test_edit_a_direct_pa(admin_client, world):
    inv = await _direct_invoice(admin_client, world["v1"])
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_body(world, [inv["id"]]))).json()

    moved = await admin_client.patch(f"/api/v1/pa/{pa['id']}", json={
        "department_id": str(world["qa"]), "cost_center_id": str(world["qa_cc"]),
        "budget_account_code": "CRM0150103", "notes": "Lab audit"})
    assert moved.status_code == 200, moved.text
    body = moved.json()
    assert (body["department_id"], body["cost_center_id"], body["budget_account_code"], body["notes"]) == \
        (str(world["qa"]), str(world["qa_cc"]), "CRM0150103", "Lab audit")

    # Department moved without its cost centre: the old one is not the new department's.
    mismatch = await admin_client.patch(f"/api/v1/pa/{pa['id']}", json={"department_id": str(world["ops"])})
    assert mismatch.status_code == 422

    # Amounts follow the invoices.
    amounts = await admin_client.patch(f"/api/v1/pa/{pa['id']}", json={"subtotal": "1.00"})
    assert amounts.status_code == 422
    blank = await admin_client.patch(f"/api/v1/pa/{pa['id']}", json={"notes": "  "})
    assert blank.status_code == 422
    cleared = await admin_client.patch(f"/api/v1/pa/{pa['id']}", json={"cost_center_id": None})
    assert cleared.status_code == 422


async def test_submit_gate_needs_every_dimension(test_engine, admin_client, world):
    inv = await _direct_invoice(admin_client, world["v1"])
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_body(world, [inv["id"]]))).json()
    async with _factory(test_engine)() as db:
        await db.execute(text("UPDATE payment_applications SET cost_center_id = NULL WHERE id = :i"),
                         {"i": pa["id"]})
        await db.commit()
    r = await admin_client.post(f"/api/v1/pa/{pa['id']}/action", json={"action": "submit"})
    assert r.status_code == 409 and "cost center" in r.json()["detail"]


async def test_dims_are_direct_only(test_engine, admin_client, world):
    """A PO / agreement / freight PA takes its dimensions elsewhere."""
    inv = await _direct_invoice(admin_client, world["v1"])
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_body(world, [inv["id"]]))).json()
    async with _factory(test_engine)() as db:
        await db.execute(text("UPDATE payment_applications SET source = 'freight' WHERE id = :i"),
                         {"i": pa["id"]})
        await db.commit()
    r = await admin_client.patch(f"/api/v1/pa/{pa['id']}", json={"budget_account_code": "X"})
    assert r.status_code == 422 and "Direct PA only" in r.json()["detail"]


# ── Invoice side effects ──────────────────────────────────────────────────────

async def test_a_held_direct_invoice_is_locked(test_engine, admin_client, world):
    inv = await _direct_invoice(admin_client, world["v1"])
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_body(world, [inv["id"]]))).json()

    edit = await admin_client.patch(f"/api/v1/invoices/{inv['id']}", json={"amount": "999.00"})
    assert edit.status_code == 409 and pa["pa_number"] in edit.json()["detail"]
    unmatch = await admin_client.post(f"/api/v1/invoices/{inv['id']}/unmatch-direct",
                                      json={"reason": "wrong route"})
    assert unmatch.status_code == 422 and pa["pa_number"] in unmatch.json()["detail"]

    async with _factory(test_engine)() as db:
        await db.execute(text("UPDATE payment_applications SET status = 'cancelled' WHERE id = :i"),
                         {"i": pa["id"]})
        await db.commit()
    back = await admin_client.post(f"/api/v1/invoices/{inv['id']}/unmatch-direct",
                                   json={"reason": "wrong route"})
    assert back.status_code == 200, back.text
    assert (back.json()["status"], back.json()["match_route"]) == ("unmatched", None)


# ── Visibility ────────────────────────────────────────────────────────────────

async def test_direct_pa_visibility_follows_its_department(test_engine, admin_client, world):
    inv = await _direct_invoice(admin_client, world["v1"])
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_body(world, [inv["id"]]))).json()

    ops_mgr, _ = await _user_client(test_engine, "dept_manager", world["ops"])
    qa_mgr, _ = await _user_client(test_engine, "dept_manager", world["qa"])
    outsider, _ = await _user_client(test_engine, "requester", world["ops"])
    async with ops_mgr, qa_mgr, outsider:
        assert (await ops_mgr.get(f"/api/v1/pa/{pa['id']}")).status_code == 200
        listed = await ops_mgr.get("/api/v1/pa", params={"page_size": 200})
        assert pa["id"] in {p["id"] for p in listed.json()["items"]}
        # The invoice it pays opens for them too (the PA page links to it).
        assert (await ops_mgr.get(f"/api/v1/invoices/{inv['id']}")).status_code == 200

        assert (await qa_mgr.get(f"/api/v1/pa/{pa['id']}")).status_code == 404
        listed = await qa_mgr.get("/api/v1/pa", params={"page_size": 200})
        assert pa["id"] not in {p["id"] for p in listed.json()["items"]}

        # A requester sees their own only, even in the same department.
        assert (await outsider.get(f"/api/v1/pa/{pa['id']}")).status_code == 404


async def test_oa_direct_rows_stay_out_of_epms(test_engine, admin_client, world):
    """OA's retired PA-DIRs (source oa_direct, invoice_ids = expense_invoices ids)
    are not EPMS's — the ownership filter, not visibility, keeps them out."""
    inv = await _direct_invoice(admin_client, world["v1"])
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_body(world, [inv["id"]]))).json()
    async with _factory(test_engine)() as db:
        await db.execute(text("UPDATE payment_applications SET source = 'oa_direct', "
                              "pa_type = 'PA-DIR' WHERE id = :i"), {"i": pa["id"]})
        await db.commit()
    assert (await admin_client.get(f"/api/v1/pa/{pa['id']}")).status_code == 404
    listed = await admin_client.get("/api/v1/pa", params={"page_size": 200})
    assert pa["id"] not in {p["id"] for p in listed.json()["items"]}


async def test_candidates_are_unpaid_direct_invoices(admin_client, world):
    free = await _direct_invoice(admin_client, world["v1"])
    taken = await _direct_invoice(admin_client, world["v1"])
    plain = await _invoice(admin_client, world["v1"])
    await admin_client.post("/api/v1/pa/direct", json=_pa_body(world, [taken["id"]]))
    r = await admin_client.get("/api/v1/pa/direct/candidates", params={"vendor_id": world["v1"]})
    assert r.status_code == 200, r.text
    ids = {i["id"] for i in r.json()["items"]}
    assert free["id"] in ids
    assert taken["id"] not in ids and plain["id"] not in ids


async def test_chain_reads_a_direct_invoice_as_matched(admin_client, world):
    inv = await _direct_invoice(admin_client, world["v1"])
    steps = {s["key"]: s for s in (await admin_client.get(f"/api/v1/invoices/{inv['id']}/chain")).json()["steps"]}
    assert steps["match_po"]["state"] == "done" and "Direct PA" in steps["match_po"]["detail"]
    assert steps["link_gr"]["state"] == "not_applicable"
    assert steps["create_pa"]["state"] == "pending"
    pa = (await admin_client.post("/api/v1/pa/direct", json=_pa_body(world, [inv["id"]]))).json()
    steps = {s["key"]: s for s in (await admin_client.get(f"/api/v1/invoices/{inv['id']}/chain")).json()["steps"]}
    assert steps["create_pa"]["state"] == "done"
    assert steps["create_pa"]["refs"][0]["number"] == pa["pa_number"]


async def test_mark_direct_refuses_an_invoice_already_matched_to_a_po(test_engine, admin_client, world):
    inv = await _invoice(admin_client, world["v1"])
    async with _factory(test_engine)() as db:
        await db.execute(text("UPDATE invoices SET status = 'matched', match_route = 'po' WHERE id = :i"),
                         {"i": inv["id"]})
        await db.commit()
    r = await admin_client.post(f"/api/v1/invoices/{inv['id']}/mark-direct")
    assert r.status_code == 409


async def test_mark_direct_waits_for_a_concurrent_match_and_then_refuses(test_engine, admin_client, world):
    """Two requests on one unmatched invoice: a PO match holding the row and
    mark-direct. mark-direct must wait for the lock and then see `matched` —
    never both succeed (one invoice payable by two PAs)."""
    import asyncio
    inv = await _invoice(admin_client, world["v1"])
    async with _factory(test_engine)() as matcher:
        await matcher.execute(text("SELECT id FROM invoices WHERE id = :i FOR UPDATE"), {"i": inv["id"]})
        racing = asyncio.create_task(admin_client.post(f"/api/v1/invoices/{inv['id']}/mark-direct"))
        await asyncio.sleep(1.0)
        assert not racing.done(), "mark-direct did not wait for the row lock"
        await matcher.execute(text("UPDATE invoices SET status = 'matched', match_route = 'po' WHERE id = :i"),
                              {"i": inv["id"]})
        await matcher.commit()
    r = await asyncio.wait_for(racing, timeout=15)
    assert r.status_code == 409
    got = (await admin_client.get(f"/api/v1/invoices/{inv['id']}")).json()
    assert (got["status"], got["match_route"]) == ("matched", "po")


async def test_po_match_refuses_a_direct_invoice(admin_client, world):
    inv = await _direct_invoice(admin_client, world["v1"])
    r = await admin_client.post(f"/api/v1/invoices/{inv['id']}/match", json={"po_id": str(uuid.uuid4())})
    assert r.status_code == 409


async def test_database_refuses_a_direct_invoice_with_a_po(test_engine, admin_client, world):
    """CHECK ck_invoices_direct_no_po: the backstop behind the row locks.
    (CHECK constraints fire before the FK trigger, so any uuid shows it.)"""
    from sqlalchemy.exc import IntegrityError
    inv = await _direct_invoice(admin_client, world["v1"])
    async with _factory(test_engine)() as db:
        with pytest.raises(IntegrityError, match="ck_invoices_direct_no_po"):
            await db.execute(text("UPDATE invoices SET po_id = :p WHERE id = :i"),
                             {"p": uuid.uuid4(), "i": inv["id"]})
        await db.rollback()


async def test_a_direct_invoice_takes_no_goods_receipt(admin_client, world):
    inv = await _direct_invoice(admin_client, world["v1"])
    r = await admin_client.patch(f"/api/v1/invoices/{inv['id']}", json={"gr_ids": [str(uuid.uuid4())]})
    assert r.status_code == 409 and "Direct PA" in r.json()["detail"]
    # Positive control: an edit that is not a GR link still goes through while no PA holds it.
    ok = await admin_client.patch(f"/api/v1/invoices/{inv['id']}", json={"notes": "checked"})
    assert ok.status_code == 200, ok.text
