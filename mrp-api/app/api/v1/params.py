"""Planning parameters (key-value settings), design doc
2026-08-12-mrp-weekly-planning Task 2.

`GET /params` returns every row in `mrp_planning_params` flattened to a
plain `{key: value}` dict. `PUT /params/{key}` only accepts whitelisted
keys — `week_calendar_mode` is the only one as of this task; Phase 1C's
raw_material_loss_rate / packaging_loss_rate will register into the same
`_WRITABLE_PARAMS` map later (see app/models/params.py's docstring for why
the table itself is a generic key-value store rather than dedicated
columns).

`get_param`/`set_param` are the reusable interface other services (future
week-bucket-aware code, e.g. the MPS engine) import to read/write a param
without going through HTTP — same "produce a plain function, not just an
endpoint" idiom app/services/capacity.py's resolve_limits_for_week follows.

GET is gated `mrp.report.view`; PUT is gated `mrp.param.write` — same key
app/api/v1/capacity.py's write endpoints and admin_sync.py's /wms-sync use
(see tests/test_permission_gates.py).
"""
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Callable

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.core.deps import SessionDep
from app.models.params import MrpPlanningParam
from app.services.week_calendar import WEEK_MODES

router = APIRouter(prefix="/params", tags=["params"])

ReadDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]
WriteDep = Annotated[dict, Depends(require_permission("mrp.param.write"))]


def _sub_to_uuid(payload: dict) -> uuid.UUID | None:
    """Same never-raises idiom app/api/v1/intent.py's `_sub_to_uuid` uses to
    turn the JWT `sub` claim into an actor id for `updated_by` — a missing
    or malformed `sub` degrades to an unattributed (None) write rather than
    a 500."""
    sub = payload.get("sub")
    if not sub:
        return None
    try:
        return uuid.UUID(sub)
    except ValueError:
        return None


async def get_param(db: AsyncSession, key: str, default: Any = None) -> Any:
    """Fetch one param's value, or `default` if the key has no row."""
    row = await db.get(MrpPlanningParam, key)
    return row.value if row is not None else default


async def set_param(db: AsyncSession, key: str, value: Any, actor: uuid.UUID | None) -> MrpPlanningParam:
    """Upsert one param's value. Does not validate `value` — callers (the
    PUT endpoint below) are responsible for checking it against whatever
    that key's allowed values are before calling this."""
    row = await db.get(MrpPlanningParam, key)
    now = datetime.now(timezone.utc)
    if row is None:
        row = MrpPlanningParam(key=key, value=value, updated_by=actor, updated_at=now)
        db.add(row)
    else:
        row.value = value
        row.updated_by = actor
        row.updated_at = now
    await db.commit()
    await db.refresh(row)
    return row


def _validate_week_calendar_mode(value: Any) -> None:
    if value not in WEEK_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown week_calendar_mode {value!r}; must be one of {WEEK_MODES}",
        )


# Whitelist of keys PUT /params/{key} accepts, each mapped to a validator
# that raises HTTPException(422) on a bad value. Phase 1C adds more entries
# here — it must never simplify this into a single-key special case.
_WRITABLE_PARAMS: dict[str, Callable[[Any], None]] = {
    "week_calendar_mode": _validate_week_calendar_mode,
}


class ParamUpdate(BaseModel):
    value: Any


@router.get("", response_model=dict[str, Any])
async def list_params(db: SessionDep, _: ReadDep):
    rows = (await db.execute(select(MrpPlanningParam))).scalars().all()
    return {row.key: row.value for row in rows}


@router.put("/{key}", response_model=dict[str, Any])
async def update_param(key: str, body: ParamUpdate, db: SessionDep, payload: WriteDep):
    validator = _WRITABLE_PARAMS.get(key)
    if validator is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown parameter key {key!r}",
        )
    validator(body.value)
    row = await set_param(db, key, body.value, _sub_to_uuid(payload))
    return {row.key: row.value}
