"""CRUD for decomposition factors, factor values, and the library templates."""
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.factor import (
    BudgetAccountFactor, BudgetAccountFactorValue,
    FactorTemplate, FactorTemplateValue,
)
from app.models.plan import BudgetPlanBreakdown, BudgetPlanLine
from app.schemas.factor import (
    FactorCreate, FactorUpdate, FactorValueCreate, FactorValueUpdate,
    FactorTemplateCreate, FactorTemplateUpdate,
    FactorTemplateValueCreate, FactorTemplateValueUpdate,
    FactorFromTemplate,
)


# Hard cap — the new Matrix editor (Primary=Rows × 2nd=Cols × 3rd=SubCols)
# assumes ≤3 factors per Account. The `max_factors_per_account` setting and
# the passed-in `max_allowed` param are clamped to this ceiling.
MAX_FACTORS_PER_ACCOUNT = 3


# ── Factor ────────────────────────────────────────────────────────────────────

async def list_factors_for_account(
    db: AsyncSession, account_id: uuid.UUID,
) -> list[BudgetAccountFactor]:
    result = await db.execute(
        select(BudgetAccountFactor)
        .where(BudgetAccountFactor.account_id == account_id)
        .order_by(BudgetAccountFactor.sort_order, BudgetAccountFactor.factor_code)
    )
    return list(result.scalars().all())


async def get_factor(db: AsyncSession, factor_id: uuid.UUID) -> BudgetAccountFactor | None:
    return await db.get(BudgetAccountFactor, factor_id)


async def count_factors_for_account(db: AsyncSession, account_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count(BudgetAccountFactor.id))
        .where(BudgetAccountFactor.account_id == account_id)
    )
    return int(result.scalar_one() or 0)


