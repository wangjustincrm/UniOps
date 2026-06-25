"""Approval execution endpoints — workflow state transitions for all UniOps modules."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.crud.engine import _DOC_META, execute_action
from app.db.base import get_db
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
