"""Assistant fixes from the 2026-10-20 issue review, batch 1.

Each rule the guide states is pinned to the code that enforces it, so the guide
fails here rather than drifting when the behaviour changes.
"""
import inspect

from app.api.v1 import pa as pa_api
from app.crud import agreement as agreement_crud
from app.models.agreement import PurchaseAgreement
from app.services import doc_type_guide, reply_language


def _rules(doc_type):
    return {r["id"]: r["answer"] for r in doc_type_guide.build(doc_type)["rules"]}


def _type(doc_type, value):
    return next(t for t in doc_type_guide.build(doc_type)["types"]
                if t["value"] == value)


# ── ISS-0023: agreements ─────────────────────────────────────────────────────

def test_house_account_is_not_described_as_an_open_po():
    """The guide said purchases are "charged against an open purchase order";
    the agreement route raises no PO at all."""
    notes = " ".join(_type("agreement", "house_account")["notes"]).lower()
    assert "open purchase order" not in notes
    assert "no pr or po is raised" in notes
    assert "no PO, no GR" in inspect.getsource(pa_api)


def test_grace_window_rule_matches_the_admission_predicate():
    answer = _rules("agreement")["grace_window"]
    default = PurchaseAgreement.__table__.c.grace_days.server_default.arg
    assert default == "30" and "30" in answer
    predicate = inspect.getsource(agreement_crud._admissible_predicate)
    # "Valid From is not checked" — the predicate must not read it.
    assert "valid_from)" not in predicate.split('"""')[-1]
    assert "Valid From is not checked" in answer


def test_nte_rule_matches_the_payment_route():
    """Not to Exceed blocks nothing: the PA route never reads it."""
    assert "warning only" in _rules("agreement")["nte_is_warning_only"]
    assert "not_to_exceed" not in inspect.getsource(pa_api)


def test_house_account_has_a_fixed_chinese_name():
    """It was translated as 房间账户 (a hotel room account)."""
    assert reply_language.TERMS_ZH["House Account"] == "挂账账户"


# ── ISS-0025: over-budget ────────────────────────────────────────────────────

async def test_process_payload_carries_justification_and_over_budget_rules(monkeypatch):
    from app.services import guide_view

    async def fake_forward(method, path, token, **kw):
        return 200, {"doc_type": "pr", "steps": [],
                     "over_budget": {"mode": "allow",
                                     "mode_label": "Allow without extra approval",
                                     "added_steps": [], "blocks_submission": False,
                                     "justification_required": True, "note": "n"}}

    monkeypatch.setattr(guide_view.approval_client, "forward", fake_forward)
    got = await guide_view.process_steps("pr", "t")
    assert got["over_budget"]["justification_required"] is True
    ids = {r["id"] for r in got["rules"]}
    assert {"budget_check_applies_to_types_2_to_6", "over_budget_checked_on_save",
            "budget_not_reserved"} <= ids
    # The same rules the document-type route carries, not a copy.
    assert got["rules"] == [r for r in doc_type_guide.build("pr")["rules"]
                            if r.get("topic") == "over_budget"]
    # Other processes carry no PR rules.
    po = await guide_view.process_steps("po", "t")
    assert "rules" not in po


def test_narrator_is_told_to_give_the_whole_frozen_mode_note():
    """The narrator was told to say only "fixed when the PR is submitted",
    dropping that a looser mode skips steps a PR has not yet passed."""
    from app.services import assistant_llm

    guide = assistant_llm._GUIDE_SYSTEM
    assert "justification_required" in guide
    assert "looser one skips over-budget steps" in guide
    assert "is fixed when the PR is submitted" not in guide


def test_over_budget_is_decided_on_save_not_on_submit():
    """over_budget_checked_on_save says submit does not re-check. Pin it: the
    only callers of the computation are create and update."""
    from app.crud import pr as pr_crud

    src = inspect.getsource(pr_crud)
    assert src.count("await _compute_over_budget(") == 2
    assert "_compute_over_budget" in inspect.getsource(pr_crud.create)
    assert "_compute_over_budget" in inspect.getsource(pr_crud.update)


def test_budget_unreachable_is_treated_as_within_budget():
    from app.crud import pr as pr_crud

    assert "return False, None" in inspect.getsource(pr_crud.compute_budget_check)
    assert "treated as within budget" in _rules("pr")["budget_not_reserved"]


