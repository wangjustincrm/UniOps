"""CRUD for shared catalog (L1 + Account)."""
import csv
import io
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import BudgetAccount, BudgetL1
from app.models.factor import BudgetAccountFactor, BudgetAccountFactorValue
from app.models.ledger import BudgetLedger
from app.models.plan import BudgetPlanLine
from app.schemas.catalog import (
    BudgetAccountCreate, BudgetAccountUpdate,
    BudgetL1Create, BudgetL1Update, CatalogImportResult,
)


# ── L1 ────────────────────────────────────────────────────────────────────────

async def list_l1(
    db: AsyncSession, *, is_active: bool | None = None,
) -> list[BudgetL1]:
    q = select(BudgetL1).order_by(BudgetL1.sort_order, BudgetL1.code)
    if is_active is not None:
        q = q.where(BudgetL1.is_active.is_(is_active))
    result = await db.execute(q)
    return list(result.scalars().all())


async def get_l1(db: AsyncSession, l1_id: uuid.UUID) -> BudgetL1 | None:
    return await db.get(BudgetL1, l1_id)


async def get_l1_by_code(db: AsyncSession, code: str) -> BudgetL1 | None:
    result = await db.execute(select(BudgetL1).where(BudgetL1.code == code))
    return result.scalar_one_or_none()


async def create_l1(
    db: AsyncSession, payload: BudgetL1Create, actor_id: uuid.UUID,
) -> BudgetL1:
    existing = await get_l1_by_code(db, payload.code)
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, f"L1 code '{payload.code}' already exists")
    l1 = BudgetL1(
        code=payload.code, name=payload.name, description=payload.description,
        sort_order=payload.sort_order, created_by=actor_id, updated_by=actor_id,
    )
    db.add(l1)
    await db.flush()
    await db.refresh(l1)
    return l1


async def update_l1(
    db: AsyncSession, l1: BudgetL1, payload: BudgetL1Update, actor_id: uuid.UUID,
) -> BudgetL1:
    for field in ("name", "description", "sort_order", "is_active"):
        val = getattr(payload, field)
        if val is not None:
            setattr(l1, field, val)
    l1.updated_by = actor_id
    # Cascade active-state to all child accounts: deactivating an L1 deactivates
    # every L2 under it; reactivating an L1 reactivates them.
    if payload.is_active is not None:
        await db.execute(
            update(BudgetAccount)
            .where(BudgetAccount.l1_id == l1.id)
            .values(is_active=payload.is_active, updated_by=actor_id)
        )
    await db.flush()
    await db.refresh(l1)
    return l1


async def _accounts_referenced(db: AsyncSession, account_ids: list[uuid.UUID]) -> bool:
    """True if any of these accounts appears in a budget_plan_line or budget_ledger."""
    if not account_ids:
        return False
    plan_refs = await db.execute(
        select(func.count(BudgetPlanLine.id)).where(BudgetPlanLine.account_id.in_(account_ids))
    )
    if plan_refs.scalar_one() > 0:
        return True
    ledger_refs = await db.execute(
        select(func.count(BudgetLedger.id)).where(BudgetLedger.account_id.in_(account_ids))
    )
    return ledger_refs.scalar_one() > 0


async def _purge_account(db: AsyncSession, acct: BudgetAccount) -> None:
    """Physically remove an account and its factor config (FKs are RESTRICT, so the
    factor values + factors must be deleted before the account)."""
    factor_ids = (
        await db.execute(
            select(BudgetAccountFactor.id).where(BudgetAccountFactor.account_id == acct.id)
        )
    ).scalars().all()
    if factor_ids:
        await db.execute(
            delete(BudgetAccountFactorValue).where(BudgetAccountFactorValue.factor_id.in_(factor_ids))
        )
        await db.execute(
            delete(BudgetAccountFactor).where(BudgetAccountFactor.account_id == acct.id)
        )
    await db.delete(acct)


async def delete_l1(db: AsyncSession, l1: BudgetL1) -> None:
    """Hard delete an L1 and every L2 account under it.

    Refused (409) if any child account is referenced by budget plans or actuals —
    deactivate the L1 instead (which also deactivates its accounts).
    """
    accts = (
        await db.execute(select(BudgetAccount).where(BudgetAccount.l1_id == l1.id))
    ).scalars().all()
    if await _accounts_referenced(db, [a.id for a in accts]):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "L1 contains accounts referenced by budget plans or actuals — "
            "deactivate it instead of deleting.",
        )
    for a in accts:
        await _purge_account(db, a)
    await db.flush()
    await db.delete(l1)
    await db.flush()


# ── Account ───────────────────────────────────────────────────────────────────

async def list_accounts(
    db: AsyncSession, *,
    l1_id: uuid.UUID | None = None,
    is_active: bool | None = None,
    decomposition_enabled: bool | None = None,
) -> list[BudgetAccount]:
    q = select(BudgetAccount).order_by(BudgetAccount.sort_order, BudgetAccount.code)
    if l1_id is not None:
        q = q.where(BudgetAccount.l1_id == l1_id)
    if is_active is not None:
        q = q.where(BudgetAccount.is_active.is_(is_active))
    if decomposition_enabled is not None:
        q = q.where(BudgetAccount.decomposition_enabled.is_(decomposition_enabled))
    result = await db.execute(q)
    return list(result.scalars().all())


async def get_account(db: AsyncSession, account_id: uuid.UUID) -> BudgetAccount | None:
    return await db.get(BudgetAccount, account_id)


