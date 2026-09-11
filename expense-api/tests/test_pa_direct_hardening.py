"""PA-DIR creation hardening: number allocation, invoice reuse, and submit authz.

Three defects that all lived in the same corner of the Direct-PA flow:

* `pa_number` was minted with `count(*)+1` and no lock, on a table shared with
  epms-api under the SAME `PA-YYYYMMDD-` prefix — the exact failure
  `crud/_numbering.py` was written to prevent, reintroduced here.
* the "invoice already used" branch sat after `status != "reviewed"` and was
  therefore unreachable, so a caller reusing a spent invoice was told to go
  review it.
* `POST /pa/{id}/action {"action": "submit"}` had no owner check on either
  side of the wire.
"""
import re
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.models.pa import PaymentApplication


@pytest.fixture(autouse=True)
def _direct_pa_switched_on(monkeypatch):
    """Exercise the retired Direct PA path on purpose.

    OA's Direct PA is hidden and creation answers 410 (see DIRECT_PA_RETIRED in
    api/v1/pa.py). The code behind the flag is kept rather than deleted, and
    kept means kept WORKING — so these tests flip it on. If the product
    decision is reversed, this file is what says whether the feature still
    functions; without it the code would rot silently behind the flag.

    The flag itself is covered by test_direct_pa_retired.py.
    """
    monkeypatch.setattr("app.api.v1.pa.DIRECT_PA_RETIRED", False)


async def _reviewed_invoice(client) -> dict:
    resp = await client.post(
        "/api/v1/invoices",
        json={
            "file_name": "vendor_invoice.pdf",
            "file_mime_type": "application/pdf",
            "file_size_bytes": 204800,
            "invoice_number": f"INV-{uuid.uuid4().hex[:8]}",
            "vendor_id": str(uuid.uuid4()),
            "vendor_name": "Titan Power Ltd",
            "currency": "CAD",
            "subtotal": "1000.00",
            "tax_amount": "130.00",
            "total_amount": "1130.00",
        },
    )
    assert resp.status_code == 201
    return resp.json()


def _payload(invoice_id: str, **overrides) -> dict:
    base = {
        "invoice_id": invoice_id,
        "vendor_name": "Titan Power Ltd",
        "payment_amount": "1130.00",
        "currency": "CAD",
    }
    base.update(overrides)
    return base


async def _create_pa(client) -> dict:
    inv = await _reviewed_invoice(client)
    resp = await client.post("/api/v1/pa/direct", json=_payload(inv["id"]))
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── Number allocation ─────────────────────────────────────────────────────────

async def test_pa_number_survives_a_gap_in_todays_window(admin_client, db_session):
    """A PA leaving today's prefix window must not make the next one reuse a number.

    Mirrors test_numbering.py for expense claims. Under `count(*)+1` the third
    allocation below reissues the second PA's number and dies on pa_number's
    UNIQUE index; keying off the max tail it moves past it.
    """
    first = await _create_pa(admin_client)
    second = await _create_pa(admin_client)

    # Move `first` out of today's PA-<today> window, so count() now lags the
    # real max tail by one — the same shape a deletion or an epms-api-side
    # allocation produces on this shared table.
    await db_session.execute(
        text("UPDATE payment_applications SET pa_number = :n WHERE id = :i"),
        {"n": f"PA-19000101-{uuid.uuid4().hex[:4]}", "i": first["id"]},
    )
    await db_session.commit()

    third = await _create_pa(admin_client)

    assert third["pa_number"] != second["pa_number"]
    tail = lambda n: int(n.rsplit("-", 1)[1])          # noqa: E731
    assert tail(third["pa_number"]) == tail(second["pa_number"]) + 1


async def test_pa_number_continues_past_a_number_minted_elsewhere(admin_client, db_session):
    """epms-api mints EPMS PAs from the same prefix. OA must continue past them."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    db_session.add(PaymentApplication(
        id=uuid.uuid4(),
        pa_number=f"PA-{today}-0042",
        title="EPMS-side PA",
        vendor_id=uuid.uuid4(),
        vendor_name="V",
        invoice_ids=[],
        gr_ids=[],
        pa_type="regular",
        subtotal=1, payment_amount=1,
        currency="CAD",
        status="draft",
        approval_step_idx=0,
        created_by=uuid.uuid4(),
    ))
    await db_session.commit()

    pa = await _create_pa(admin_client)

    assert pa["pa_number"] == f"PA-{today}-0043"


async def test_pa_number_still_matches_the_documented_format(admin_client):
    pa = await _create_pa(admin_client)
    assert re.match(r"^PA-\d{8}-\d{4}$", pa["pa_number"])


# ── Invoice reuse ─────────────────────────────────────────────────────────────

async def test_reusing_a_spent_invoice_says_so(admin_client):
    """The 409 must name the real problem, not send the caller off to review."""
    inv = await _reviewed_invoice(admin_client)
    first = await admin_client.post("/api/v1/pa/direct", json=_payload(inv["id"]))
    assert first.status_code == 201

    again = await admin_client.post("/api/v1/pa/direct", json=_payload(inv["id"]))

    assert again.status_code == 409
    detail = again.json()["detail"]
    assert "already linked" in detail
    assert first.json()["pa_number"] in detail
    assert "reviewed" not in detail


# ── Submit authorization ──────────────────────────────────────────────────────

async def test_another_user_cannot_submit_my_pa(requester_client, requester_client_b, mocker):
    delegate = mocker.patch("app.api.v1.pa.delegate_action",
                            new_callable=mocker.AsyncMock, return_value=None)
    pa = await _create_pa(requester_client)

    resp = await requester_client_b.post(
        f"/api/v1/pa/{pa['id']}/action", json={"action": "submit"})

    assert resp.status_code == 403
    assert "creator" in resp.json()["detail"]
    delegate.assert_not_called()          # rejected before it reaches the engine


async def test_creator_can_submit_their_own_pa(requester_client, mocker):
    delegate = mocker.patch("app.api.v1.pa.delegate_action",
                            new_callable=mocker.AsyncMock, return_value=None)
    pa = await _create_pa(requester_client)

    resp = await requester_client.post(
        f"/api/v1/pa/{pa['id']}/action", json={"action": "submit"})

    assert resp.status_code == 200
    delegate.assert_called_once()


async def test_admin_can_submit_anyones_pa(requester_client, admin_client, mocker):
    delegate = mocker.patch("app.api.v1.pa.delegate_action",
                            new_callable=mocker.AsyncMock, return_value=None)
    pa = await _create_pa(requester_client)

    resp = await admin_client.post(
        f"/api/v1/pa/{pa['id']}/action", json={"action": "submit"})

    assert resp.status_code == 200
    delegate.assert_called_once()


@pytest.mark.parametrize("action", ["approve", "return", "cancel"])
async def test_non_submit_actions_still_reach_the_engine(requester_client_b, requester_client,
                                                         mocker, action):
    """Only `submit` is gated here — everything else is approval-api's call,
    and this guard must not start swallowing those."""
    delegate = mocker.patch("app.api.v1.pa.delegate_action",
                            new_callable=mocker.AsyncMock, return_value=None)
    pa = await _create_pa(requester_client)

    resp = await requester_client_b.post(
        f"/api/v1/pa/{pa['id']}/action", json={"action": action})

    assert resp.status_code == 200
    delegate.assert_called_once()