# ── ISS-0065: the retired Direct PA is not a process to describe ─────────────

def test_direct_pa_is_not_offered_as_a_process():
    from app.services import assistant_llm, guide_view

    assert "pa_dir" not in guide_view.SUPPORTED_PROCESSES
    enum = assistant_llm._PROCESS_TOOL["input_schema"]["properties"]["doc_type"]["enum"]
    assert "pa_dir" not in enum
    assert "discontinued" in _rules("pa")["direct_pa_retired"]


# ── ISS-0074 / ISS-0053 / ISS-0048: a named day is the plant's whole day ─────

async def _seed_pos_at(test_engine, admin_client, tag, stamps):
    """One PO per (suffix, aware datetime), created_at set explicitly."""
    import uuid
    from decimal import Decimal

    from jose import jwt
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.config import settings
    from app.models.po import PurchaseOrder
    from app.models.vendor import Vendor

    token = admin_client.headers["Authorization"].split()[1]
    uid = uuid.UUID(jwt.decode(token, settings.JWT_SECRET_KEY,
                               algorithms=[settings.JWT_ALGORITHM])["sub"])
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:6]}", name="Day Bounds Vendor",
                        category="Services", contact_name="A", contact_email="a@t.test")
        db.add(vendor)
        await db.flush()
        for suffix, at in stamps:
            db.add(PurchaseOrder(
                number=f"PO-DAY-{tag}-{suffix}", title="d", type=2, status="approved",
                subtotal=Decimal("1"), total=Decimal("1"), vendor_id=vendor.id,
                vendor_name=vendor.name, created_by=uid, created_at=at))
        await db.commit()


async def _numbers(admin_client, tag, **query):
    r = await admin_client.post("/api/v1/assistant/query", json={
        "entity": "purchase_order", "select": ["number"],
        "where": [{"field": "number", "op": "like", "value": f"PO-DAY-{tag}"}]
                 + query.pop("where", []),
        **query,
    })
    assert r.status_code == 200, r.text
    return {row["number"].rsplit("-", 1)[1] for row in r.json()["rows"]}


async def test_a_named_day_is_the_plants_whole_day(test_engine, admin_client):
    """from = to = one day used to find nothing (the day ended at its own
    midnight), and the day began at midnight UTC = 20:00 the evening before."""
    import uuid
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/Toronto")
    tag = uuid.uuid4().hex[:6]
    await _seed_pos_at(test_engine, admin_client, tag, [
        ("early", datetime(2026, 10, 19, 0, 30, tzinfo=tz)),
        ("late", datetime(2026, 10, 19, 23, 30, tzinfo=tz)),   # 03:30 UTC on the 20th
        ("eve", datetime(2026, 10, 18, 21, 0, tzinfo=tz)),     # 01:00 UTC on the 19th
        ("next", datetime(2026, 10, 20, 0, 0, tzinfo=tz)),
    ])
    day = {"early", "late"}

    assert await _numbers(admin_client, tag,
                          period={"from": "2026-10-19", "to": "2026-10-19"}) == day
    assert await _numbers(admin_client, tag, period={"from": "2026-10-18",
                                                     "to": "2026-10-19"}) == day | {"eve"}
    for op, value, want in [
        ("eq", "2026-10-19", day),
        ("between", ["2026-10-19", "2026-10-19"], day),
        ("lte", "2026-10-19", day | {"eve"}),
        ("lt", "2026-10-19", {"eve"}),
        ("gte", "2026-10-19", day | {"next"}),
        ("gt", "2026-10-19", {"next"}),
        ("ne", "2026-10-19", {"eve", "next"}),
    ]:
        got = await _numbers(admin_client, tag, where=[
            {"field": "created_at", "op": op, "value": value}])
        assert got == want, (op, got)

    # A full timestamp still means exactly what it says.
    got = await _numbers(admin_client, tag, where=[
        {"field": "created_at", "op": "lt", "value": "2026-10-19T12:00:00-04:00"}])
    assert got == {"eve", "early"}


def test_timestamps_reach_the_narrator_in_plant_time():
    """An invoice uploaded at 17:39 was reported as 21:39 (UTC)."""
    from datetime import datetime, timezone

    from app.services import controlled_query

    got = controlled_query._serialise(datetime(2026, 10, 19, 21, 39, tzinfo=timezone.utc))
    assert got == "2026-10-19T17:39:00-04:00"


