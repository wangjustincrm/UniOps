"""PO sign-off — the signature flow over NC-imported POs.

approval-api owns the workflow itself (doc_type "posign"); it is stubbed here,
because what these tests are about is everything on the EPMS side: who may
raise a sign-off, the refusal to start one nobody can finish, the signature
snapshot, and the append-only justification thread.
"""
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import create_access_token
from app.crud import user as user_crud
from app.main import create_app
from app.models.approval import ApprovalEvent
from app.models.po import PurchaseOrder
from app.models.po_signoff_signature import PoSignoffSignature
from app.models.task import Task
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio

_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="

_WORKFLOW = [
    {"id": "proc_mgr", "role": "procurement_manager",
     "label": "Purchasing Manager", "sig_slot": "initials"},
    {"id": "opm", "role": "opm", "label": "Operations Manager",
     "sig_slot": "signature"},
]


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _make_user(test_engine, role: str, *, signature: str | None = None):
    async with _factory(test_engine)() as db:
        user = await user_crud.create(db, RegisterRequest(
            email=f"{role}-{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass1!", full_name=f"Test {role}", role=role))
        if signature:
            user.signature_image = signature
        await db.commit()
        await db.refresh(user)
        return user


def _client_for(user) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test",
        headers={"Authorization": f"Bearer {create_access_token(str(user.id), user.role)}"})


async def _make_po(test_engine, creator, *, source="nc", status="nc_pending"):
    async with _factory(test_engine)() as db:
        suffix = uuid.uuid4().hex[:6]
        vendor = Vendor(
            id=uuid.uuid4(), code=f"V{suffix}", name=f"V-{suffix}",
            category="supplier", contact_name="Contact",
            contact_email=f"{suffix}@example.com", payment_terms="net30",
            currency="CAD", is_active=True, is_supplier=True, is_customer=False)
        db.add(vendor)
        await db.flush()
        po = PurchaseOrder(
            id=uuid.uuid4(), number=f"PO-{uuid.uuid4().hex[:8]}", title="NC import",
            type=1, status=status, currency="CAD",
            subtotal=Decimal("100.00"), tax_rate=Decimal("0"),
            tax_amount=Decimal("0"), total=Decimal("100.00"),
            vendor_id=vendor.id, vendor_name=vendor.name,
            source=source, nc_source_pk="1001" if source == "nc" else None,
            created_by=creator.id,
        )
        db.add(po)
        await db.commit()
        return po.id


@pytest.fixture
def stub_engine(monkeypatch, test_engine):
    """Stand in for approval-api, moving signoff_status the way the engine does."""
    calls: list[tuple[str, str]] = []

    async def _steps(doc_type, doc_id, token):  # noqa: ANN001
        assert doc_type == "posign"
        return _WORKFLOW

    async def _delegate(doc_type, doc_id, action, comment, token):  # noqa: ANN001
        calls.append((action, comment or ""))
        async with _factory(test_engine)() as db:
            po = (await db.execute(select(PurchaseOrder).where(
                PurchaseOrder.id == uuid.UUID(doc_id)))).scalar_one()
            actor = (await db.execute(select(User).limit(1))).scalar_one()
            # Close whatever was open, exactly as _complete_tasks does.
            for t in (await db.execute(select(Task).where(
                    Task.document_type == "posign", Task.document_id == po.id,
                    Task.is_completed.is_(False)))).scalars().all():
                t.is_completed = True
            open_step: int | None = None
            if action == "submit":
                po.signoff_status, po.signoff_step_idx = "submitted", 0
                open_step = 0
            elif action == "approve":
                nxt = po.signoff_step_idx + 1
                if nxt < len(_WORKFLOW):
                    po.signoff_status, po.signoff_step_idx = "in_review", nxt
                    open_step = nxt
                else:
                    po.signoff_status = "approved"
            elif action == "return":
                po.signoff_status, po.signoff_step_idx = "returned", 0
            if open_step is not None:
                # Broadcast to the post, assigned_user_id NULL — the engine's
                # treatment of singleton posts, and what the signer's document
                # visibility keys off.
                db.add(Task(
                    id=uuid.uuid4(), type="sign_po", priority="normal",
                    document_type="posign", document_id=po.id,
                    document_number=po.number,
                    assigned_role=_WORKFLOW[open_step]["role"],
                    assigned_user_id=None,
                    title=f"Sign PO: {po.number}",
                    description="signature required"))
            db.add(ApprovalEvent(
                document_type="posign", document_id=po.id, document_number=po.number,
                step_idx=po.signoff_step_idx, action=action,
                actor_id=actor.id, actor_role="stub", comment=comment))
            await db.commit()

    from app.services import approval_client
    monkeypatch.setattr(approval_client, "get_workflow_steps", _steps)
    monkeypatch.setattr(approval_client, "delegate_action", _delegate)
    return calls