async def get_account_by_code_and_l1(
    db: AsyncSession, code: str, l1_id: uuid.UUID,
) -> BudgetAccount | None:
    result = await db.execute(
        select(BudgetAccount).where(
            BudgetAccount.code == code,
            BudgetAccount.l1_id == l1_id,
        )
    )
    return result.scalar_one_or_none()


async def get_account_by_code(db: AsyncSession, code: str) -> BudgetAccount | None:
    """Lookup by global code (assumes account codes are unique across L1s when used cross-service)."""
    result = await db.execute(
        select(BudgetAccount).where(BudgetAccount.code == code).limit(1)
    )
    return result.scalar_one_or_none()


async def create_account(
    db: AsyncSession, payload: BudgetAccountCreate, actor_id: uuid.UUID,
) -> BudgetAccount:
    l1 = await get_l1(db, payload.l1_id)
    if l1 is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"L1 {payload.l1_id} not found")
    existing = await get_account_by_code_and_l1(db, payload.code, payload.l1_id)
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Account code '{payload.code}' already exists in L1 '{l1.code}'",
        )
    acct = BudgetAccount(
        code=payload.code, name=payload.name, description=payload.description,
        l1_id=payload.l1_id, sort_order=payload.sort_order,
        decomposition_enabled=payload.decomposition_enabled,
        created_by=actor_id, updated_by=actor_id,
    )
    db.add(acct)
    await db.flush()
    await db.refresh(acct)
    return acct


async def update_account(
    db: AsyncSession, acct: BudgetAccount, payload: BudgetAccountUpdate, actor_id: uuid.UUID,
) -> BudgetAccount:
    for field in ("name", "description", "sort_order", "is_active", "decomposition_enabled"):
        val = getattr(payload, field)
        if val is not None:
            setattr(acct, field, val)
    acct.updated_by = actor_id
    await db.flush()
    await db.refresh(acct)
    return acct


async def delete_account(db: AsyncSession, acct: BudgetAccount) -> None:
    """Hard delete an account that was never referenced by budget data.

    "Referenced" = appears in any budget_plan_line or budget_ledger row. If so,
    deletion is refused (409) — the account should be deactivated instead. Only
    truly unused accounts are physically removed.
    """
    if await _accounts_referenced(db, [acct.id]):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Account is referenced by budget plans or actuals — deactivate it instead of deleting.",
        )
    await _purge_account(db, acct)
    await db.flush()


# ── CSV Import / Export ───────────────────────────────────────────────────────

async def import_catalog_csv(
    db: AsyncSession, csv_text: str, actor_id: uuid.UUID,
) -> CatalogImportResult:
    """Import L1 + Account catalog from CSV.

    Format: code, name, l1_code, l1_name, decomposition_enabled
    - rows where l1_code blank → L1 row
    - rows with l1_code → Account row under that L1
    Upsert by code.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    result = CatalogImportResult(l1_created=0, l1_updated=0, accounts_created=0, accounts_updated=0)

    # Process L1 rows first
    rows = list(reader)
    for row in rows:
        if not row.get("l1_code"):
            code = (row.get("code") or "").strip()
            name = (row.get("name") or "").strip()
            if not code or not name:
                continue
            existing = await get_l1_by_code(db, code)
            if existing:
                existing.name = name
                existing.updated_by = actor_id
                result.l1_updated += 1
            else:
                db.add(BudgetL1(code=code, name=name, created_by=actor_id, updated_by=actor_id))
                result.l1_created += 1

    await db.flush()

    # Then Account rows
    for row in rows:
        l1_code = (row.get("l1_code") or "").strip()
        if not l1_code:
            continue
        code = (row.get("code") or "").strip()
        name = (row.get("name") or "").strip()
        decomp = (row.get("decomposition_enabled") or "").strip().lower() in ("true", "1", "yes")
        if not code or not name:
            continue
        l1 = await get_l1_by_code(db, l1_code)
        if l1 is None:
            result.errors.append(f"Account '{code}' references unknown L1 '{l1_code}'")
            continue
        existing = await get_account_by_code_and_l1(db, code, l1.id)
        if existing:
            existing.name = name
            existing.decomposition_enabled = decomp
            existing.updated_by = actor_id
            result.accounts_updated += 1
        else:
            db.add(BudgetAccount(
                code=code, name=name, l1_id=l1.id,
                decomposition_enabled=decomp,
                created_by=actor_id, updated_by=actor_id,
            ))
            result.accounts_created += 1

    await db.flush()
    return result


async def export_catalog_csv(db: AsyncSession) -> str:
    """Export L1 + Accounts to CSV. Format matches import."""
    l1s = await list_l1(db)
    accts = await list_accounts(db)
    l1_by_id: dict[Any, BudgetL1] = {l1.id: l1 for l1 in l1s}

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["code", "name", "l1_code", "l1_name", "decomposition_enabled"])
    writer.writeheader()
    for l1 in l1s:
        writer.writerow({"code": l1.code, "name": l1.name, "l1_code": "", "l1_name": "", "decomposition_enabled": ""})
    for a in accts:
        l1 = l1_by_id.get(a.l1_id)
        writer.writerow({
            "code": a.code, "name": a.name,
            "l1_code": l1.code if l1 else "", "l1_name": l1.name if l1 else "",
            "decomposition_enabled": "true" if a.decomposition_enabled else "false",
        })
    return buf.getvalue()