def test_planner_is_told_dates_are_day_first_and_local():
    from app.services import assistant_llm

    note = assistant_llm._today_note()
    assert "DD/MM/YYYY" in note and "10/11 is 10 November" in note


def test_new_invoices_are_asked_about_by_upload_date():
    from app.core.ontology import REGISTRY
    from app.services import assistant_llm

    assert "Upload Date" in REGISTRY["invoice"].fields["created_at"].label
    assert "set period.field\n  to created_at" in assistant_llm._PLAN_SYSTEM


# ── ISS-0072 / ISS-0059 / ISS-0042 / ISS-0043: what a paid PA looks like ─────

def test_pa_status_values_say_what_they_mean():
    from app.core.ontology import REGISTRY

    status = REGISTRY["payment_application"].fields["status"]
    labels = dict(status.value_labels)
    # Every declared value has a meaning, and the two that were misread say so.
    assert set(labels) == set(status.values)
    assert labels["processed"].startswith("Paid")
    assert "awaiting payment" in labels["approved"]
    assert "Legacy" in labels["paid"]


def test_pa_carries_its_dates():
    from app.core.ontology import REGISTRY

    fields = REGISTRY["payment_application"].fields
    assert fields["paid_on"].kind == "date"
    assert fields["approved_at"].kind == fields["submitted_at"].kind == "datetime"


async def test_paid_on_is_the_date_finance_entered(test_engine, admin_client):
    """finance-api stores the payment DATE as midnight UTC; read in plant time
    that is 20:00 the evening before. paid_on must give back the entered date,
    and "paid on that day" must find the PA."""
    import uuid
    from datetime import datetime, timezone
    from decimal import Decimal

    from jose import jwt
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.core.config import settings
    from app.models.pa import PaymentApplication
    from app.models.po import PurchaseOrder
    from app.models.vendor import Vendor

    token = admin_client.headers["Authorization"].split()[1]
    uid = uuid.UUID(jwt.decode(token, settings.JWT_SECRET_KEY,
                               algorithms=[settings.JWT_ALGORITHM])["sub"])
    tag = uuid.uuid4().hex[:8]
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        vendor = Vendor(code=f"V-{tag[:6]}", name="Paid On Vendor", category="Services",
                        contact_name="A", contact_email="a@t.test")
        db.add(vendor)
        await db.flush()
        po = PurchaseOrder(number=f"PO-PAID-{tag}", title="p", type=2, status="issued",
                           subtotal=Decimal("5"), total=Decimal("5"), vendor_id=vendor.id,
                           vendor_name=vendor.name, created_by=uid)
        db.add(po)
        await db.flush()
        db.add(PaymentApplication(
            pa_number=f"PA-PAID-{tag}", title="paid", po_id=po.id, po_number=po.number,
            vendor_id=vendor.id, vendor_name=vendor.name, invoice_ids=[], gr_ids=[],
            subtotal=Decimal("5"), payment_amount=Decimal("5"), status="processed",
            created_by=uid, paid_at=datetime(2026, 10, 19, tzinfo=timezone.utc)))
        await db.commit()

    async def query(day):
        r = await admin_client.post("/api/v1/assistant/query", json={
            "entity": "payment_application", "select": ["pa_number", "paid_on"],
            "where": [{"field": "pa_number", "op": "eq", "value": f"PA-PAID-{tag}"},
                      {"field": "paid_on", "op": "eq", "value": day}]})
        assert r.status_code == 200, r.text
        return r.json()["rows"]

    rows = await query("2026-10-19")
    assert [row["paid_on"] for row in rows] == ["2026-10-19"]
    assert await query("2026-10-18") == []


def test_po_returned_is_declared():
    from app.core.ontology import REGISTRY

    status = REGISTRY["purchase_order"].fields["status"]
    assert "returned" in status.values
    assert "not rejected" in dict(status.value_labels)["returned"]
    import inspect

    from app.crud import po as po_crud
    assert 'po.status = "returned"' in inspect.getsource(po_crud)


def test_paid_pa_rule_is_stated():
    assert "Processed" in _rules("pa")["paid_pa_is_processed"]
