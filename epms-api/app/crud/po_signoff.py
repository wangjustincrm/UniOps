"""PO sign-off — the two-step signature flow over NC-imported POs.

The workflow itself is executed by approval-api (doc_type "posign", walking
purchase_orders.signoff_status / signoff_step_idx). What lives here is
everything the engine does not know about: whether the configured signers can
actually sign, the signature snapshots taken at the moment of signing, and the
append-only justification thread.
"""
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access_scope import role_holder_ids
from app.models.approval import ApprovalEvent
from app.models.po import PurchaseOrder
from app.models.po_signoff_signature import PoSignoffSignature
from app.models.user import User

# doc_type used by approval-api for this workflow, and the document_type these
# rows carry in the shared approval_events / tasks tables. Deliberately NOT
# "po": the PO's own approval chain writes rows under that one.
SIGNOFF_DOC_TYPE = "posign"

# Sign-off is only offered on POs mirrored from NC. A locally raised PO already
# walks the PO approval chain, which is what this flow replaces for NC imports.
SIGNOFF_SOURCES = ("nc",)

# Statuses in which a sign-off may be raised. The engine enforces the sign-off's
# own state machine; this is about the PO itself.
_SUBMITTABLE_PO_STATUSES = ("nc_pending", "issued")

_OPEN_STATUSES = ("submitted", "in_review")


def is_signoff_eligible(po: PurchaseOrder) -> bool:
    return po.source in SIGNOFF_SOURCES and po.status in _SUBMITTABLE_PO_STATUSES


async def _holder_details(
    db: AsyncSession, workflow: list[dict],
) -> dict[str, list[tuple[uuid.UUID, str, bool]]]:
    """role -> [(user_id, full_name, has_signature)] for every workflow role."""
    codes = tuple({s["role"] for s in workflow})
    if not codes:
        return {}
    holders = await role_holder_ids(db, codes=codes)
    wanted = {uid for ids in holders.values() for uid in ids}
    if not wanted:
        return {role: [] for role in codes}
    rows = (await db.execute(
        select(User.id, User.full_name, User.signature_image).where(User.id.in_(wanted))
    )).all()
    by_id = {uid: (name, bool(sig)) for uid, name, sig in rows}
    out: dict[str, list[tuple[uuid.UUID, str, bool]]] = {}
    for role in codes:
        out[role] = [
            (uid, by_id[uid][0], by_id[uid][1])
            for uid in sorted(holders.get(role, set()), key=str)
            if uid in by_id
        ]
    return out


async def build_step_states(
    db: AsyncSession, po: PurchaseOrder, workflow: list[dict],
) -> tuple[list[dict], list[str]]:
    """Per-step state plus the reasons a sign-off cannot be raised right now."""
    details = await _holder_details(db, workflow)
    signed = {s.step_idx: s for s in po.signoff_signatures}

    steps: list[dict] = []
    blockers: list[str] = []
    for idx, node in enumerate(workflow):
        holders = details.get(node["role"], [])
        unsigned = [name for _uid, name, has_sig in holders if not has_sig]
        if not holders:
            blockers.append(
                f"No active holder of {node['label']}. Assign the role in "
                f"Portal → Access Control before raising a sign-off."
            )
        elif not any(has_sig for _uid, _name, has_sig in holders):
            who = ", ".join(name for _uid, name, _s in holders)
            blockers.append(
                f"{node['label']} ({who}) has not set a signature in My Profile."
            )
        snap = signed.get(idx)
        steps.append({
            "id": node.get("id", str(idx)),
            "role": node["role"],
            "label": node.get("label", node["role"]),
            "sig_slot": node.get("sig_slot"),
            "holder_count": len(holders),
            "holders_without_signature": unsigned,
            "signed_by_name": snap.signer_name if snap else None,
            "signed_at": snap.signed_at if snap else None,
        })
    return steps, blockers


async def load_thread(db: AsyncSession, po_id: uuid.UUID) -> list[dict]:
    """The append-only justification thread, oldest first.

    Every sign-off action lands in approval_events, so the submitter's case, a
    signer's objection and any follow-up addition read as one conversation.
    Nothing here is ever updated — an addition is a new row.
    """
    rows = (await db.execute(
        select(ApprovalEvent, User.full_name)
        .outerjoin(User, User.id == ApprovalEvent.actor_id)
        .where(
            ApprovalEvent.document_type == SIGNOFF_DOC_TYPE,
            ApprovalEvent.document_id == po_id,
        )
        .order_by(ApprovalEvent.created_at.asc())
    )).all()
    return [{
        "action": ev.action,
        "actor_name": name,
        "actor_role": ev.actor_role,
        "comment": ev.comment,
        "at": ev.created_at,
    } for ev, name in rows]


async def is_participant(
    db: AsyncSession, po: PurchaseOrder, workflow: list[dict], actor_id: uuid.UUID,
) -> bool:
    """May this user add to the thread? Submitter and configured signers only."""
    if po.signoff_submitted_by == actor_id:
        return True
    codes = tuple({s["role"] for s in workflow})
    if not codes:
        return False
    holders = await role_holder_ids(db, codes=codes)
    return any(actor_id in ids for ids in holders.values())


async def actor_signature(db: AsyncSession, actor_id: uuid.UUID) -> tuple[str, str] | None:
    """(full_name, signature_image) if this user has a preset signature."""
    row = (await db.execute(
        select(User.full_name, User.signature_image).where(User.id == actor_id)
    )).first()
    if row is None or not row[1]:
        return None
    return row[0], row[1]


async def clear_signatures(db: AsyncSession, po_id: uuid.UUID) -> None:
    """Drop every signature on this PO.

    Called when a returned sign-off is resubmitted: a signature certifies the
    round it was given in, and the document may well have changed since.
    """
    await db.execute(sa.delete(PoSignoffSignature).where(PoSignoffSignature.po_id == po_id))


async def record_signature(
    db: AsyncSession,
    po_id: uuid.UUID,
    step_idx: int,
    node: dict[str, Any],
    actor_id: uuid.UUID,
    signer_name: str,
    signature_image: str,
) -> None:
    """Snapshot the signature onto the document.

    The image is copied, not referenced: the PO PDF is regenerated on demand,
    so rendering from users.signature_image would let a later profile edit
    restamp every document this person ever signed.
    """
    await db.execute(sa.delete(PoSignoffSignature).where(
        PoSignoffSignature.po_id == po_id, PoSignoffSignature.step_idx == step_idx))
    db.add(PoSignoffSignature(
        id=uuid.uuid4(),
        po_id=po_id,
        step_idx=step_idx,
        role=node["role"],
        sig_slot=node.get("sig_slot"),
        signed_by=actor_id,
        signer_name=signer_name,
        signature_image=signature_image,
    ))


def add_event(
    db: AsyncSession,
    po: PurchaseOrder,
    action: str,
    actor_id: uuid.UUID,
    actor_role: str,
    comment: str | None,
) -> None:
    """Append a sign-off event that the engine does not write itself (note)."""
    db.add(ApprovalEvent(
        document_type=SIGNOFF_DOC_TYPE,
        document_id=po.id,
        document_number=po.number,
        step_idx=po.signoff_step_idx,
        action=action,
        actor_id=actor_id,
        actor_role=actor_role,
        comment=comment,
    ))


def is_open(po: PurchaseOrder) -> bool:
    return po.signoff_status in _OPEN_STATUSES