async def _signatures(test_engine, po_id):
    async with _factory(test_engine)() as db:
        return (await db.execute(
            select(PoSignoffSignature)
            .where(PoSignoffSignature.po_id == po_id)
            .order_by(PoSignoffSignature.step_idx)
        )).scalars().all()


# ── raising a sign-off ────────────────────────────────────────────────────

async def test_submit_is_refused_when_a_signer_has_no_signature(
    test_engine, stub_engine, admin_client,
):
    officer = await _make_user(test_engine, "erp_pa_officer")
    await _make_user(test_engine, "procurement_manager")        # no signature
    await _make_user(test_engine, "opm", signature=_PNG)
    po_id = await _make_po(test_engine, officer)

    resp = await admin_client.post(f"/api/v1/po/{po_id}/signoff/submit",
                                   json={"justification": "September run."})
    assert resp.status_code == 409, resp.text
    assert "has not set a signature" in resp.json()["detail"]
    # Nothing was handed to the engine — the sign-off never entered an inbox.
    assert stub_engine == []


async def test_submit_is_refused_when_a_step_has_no_holder(
    test_engine, stub_engine, admin_client,
):
    officer = await _make_user(test_engine, "erp_pa_officer")
    await _make_user(test_engine, "opm", signature=_PNG)
    # The suite shares one database, so retire any Purchasing Manager an
    # earlier test left behind — this test is about the step having nobody.
    async with _factory(test_engine)() as db:
        for u in (await db.execute(select(User).where(
                User.role == "procurement_manager"))).scalars().all():
            u.is_active = False
        await db.commit()
    po_id = await _make_po(test_engine, officer)

    resp = await admin_client.post(f"/api/v1/po/{po_id}/signoff/submit",
                                   json={"justification": "September run."})
    assert resp.status_code == 409, resp.text
    assert "No active holder" in resp.json()["detail"]


async def test_submit_is_refused_on_a_locally_raised_po(
    test_engine, stub_engine, admin_client,
):
    officer = await _make_user(test_engine, "erp_pa_officer")
    await _make_user(test_engine, "procurement_manager", signature=_PNG)
    await _make_user(test_engine, "opm", signature=_PNG)
    po_id = await _make_po(test_engine, officer, source=None, status="approved")

    resp = await admin_client.post(f"/api/v1/po/{po_id}/signoff/submit",
                                   json={"justification": "Local PO."})
    assert resp.status_code == 409, resp.text
    assert "imported from NC" in resp.json()["detail"]


async def test_submit_records_the_raiser_and_opens_the_thread(
    test_engine, stub_engine, admin_client,
):
    officer = await _make_user(test_engine, "erp_pa_officer")
    await _make_user(test_engine, "procurement_manager", signature=_PNG)
    await _make_user(test_engine, "opm", signature=_PNG)
    po_id = await _make_po(test_engine, officer)

    resp = await admin_client.post(f"/api/v1/po/{po_id}/signoff/submit",
                                   json={"justification": "Covers the September run."})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "submitted"
    assert body["submitted_by"] is not None
    # The justification opens the thread rather than living in a column.
    assert body["thread"][0]["action"] == "submit"
    assert body["thread"][0]["comment"] == "Covers the September run."
    assert [s["sig_slot"] for s in body["steps"]] == ["initials", "signature"]


async def test_submit_requires_the_signoff_permission(test_engine, stub_engine):
    officer = await _make_user(test_engine, "erp_pa_officer")
    await _make_user(test_engine, "procurement_manager", signature=_PNG)
    po_id = await _make_po(test_engine, officer)
    # A requester holds no epms.po.signoff grant.
    outsider = await _make_user(test_engine, "requester")
    async with _client_for(outsider) as c:
        resp = await c.post(f"/api/v1/po/{po_id}/signoff/submit",
                            json={"justification": "Not mine to raise."})
    assert resp.status_code == 403, resp.text


