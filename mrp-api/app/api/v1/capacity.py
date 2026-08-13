"""Capacity rules CRUD (Phase 1B Task 1, design §6.4; min_output_qty +
per-week exceptions added by the weekly-MPS Task 3).

Plain master-data CRUD over `mrp_capacity_rules` — same shape as
app/api/v1/consignment.py's stock endpoints (require_permission deps, a
`_get_..._or_404` helper, Pydantic Create/Update/Response schemas). GET is
gated by `mrp.report.view`; POST/PATCH/DELETE by `mrp.param.write` (the same
key admin_sync's `/admin/wms-sync` uses — see
tests/test_permission_gates.py).

`limit_value` is Numeric(18,3); Pydantic (de)serializes Decimal as a JSON
string, never a float, so callers must always send/receive it quoted (e.g.
`"12"` in, `"12.000"` out) — see feedback_uniops_decimal_as_string.

`uom` stays a free string exactly as it already was for max_sku_count/
max_output_qty (there is no server-side enum here, nor was there before
this task) — it is pinned to `'KG'` by convention only, per this task's
brief: Phase 1B once shipped a KG/MT selector and it produced a silent
1000x error, so this task does not reopen unit selection.

`constraint_type` IS validated (`_validate_constraint_type`, fix round 1)
against the three values the resolvers in `app/services/capacity.py`
actually match on (`max_output_qty`, `max_sku_count`, `min_output_qty`), on
every create/update for both `/rules` and `/exceptions`. Originally shipped
as a bare unvalidated string (matching pre-existing behaviour for the first
two constraint types) — fixed after review flagged that a typo'd value
would be silently stored and then silently ignored by both resolvers, with
no signal to the caller that the rule/exception does nothing.

## min_output_qty <=  max_output_qty validation — judgment call

`min_output_qty` is a soft floor (see `app/services/mps_engine.py`'s
`CapacityLimits.min_output_qty` and `app/services/capacity.py`'s
`resolve_limits_for_week` docstrings) but a *standing rule* asserting
min > max for any moment both are simultaneously active is still just bad
data entry — nothing downstream could ever satisfy it. `_validate_min_max`
below rejects that at write time on `/rules` create+update.

Rules carry an effective window; two rules can overlap only partially. The
check here is: for every OTHER active rule of the opposite output-qty type
in the same (scope_type, scope_ref) whose window overlaps this rule's window
AT ALL (even partially, `effective_to=None` treated as unbounded), the
min value must not exceed the max value. This is correct specifically
because neither rule's `limit_value` varies within its own window — a
constant value that's fine for the *whole* overlap is equivalently fine for
*any* moment of it, so "windows overlap" and "the min/max conflict during
the overlap" reduce to the same single scalar comparison. A rule that is
(or would become) `is_active=False` is exempted — an inactive rule is not in
effect and cannot conflict with anything.

**Exceptions (`/capacity/exceptions`) deliberately do NOT get this same
check.** An exception's whole purpose is a single week's override, and the
brief's own example is exactly a case this check would wrongly block: a
factory with a standing `min_output_qty` rule declares a shutdown week via
a `max_output_qty=0` exception. That exception's value (0) is far below the
standing min — correct and intended for a shutdown, not an error — and
requiring the caller to *also* file a matching `min_output_qty=0` exception
for the same week just to satisfy a write-time gate would be exactly the
kind of hard-constraint behavior the brief says `min_output_qty` must never
exhibit. The soft-floor invariant ("never causes a capacity gap or a
rejected plan") is enforced where it actually matters — in the week-based
scheduler a later task wires up — not by rejecting an exception CRUD write.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.authz import require_permission
from app.core.deps import SessionDep
from app.models.capacity import MrpCapacityException, MrpCapacityRule

router = APIRouter(prefix="/capacity", tags=["capacity"])

ReadDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]
WriteDep = Annotated[dict, Depends(require_permission("mrp.param.write"))]


# ── Schemas ──────────────────────────────────────────────────────────────


class CapacityRuleCreate(BaseModel):
    scope_type: str
    scope_ref: str | None = None
    constraint_type: str
    limit_value: Decimal
    uom: str | None = None
    effective_from: date
    effective_to: date | None = None
    is_active: bool = True


class CapacityRuleUpdate(BaseModel):
    scope_type: str | None = None
    scope_ref: str | None = None
    constraint_type: str | None = None
    limit_value: Decimal | None = None
    uom: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    is_active: bool | None = None


class CapacityRuleResponse(BaseModel):
    id: uuid.UUID
    scope_type: str
    scope_ref: str | None
    constraint_type: str
    limit_value: Decimal
    uom: str | None
    effective_from: date
    effective_to: date | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CapacityExceptionCreate(BaseModel):
    week_start: date
    scope_type: str
    scope_ref: str | None = None
    constraint_type: str
    limit_value: Decimal
    uom: str | None = None
    reason: str | None = None
    is_active: bool = True


class CapacityExceptionUpdate(BaseModel):
    week_start: date | None = None
    scope_type: str | None = None
    scope_ref: str | None = None
    constraint_type: str | None = None
    limit_value: Decimal | None = None
    uom: str | None = None
    reason: str | None = None
    is_active: bool | None = None


class CapacityExceptionResponse(BaseModel):
    id: uuid.UUID
    week_start: date
    scope_type: str
    scope_ref: str | None
    constraint_type: str
    limit_value: Decimal
    uom: str | None
    reason: str | None
    is_active: bool

    model_config = {"from_attributes": True}


# ── Helpers ──────────────────────────────────────────────────────────────


async def _get_rule_or_404(db: SessionDep, rule_id: uuid.UUID) -> MrpCapacityRule:
    row = await db.get(MrpCapacityRule, rule_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="capacity rule not found")
    return row


async def _get_exception_or_404(db: SessionDep, exception_id: uuid.UUID) -> MrpCapacityException:
    row = await db.get(MrpCapacityException, exception_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="capacity exception not found")
    return row


_KNOWN_CONSTRAINT_TYPES = ("max_output_qty", "max_sku_count", "min_output_qty")


def _validate_constraint_type(constraint_type: str) -> None:
    """422 (never a silent no-op) on any `constraint_type` outside the
    three values `resolve_effective_rules`/`resolve_limits_for_week`
    (app/services/capacity.py) actually match on. Before this check, a
    typo'd value (e.g. `'max_output_qtyy'`) was accepted and stored by both
    `/rules` and `/exceptions`, and then silently matched nothing in either
    resolver -- a rule or exception that looks saved in the UI/API response
    but does nothing, the exact class of silent failure this codebase keeps
    getting bitten by. No other constraint_type string exists anywhere in
    this repo (verified by grep before adding this), so this rejects
    nothing legitimate -- only genuinely unknown values."""
    if constraint_type not in _KNOWN_CONSTRAINT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown constraint_type {constraint_type!r}; must be one of {_KNOWN_CONSTRAINT_TYPES}",
        )


_MIN_MAX_TYPES = ("min_output_qty", "max_output_qty")


async def _validate_min_max(
    db: SessionDep,
    *,
    scope_type: str,
    scope_ref: str | None,
    constraint_type: str,
    limit_value: Decimal,
    effective_from: date,
    effective_to: date | None,
    is_active: bool,
    exclude_id: uuid.UUID | None = None,
) -> None:
    """Reject (422) a create/update whose resulting min_output_qty would
    exceed a same-scope max_output_qty rule for any moment both are active
    — see this module's docstring for the overlap reasoning and why
    exceptions don't get this same check."""
    if constraint_type not in _MIN_MAX_TYPES or not is_active:
        return
    other_type = "max_output_qty" if constraint_type == "min_output_qty" else "min_output_qty"

    stmt = select(MrpCapacityRule).where(
        MrpCapacityRule.scope_type == scope_type,
        MrpCapacityRule.constraint_type == other_type,
        MrpCapacityRule.is_active.is_(True),
    )
    stmt = stmt.where(MrpCapacityRule.scope_ref.is_(None) if scope_ref is None else MrpCapacityRule.scope_ref == scope_ref)
    if exclude_id is not None:
        stmt = stmt.where(MrpCapacityRule.id != exclude_id)
    candidates = (await db.execute(stmt)).scalars().all()

    this_to = effective_to or date.max
    for other in candidates:
        other_to = other.effective_to or date.max
        if not (effective_from <= other_to and other.effective_from <= this_to):
            continue  # windows never overlap -- both rules can't be active at once
        min_val = limit_value if constraint_type == "min_output_qty" else other.limit_value
        max_val = other.limit_value if constraint_type == "min_output_qty" else limit_value
        if min_val > max_val:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"min_output_qty {min_val} would exceed max_output_qty {max_val} "
                    f"for scope_type={scope_type!r} scope_ref={scope_ref!r} during their "
                    f"overlapping effective window (this rule "
                    f"{effective_from}–{effective_to or 'open-ended'}, conflicting "
                    f"rule {other.effective_from}–{other.effective_to or 'open-ended'})"
                ),
            )


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("/rules", response_model=list[CapacityRuleResponse])
async def list_rules(db: SessionDep, _: ReadDep):
    rows = (await db.execute(select(MrpCapacityRule).order_by(MrpCapacityRule.created_at))).scalars().all()
    return rows


