"""CRUD for Purchase Agreement (AGR)."""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud._numbering import next_number
from app.models.agreement import PurchaseAgreement
from app.schemas.agreement import AgreementCreate, AgreementUpdate

# 只有 draft 可编辑 —— 一旦进入审批,改额度/有效期/供应商必须重走审批(spec §6)。
EDITABLE_STATUSES = ("draft", "returned")


async def _next_number(db: AsyncSession) -> str:
    ym = datetime.now(timezone.utc).strftime("%Y%m")
    return await next_number(db, PurchaseAgreement.number, f"AGR-{ym}-", width=4)


async def get_by_id(db: AsyncSession, agreement_id: uuid.UUID) -> PurchaseAgreement | None:
    return (await db.execute(
        select(PurchaseAgreement).where(PurchaseAgreement.id == agreement_id)
    )).scalar_one_or_none()


async def get_all(
    db: AsyncSession,
    *,
    status: str | None = None,
    vendor_id: uuid.UUID | None = None,
    agreement_type: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[PurchaseAgreement], int]:
    q = select(PurchaseAgreement)
    if status:
        q = q.where(PurchaseAgreement.status == status)
    if vendor_id:
        q = q.where(PurchaseAgreement.vendor_id == vendor_id)
    if agreement_type:
        q = q.where(PurchaseAgreement.agreement_type == agreement_type)
    if search:
        like = f"%{search}%"
        q = q.where(or_(
            PurchaseAgreement.number.ilike(like),
            PurchaseAgreement.title.ilike(like),
            PurchaseAgreement.vendor_name.ilike(like),
            PurchaseAgreement.vendor_reference.ilike(like),
            PurchaseAgreement.contract_no.ilike(like),
        ))

    total = (await db.execute(
        select(func.count()).select_from(q.subquery())
    )).scalar_one()

    rows = (await db.execute(
        q.order_by(PurchaseAgreement.created_at.desc())
         .offset((page - 1) * page_size).limit(page_size)
    )).scalars().all()
    return list(rows), total


async def create(
    db: AsyncSession,
    body: AgreementCreate,
    *,
    vendor_name: str,
    created_by: uuid.UUID,
) -> PurchaseAgreement:
    agr = PurchaseAgreement(
        number=await _next_number(db),
        vendor_name=vendor_name,
        created_by=created_by,
        **body.model_dump(),
    )
    db.add(agr)
    await db.commit()
    await db.refresh(agr)
    return agr


async def update(
    db: AsyncSession, agr: PurchaseAgreement, body: AgreementUpdate
) -> PurchaseAgreement:
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(agr, field, value)
    await db.commit()
    await db.refresh(agr)
    return agr


def _admissible_predicate(today: date):
    """The ONE place agreement admission is expressed: the agreement has been
    approved (status "active", or "expired") AND today is still inside
    `valid_to + grace_days`. Postgres `date - date` is an integer day count,
    so `(today - valid_to) <= grace_days` needs no interval construction.

    ⚠️ 有效期是在这里生效的,不靠任何后台任务。 The date test deliberately
    applies to "active" too, not just "expired". Nothing in this codebase ever
    writes "expired" or "closed" — the only status write anywhere is
    approval-api engine._post_approve_agr setting "active", AgreementUpdate
    has no status field, and there is no scheduler. An earlier form of this
    predicate admitted `status == "active"` unconditionally, which meant
    valid_to and grace_days had no effect on anything: approving an agreement
    produced a permanently open authorisation. With the NTE ceiling being
    warn-only by explicit user decision (spec §7 control #2), that left the
    feature with no enforced limit at all — exactly the "permanently open PO"
    it exists to replace, while the spec rates validity a *strong* control
    (§5.1, §7 control #3). Folding the comparison in here enforces the window
    on every read path with no background job to deploy, monitor or backfill.

    The "expired" arm is retained rather than collapsed into a status-blind
    date test so that a future sweeper CAN still mark agreements expired (for
    reporting / list filters) without changing admission semantics — but
    admissibility never *depends* on a status nothing writes. Statuses that
    are not approvals of spend (draft / in_review / closed / cancelled) stay
    out regardless of dates.

    The grace window exists because a period's statement always arrives after
    the period closes — an agreement expiring 8/31 still has to absorb the
    invoice that lands 9/3 (spec §5.1, E7).

    `valid_from` is intentionally NOT part of the test: 1A exists to clear a
    backlog of already-issued invoices against a back-dated agreement, and the
    approval chain — not the calendar — is what authorises the start.

    Returns a SQLAlchemy ColumnElement, not a Python bool — used directly in
    `candidates_for_vendor`'s multi-row WHERE below AND by `is_admissible`'s
    single-row check, so there is exactly one copy of this rule (code review
    I-3 follow-through: an earlier round had two independent copies — this
    SQL clause and a Python if/elif in is_admissible — that happened to agree
    but had no structural reason to stay in sync). crud/invoice.py's agreement
    match branch calls is_admissible() for the same reason.
    """
    return and_(
        PurchaseAgreement.status.in_(("active", "expired")),
        (literal(today) - PurchaseAgreement.valid_to) <= PurchaseAgreement.grace_days,
    )


async def is_admissible(
    db: AsyncSession, agr: PurchaseAgreement, on_date: date | None = None
) -> bool:
    """Whether NEW spend may be raised against `agr` right now — an invoice
    match, or a Payment Application (Task 7). A targeted single-row query on
    the primary key evaluating `_admissible_predicate`, not a Python
    re-implementation of it — `agr` may be a caller's already-loaded (and
    potentially stale) copy; re-reading through the SAME predicate the list
    query uses is the whole point of factoring it out.
    """
    today = on_date or date.today()
    row = (await db.execute(
        select(PurchaseAgreement.id).where(
            PurchaseAgreement.id == agr.id,
            _admissible_predicate(today),
        )
    )).scalar_one_or_none()
    return row is not None


async def candidates_for_vendor(
    db: AsyncSession, vendor_id: uuid.UUID, on_date: date | None = None
) -> list[PurchaseAgreement]:
    """Agreements an invoice from this vendor may be matched against —
    admission per `_admissible_predicate` (spec §5.1)."""
    today = on_date or date.today()
    q = select(PurchaseAgreement).where(
        PurchaseAgreement.vendor_id == vendor_id,
        _admissible_predicate(today),
    ).order_by(PurchaseAgreement.number)
    return list((await db.execute(q)).scalars().all())
