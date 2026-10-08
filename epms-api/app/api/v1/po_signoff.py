"""PO sign-off endpoints — the signature flow over NC-imported POs.

Execution lives in approval-api (doc_type "posign"); this module owns the parts
the engine does not know about: who may raise a sign-off, whether the signers
can actually sign, the signature snapshots, and the justification thread.

Ordering rule for every endpoint here: NEVER write purchase_orders before
calling the engine. The engine updates the same row over its own connection, so
a local UPDATE flushed first would hold the row lock while this request waits on
the engine's HTTP response — a deadlock that resolves only when the client
times out. Local writes happen after the engine's transaction has committed
(the same discipline po.py's /action endpoint follows).
"""
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.core.access_scope import build_scope, is_po_visible, role_holder_ids
from app.core.authz import require_permission
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.crud import po as po_crud
from app.crud import po_signoff as signoff_crud
from app.models.task import Task
from app.models.user import User
from app.schemas.po_signoff import (
    SignoffCommentRequest, SignoffSignRequest, SignoffState, SignoffSubmitRequest,
)
from app.services import approval_client
from app.services.notification import fire_and_forget_notify, fire_and_forget_signoff_complete

router = APIRouter(prefix="/po", tags=["po-signoff"])

# Raising a sign-off is the ERP PA Officer's job — the same people who fill in
# the buyer detail an NC import does not carry. Signing is NOT gated on a
# permission key: the engine admits the holder of the step's role and nobody
# else, and that is the only correct gate.
PoSignoffDep = Annotated[dict, Depends(require_permission("epms.po.signoff"))]


async def _load(po_id: uuid.UUID, db: SessionDep, user: dict):
    po = await po_crud.get_by_id(db, po_id)
    if po is None:
        raise HTTPException(status_code=404, detail="PO not found")
    scope = await build_scope(db, user)
    if not await is_po_visible(db, po_id, scope):
        raise HTTPException(status_code=404, detail="PO not found")
    return po