@router.post("/rules", response_model=CapacityRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(body: CapacityRuleCreate, db: SessionDep, _: WriteDep):
    _validate_constraint_type(body.constraint_type)
    await _validate_min_max(
        db, scope_type=body.scope_type, scope_ref=body.scope_ref,
        constraint_type=body.constraint_type, limit_value=body.limit_value,
        effective_from=body.effective_from, effective_to=body.effective_to,
        is_active=body.is_active,
    )
    row = MrpCapacityRule(**body.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.patch("/rules/{rule_id}", response_model=CapacityRuleResponse)
async def update_rule(rule_id: uuid.UUID, body: CapacityRuleUpdate, db: SessionDep, _: WriteDep):
    row = await _get_rule_or_404(db, rule_id)
    updates = body.model_dump(exclude_unset=True)
    if "constraint_type" in updates:
        _validate_constraint_type(updates["constraint_type"])
    await _validate_min_max(
        db,
        scope_type=updates.get("scope_type", row.scope_type),
        scope_ref=updates.get("scope_ref", row.scope_ref),
        constraint_type=updates.get("constraint_type", row.constraint_type),
        limit_value=updates.get("limit_value", row.limit_value),
        effective_from=updates.get("effective_from", row.effective_from),
        effective_to=updates.get("effective_to", row.effective_to),
        is_active=updates.get("is_active", row.is_active),
        exclude_id=row.id,
    )
    for field, value in updates.items():
        setattr(row, field, value)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(rule_id: uuid.UUID, db: SessionDep, _: WriteDep):
    row = await _get_rule_or_404(db, rule_id)
    await db.delete(row)
    await db.commit()


# ── Endpoints: per-week exceptions ──────────────────────────────────────


@router.get("/exceptions", response_model=list[CapacityExceptionResponse])
async def list_exceptions(db: SessionDep, _: ReadDep):
    rows = (await db.execute(
        select(MrpCapacityException).order_by(MrpCapacityException.week_start)
    )).scalars().all()
    return rows


@router.post("/exceptions", response_model=CapacityExceptionResponse, status_code=status.HTTP_201_CREATED)
async def create_exception(body: CapacityExceptionCreate, db: SessionDep, _: WriteDep):
    _validate_constraint_type(body.constraint_type)
    row = MrpCapacityException(**body.model_dump())
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"a capacity exception already exists for week_start={body.week_start} "
                f"scope_type={body.scope_type} scope_ref={body.scope_ref!r} "
                f"constraint_type={body.constraint_type}"
            ),
        ) from exc
    await db.refresh(row)
    return row


@router.patch("/exceptions/{exception_id}", response_model=CapacityExceptionResponse)
async def update_exception(exception_id: uuid.UUID, body: CapacityExceptionUpdate, db: SessionDep, _: WriteDep):
    row = await _get_exception_or_404(db, exception_id)
    updates = body.model_dump(exclude_unset=True)
    if "constraint_type" in updates:
        _validate_constraint_type(updates["constraint_type"])
    for field, value in updates.items():
        setattr(row, field, value)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="updating this exception would collide with an existing "
                   "(week_start, scope_type, scope_ref, constraint_type)",
        ) from exc
    await db.refresh(row)
    return row


@router.delete("/exceptions/{exception_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_exception(exception_id: uuid.UUID, db: SessionDep, _: WriteDep):
    row = await _get_exception_or_404(db, exception_id)
    await db.delete(row)
    await db.commit()
