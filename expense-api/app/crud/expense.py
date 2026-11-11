"""CRUD + approval workflow for expense claims (EXP / MIL)."""
import uuid
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# NOTE: BudgetAccount mirror removed — expense-api no longer writes directly to
# budget_accounts. Actual spend is now reported to budget-api via /book-expense.
from app.core.plant_time import plant_today
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
    # The plant's date, not UTC (an evening claim in Toronto is still today).
    # next_number takes max tail + 1 per prefix, so the switch cannot collide.
    today = plant_today().strftime("%Y%m%d")
    return await next_number(db, ExpenseClaim.claim_number, f"{claim_type}-{today}-", width=4)


# ── Helpers ────────────────────────────────────────────────────────────────────

# OA EXP-002: a general expense claim has up to 20 lines. The form stops at 20,
# but the server took any number (ISS-0199). Only EXP: the PRD sets no line cap
# for TRV, MIL uses trip_items, and custom-form claims carry no lines.
MAX_EXP_LINES = 20


def _check_exp_line_count(claim_type: str, line_items) -> None:
    if claim_type == "EXP" and line_items is not None and len(line_items) > MAX_EXP_LINES:
        raise ValueError(
            f"A general expense claim can have at most {MAX_EXP_LINES} lines "
            f"(this one has {len(line_items)}).")


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


# Fallbacks match the seeded ExpensePolicyConfig defaults — used only when no
# policy row exists at all (fresh install, test DB).
_MEAL_FALLBACKS = {"breakfast": 23.0, "lunch": 23.0, "dinner": 46.0, "incidental": 17.30}


async def _trv_over_meal_limit(db: AsyncSession, line_items) -> bool:
    """Does any line name a meal and exceed that meal's per-diem cap?

    Shared by create and update. It used to be inline in create only, so a TRV
    could be raised inside the limits, returned, edited UP past them, and
    resubmitted with is_over_budget still False — which matters because
    over-limit is what injects the conditional Finance Manager step (PRD
    WF-002). Accepts either the inbound schema objects or the persisted rows;
    both expose .description and .net_amount.
    """
    limits = await _meal_limits(db)
    for li in line_items:
        meal = _meal_of(li, limits)
        # A period line's net is days x rate; the cap is per day.
        if meal and float(_per_day(li)) > limits[meal]:
            return True
    return False


async def _meal_limits(db: AsyncSession) -> dict[str, float]:
    policy = await _get_policy(db)
    return {
        meal: float(getattr(policy, f"meal_{meal}_limit")) if policy else default
        for meal, default in _MEAL_FALLBACKS.items()
    }


def _meal_of(li, limits: dict[str, float]) -> str | None:
    desc = (li.description or "").lower()
    return next((meal for meal in limits if meal in desc), None)


def _per_day(li) -> Decimal:
    return li.daily_rate if li.period_from is not None else li.net_amount


def _is_abroad(country: str | None) -> bool:
    return country is not None and country != "CA"


async def _check_trv_lines(db: AsyncSession, from_date, to_date, country, currency,
                           line_items) -> None:
    """Rules for a TRV's lines that need the policy or the claim header.

    Raises ValueError (the routes answer 409/422 with the message). Run on
    the inbound values before anything is written, on create and on every
    edit — an edit that only moves the trip dates must re-check the periods.

    - A period line's daily rate is HARD-capped at the meal/incidental limit
      (business rule 2026-10-16). Single-day lines keep the soft rule: flagged
      over-limit, not refused (_trv_over_meal_limit).
    - A period must sit inside the trip's From/To dates.
    - Abroad (travel_country other than CA) lines carry no Canadian sales tax
      and no tax code. In Canada the claimant picks the code per line.
    - TRV is claimed in CAD only; the claimant converts foreign receipts
      (business rule 2026-10-16).
    """
    if currency != "CAD":
        raise ValueError(
            f"travel expense claims are in CAD only (got {currency}); "
            "convert foreign-currency amounts to CAD")
    if country is not None and (len(country) != 2 or not country.isalpha() or not country.isupper()):
        raise ValueError(f"travel_country must be a two-letter upper-case country code, got {country!r}")
    limits = await _meal_limits(db)
    for li in line_items:
        if li.period_from is not None:
            meal = _meal_of(li, limits)
            if meal and float(li.daily_rate) > limits[meal]:
                raise ValueError(
                    f"line {li.line_number}: daily rate {li.daily_rate} exceeds the "
                    f"{meal} limit {limits[meal]:.2f}")
            if (from_date and li.period_from < from_date) or (to_date and li.period_to > to_date):
                raise ValueError(
                    f"line {li.line_number}: period {li.period_from} – {li.period_to} "
                    f"is outside the trip dates {from_date} – {to_date}")
        if _is_abroad(country) and (li.tax_amount != 0 or li.tax_code):
            raise ValueError(
                f"line {li.line_number}: travel outside Canada carries no sales tax "
                f"(tax_amount {li.tax_amount}, tax_code {li.tax_code})")