# ── signing ───────────────────────────────────────────────────────────────

async def test_signing_snapshots_the_signature_onto_the_document(
    test_engine, stub_engine, admin_client,
):
    officer = await _make_user(test_engine, "erp_pa_officer")
    pm = await _make_user(test_engine, "procurement_manager", signature=_PNG)
    await _make_user(test_engine, "opm", signature=_PNG)
    po_id = await _make_po(test_engine, officer)
    await admin_client.post(f"/api/v1/po/{po_id}/signoff/submit",
                            json={"justification": "Why."})

    async with _client_for(pm) as c:
        resp = await c.post(f"/api/v1/po/{po_id}/signoff/sign", json={})
    assert resp.status_code == 200, resp.text

    sigs = await _signatures(test_engine, po_id)
    assert len(sigs) == 1
    assert sigs[0].step_idx == 0
    assert sigs[0].role == "procurement_manager"
    assert sigs[0].sig_slot == "initials"
    assert sigs[0].signer_name == pm.full_name
    # A copy, not a reference: a later profile edit must not restamp this PO.
    assert sigs[0].signature_image == _PNG


async def test_signing_without_a_preset_signature_is_refused_before_the_engine_runs(
    test_engine, stub_engine, admin_client,
):
    officer = await _make_user(test_engine, "erp_pa_officer")
    pm = await _make_user(test_engine, "procurement_manager", signature=_PNG)
    await _make_user(test_engine, "opm", signature=_PNG)
    po_id = await _make_po(test_engine, officer)
    await admin_client.post(f"/api/v1/po/{po_id}/signoff/submit",
                            json={"justification": "Why."})

    # Strip the signature after the sign-off was raised.
    async with _factory(test_engine)() as db:
        user = (await db.execute(select(User).where(User.id == pm.id))).scalar_one()
        user.signature_image = None
        await db.commit()

    before = len(stub_engine)
    async with _client_for(pm) as c:
        resp = await c.post(f"/api/v1/po/{po_id}/signoff/sign", json={})
    assert resp.status_code == 409, resp.text
    assert "My Profile" in resp.json()["detail"]
    # The step must not advance — otherwise the PDF carries a blank box forever.
    assert len(stub_engine) == before
    assert await _signatures(test_engine, po_id) == []


async def test_signing_never_touches_the_pos_own_status(
    test_engine, stub_engine, admin_client,
):
    officer = await _make_user(test_engine, "erp_pa_officer")
    pm = await _make_user(test_engine, "procurement_manager", signature=_PNG)
    await _make_user(test_engine, "opm", signature=_PNG)
    po_id = await _make_po(test_engine, officer)
    await admin_client.post(f"/api/v1/po/{po_id}/signoff/submit",
                            json={"justification": "Why."})
    async with _client_for(pm) as c:
        await c.post(f"/api/v1/po/{po_id}/signoff/sign", json={})

    async with _factory(test_engine)() as db:
        po = (await db.execute(select(PurchaseOrder).where(
            PurchaseOrder.id == po_id))).scalar_one()
    # nc_purchase_sync owns this column and rewrites it every run.
    assert po.status == "nc_pending"
    assert po.approval_step_idx == 0


