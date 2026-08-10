"""CRUD + approval workflow for expense claims (EXP / MIL)."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# NOTE: BudgetAccount mirror removed — expense-api no longer writes directly to
# budget_accounts. Actual spend is now reported to budget-api via /book-expense.
from app.crud._numbering import next_number
from app.models.expense import (
    ExpenseApprovalEvent, ExpenseAttachment, ExpenseClaim,
    ExpenseLineItem, ExpenseTraveler, ExpenseTripItem,
)
from app.schemas.expense import (
    ExpenseClaimCreate, ExpenseClaimUpdate, ExpenseActionRequest,
)
from app.services import budget_client


# ── Number generation ──────────────────────────────────────────────────────────

async def _next_number(db: AsyncSession, claim_type: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return await next_number(db, ExpenseClaim.claim_number, f"{claim_type}-{today}-", width=4)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _compute_totals_exp(line_items: list) -> tuple[Decimal, Decimal, Decimal]:
    total = sum(li.total_amount for li in line_items)
    tax = sum(li.tax_amount for li in line_items)
    net = sum(li.net_amount for li in line_items)
    return Decimal(total), Decimal(tax), Decimal(net)


def _compute_totals_mil(trip_items: list) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Server is the single source of truth for the round-trip multiplier.

    `distance_km` is stored one-way; round trips double both the km and the
    amount. Deriving the per-trip amount here (instead of trusting the
    client-sent `amount`) keeps total_km and total_amount consistent even if a
    stale/buggy client sends the one-way amount with is_round_trip=True.
    """
    cent = Decimal("0.01")
    total = Decimal("0")
    total_km = Decimal("0")
    for ti in trip_items:
        factor = 2 if ti.is_round_trip else 1
        ti.amount = (ti.distance_km * ti.rate_per_km * factor).quantize(cent)
        total += ti.amount
        total_km += ti.distance_km * factor
    return total, Decimal("0"), total, total_km


# ── CRUD ───────────────────────────────────────────────────────────────────────

async def _get_policy(db: AsyncSession):
    from app.models.policy import ExpensePolicyConfig
    result = await db.execute(select(ExpensePolicyConfig).limit(1))
    return result.scalar_one_or_none()


async def create_claim(
    db: AsyncSession,
    data: ExpenseClaimCreate,
    user_id: uuid.UUID,
    user_name: str,
    department_id: uuid.UUID | None,
    department_name: str,
) -> ExpenseClaim:
    claim_number = await _next_number(db, data.claim_type)
    claim = ExpenseClaim(
        claim_number=claim_number,
        claim_type=data.claim_type,
        employee_id=user_id,
        employee_name=user_name,
        department_id=department_id,
        department_name=department_name,
        submission_date=data.submission_date,
        currency=data.currency,
        project_id=data.project_id,
        notes=data.notes,
        purpose=data.purpose,
        vehicle_description=data.vehicle_description,
        vehicle_owned_by=data.vehicle_owned_by,
        travel_from_date=data.travel_from_date,
        travel_to_date=data.travel_to_date,
        travel_destination=data.travel_destination,
        transport_modes=data.transport_modes,
        leave_from_date=data.leave_from_date,
        leave_to_date=data.leave_to_date,
        travel_application_id=data.travel_application_id,
        status="draft",
        created_by=user_id,
    )
    db.add(claim)
    await db.flush()

    if data.claim_type == "EXP":
        for li_data in data.line_items:
            db.add(ExpenseLineItem(claim_id=claim.id, **li_data.model_dump()))
        await db.flush()
        await db.refresh(claim, ["line_items"])
        t, tx, net = _compute_totals_exp(claim.line_items)
        claim.total_amount, claim.tax_amount, claim.net_amount = t, tx, net

    elif data.claim_type == "MIL":
        for ti_data in data.trip_items:
            db.add(ExpenseTripItem(claim_id=claim.id, **ti_data.model_dump()))
        await db.flush()
        await db.refresh(claim, ["trip_items"])
        t, _, net, total_km = _compute_totals_mil(claim.trip_items)
        claim.total_amount = t
        claim.tax_amount = Decimal("0")
        claim.net_amount = net
        claim.total_km = total_km

    elif data.claim_type == "TRV":
        # TRV uses line_items (same as EXP) + meal limit check
        policy = await _get_policy(db)
        meal_limits = {
            "breakfast":   float(policy.meal_breakfast_limit)   if policy else 23.0,
            "lunch":       float(policy.meal_lunch_limit)       if policy else 23.0,
            "dinner":      float(policy.meal_dinner_limit)      if policy else 46.0,
            "incidental":  float(policy.meal_incidental_limit)  if policy else 17.30,
        }
        is_over = False
        for li_data in data.line_items:
            db.add(ExpenseLineItem(claim_id=claim.id, **li_data.model_dump()))
            desc_lower = li_data.description.lower()
            for meal, limit in meal_limits.items():
                if meal in desc_lower and float(li_data.net_amount) > limit:
                    is_over = True
        await db.flush()
        await db.refresh(claim, ["line_items"])
        t, tx, net = _compute_totals_exp(claim.line_items)
        claim.total_amount, claim.tax_amount, claim.net_amount = t, tx, net
        claim.is_over_budget = is_over

    elif data.claim_type.startswith("CFM"):
        # CFM stores line_items as generic entries; no special totals logic
        for li_data in data.line_items:
            db.add(ExpenseLineItem(claim_id=claim.id, **li_data.model_dump()))
        await db.flush()
        await db.refresh(claim, ["line_items"])
        if claim.line_items:
            t, tx, net = _compute_totals_exp(claim.line_items)
            claim.total_amount, claim.tax_amount, claim.net_amount = t, tx, net

    elif data.claim_type == "TRA":
        # Travel Application: no money; persist traveler roster. Totals stay 0.
        for i, tr in enumerate(data.travelers):
            db.add(ExpenseTraveler(
                claim_id=claim.id, user_id=tr.user_id,
                user_name=tr.user_name, seq=tr.seq if tr.seq is not None else i))
        claim.total_amount = Decimal("0.00")
        claim.tax_amount = Decimal("0.00")
        claim.net_amount = Decimal("0.00")
        await db.flush()

    await db.flush()
    await db.refresh(claim, ["line_items", "trip_items", "attachments", "approval_events", "travelers"])
    return claim


