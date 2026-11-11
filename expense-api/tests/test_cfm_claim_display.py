"""ISS-0195: a custom-form claim's answers are a JSON blob in `notes`
(oa CfmCreatePage). The detail endpoint resolves them the way the claim PDF
does (claim_pdf.cfm_form) and the OA Task Inbox titles the card by the form
name — neither may show the raw JSON. `notes` itself stays the blob: the edit
form unpacks it (WF-RET-002).

Example: CFM_TESTCF-20261103-0002, form "[TEST] 测试表单" (code TESTCF).
"""
import json
import uuid
from datetime import date

import pytest
from sqlalchemy import select

import app.db.base as db_module
from app.models.custom_form import CustomFormDefinition
from app.models.expense import ExpenseClaim
from tests.conftest import _client, _make_token

CODE = "T195"
NOTES = json.dumps({
    "event_name": "[TEST-Claude] R1 CFM-A event", "attendees": "5",
    "event_date": "2026-11-10", "category": "Conference",
    "details": "[TEST] details text", "_user_notes": "[TEST-Claude] R1 CFM-A",
})


async def _form() -> None:
    async with db_module.AsyncSessionLocal() as db:
        if (await db.execute(select(CustomFormDefinition).where(
                CustomFormDefinition.code == CODE))).scalar_one_or_none():
            return
        db.add(CustomFormDefinition(
            code=CODE, name="[TEST] 测试表单", workflow_key=f"cfm_{CODE.lower()}",
            field_schema=[
                {"name": "event_name", "label": "Event Name", "field_type": "text"},
                {"name": "attendees", "label": "Attendees", "field_type": "number"},
                {"name": "event_date", "label": "Event Date", "field_type": "date"},
                {"name": "category", "label": "Category", "field_type": "select"},
                {"name": "details", "label": "Details", "field_type": "textarea"},
            ],
        ))
        await db.commit()


async def _claim(status: str, owner: uuid.UUID) -> uuid.UUID:
    cid = uuid.uuid4()
    async with db_module.AsyncSessionLocal() as db:
        db.add(ExpenseClaim(
            id=cid, claim_number=f"CFM_{CODE}-{cid.hex[:6]}", claim_type=f"CFM_{CODE}",
            employee_id=owner, employee_name="TEST Requester", department_name="Ops",
            submission_date=date(2026, 11, 3), status=status, created_by=owner,
            notes=NOTES,
        ))
        await db.commit()
    return cid


@pytest.mark.asyncio
async def test_detail_resolves_the_form_answers(test_engine):
    await _form()
    owner = uuid.uuid4()
    cid = await _claim("draft", owner)
    async with _client(_make_token("requester", str(owner))) as c:   # a normal employee
        r = await c.get(f"/api/v1/expenses/{cid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["form_name"] == "[TEST] 测试表单"
    assert body["form_fields"] == [
        {"label": "Event Name", "value": "[TEST-Claude] R1 CFM-A event"},
        {"label": "Attendees", "value": "5"},
        {"label": "Event Date", "value": "2026-11-10"},
        {"label": "Category", "value": "Conference"},
        {"label": "Details", "value": "[TEST] details text"},
    ]
    assert body["user_notes"] == "[TEST-Claude] R1 CFM-A"
    # The edit form pre-fills from the blob — it must still be there.
    assert json.loads(body["notes"])["event_name"] == "[TEST-Claude] R1 CFM-A event"


@pytest.mark.asyncio
async def test_other_claim_types_carry_no_form(test_engine):
    owner = uuid.uuid4()
    cid = uuid.uuid4()
    async with db_module.AsyncSessionLocal() as db:
        db.add(ExpenseClaim(
            id=cid, claim_number=f"EXP-T195-{cid.hex[:6]}", claim_type="EXP",
            employee_id=owner, employee_name="X", department_name="Ops",
            submission_date=date(2026, 11, 3), status="draft", created_by=owner,
            notes="plain note",
        ))
        await db.commit()
    async with _client(_make_token("requester", str(owner))) as c:
        body = (await c.get(f"/api/v1/expenses/{cid}")).json()
    assert body["form_fields"] is None and body["user_notes"] is None
    assert body["notes"] == "plain note"


@pytest.mark.asyncio
@pytest.mark.parametrize("role, status, task_type", [
    ("payment_officer", "approved", "pay_expense"),   # Record Payment card
    ("requester", "submitted", "submitted_expense"),  # the claimant's own card
])
async def test_task_inbox_card_is_titled_by_the_form(test_engine, role, status, task_type):
    await _form()
    owner = uuid.uuid4()
    viewer = owner if role == "requester" else uuid.uuid4()
    cid = await _claim(status, owner)
    async with _client(_make_token(role, str(viewer))) as c:
        r = await c.get("/api/v1/tasks")
    assert r.status_code == 200, r.text
    card = next(t for t in r.json()["items"] if t["doc_id"] == str(cid))
    assert card["task_type"] == task_type
    assert card["title"] == "[TEST] 测试表单"
    assert "{" not in card["title"]
