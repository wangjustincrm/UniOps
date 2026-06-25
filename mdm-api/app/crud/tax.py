"""Tax determination — place-of-supply rule matching (FIN-TAX-002/003).

Matching: active rules in effect on `as_of`, where every criterion is either
NULL (wildcard) or equals the input; highest priority wins; ties broken by
specificity (more non-NULL criteria first). No match → TaxDeterminationError
(explicit 422 upstream — never a silent fallback).
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tax import TaxCode, TaxRule
from app.schemas.tax import TaxCodeCreate, TaxCodeUpdate


class TaxDeterminationError(Exception):
    pass


async def determine(
    db: AsyncSession,
    *,
    direction: str = "any",
    province: str | None = None,
    customer_type: str | None = None,
    item_tax_class: str | None = None,
    as_of: date | None = None,
) -> dict:
    as_of = as_of or date.today()

    q = (
        select(TaxRule)
        .where(
            TaxRule.active.is_(True),
            or_(TaxRule.direction == "any", TaxRule.direction == direction),
            or_(TaxRule.province.is_(None), TaxRule.province == province),
            or_(TaxRule.customer_type.is_(None), TaxRule.customer_type == customer_type),
            or_(TaxRule.item_tax_class.is_(None), TaxRule.item_tax_class == item_tax_class),
            or_(TaxRule.effective_from.is_(None), TaxRule.effective_from <= as_of),
            or_(TaxRule.effective_to.is_(None), TaxRule.effective_to >= as_of),
        )
        .order_by(TaxRule.priority.desc())
    )
    candidates = (await db.execute(q)).scalars().all()
    if not candidates:
        raise TaxDeterminationError(
            f"No tax rule matches direction={direction} province={province} "
            f"customer_type={customer_type} item_tax_class={item_tax_class} — "
            "configure a rule in tax_rules; treatments are never hardcoded"
        )

    def specificity(r: TaxRule) -> int:
        return sum(x is not None for x in (r.province, r.customer_type, r.item_tax_class)) \
            + (r.direction != "any")

    top_priority = candidates[0].priority
    rule = max((r for r in candidates if r.priority == top_priority), key=specificity)

    codes: list[dict] = []
    combined = Decimal("0")
    for code in rule.tax_code_list:
        row = (await db.execute(
            select(TaxCode).where(
                TaxCode.code == code,
                TaxCode.active.is_(True),
                TaxCode.effective_from <= as_of,
                or_(TaxCode.effective_to.is_(None), TaxCode.effective_to >= as_of),
            ).order_by(TaxCode.effective_from.desc())
        )).scalars().first()
        if row is None:
            raise TaxDeterminationError(
                f"Rule {rule.id} references tax code '{code}' with no rate effective on {as_of}"
            )
        codes.append({
            "code": row.code, "tax_type": row.tax_type, "rate": row.rate,
            "recoverable": row.recoverable, "name": row.name,
        })
        combined += row.rate

    return {"rule_id": rule.id, "codes": codes, "combined_rate": combined}


async def list_codes(db: AsyncSession, as_of: date | None = None) -> list[TaxCode]:
    as_of = as_of or date.today()
    q = (
        select(TaxCode)
        .where(
            TaxCode.active.is_(True),
            TaxCode.effective_from <= as_of,
            or_(TaxCode.effective_to.is_(None), TaxCode.effective_to >= as_of),
        )
        .order_by(TaxCode.code)
    )
    return list((await db.execute(q)).scalars().all())


# ── Admin CRUD (Finance Tax Settings) ────────────────────────────────────────
# list_codes() above is the consumer/dropdown view (active, effective-on-date).
# The functions below are the management view: every row, every version.

async def list_all_codes(db: AsyncSession) -> list[TaxCode]:
    q = select(TaxCode).order_by(TaxCode.code, TaxCode.effective_from.desc())
    return list((await db.execute(q)).scalars().all())


async def get_code_by_id(db: AsyncSession, code_id: uuid.UUID) -> TaxCode | None:
    return (await db.execute(
        select(TaxCode).where(TaxCode.id == code_id)
    )).scalar_one_or_none()


async def get_code_version(db: AsyncSession, code: str, effective_from: date) -> TaxCode | None:
    """Look up the exact rate-version row (enforces the uq_tax_codes_code_from key)."""
    return (await db.execute(
        select(TaxCode).where(
            TaxCode.code == code,
            TaxCode.effective_from == effective_from,
        )
    )).scalar_one_or_none()


async def create_code(db: AsyncSession, payload: TaxCodeCreate) -> TaxCode:
    row = TaxCode(
        code=payload.code,
        name=payload.name,
        tax_type=payload.tax_type,
        province=payload.province,
        rate=payload.rate,
        recoverable=payload.recoverable,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        active=payload.active,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def update_code(db: AsyncSession, row: TaxCode, payload: TaxCodeUpdate) -> TaxCode:
    # exclude_unset so an omitted field is "leave alone" while an explicit
    # null (province / effective_to) clears it.
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    await db.flush()
    await db.refresh(row)
    return row


async def deactivate_code(db: AsyncSession, row: TaxCode) -> TaxCode:
    """Soft delete: keep the row so documents that snapshotted it stay valid."""
    row.active = False
    await db.flush()
    await db.refresh(row)
    return row
