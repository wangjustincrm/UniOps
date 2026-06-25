"""Catalog lifecycle: L1 active-state cascade + hard-delete guard.

  - Deactivating/reactivating an L1 cascades is_active to all its L2 accounts.
  - An account is hard-deletable only if it has no budget_plan_line / budget_ledger
    references; otherwise delete is refused (409) and it must be deactivated.
"""
import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.crud import catalog as catalog_crud
from app.models.catalog import BudgetAccount, BudgetL1
from app.models.factor import BudgetAccountFactor, BudgetAccountFactorValue
from app.models.ledger import BudgetLedger
from app.models.plan import BudgetPlan, BudgetPlanLine
from app.schemas.catalog import BudgetL1Update

pytestmark = pytest.mark.asyncio

CC = uuid.uuid4()
FY = 2026


async def _seed(db, *, n_accounts=2, active=True):
    l1 = BudgetL1(code="L1", name="Marketing", sort_order=0, is_active=True)
    db.add(l1)
    await db.flush()
    accts = []
    for i in range(n_accounts):
        a = BudgetAccount(
            code=f"A{i}", name=f"Acct {i}", l1_id=l1.id, sort_order=i, is_active=active,
        )
        db.add(a)
        accts.append(a)
    await db.flush()
    return l1, accts


# ── L1 cascade ────────────────────────────────────────────────────────────────

async def test_deactivate_l1_cascades_accounts_inactive(db_session):
    db = db_session
    l1, accts = await _seed(db, n_accounts=3, active=True)

    await catalog_crud.update_l1(db, l1, BudgetL1Update(is_active=False), uuid.uuid4())

    for a in accts:
        await db.refresh(a)
        assert a.is_active is False
    assert l1.is_active is False


async def test_reactivate_l1_cascades_accounts_active(db_session):
    db = db_session
    l1, accts = await _seed(db, n_accounts=2, active=False)
    l1.is_active = False
    await db.flush()

    await catalog_crud.update_l1(db, l1, BudgetL1Update(is_active=True), uuid.uuid4())

    for a in accts:
        await db.refresh(a)
        assert a.is_active is True


async def test_update_l1_without_is_active_leaves_accounts(db_session):
    db = db_session
    l1, accts = await _seed(db, n_accounts=2, active=True)
    # Editing only the name must NOT touch child active-state.
    await catalog_crud.update_l1(db, l1, BudgetL1Update(name="Renamed"), uuid.uuid4())
    for a in accts:
        await db.refresh(a)
        assert a.is_active is True


# ── Delete guard ──────────────────────────────────────────────────────────────

async def test_delete_unreferenced_account_removes_it(db_session):
    db = db_session
    _l1, accts = await _seed(db, n_accounts=1)
    acct = accts[0]
    await catalog_crud.delete_account(db, acct)
    assert await catalog_crud.get_account(db, acct.id) is None


async def test_delete_account_with_plan_line_blocked(db_session):
    db = db_session
    _l1, accts = await _seed(db, n_accounts=1)
    acct = accts[0]
    plan = BudgetPlan(
        cost_center_id=CC, fiscal_year=FY, status="draft",
        is_current=True, version=1, created_by=uuid.uuid4(),
    )
    db.add(plan)
    await db.flush()
    db.add(BudgetPlanLine(plan_id=plan.id, account_id=acct.id, month=1, amount=Decimal("10")))
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await catalog_crud.delete_account(db, acct)
    assert exc.value.status_code == 409
    assert await catalog_crud.get_account(db, acct.id) is not None


# ── L1 hard delete (cascade to accounts) ──────────────────────────────────────

async def test_delete_l1_cascades_unreferenced_accounts(db_session):
    db = db_session
    l1, accts = await _seed(db, n_accounts=3)
    acct_ids = [a.id for a in accts]
    await catalog_crud.delete_l1(db, l1)
    assert await catalog_crud.get_l1(db, l1.id) is None
    for aid in acct_ids:
        assert await catalog_crud.get_account(db, aid) is None


async def test_delete_l1_blocked_when_any_account_referenced(db_session):
    db = db_session
    l1, accts = await _seed(db, n_accounts=2)
    plan = BudgetPlan(
        cost_center_id=CC, fiscal_year=FY, status="draft",
        is_current=True, version=1, created_by=uuid.uuid4(),
    )
    db.add(plan)
    await db.flush()
    db.add(BudgetPlanLine(plan_id=plan.id, account_id=accts[1].id, month=1, amount=Decimal("10")))
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await catalog_crud.delete_l1(db, l1)
    assert exc.value.status_code == 409
    # Nothing deleted.
    assert await catalog_crud.get_l1(db, l1.id) is not None
    for a in accts:
        assert await catalog_crud.get_account(db, a.id) is not None


async def test_delete_l1_purges_account_factors(db_session):
    db = db_session
    l1, accts = await _seed(db, n_accounts=1)
    acct = accts[0]
    factor = BudgetAccountFactor(account_id=acct.id, factor_code="brand", factor_name="Brand", sort_order=0)
    db.add(factor)
    await db.flush()
    db.add(BudgetAccountFactorValue(factor_id=factor.id, value_code="A", value_name="Brand A", sort_order=0))
    await db.flush()

    # Factor RESTRICT FKs must not block deletion of an unreferenced account.
    await catalog_crud.delete_l1(db, l1)
    assert await catalog_crud.get_l1(db, l1.id) is None
    assert await catalog_crud.get_account(db, acct.id) is None


async def test_delete_account_with_ledger_blocked(db_session):
    db = db_session
    _l1, accts = await _seed(db, n_accounts=1)
    acct = accts[0]
    db.add(BudgetLedger(
        source_service="expense", source_doc_type="claim", source_doc_id=uuid.uuid4(),
        operation="book_expense", cost_center_id=CC, account_id=acct.id,
        fiscal_year=FY, month=1, amount=Decimal("25"),
    ))
    await db.flush()

    with pytest.raises(HTTPException) as exc:
        await catalog_crud.delete_account(db, acct)
    assert exc.value.status_code == 409
    assert await catalog_crud.get_account(db, acct.id) is not None