async def test_a_restricted_signatory_can_open_the_po_they_must_sign(
    test_engine, stub_engine, admin_client,
):
    """OPM is a RESTRICTED role and an NC PO has neither department nor PR.

    So the department-scoped po_subq cannot contain it: without the sign-off
    arm of is_po_visible, the task lands in the OPM's inbox and the link 404s.
    """
    officer = await _make_user(test_engine, "erp_pa_officer")
    pm = await _make_user(test_engine, "procurement_manager", signature=_PNG)
    opm = await _make_user(test_engine, "opm", signature=_PNG)
    po_id = await _make_po(test_engine, officer)
    await admin_client.post(f"/api/v1/po/{po_id}/signoff/submit",
                            json={"justification": "Why."})

    # Step 1 is the PM's — the OPM has no open task yet and no scope either.
    async with _client_for(opm) as c:
        assert (await c.get(f"/api/v1/po/{po_id}/signoff")).status_code == 404

    async with _client_for(pm) as c:
        await c.post(f"/api/v1/po/{po_id}/signoff/sign", json={})

    # Now it is the OPM's step: the broadcast task makes the PO reachable.
    async with _client_for(opm) as c:
        resp = await c.get(f"/api/v1/po/{po_id}/signoff")
        assert resp.status_code == 200, resp.text
        assert resp.json()["can_sign"] is True
        assert (await c.get(f"/api/v1/po/{po_id}")).status_code == 200

        signed = await c.post(f"/api/v1/po/{po_id}/signoff/sign", json={})
        assert signed.status_code == 200, signed.text
        assert signed.json()["status"] == "approved"
        # Having signed it, they keep access after the task closes.
        assert (await c.get(f"/api/v1/po/{po_id}")).status_code == 200


# ── return and resubmission ───────────────────────────────────────────────

async def test_resubmitting_after_a_return_clears_the_earlier_signatures(
    test_engine, stub_engine, admin_client,
):
    officer = await _make_user(test_engine, "erp_pa_officer")
    pm = await _make_user(test_engine, "procurement_manager", signature=_PNG)
    opm = await _make_user(test_engine, "opm", signature=_PNG)
    po_id = await _make_po(test_engine, officer)
    await admin_client.post(f"/api/v1/po/{po_id}/signoff/submit",
                            json={"justification": "First attempt."})
    async with _client_for(pm) as c:
        await c.post(f"/api/v1/po/{po_id}/signoff/sign", json={})
    assert len(await _signatures(test_engine, po_id)) == 1

    async with _client_for(opm) as c:
        resp = await c.post(f"/api/v1/po/{po_id}/signoff/return",
                            json={"comment": "Why this quantity?"})
    assert resp.status_code == 200, resp.text

    resp = await admin_client.post(f"/api/v1/po/{po_id}/signoff/submit",
                                   json={"justification": "Because of the shortfall."})
    assert resp.status_code == 200, resp.text
    # The Purchasing Manager's signature certified the previous round only.
    assert await _signatures(test_engine, po_id) == []


# ── the justification thread ──────────────────────────────────────────────

async def test_the_thread_is_append_only_and_reads_in_order(
    test_engine, stub_engine, admin_client,
):
    officer = await _make_user(test_engine, "erp_pa_officer")
    pm = await _make_user(test_engine, "procurement_manager", signature=_PNG)
    await _make_user(test_engine, "opm", signature=_PNG)
    po_id = await _make_po(test_engine, officer)

    await admin_client.post(f"/api/v1/po/{po_id}/signoff/submit",
                            json={"justification": "Initial case."})
    async with _client_for(pm) as c:
        await c.post(f"/api/v1/po/{po_id}/signoff/note",
                     json={"comment": "Which line is the mould fee?"})
    resp = await admin_client.post(f"/api/v1/po/{po_id}/signoff/note",
                                   json={"comment": "Line 3, one-off tooling."})
    assert resp.status_code == 200, resp.text

    thread = resp.json()["thread"]
    assert [e["comment"] for e in thread] == [
        "Initial case.",
        "Which line is the mould fee?",
        "Line 3, one-off tooling.",
    ]


async def test_outsiders_cannot_add_to_the_thread(
    test_engine, stub_engine, admin_client,
):
    officer = await _make_user(test_engine, "erp_pa_officer")
    await _make_user(test_engine, "procurement_manager", signature=_PNG)
    await _make_user(test_engine, "opm", signature=_PNG)
    po_id = await _make_po(test_engine, officer)
    await admin_client.post(f"/api/v1/po/{po_id}/signoff/submit",
                            json={"justification": "Initial case."})

    # ap_clerk sees every PO (unrestricted), so this exercises the participant
    # check rather than document scope — a restricted role would 404 first.
    outsider = await _make_user(test_engine, "ap_clerk")
    async with _client_for(outsider) as c:
        resp = await c.post(f"/api/v1/po/{po_id}/signoff/note",
                            json={"comment": "Just passing by."})
    assert resp.status_code == 403, resp.text
