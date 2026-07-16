"""Chart of Accounts + account mappings API (Phase a A1).

COA rows and mappings are data (IFRS template seeded by migration 0005);
finance maintains them here. Mapping upserts feed the executor's
account_code stamping — they change how money is classified, so writes are
role-gated and audited later via the identity audit pattern (A2).
"""
import csv
import io
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.coa import AccountMapping, AuxDimensionType, ChartOfAccount

router = APIRouter(prefix="/coa", tags=["chart-of-accounts"])

_MANAGE_ROLES = {"system_admin", "finance_manager"}
_ACCOUNT_TYPES = {"asset", "liability", "equity", "revenue", "expense"}


async def _can_manage(db: AsyncSession, user: dict) -> bool:
    """finance_manager is a company-unique singleton POST (identity enforces
    one holder) — if it's your PRIMARY role you genuinely hold it, so PRIMARY
    (jwt.role) UNION ADDITIONAL (user_roles) both qualify.

    finance_bp is NOT a singleton post — it's a job FUNCTION many people
    carry (identity exempts it from the singleton index for exactly that
    reason), not an assignment. Reading jwt.role/primary role for finance_bp
    would silently promote every employee whose primary role happens to be
    finance_bp into a COA manager, even if they were never added to the
    assignment list — mirrors approval-api's _post_holders split
    (workflow.py) for the same doctrine. So finance_bp must resolve from
    user_roles ONLY, never from the primary role."""
    if user.get("role") in _MANAGE_ROLES:
        return True
    from app.crud.payment_execute import _user_role_codes
    uid = uuid.UUID(str(user.get("sub", "")))
    codes = await _user_role_codes(db, uid, user.get("role", ""))
    if "finance_manager" in codes:
        return True
    from sqlalchemy import text
    row = (await db.execute(text(
        "SELECT 1 FROM user_roles WHERE user_id = :u AND role_code = 'finance_bp'"),
        {"u": str(uid)})).first()
    return row is not None


async def _require_manage(db: AsyncSession, user: dict) -> None:
    if not await _can_manage(db, user):
        raise HTTPException(status_code=403, detail="Insufficient role to manage the chart of accounts")


class AuxDim(BaseModel):
    code: str
    required: bool = False  # required = mandatory on postings; default optional


def _coerce_aux(value: list) -> list["AuxDim"]:
    """Accept both bare codes and {code, required} objects."""
    out: list[AuxDim] = []
    for v in value or []:
        out.append(AuxDim(code=v) if isinstance(v, str) else AuxDim.model_validate(v))
    return out


async def _valid_aux_codes(db: AsyncSession) -> set[str]:
    rows = (await db.execute(
        select(AuxDimensionType.code).where(AuxDimensionType.is_active.is_(True))
    )).scalars().all()
    return set(rows)


def _validate_aux(aux: list, valid_codes: set[str]) -> list[dict]:
    dims = _coerce_aux(aux)
    bad = {d.code for d in dims} - valid_codes
    if bad:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown aux dimensions {sorted(bad)}; allowed: {sorted(valid_codes)}",
        )
    seen: dict[str, dict] = {}
    for d in dims:  # dedupe by code, later entries win
        seen[d.code] = {"code": d.code, "required": d.required}
    return list(seen.values())


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    account_type: str
    subtype: str | None
    normal_balance: str
    is_postable: bool
    parent_code: str | None
    is_active: bool
    aux_dimensions: list[AuxDim] = []
    quantity_accounting: bool = False
    default_uom: str | None = None
    default_currency: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    cash_flow_category: str | None = None
    mnemonic: str | None = None
    is_off_balance: bool = False


class AccountUpsert(BaseModel):
    code: str = Field(min_length=1, max_length=10)
    name: str = Field(min_length=1, max_length=255)
    account_type: str
    normal_balance: str
    subtype: str | None = None
    is_postable: bool = True
    parent_code: str | None = None
    is_active: bool = True
    aux_dimensions: list = []   # bare codes or {code, required} — normalized by _validate_aux
    quantity_accounting: bool = False
    default_uom: str | None = None
    default_currency: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    cash_flow_category: str | None = None
    mnemonic: str | None = None
    is_off_balance: bool = False


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    account_type: str | None = None
    normal_balance: str | None = None
    subtype: str | None = None
    is_postable: bool | None = None
    parent_code: str | None = None
    is_active: bool | None = None
    aux_dimensions: list | None = None
    quantity_accounting: bool | None = None
    default_uom: str | None = None
    default_currency: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    cash_flow_category: str | None = None
    mnemonic: str | None = None
    is_off_balance: bool | None = None