async def create_claim(
    db: AsyncSession,
    data: ExpenseClaimCreate,
    user_id: uuid.UUID,
    user_name: str,
    department_id: uuid.UUID | None,
    department_name: str,
    prepared_by: uuid.UUID | None = None,
) -> ExpenseClaim:
    """`user_id` is the claimant. `prepared_by` is who filed it, when that is
    someone else (a TRV filed on a traveler's behalf)."""
    _check_exp_line_count(data.claim_type, data.line_items)
    if data.claim_type == "TRV":
        await _check_trv_lines(db, data.travel_from_date, data.travel_to_date,
                               data.travel_country, data.currency, data.line_items)
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
        travel_country=data.travel_country,
        transport_modes=data.transport_modes,
        leave_from_date=data.leave_from_date,
        leave_to_date=data.leave_to_date,
        travel_application_id=data.travel_application_id,
        status="draft",
        created_by=prepared_by or user_id,
    )
    db.add(claim)
    await db.flush()

    if data.claim_type == "EXP":
        for li_data in data.line_items:
            db.add(ExpenseLineItem(claim_id=claim.id, **li_data.model_dump(exclude={"id"})))
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
        for li_data in data.line_items:
            db.add(ExpenseLineItem(claim_id=claim.id, **li_data.model_dump(exclude={"id"})))
        await db.flush()
        await db.refresh(claim, ["line_items"])
        t, tx, net = _compute_totals_exp(claim.line_items)
        claim.total_amount, claim.tax_amount, claim.net_amount = t, tx, net
        claim.is_over_budget = await _trv_over_meal_limit(db, claim.line_items)

    elif data.claim_type.startswith("CFM"):
        # CFM stores line_items as generic entries; no special totals logic
        for li_data in data.line_items:
            db.add(ExpenseLineItem(claim_id=claim.id, **li_data.model_dump(exclude={"id"})))
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


def may_delete_attachments(claim: ExpenseClaim, caller: uuid.UUID, role: str) -> bool:
    """Who may delete a claim's attachments: the claimant, whoever filed it for
    them, or System Admin (ATT-OA-002). The draft/returned status rule is
    checked separately."""
    return claim.employee_id == caller or is_preparer(claim, caller) or role == "system_admin"


def lines_removed_by(data: ExpenseClaimUpdate, old_ids: set[uuid.UUID],
                     claim: ExpenseClaim) -> set[uuid.UUID]:
    """Ids of the claim's lines that an edit took away.

    Only answered for a client that sends line ids back (an `id` key on its
    lines, even null — today's OA forms). One that does not would make every
    line look removed and new, so for it, and for an edit that did not touch
    the lines, nothing counts as removed.
    """
    if data.line_items is None or not any("id" in li.model_fields_set for li in data.line_items):
        return set()
    return old_ids - {li.id for li in claim.line_items}


async def scans_of_lines(db: AsyncSession, claim_id: uuid.UUID,
                         line_ids: set[uuid.UUID]) -> list[ExpenseAttachment]:
    """Receipts scanned on the given lines (source = scan). Manually attached
    files and pre-existing rows (source NULL) are never in this list."""
    if not line_ids:
        return []
    res = await db.execute(select(ExpenseAttachment).where(
        ExpenseAttachment.claim_id == claim_id,
        ExpenseAttachment.source == "scan",
        ExpenseAttachment.line_item_id.in_(line_ids),
    ))
    return list(res.scalars())