async def update_claim(
    db: AsyncSession,
    claim: ExpenseClaim,
    data: ExpenseClaimUpdate,
) -> ExpenseClaim:
    if claim.status not in ("draft", "returned"):
        raise ValueError("Only draft or returned claims can be edited")

    for field in ("submission_date", "currency", "project_id", "notes", "purpose",
                  "vehicle_description", "vehicle_owned_by"):
        val = getattr(data, field, None)
        if val is not None:
            setattr(claim, field, val)

    if data.line_items is not None:
        for existing in list(claim.line_items):
            await db.delete(existing)
        await db.flush()
        for li_data in data.line_items:
            db.add(ExpenseLineItem(claim_id=claim.id, **li_data.model_dump()))
        await db.flush()
        await db.refresh(claim, ["line_items"])
        t, tx, net = _compute_totals_exp(claim.line_items)
        claim.total_amount, claim.tax_amount, claim.net_amount = t, tx, net

    if data.trip_items is not None:
        for existing in list(claim.trip_items):
            await db.delete(existing)
        await db.flush()
        for ti_data in data.trip_items:
            db.add(ExpenseTripItem(claim_id=claim.id, **ti_data.model_dump()))
        await db.flush()
        await db.refresh(claim, ["trip_items"])
        t, _, net, total_km = _compute_totals_mil(claim.trip_items)
        claim.total_amount = t
        claim.tax_amount = Decimal("0")
        claim.net_amount = net
        claim.total_km = total_km

    return claim


async def get_by_id(db: AsyncSession, claim_id: uuid.UUID) -> ExpenseClaim | None:
    # Eager-load relationships so ExpenseClaimResponse serialization (sync attribute
    # access in pydantic) never triggers a lazy load → MissingGreenlet in async SA.
    result = await db.execute(
        select(ExpenseClaim)
        .where(ExpenseClaim.id == claim_id)
        .options(
            selectinload(ExpenseClaim.line_items),
            selectinload(ExpenseClaim.trip_items),
            selectinload(ExpenseClaim.attachments),
            selectinload(ExpenseClaim.approval_events),
            selectinload(ExpenseClaim.travelers),
        )
    )
    return result.scalar_one_or_none()


async def list_claims(
    db: AsyncSession,
    claim_type: str | None = None,
    status: str | None = None,
    employee_id: uuid.UUID | None = None,
    exclude_types: list[str] | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[ExpenseClaim], int]:
    q = select(ExpenseClaim).order_by(ExpenseClaim.created_at.desc())
    if claim_type:
        q = q.where(ExpenseClaim.claim_type == claim_type)
    if exclude_types:
        q = q.where(ExpenseClaim.claim_type.not_in(exclude_types))
    if status:
        q = q.where(ExpenseClaim.status == status)
    if employee_id:
        q = q.where(ExpenseClaim.employee_id == employee_id)

    count_result = await db.execute(
        select(func.count()).select_from(q.subquery())
    )
    total = count_result.scalar_one()

    q = q.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    return list(result.scalars().all()), total


async def count_pending(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count()).where(
            ExpenseClaim.status.in_(["submitted", "in_review"])
        )
    )
    return result.scalar_one()


async def list_eligible_travel_apps(db: AsyncSession, user_id: uuid.UUID) -> list[ExpenseClaim]:
    """Approved TRAs on which `user_id` is a listed traveler (for the TRV picker)."""
    q = (
        select(ExpenseClaim)
        .join(ExpenseTraveler, ExpenseTraveler.claim_id == ExpenseClaim.id)
        .where(ExpenseClaim.claim_type == "TRA",
               ExpenseClaim.status == "approved",
               ExpenseTraveler.user_id == user_id)
        .order_by(ExpenseClaim.travel_from_date.desc().nullslast(),
                  ExpenseClaim.created_at.desc())
    )
    return list((await db.execute(q)).scalars().unique().all())



# NOTE (Phase a A0): after_payment / _book_budget moved to finance-api's
# unified payment executor (payment_execute._book_claim_budget + audit row).
# /pay is a pure forward now — see app/api/v1/expenses.py.
