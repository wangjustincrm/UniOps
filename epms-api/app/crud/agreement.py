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


def is_admissible(agr: PurchaseAgreement, on_date: date | None = None) -> bool:
    """Whether NEW spend may be raised against this agreement right now — an
    invoice match, or (Task 7) a Payment Application.

    Same admission rule as `candidates_for_vendor`'s SQL predicate below:
    status == "active", OR status == "expired" and still inside its grace
    window. That query is a multi-row WHERE clause (can't call this function
    per-row without breaking to Python-side filtering); this is the single-row
    Python-side twin for call sites that already hold one loaded row (PA
    creation). ⚠️ SIBLING COPY — keep this rule in lock-step with
    candidates_for_vendor if either changes (see access_scope.py's
    "SIBLING COPY" convention for the same situation elsewhere in this repo).
    """
    today = on_date or date.today()
    if agr.status == "active":
        return True
    if agr.status == "expired":
        return (today - agr.valid_to).days <= agr.grace_days
    return False


async def candidates_for_vendor(
    db: AsyncSession, vendor_id: uuid.UUID, on_date: date | None = None
) -> list[PurchaseAgreement]:
    """Agreements an invoice from this vendor may be matched against.

    Admission (spec §5.1): status == "active", OR status == "expired" and today
    is still within valid_to + grace_days. The grace branch exists because a
    period's statement always arrives after the period closes — an agreement
    expiring 8/31 still has to absorb the invoice that lands on 9/3.

    The grace predicate is written as an integer day difference rather than an
    interval addition: in Postgres `date - date` yields an integer number of
    days, so `(today - valid_to) <= grace_days` needs no interval construction
    and reads the same as the rule it encodes.
    """
    today = on_date or date.today()
    q = select(PurchaseAgreement).where(
        PurchaseAgreement.vendor_id == vendor_id,
        or_(
            PurchaseAgreement.status == "active",
            and_(
                PurchaseAgreement.status == "expired",
                (literal(today) - PurchaseAgreement.valid_to) <= PurchaseAgreement.grace_days,
            ),
        ),
    ).order_by(PurchaseAgreement.number)
    return list((await db.execute(q)).scalars().all())