async def _workflow(po_id: uuid.UUID, token: str) -> list[dict]:
    try:
        return await approval_client.get_workflow_steps(
            signoff_crud.SIGNOFF_DOC_TYPE, str(po_id), token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


async def _state(db, po, workflow: list[dict], actor_id: uuid.UUID) -> dict:
    steps, blockers = await signoff_crud.build_step_states(db, po, workflow)
    thread = await signoff_crud.load_thread(db, po.id)

    submitted_by_name = None
    if po.signoff_submitted_by:
        submitted_by_name = (await db.execute(
            select(User.full_name).where(User.id == po.signoff_submitted_by)
        )).scalar_one_or_none()

    ineligible = signoff_crud.ineligible_reason(po)
    eligible = ineligible is None
    if ineligible:
        blockers.insert(0, ineligible)
    if not workflow:
        blockers.insert(0, "No sign-off workflow is configured. "
                           "Set one up in Portal → Approval Workflows.")

    open_now = signoff_crud.is_open(po)
    current = workflow[po.signoff_step_idx] if open_now and po.signoff_step_idx < len(workflow) else None
    can_sign = False
    if current is not None:
        holders = await role_holder_ids(db, codes=(current["role"],))
        can_sign = actor_id in holders.get(current["role"], set())

    return {
        "status": po.signoff_status,
        "step_idx": po.signoff_step_idx,
        "submitted_by": po.signoff_submitted_by,
        "submitted_by_name": submitted_by_name,
        "submitted_at": po.signoff_submitted_at,
        "steps": steps,
        "thread": thread,
        "can_submit": eligible and not blockers
                      and po.signoff_status in ("draft", "returned"),
        "can_sign": can_sign,
        "can_note": await signoff_crud.is_participant(db, po, workflow, actor_id),
        "blockers": blockers,
    }


async def _notify_open_tasks(db, po_id: uuid.UUID) -> None:
    tasks = (await db.execute(select(Task).where(
        Task.document_type == signoff_crud.SIGNOFF_DOC_TYPE,
        Task.document_id == po_id,
        Task.is_completed.is_(False),
    ))).scalars().all()
    for task in tasks:
        fire_and_forget_notify(task, db)


@router.get("/{po_id}/signoff", response_model=SignoffState)
async def get_signoff(
    po_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload, token: BearerToken,
):
    po = await _load(po_id, db, user)
    workflow = await _workflow(po_id, token)
    return await _state(db, po, workflow, uuid.UUID(user["sub"]))


@router.post("/{po_id}/signoff/submit", response_model=SignoffState)
async def submit_signoff(
    po_id: uuid.UUID,
    body: SignoffSubmitRequest,
    db: SessionDep,
    user: PoSignoffDep,
    token: BearerToken,
):
    po = await _load(po_id, db, user)
    ineligible = signoff_crud.ineligible_reason(po)
    if ineligible:
        raise HTTPException(status_code=409, detail=ineligible)

    workflow = await _workflow(po_id, token)
    if not workflow:
        raise HTTPException(
            status_code=409,
            detail="No sign-off workflow is configured. "
                   "Set one up in Portal → Approval Workflows.")
    _steps, blockers = await signoff_crud.build_step_states(db, po, workflow)
    if blockers:
        # Refusing here rather than at signing time: a sign-off that cannot be
        # completed should never enter anyone's inbox in the first place.
        raise HTTPException(status_code=409, detail=" ".join(blockers))

    # Read before the engine moves anything.
    was_returned = po.signoff_status == "returned"

    try:
        await approval_client.delegate_action(
            signoff_crud.SIGNOFF_DOC_TYPE, str(po_id), "submit", body.justification, token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    await db.refresh(po)
    if was_returned:
        # A signature certifies the round it was given in. Resubmitting after a
        # return starts a fresh one.
        await signoff_crud.clear_signatures(db, po_id)
    po.signoff_submitted_by = uuid.UUID(user["sub"])
    po.signoff_submitted_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(po)

    await _notify_open_tasks(db, po_id)
    return await _state(db, po, workflow, uuid.UUID(user["sub"]))


@router.post("/{po_id}/signoff/sign", response_model=SignoffState)
async def sign_signoff(
    po_id: uuid.UUID,
    body: SignoffSignRequest,
    db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
):
    po = await _load(po_id, db, user)
    if not signoff_crud.is_open(po):
        raise HTTPException(
            status_code=409,
            detail=f"This PO's sign-off is not awaiting a signature (status: {po.signoff_status}).")

    workflow = await _workflow(po_id, token)
    step_idx = po.signoff_step_idx          # the engine advances this; read it first
    if step_idx >= len(workflow):
        raise HTTPException(status_code=409, detail="Sign-off step is out of range.")
    node = workflow[step_idx]

    actor_id = uuid.UUID(user["sub"])
    signature = await signoff_crud.actor_signature(db, actor_id)
    if signature is None:
        # Checked before the engine runs: otherwise the step would advance and
        # the document would carry a blank signature box forever.
        raise HTTPException(
            status_code=409,
            detail="You have not set a signature yet. Add one in My Profile before signing.")
    signer_name, signature_image = signature

    try:
        await approval_client.delegate_action(
            signoff_crud.SIGNOFF_DOC_TYPE, str(po_id), "approve", body.comment, token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    await db.refresh(po)
    await signoff_crud.record_signature(
        db, po_id, step_idx, node, actor_id, signer_name, signature_image)
    # Committed here rather than at request teardown so the completion email,
    # which opens its own session, can see the signature it is about to list.
    await db.commit()
    await db.refresh(po)

    if po.signoff_status == "approved":
        # Rebuild the PDF so it carries the signatures, replacing the unsigned
        # one already attached.
        from app.api.v1.po import _generate_po_pdf_background
        from app.core.background import spawn
        spawn(_generate_po_pdf_background(po_id, po.number, token, replace=True),
              name=f"po_pdf_signed:{po.number}")
        fire_and_forget_signoff_complete(po_id)
    await _notify_open_tasks(db, po_id)
    return await _state(db, po, workflow, actor_id)


@router.post("/{po_id}/signoff/return", response_model=SignoffState)
async def return_signoff(
    po_id: uuid.UUID,
    body: SignoffCommentRequest,
    db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
):
    po = await _load(po_id, db, user)
    if not signoff_crud.is_open(po):
        raise HTTPException(
            status_code=409,
            detail=f"This PO's sign-off is not awaiting a signature (status: {po.signoff_status}).")
    workflow = await _workflow(po_id, token)

    try:
        await approval_client.delegate_action(
            signoff_crud.SIGNOFF_DOC_TYPE, str(po_id), "return", body.comment, token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    await db.refresh(po)
    await _notify_open_tasks(db, po_id)
    return await _state(db, po, workflow, uuid.UUID(user["sub"]))


@router.post("/{po_id}/signoff/note", response_model=SignoffState)
async def add_signoff_note(
    po_id: uuid.UUID,
    body: SignoffCommentRequest,
    db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
):
    """Append to the justification thread without moving the workflow.

    The thread is append-only by construction — this writes a new
    approval_events row, and nothing ever updates one. A signer who wants more
    detail can ask for it here instead of returning the whole sign-off, and the
    buyer answers in the same thread the signature is given against.
    """
    po = await _load(po_id, db, user)
    workflow = await _workflow(po_id, token)
    actor_id = uuid.UUID(user["sub"])
    if not await signoff_crud.is_participant(db, po, workflow, actor_id):
        raise HTTPException(
            status_code=403,
            detail="Only the person who raised this sign-off and its signatories can add notes.")

    signoff_crud.add_event(
        db, po, "note", actor_id, user.get("role", ""), body.comment)
    await db.commit()
    await db.refresh(po)
    return await _state(db, po, workflow, actor_id)