class ImportResult(BaseModel):
    inserted: int
    updated: int
    errors: list[str] = []


_CSV_COLUMNS = ["code", "name", "account_type", "normal_balance", "subtype",
                "parent_code", "is_postable", "is_active", "aux_dimensions",
                "quantity_accounting", "default_uom", "default_currency",
                "effective_from", "effective_to", "cash_flow_category",
                "mnemonic", "is_off_balance"]


def _validate_upsert_fields(account_type: str | None, normal_balance: str | None) -> None:
    if account_type is not None and account_type not in _ACCOUNT_TYPES:
        raise HTTPException(status_code=422, detail=f"account_type must be one of {sorted(_ACCOUNT_TYPES)}")
    if normal_balance is not None and normal_balance not in ("debit", "credit"):
        raise HTTPException(status_code=422, detail="normal_balance must be debit or credit")


class MappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    mapping_type: str
    source_code: str
    account_code: str


class MappingUpsert(BaseModel):
    account_code: str = Field(min_length=1, max_length=10)


class CoaPermissions(BaseModel):
    can_manage: bool


@router.get("/permissions", response_model=CoaPermissions)
async def coa_permissions(
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Server-side capability resolution (JWT roles ∪ ADDITIONAL roles held in
    identity's user_roles) — the UI gates its write actions on this, never on
    jwt.role."""
    return CoaPermissions(can_manage=await _can_manage(db, user))


@router.get("", response_model=list[AccountOut])
async def list_accounts(
    _: CurrentUser,
    db: AsyncSession = Depends(get_db),
    account_type: str | None = Query(default=None),
    postable_only: bool = Query(default=False),
    include_inactive: bool = Query(default=False),
):
    q = select(ChartOfAccount)
    if not include_inactive:
        q = q.where(ChartOfAccount.is_active.is_(True))
    if account_type:
        q = q.where(ChartOfAccount.account_type == account_type)
    if postable_only:
        q = q.where(ChartOfAccount.is_postable.is_(True))
    return (await db.execute(q.order_by(ChartOfAccount.code))).scalars().all()


@router.post("", response_model=AccountOut, status_code=201)
async def create_account(
    body: AccountUpsert,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    await _require_manage(db, user)
    _validate_upsert_fields(body.account_type, body.normal_balance)
    aux = _validate_aux(body.aux_dimensions, await _valid_aux_codes(db))
    exists = (await db.execute(
        select(ChartOfAccount).where(ChartOfAccount.code == body.code)
    )).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail=f"Account '{body.code}' already exists")
    row = ChartOfAccount(**{**body.model_dump(), "aux_dimensions": aux})
    db.add(row)
    await db.flush()
    await db.commit()
    return row


@router.patch("/{code}", response_model=AccountOut)
async def update_account(
    code: str,
    body: AccountPatch,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    await _require_manage(db, user)
    _validate_upsert_fields(body.account_type, body.normal_balance)
    row = (await db.execute(
        select(ChartOfAccount).where(ChartOfAccount.code == code)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Account '{code}' not found")
    data = body.model_dump(exclude_unset=True)
    if "aux_dimensions" in data and data["aux_dimensions"] is not None:
        data["aux_dimensions"] = _validate_aux(data["aux_dimensions"], await _valid_aux_codes(db))
    for k, v in data.items():
        setattr(row, k, v)
    await db.flush()
    await db.commit()
    return row


@router.get("/export")
async def export_accounts(
    _: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """CSV export (system-wide import/export convention) — same columns the
    importer accepts, so export → edit in Excel → import round-trips."""
    rows = (await db.execute(
        select(ChartOfAccount).order_by(ChartOfAccount.code)
    )).scalars().all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_COLUMNS)
    for a in rows:
        writer.writerow([
            a.code, a.name, a.account_type, a.normal_balance,
            a.subtype or "", a.parent_code or "",
            "true" if a.is_postable else "false",
            "true" if a.is_active else "false",
            # dim* = required, dim = optional (e.g. "partner*|item")
            "|".join(d["code"] + ("*" if d.get("required") else "")
                     for d in (a.aux_dimensions or [])),
            "true" if a.quantity_accounting else "false",
            a.default_uom or "", a.default_currency or "",
            a.effective_from.isoformat() if a.effective_from else "",
            a.effective_to.isoformat() if a.effective_to else "",
            a.cash_flow_category or "", a.mnemonic or "",
            "true" if a.is_off_balance else "false",
        ])
    return Response(
        content="﻿" + buf.getvalue(),  # BOM for Excel
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=chart-of-accounts.csv"},
    )


@router.post("/import", response_model=ImportResult)
async def import_accounts(
    file: UploadFile,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """CSV file upsert by code (system-wide import/export convention) — the
    entry point for loading an existing COA (e.g. the NC65 chart) wholesale.
    Existing codes are updated, new codes inserted, nothing deleted.
    Invalid rows are skipped and reported in `errors`."""
    await _require_manage(db, user)
    content = await file.read()
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    required = {"code", "name", "account_type", "normal_balance"}
    if not reader.fieldnames or not required.issubset({f.strip() for f in reader.fieldnames}):
        raise HTTPException(
            status_code=422,
            detail=f"CSV header must include {sorted(required)}; full columns: {_CSV_COLUMNS}",
        )

    def _bool(v: str | None, default: bool) -> bool:
        if v is None or v.strip() == "":
            return default
        return v.strip().lower() not in ("false", "0", "no")

    def _d(v: str | None) -> date | None:
        v = (v or "").strip()
        return date.fromisoformat(v) if v else None

    valid_codes = await _valid_aux_codes(db)
    inserted = updated = 0
    errors: list[str] = []
    for n, raw in enumerate(reader, start=2):  # header is line 1
        try:
            r = AccountUpsert(
                code=(raw.get("code") or "").strip(),
                name=(raw.get("name") or "").strip(),
                account_type=(raw.get("account_type") or "").strip().lower(),
                normal_balance=(raw.get("normal_balance") or "").strip().lower(),
                subtype=(raw.get("subtype") or "").strip() or None,
                parent_code=(raw.get("parent_code") or "").strip() or None,
                is_postable=_bool(raw.get("is_postable"), True),
                is_active=_bool(raw.get("is_active"), True),
                aux_dimensions=[
                    {"code": d.strip().rstrip("*"), "required": d.strip().endswith("*")}
                    for d in (raw.get("aux_dimensions") or "").split("|") if d.strip()
                ],
                quantity_accounting=_bool(raw.get("quantity_accounting"), False),
                default_uom=(raw.get("default_uom") or "").strip() or None,
                default_currency=(raw.get("default_currency") or "").strip() or None,
                effective_from=_d(raw.get("effective_from")),
                effective_to=_d(raw.get("effective_to")),
                cash_flow_category=(raw.get("cash_flow_category") or "").strip() or None,
                mnemonic=(raw.get("mnemonic") or "").strip() or None,
                is_off_balance=_bool(raw.get("is_off_balance"), False),
            )
            _validate_upsert_fields(r.account_type, r.normal_balance)
            aux = _validate_aux(r.aux_dimensions, valid_codes)
        except HTTPException as exc:
            errors.append(f"Line {n}: {exc.detail}")
            continue
        except Exception as exc:  # pydantic validation
            errors.append(f"Line {n}: {exc}")
            continue

        row = (await db.execute(
            select(ChartOfAccount).where(ChartOfAccount.code == r.code)
        )).scalar_one_or_none()
        if row is None:
            db.add(ChartOfAccount(**{**r.model_dump(), "aux_dimensions": aux}))
            inserted += 1
        else:
            for k, v in r.model_dump().items():
                setattr(row, k, v if k != "aux_dimensions" else aux)
            updated += 1
    await db.flush()
    await db.commit()
    return ImportResult(inserted=inserted, updated=updated, errors=errors)


@router.delete("/{code}", status_code=204)
async def delete_account(
    code: str,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    """Hard-delete an account that has never been referenced. Referenced or
    parent accounts must be deactivated instead (history is immutable)."""
    await _require_manage(db, user)
    row = (await db.execute(
        select(ChartOfAccount).where(ChartOfAccount.code == code)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Account '{code}' not found")

    from sqlalchemy import func
    from app.models.posting import PostingLine

    postings = (await db.execute(
        select(func.count()).select_from(PostingLine).where(PostingLine.account_code == code)
    )).scalar_one()
    mappings = (await db.execute(
        select(func.count()).select_from(AccountMapping).where(AccountMapping.account_code == code)
    )).scalar_one()
    children = (await db.execute(
        select(func.count()).select_from(ChartOfAccount).where(ChartOfAccount.parent_code == code)
    )).scalar_one()
    reasons = []
    if postings:
        reasons.append(f"{postings} posting line(s)")
    if mappings:
        reasons.append(f"{mappings} account mapping(s)")
    if children:
        reasons.append(f"{children} child account(s)")
    if reasons:
        raise HTTPException(
            status_code=409,
            detail=f"Account '{code}' is referenced by {', '.join(reasons)} — deactivate it instead",
        )
    await db.delete(row)
    await db.flush()
    await db.commit()


# ── auxiliary dimension types (辅助核算项目录, A1.7) ─────────────────────────────

class AuxTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    master_source: str | None
    storage: str
    builtin: bool
    is_active: bool
    sort_order: int


class AuxTypeIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=255)
    master_source: str | None = None
    is_active: bool = True
    sort_order: int = 100
    # new custom types always store in the side table; built-in column dims are seeded
    storage: str = "aux_table"


@router.get("/aux-types", response_model=list[AuxTypeOut])
async def list_aux_types(
    _: CurrentUser,
    db: AsyncSession = Depends(get_db),
    include_inactive: bool = Query(default=False),
):
    q = select(AuxDimensionType)
    if not include_inactive:
        q = q.where(AuxDimensionType.is_active.is_(True))
    return (await db.execute(
        q.order_by(AuxDimensionType.sort_order, AuxDimensionType.code)
    )).scalars().all()


@router.post("/aux-types", response_model=AuxTypeOut, status_code=201)
async def create_aux_type(body: AuxTypeIn, user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    if body.storage not in ("aux_table", "column"):
        raise HTTPException(status_code=422, detail="storage must be aux_table or column")
    exists = (await db.execute(
        select(AuxDimensionType).where(AuxDimensionType.code == body.code)
    )).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail=f"Aux dimension '{body.code}' already exists")
    row = AuxDimensionType(**body.model_dump(), builtin=False)
    db.add(row)
    await db.flush()
    await db.commit()
    return row


@router.patch("/aux-types/{code}", response_model=AuxTypeOut)
async def update_aux_type(code: str, body: dict, user: CurrentUser,
                          db: AsyncSession = Depends(get_db)):
    """Rename / reorder / activate-deactivate. Built-in `code`/`storage` are
    immutable (they map to real spine columns); only name/sort_order/is_active."""
    await _require_manage(db, user)
    row = (await db.execute(
        select(AuxDimensionType).where(AuxDimensionType.code == code)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Aux dimension '{code}' not found")
    for k in ("name", "master_source", "is_active", "sort_order"):
        if k in body and body[k] is not None:
            setattr(row, k, body[k])
    await db.flush()
    await db.commit()
    return row


@router.get("/mappings", response_model=list[MappingOut])
async def list_mappings(
    _: CurrentUser,
    db: AsyncSession = Depends(get_db),
    mapping_type: str | None = Query(default=None),
):
    q = select(AccountMapping)
    if mapping_type:
        q = q.where(AccountMapping.mapping_type == mapping_type)
    return (await db.execute(q.order_by(AccountMapping.mapping_type,
                                        AccountMapping.source_code))).scalars().all()


@router.put("/mappings/{mapping_type}/{source_code}", response_model=MappingOut)
async def upsert_mapping(
    mapping_type: str,
    source_code: str,
    body: MappingUpsert,
    user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    await _require_manage(db, user)
    if mapping_type not in ("line_role", "budget_account"):
        raise HTTPException(status_code=422, detail="mapping_type must be line_role or budget_account")

    account = (await db.execute(
        select(ChartOfAccount).where(
            ChartOfAccount.code == body.account_code,
            ChartOfAccount.is_active.is_(True),
        )
    )).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail=f"Account '{body.account_code}' not found")
    if not account.is_postable:
        raise HTTPException(status_code=422, detail=f"Account '{body.account_code}' is a header — not postable")

    row = (await db.execute(
        select(AccountMapping).where(
            AccountMapping.mapping_type == mapping_type,
            AccountMapping.source_code == source_code,
        )
    )).scalar_one_or_none()
    if row is None:
        row = AccountMapping(mapping_type=mapping_type, source_code=source_code,
                             account_code=body.account_code)
        db.add(row)
    else:
        row.account_code = body.account_code
    await db.flush()
    await db.commit()
    return row