async def create_factor(
    db: AsyncSession, account_id: uuid.UUID, payload: FactorCreate, *, max_allowed: int,
) -> BudgetAccountFactor:
    # Clamp to the hard ceiling; legacy settings >3 are ignored.
    effective_max = min(max_allowed, MAX_FACTORS_PER_ACCOUNT)
    existing_count = await count_factors_for_account(db, account_id)
    if existing_count >= effective_max:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Max {MAX_FACTORS_PER_ACCOUNT} factors per Account "
            "(Primary × 2nd × 3rd = Rows × Columns × Sub-Columns). "
            "Remove or deactivate an existing factor first.",
        )
    existing = await db.execute(
        select(BudgetAccountFactor).where(
            BudgetAccountFactor.account_id == account_id,
            BudgetAccountFactor.factor_code == payload.factor_code,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Factor '{payload.factor_code}' already exists for this account")

    factor = BudgetAccountFactor(
        account_id=account_id,
        factor_code=payload.factor_code,
        factor_name=payload.factor_name,
        sort_order=payload.sort_order,
    )
    db.add(factor)
    await db.flush()
    for v in payload.values:
        db.add(BudgetAccountFactorValue(
            factor_id=factor.id,
            value_code=v.value_code, value_name=v.value_name, sort_order=v.sort_order,
        ))
    await db.flush()
    await db.refresh(factor)
    return factor


async def update_factor(
    db: AsyncSession, factor: BudgetAccountFactor, payload: FactorUpdate,
) -> BudgetAccountFactor:
    for field in ("factor_name", "sort_order", "is_active"):
        val = getattr(payload, field)
        if val is not None:
            setattr(factor, field, val)
    await db.flush()
    await db.refresh(factor)
    return factor


async def delete_factor(db: AsyncSession, factor: BudgetAccountFactor) -> None:
    """Block delete if any plan_breakdown references this factor (by factor_code in JSONB)."""
    # Check breakdowns referencing this factor_code across plan_lines of the parent account
    ref_q = (
        select(func.count(BudgetPlanBreakdown.id))
        .join(BudgetPlanLine, BudgetPlanBreakdown.plan_line_id == BudgetPlanLine.id)
        .where(
            BudgetPlanLine.account_id == factor.account_id,
            BudgetPlanBreakdown.factor_combo.has_key(factor.factor_code),  # noqa: W601
        )
    )
    result = await db.execute(ref_q)
    if int(result.scalar_one() or 0) > 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Factor has plan_breakdowns referencing it; cannot delete. Mark is_active=false instead.",
        )
    await db.delete(factor)
    await db.flush()


# ── Factor Value ──────────────────────────────────────────────────────────────

async def get_factor_value(db: AsyncSession, value_id: uuid.UUID) -> BudgetAccountFactorValue | None:
    return await db.get(BudgetAccountFactorValue, value_id)


async def create_factor_value(
    db: AsyncSession, factor_id: uuid.UUID, payload: FactorValueCreate,
) -> BudgetAccountFactorValue:
    factor = await get_factor(db, factor_id)
    if factor is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Factor not found")
    existing = await db.execute(
        select(BudgetAccountFactorValue).where(
            BudgetAccountFactorValue.factor_id == factor_id,
            BudgetAccountFactorValue.value_code == payload.value_code,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Value '{payload.value_code}' already exists")
    v = BudgetAccountFactorValue(
        factor_id=factor_id,
        value_code=payload.value_code, value_name=payload.value_name, sort_order=payload.sort_order,
    )
    db.add(v)
    await db.flush()
    await db.refresh(v)
    return v


async def update_factor_value(
    db: AsyncSession, value: BudgetAccountFactorValue, payload: FactorValueUpdate,
) -> BudgetAccountFactorValue:
    for field in ("value_name", "sort_order", "is_active"):
        val = getattr(payload, field)
        if val is not None:
            setattr(value, field, val)
    await db.flush()
    await db.refresh(value)
    return value


async def delete_factor_value(db: AsyncSession, value: BudgetAccountFactorValue) -> None:
    """Soft delete only (set is_active=false). Plan breakdowns may reference this value historically."""
    value.is_active = False
    await db.flush()


# ── Factor Library — Templates ────────────────────────────────────────────────

async def list_templates(db: AsyncSession, *, active_only: bool = False) -> list[FactorTemplate]:
    q = select(FactorTemplate)
    if active_only:
        q = q.where(FactorTemplate.is_active.is_(True))
    q = q.order_by(FactorTemplate.factor_code)
    return list((await db.execute(q)).scalars().all())


async def get_template(db: AsyncSession, template_id: uuid.UUID) -> FactorTemplate | None:
    return await db.get(FactorTemplate, template_id)


async def create_template(db: AsyncSession, payload: FactorTemplateCreate) -> FactorTemplate:
    existing = await db.execute(
        select(FactorTemplate).where(FactorTemplate.factor_code == payload.factor_code)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Factor template '{payload.factor_code}' already exists")
    tmpl = FactorTemplate(
        factor_code=payload.factor_code,
        factor_name=payload.factor_name,
        description=payload.description,
    )
    db.add(tmpl)
    await db.flush()
    for v in payload.values:
        db.add(FactorTemplateValue(
            template_id=tmpl.id,
            value_code=v.value_code, value_name=v.value_name, sort_order=v.sort_order,
        ))
    await db.flush()
    await db.refresh(tmpl)
    return tmpl


async def update_template(
    db: AsyncSession, tmpl: FactorTemplate, payload: FactorTemplateUpdate,
) -> FactorTemplate:
    for field in ("factor_name", "description", "is_active"):
        val = getattr(payload, field)
        if val is not None:
            setattr(tmpl, field, val)
    await db.flush()
    await db.refresh(tmpl)
    return tmpl


async def delete_template(db: AsyncSession, tmpl: FactorTemplate) -> None:
    """Hard delete. Library templates are pure presets — per-Account factors that
    were copied from this template are NOT affected, so this is always safe."""
    await db.delete(tmpl)
    await db.flush()


# ── Factor Library — Template Values ──────────────────────────────────────────

async def get_template_value(db: AsyncSession, value_id: uuid.UUID) -> FactorTemplateValue | None:
    return await db.get(FactorTemplateValue, value_id)


async def create_template_value(
    db: AsyncSession, template_id: uuid.UUID, payload: FactorTemplateValueCreate,
) -> FactorTemplateValue:
    tmpl = await get_template(db, template_id)
    if tmpl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Factor template not found")
    existing = await db.execute(
        select(FactorTemplateValue).where(
            FactorTemplateValue.template_id == template_id,
            FactorTemplateValue.value_code == payload.value_code,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Value '{payload.value_code}' already exists")
    v = FactorTemplateValue(
        template_id=template_id,
        value_code=payload.value_code, value_name=payload.value_name, sort_order=payload.sort_order,
    )
    db.add(v)
    await db.flush()
    await db.refresh(v)
    return v


async def update_template_value(
    db: AsyncSession, value: FactorTemplateValue, payload: FactorTemplateValueUpdate,
) -> FactorTemplateValue:
    for field in ("value_name", "sort_order", "is_active"):
        val = getattr(payload, field)
        if val is not None:
            setattr(value, field, val)
    await db.flush()
    await db.refresh(value)
    return value


async def delete_template_value(db: AsyncSession, value: FactorTemplateValue) -> None:
    """Hard delete is OK — template values are independent of any per-Account copies."""
    await db.delete(value)
    await db.flush()


# ── Clone template → per-Account factor ───────────────────────────────────────

async def create_factor_from_template(
    db: AsyncSession,
    account_id: uuid.UUID,
    payload: FactorFromTemplate,
    *,
    max_allowed: int,
) -> BudgetAccountFactor:
    """Copy a library template into per-Account factor + values.

    factor_code/factor_name optionally override the template defaults; only
    active template values are copied (is_active=True). The resulting Account
    factor has no FK back to the template — later template edits do not
    propagate.
    """
    tmpl = await get_template(db, payload.template_id)
    if tmpl is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Factor template not found")
    if not tmpl.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Factor template is inactive; reactivate it before attaching")

    effective_code = (payload.factor_code or tmpl.factor_code).strip()
    effective_name = (payload.factor_name or tmpl.factor_name).strip()

    effective_max = min(max_allowed, MAX_FACTORS_PER_ACCOUNT)
    existing_count = await count_factors_for_account(db, account_id)
    if existing_count >= effective_max:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Max {MAX_FACTORS_PER_ACCOUNT} factors per Account "
            "(Primary × 2nd × 3rd = Rows × Columns × Sub-Columns). "
            "Remove or deactivate an existing factor first.",
        )
    existing = await db.execute(
        select(BudgetAccountFactor).where(
            BudgetAccountFactor.account_id == account_id,
            BudgetAccountFactor.factor_code == effective_code,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Factor '{effective_code}' already exists for this account",
        )

    factor = BudgetAccountFactor(
        account_id=account_id,
        factor_code=effective_code,
        factor_name=effective_name,
        sort_order=payload.sort_order,
    )
    db.add(factor)
    await db.flush()
    for v in tmpl.values:
        if not v.is_active:
            continue
        db.add(BudgetAccountFactorValue(
            factor_id=factor.id,
            value_code=v.value_code,
            value_name=v.value_name,
            sort_order=v.sort_order,
        ))
    await db.flush()
    await db.refresh(factor)
    return factor
