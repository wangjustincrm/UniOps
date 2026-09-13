"""Approval execution endpoints — workflow state transitions for all UniOps modules."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.crud.engine import (_DOC_META, _resolve_meta, build_effective_workflow,
                             execute_action, preflight_submit)
from app.db.base import get_db
from app.models.config import CompanyConfig
from app.schemas.action import ActionRequest, ActionResult

router = APIRouter(prefix="/approvals", tags=["approvals"])

# Known static action keys; cfm_<code> variants are also accepted (validated by engine).
_STATIC_DOC_TYPES = frozenset(_DOC_META.keys())


def _is_valid_doc_type(doc_type: str) -> bool:
    return doc_type in _STATIC_DOC_TYPES or doc_type.startswith("cfm_")


@router.post("/{doc_type}/{doc_id}/action", response_model=ActionResult)
async def run_action(
    doc_type: str,
    doc_id: uuid.UUID,
    body: ActionRequest,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = ...,
):
    if not _is_valid_doc_type(doc_type):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown doc_type '{doc_type}'. Valid types: {sorted(_STATIC_DOC_TYPES)} (plus cfm_<code>)",
        )

    actor_id = uuid.UUID(user["sub"])
    actor_role = user.get("role", "")

    try:
        result = await execute_action(
            db,
            doc_type=doc_type,
            doc_id=doc_id,
            action=body.action,
            actor_id=actor_id,
            actor_role=actor_role,
            comment=body.comment,
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return result


@router.get("/{doc_type}/{doc_id}/workflow-steps")
async def workflow_steps(
    doc_type: str,
    doc_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
) -> list[dict]:
    """Return the effective ordered workflow steps for a specific document.

    Steps include any runtime-injected over-budget pre-approval nodes (PR only)
    plus the base workflow_defs steps. Optional supervisor/director nodes are
    included (skip is decided at execution/render time, not here).
    """
    try:
        meta = _resolve_meta(doc_type)
    except KeyError:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown doc_type '{doc_type}'. Valid types: {sorted(_STATIC_DOC_TYPES)} (plus cfm_<code>)",
        )

    doc = (await db.execute(
        select(meta["model"]).where(meta["model"].id == doc_id)
    )).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail=f"{doc_type.upper()} not found")

    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()
    return await build_effective_workflow(db, doc_type, doc, cfg)


@router.get("/{doc_type}/{doc_id}/preflight")
async def preflight(
    doc_type: str,
    doc_id: uuid.UUID,
    action: str = "submit",
    db: AsyncSession = Depends(get_db),
    _: CurrentUser = ...,
) -> dict:
    """Would this action go through, and if not, everything standing in the way.

    Read-only, and deliberately NOT short-circuiting: submission stops at the
    first failed gate, which is correct when actually submitting but turns
    diagnosis into three round trips. Here every gate is evaluated so the caller
    can show the whole list at once.

    Only the gates approval-api owns are reported — the state machine, budget
    mode, and approver routing. Document field rules (a PR needs a vendor) belong
    to the service that owns the document and are merged in by the caller.
    """
    if action.lower() != "submit":
        raise HTTPException(
            status_code=400,
            detail=f"preflight currently supports action=submit, not '{action}'",
        )
    try:
        _resolve_meta(doc_type)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Unknown doc_type '{doc_type}'")

    try:
        checks = await preflight_submit(db, doc_type, doc_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {
        "doc_type": doc_type,
        "doc_id": str(doc_id),
        "action": "submit",
        "allowed": all(c.passed for c in checks),
        "checks": [
            {"id": c.id, "layer": c.layer, "passed": c.passed, "message": c.message,
             "fixable_by_user": c.fixable_by_user, "owner": c.owner}
            for c in checks
        ],
    }
