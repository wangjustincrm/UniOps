"""Q-05: a custom form deactivated after a claim was drafted on it. OA.md §6.1
/ CFM-005 hide inactive forms from employees (the list stays active-only for
them) but the existing draft or returned claim must stay editable and
submittable, so its edit page reads that one form's definition through
GET /expenses/custom-forms/for-claim/{claim_id}."""
import json
import uuid
from datetime import date

import pytest

import app.db.base as db_module
from app.models.custom_form import CustomFormDefinition
from app.models.expense import ExpenseClaim
from tests.conftest import _client, _make_token


async def _inactive_form_and_claim(owner: uuid.UUID, status: str = "draft"):
    code = f"Q{uuid.uuid4().hex[:5].upper()}"
    cid = uuid.uuid4()
    async with db_module.AsyncSessionLocal() as db:
        db.add(CustomFormDefinition(
            code=code, name="[TEST] retired form", workflow_key=f"cfm_{code.lower()}",
            field_schema=[{"name": "event_name", "label": "Event Name", "field_type": "text"}],
            is_active=False,
        ))
        db.add(ExpenseClaim(
            id=cid, claim_number=f"CFM_{code}-{cid.hex[:6]}", claim_type=f"CFM_{code}",
            employee_id=owner, employee_name="TEST Requester", department_name="Ops",
            submission_date=date(2026, 11, 3), status=status, created_by=owner,
            notes=json.dumps({"event_name": "x", "_user_notes": ""}),
        ))
        await db.commit()
    return code, cid


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "returned"])
async def test_owner_reads_the_inactive_form_of_their_own_claim(test_engine, status):
    owner = uuid.uuid4()
    code, cid = await _inactive_form_and_claim(owner, status)
    async with _client(_make_token("requester", str(owner))) as c:
        r = await c.get(f"/api/v1/expenses/custom-forms/for-claim/{cid}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["code"] == code and body["is_active"] is False
        assert [f["name"] for f in body["fields"]] == ["event_name"]

        # Still hidden from the employee's form list (OA.md §6.1, CFM-005),
        # even when they ask for every form.
        listed = (await c.get("/api/v1/expenses/custom-forms",
                              params={"active_only": "false"})).json()
        assert code not in {f["code"] for f in listed}

        # The edit saves (the server never refused it; the page did).
        r = await c.patch(f"/api/v1/expenses/{cid}", json={
            "notes": json.dumps({"event_name": "edited", "_user_notes": ""})})
        assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_admin_reads_it_and_a_stranger_cannot(test_engine):
    code, cid = await _inactive_form_and_claim(uuid.uuid4())
    async with _client(_make_token("system_admin")) as c:
        r = await c.get(f"/api/v1/expenses/custom-forms/for-claim/{cid}")
        assert r.status_code == 200 and r.json()["code"] == code
    async with _client(_make_token("requester")) as c:
        r = await c.get(f"/api/v1/expenses/custom-forms/for-claim/{cid}")
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_only_custom_form_claims(test_engine):
    owner = uuid.uuid4()
    cid = uuid.uuid4()
    async with db_module.AsyncSessionLocal() as db:
        db.add(ExpenseClaim(
            id=cid, claim_number=f"EXP-{cid.hex[:6]}", claim_type="EXP",
            employee_id=owner, employee_name="TEST Requester", department_name="Ops",
            submission_date=date(2026, 11, 3), status="draft", created_by=owner))
        await db.commit()
    async with _client(_make_token("requester", str(owner))) as c:
        assert (await c.get(f"/api/v1/expenses/custom-forms/for-claim/{cid}")).status_code == 404
        assert (await c.get(f"/api/v1/expenses/custom-forms/for-claim/{uuid.uuid4()}")).status_code == 404
