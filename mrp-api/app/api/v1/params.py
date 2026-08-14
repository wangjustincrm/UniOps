"""Planning parameters (key-value settings), design doc
2026-08-12-mrp-weekly-planning Task 2.

`GET /params` returns every row in `mrp_planning_params` flattened to a
plain `{key: value}` dict. `PUT /params/{key}` only accepts whitelisted
keys — `week_calendar_mode` and `week_start_dow` as of the minimum-lot
task; Phase 1C's
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
from app.services.capacity import ExceptionShiftConflict, shift_capacity_exceptions
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


async def set_param(db: AsyncSession, key: str, value: Any, actor: uuid.UUID | None,
                    *, commit: bool = True) -> MrpPlanningParam:
    """Upsert one param's value. Does not validate `value` — callers (the
    PUT endpoint below) are responsible for checking it against whatever
    that key's allowed values are before calling this.

    `commit=False` leaves the transaction open so a caller can write the
    parameter and its side effects atomically. `week_start_dow` needs that:
    the parameter and the capacity-exception shift it forces must land
    together, or a failed shift would leave the grid moved and the shutdown
    weeks stranded on the old one."""
    row = await db.get(MrpPlanningParam, key)
    now = datetime.now(timezone.utc)
    if row is None:
        row = MrpPlanningParam(key=key, value=value, updated_by=actor, updated_at=now)
        db.add(row)
    else:
        row.value = value
        row.updated_by = actor
        row.updated_at = now
    if commit:
        await db.commit()
        await db.refresh(row)
    return row


_WEEK_CALENDAR_MODE_KEY = "week_calendar_mode"


def _validate_week_calendar_mode(value: Any) -> None:
    if value not in WEEK_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown week_calendar_mode {value!r}; must be one of {WEEK_MODES}",
        )


# Whitelist of keys PUT /params/{key} accepts, each mapped to a validator
# that raises HTTPException(422) on a bad value. Phase 1C adds more entries
# here — it must never simplify this into a single-key special case.
def _validate_week_start_dow(value: Any) -> None:
    """0=Monday .. 6=Sunday. `bool` is rejected explicitly — Python makes
    `True == 1`, so a stray boolean would otherwise plan the whole factory
    on Tuesday-start weeks."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 6:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"week_start_dow must be an integer 0..6 (0=Monday), got {value!r}",
        )


def _validate_frozen_months(value: object) -> None:
    """How many months from the current one are frozen -- their materials
    are already bought, so their plan is copied forward untouched. 0 means
    nothing is frozen. Capped at 24 because a frozen zone longer than the
    planning horizon would freeze the entire plan permanently."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 24:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"frozen_months must be an integer 0..24, got {value!r}",
        )


_WEEK_START_DOW_KEY = "week_start_dow"
FROZEN_MONTHS_KEY = "frozen_months"
DEFAULT_FROZEN_MONTHS = 3

_WRITABLE_PARAMS: dict[str, Callable[[Any], None]] = {
    "week_calendar_mode": _validate_week_calendar_mode,
    _WEEK_START_DOW_KEY: _validate_week_start_dow,
    FROZEN_MONTHS_KEY: _validate_frozen_months,
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
    if key == _WEEK_START_DOW_KEY:
        return await _update_week_start_dow(db, body.value, _sub_to_uuid(payload))
    row = await set_param(db, key, body.value, _sub_to_uuid(payload))
    return {row.key: row.value}


async def _update_week_start_dow(db: AsyncSession, value: int,
                                 actor: uuid.UUID | None) -> dict[str, Any]:
    """Write the new week start day AND move the capacity exceptions onto
    the grid it produces, in one transaction.

    Returns `exceptions_shifted` alongside the value so the UI can tell the
    planner how many maintenance weeks were re-keyed — a silent move is
    almost as bad as no move at all when the number is wrong."""
    old = await get_param(db, _WEEK_START_DOW_KEY, 0)
    old_dow = old if isinstance(old, int) and not isinstance(old, bool) else 0
    mode = await get_param(db, _WEEK_CALENDAR_MODE_KEY, "iso_thursday")
    try:
        moved = await shift_capacity_exceptions(
            db, mode=mode, old_dow=old_dow, new_dow=value)
    except ExceptionShiftConflict as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "changing the week start day would put two capacity exceptions in the "
                "same week ("
                + ", ".join(w.isoformat() for w in exc.weeks)
                + "). Remove or merge them first; nothing was changed."
            ),
        ) from exc
    await set_param(db, _WEEK_START_DOW_KEY, value, actor, commit=False)
    await db.commit()
    return {_WEEK_START_DOW_KEY: value, "exceptions_shifted": moved}