async def update_claim(
    db: AsyncSession,
    claim: ExpenseClaim,
    data: ExpenseClaimUpdate,
) -> ExpenseClaim:
    if claim.status not in ("draft", "returned"):
        raise ValueError("Only draft or returned claims can be edited")
    _check_exp_line_count(claim.claim_type, data.line_items)

    if claim.claim_type == "TRV":
        def eff(field):
            val = getattr(data, field, None)
            return val if val is not None else getattr(claim, field)
        await _check_trv_lines(
            db, eff("travel_from_date"), eff("travel_to_date"), eff("travel_country"),
            eff("currency"),
            data.line_items if data.line_items is not None else claim.line_items)

    # Every scalar field ExpenseClaimUpdate accepts is applied here. The list
    # used to stop after vehicle_owned_by, so the travel fields — which the
    # schema takes and the client sends — were parsed, validated, and silently
    # dropped: a returned TRV or TRA could not have its dates, destination or
    # transport corrected, and the save reported success.
    for field in ("submission_date", "currency", "project_id", "notes", "purpose",
                  "vehicle_description", "vehicle_owned_by",
                  "travel_from_date", "travel_to_date", "travel_destination",
                  "travel_country", "transport_modes", "leave_from_date", "leave_to_date",
                  "travel_application_id"):
        val = getattr(data, field, None)
        if val is not None:
            setattr(claim, field, val)

    # Traveller roster (TRA). Replace wholesale, like the line/trip collections:
    # an empty list is a legitimate edit ("I removed everyone"), which is why
    # this tests `is not None` rather than truthiness.
    if data.travelers is not None:
        for existing in list(claim.travelers):
            await db.delete(existing)
        await db.flush()
        for i, tr in enumerate(data.travelers):
            db.add(ExpenseTraveler(
                claim_id=claim.id, user_id=tr.user_id, user_name=tr.user_name,
                seq=tr.seq if tr.seq is not None else i))
        await db.flush()
        await db.refresh(claim, ["travelers"])

    # Only a MIL claim has mileage (user, 2026-10-14). The OA form sends a
    # general expense with `trip_items: []`; when both branches ran, that empty
    # trip list re-computed the claim as mileage — total 0.00, total_km 0 —
    # over the totals the lines had just produced (EXP-20260929-0001). So each
    # collection is applied only to the claim type it belongs to, and a non-MIL
    # claim never carries a km figure.
    is_mil = claim.claim_type == "MIL"
    if not is_mil:
        claim.total_km = None

    if data.line_items is not None and not is_mil:
        # Lines are replaced wholesale, but a line the client sends back with its
        # saved id keeps that id, so what hangs off it — the receipt scanned on
        # it (ATT-OA-005, ISS-0197) — still finds it. An id that is not one of
        # this claim's lines, or is sent twice, gets a fresh one.
        old_ids = {li.id for li in claim.line_items}
        for existing in list(claim.line_items):
            await db.delete(existing)
        await db.flush()
        reused: set[uuid.UUID] = set()
        for li_data in data.line_items:
            row = ExpenseLineItem(claim_id=claim.id, **li_data.model_dump(exclude={"id"}))
            if li_data.id in old_ids and li_data.id not in reused:
                row.id = li_data.id
                reused.add(li_data.id)
            db.add(row)
        await db.flush()
        await db.refresh(claim, ["line_items"])
        t, tx, net = _compute_totals_exp(claim.line_items)
        claim.total_amount, claim.tax_amount, claim.net_amount = t, tx, net
        # Recompute the over-limit flag on edit, not just on create — see
        # _trv_over_meal_limit. Only TRV carries meal per-diems.
        if claim.claim_type == "TRV":
            claim.is_over_budget = await _trv_over_meal_limit(db, claim.line_items)

    if data.trip_items is not None and is_mil:
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
    order_by: list | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[ExpenseClaim], int]:
    """`order_by` = ORDER BY clauses for a click-sorted list; None keeps the
    default newest-first order."""
    q = select(ExpenseClaim).order_by(*(order_by or [ExpenseClaim.created_at.desc()]))
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


async def delete_claim(db: AsyncSession, claim: ExpenseClaim) -> dict[str, int]:
    """Hard-delete a claim and everything hanging off it. The CALLER commits.

    `tasks` and `approval_events` are approval-api's polymorphic tables keyed by
    document_id with no FK to expense_claims — without an explicit purge the
    claim vanishes while its approve task lives on in approvers' inboxes. Line
    items, trip items, travelers, attachments and expense_approval_events go via
    ON DELETE CASCADE.
    """
    from app.admin.cascade import purge_shared_refs  # local: avoids crud↔admin import cycle

    refs = await purge_shared_refs(db, claim.id)
    await db.delete(claim)
    await db.flush()
    return {"expense_claims": 1, **refs}


async def count_pending(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count()).where(
            ExpenseClaim.status.in_(["submitted", "in_review"])
        )
    )
    return result.scalar_one()


def is_preparer(claim: ExpenseClaim, user_id: uuid.UUID) -> bool:
    """Whether `user_id` filed this TRV on a traveler's behalf.

    A TRV's claimant is always a traveler, the person paid; the TRA's applicant
    may not be one (TRA-20261102-0001), so `created_by` can differ from
    `employee_id`. TRV only: no other claim type is filed on behalf, so this
    grants nothing on those.
    """
    return claim.claim_type == "TRV" and claim.created_by == user_id


def traveler_claim_ids(user_id: uuid.UUID):
    """Subquery of the TRAs that list `user_id` as a traveler."""
    return select(ExpenseTraveler.claim_id).where(ExpenseTraveler.user_id == user_id)


async def is_listed_traveler(db: AsyncSession, claim_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    q = select(func.count()).select_from(ExpenseTraveler).where(
        ExpenseTraveler.claim_id == claim_id, ExpenseTraveler.user_id == user_id)
    return (await db.execute(q)).scalar_one() > 0


async def list_eligible_travel_apps(db: AsyncSession, user_id: uuid.UUID) -> list[ExpenseClaim]:
    """Approved TRAs `user_id` travels on or applied for (for the TRV picker)."""
    q = (
        select(ExpenseClaim)
        .options(selectinload(ExpenseClaim.travelers))
        .where(ExpenseClaim.claim_type == "TRA",
               ExpenseClaim.status == "approved",
               or_(ExpenseClaim.employee_id == user_id,
                   ExpenseClaim.id.in_(traveler_claim_ids(user_id))))
        .order_by(ExpenseClaim.travel_from_date.desc().nullslast(),
                  ExpenseClaim.created_at.desc())
    )
    return list((await db.execute(q)).scalars().all())



# NOTE (Phase a A0): after_payment / _book_budget moved to finance-api's
# unified payment executor (payment_execute._book_claim_budget + audit row).
# /pay is a pure forward now — see app/api/v1/expenses.py.
