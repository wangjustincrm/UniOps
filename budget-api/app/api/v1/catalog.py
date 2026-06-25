"""Catalog endpoints — L1 + Account CRUD + CSV import/export."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status

from app.core.deps import CurrentUserPayload, SessionDep, require_roles
from app.crud import catalog as catalog_crud
from app.schemas.catalog import (
    BudgetAccountCreate, BudgetAccountResponse, BudgetAccountUpdate,
    BudgetL1Create, BudgetL1Response, BudgetL1Update, BudgetL1WithAccountsResponse,
    CatalogImportResult,
)

router = APIRouter(tags=["catalog"])

_WRITE_ROLES = ("system_admin", "finance_manager", "finance_bp")


# ── L1 ────────────────────────────────────────────────────────────────────────

@router.get("/l1", response_model=list[BudgetL1WithAccountsResponse | BudgetL1Response])
async def list_l1(
    db: SessionDep,
    user: CurrentUserPayload,  # noqa: ARG001
    is_active: bool | None = Query(default=None),
    include_accounts: bool = Query(default=False),
):
    items = await catalog_crud.list_l1(db, is_active=is_active)
    if include_accounts:
        return [BudgetL1WithAccountsResponse.model_validate(l1) for l1 in items]
    return [BudgetL1Response.model_validate(l1) for l1 in items]


@router.get("/l1/{l1_id}", response_model=BudgetL1WithAccountsResponse)
async def get_l1(
    l1_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload,  # noqa: ARG001
):
    l1 = await catalog_crud.get_l1(db, l1_id)
    if l1 is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "L1 not found")
    return BudgetL1WithAccountsResponse.model_validate(l1)


@router.post("/l1", response_model=BudgetL1Response, status_code=status.HTTP_201_CREATED)
async def create_l1(
    payload: BudgetL1Create, db: SessionDep,
    user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    actor_id = uuid.UUID(user["sub"])
    l1 = await catalog_crud.create_l1(db, payload, actor_id)
    return BudgetL1Response.model_validate(l1)


@router.patch("/l1/{l1_id}", response_model=BudgetL1Response)
async def update_l1(
    l1_id: uuid.UUID, payload: BudgetL1Update, db: SessionDep,
    user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    l1 = await catalog_crud.get_l1(db, l1_id)
    if l1 is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "L1 not found")
    actor_id = uuid.UUID(user["sub"])
    l1 = await catalog_crud.update_l1(db, l1, payload, actor_id)
    return BudgetL1Response.model_validate(l1)


@router.delete("/l1/{l1_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_l1(
    l1_id: uuid.UUID, db: SessionDep,
    _: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    l1 = await catalog_crud.get_l1(db, l1_id)
    if l1 is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "L1 not found")
    await catalog_crud.delete_l1(db, l1)


# ── Account ───────────────────────────────────────────────────────────────────

@router.get("/accounts", response_model=list[BudgetAccountResponse])
async def list_accounts(
    db: SessionDep, user: CurrentUserPayload,  # noqa: ARG001
    l1_id: uuid.UUID | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    decomposition_enabled: bool | None = Query(default=None),
):
    items = await catalog_crud.list_accounts(
        db, l1_id=l1_id, is_active=is_active, decomposition_enabled=decomposition_enabled,
    )
    return [BudgetAccountResponse.model_validate(a) for a in items]


@router.get("/accounts/{account_id}", response_model=BudgetAccountResponse)
async def get_account(
    account_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload,  # noqa: ARG001
):
    acct = await catalog_crud.get_account(db, account_id)
    if acct is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    return BudgetAccountResponse.model_validate(acct)


@router.post("/accounts", response_model=BudgetAccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: BudgetAccountCreate, db: SessionDep,
    user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    actor_id = uuid.UUID(user["sub"])
    acct = await catalog_crud.create_account(db, payload, actor_id)
    return BudgetAccountResponse.model_validate(acct)


@router.patch("/accounts/{account_id}", response_model=BudgetAccountResponse)
async def update_account(
    account_id: uuid.UUID, payload: BudgetAccountUpdate, db: SessionDep,
    user: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    acct = await catalog_crud.get_account(db, account_id)
    if acct is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    actor_id = uuid.UUID(user["sub"])
    acct = await catalog_crud.update_account(db, acct, payload, actor_id)
    return BudgetAccountResponse.model_validate(acct)


@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: uuid.UUID, db: SessionDep,
    _: dict = Depends(require_roles(*_WRITE_ROLES)),
):
    acct = await catalog_crud.get_account(db, account_id)
    if acct is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    await catalog_crud.delete_account(db, acct)


# ── CSV import/export ─────────────────────────────────────────────────────────

@router.post("/catalog/import", response_model=CatalogImportResult)
async def import_catalog(
    file: UploadFile, db: SessionDep,
    user: dict = Depends(require_roles("system_admin")),
):
    content = await file.read()
    csv_text = content.decode("utf-8-sig")
    actor_id = uuid.UUID(user["sub"])
    return await catalog_crud.import_catalog_csv(db, csv_text, actor_id)


@router.get("/catalog/export")
async def export_catalog(db: SessionDep, user: CurrentUserPayload):  # noqa: ARG001
    csv_text = await catalog_crud.export_catalog_csv(db)
    return Response(
        content=csv_text, media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=budget-catalog.csv"},
    )
