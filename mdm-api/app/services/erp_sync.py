"""Sync ERP master data into local mirror tables."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.erp_material import ErpMaterial
from app.models.erp_supplier import ErpSupplier
from app.models.erp_person import ErpPerson
from app.models.erp_sync_state import ErpSyncState
from app.services.erp_client import ErpClient, ErpError


_EPOCH = datetime(1900, 1, 1, tzinfo=timezone.utc)


Kind = Literal["material", "supplier", "person"]


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    # ERP rowversion is sometimes 'yyyyMMddHHmmss' (compact, no separators).
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _lookup(rec: dict, *keys: str) -> Any:
    """Return the first present value, matching key case-insensitively."""
    lower = {k.lower(): v for k, v in rec.items()}
    for k in keys:
        if k in rec:
            return rec[k]
        if k.lower() in lower:
            return lower[k.lower()]
    return None


def _to_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _map_material(rec: dict) -> dict:
    part_no = _lookup(rec, "part_NO", "part_no")
    if part_no in (None, ""):
        raise KeyError("part_no")
    return {
        "erp_part_no": str(part_no),
        "description": _to_str(_lookup(rec, "description")),
        "unit_meas": _to_str(_lookup(rec, "unit_MEAS", "unit_meas")),
        "dim_quality": _to_str(_lookup(rec, "dim_QUALITY", "dim_quality")),
        "weight_net": _to_decimal(_lookup(rec, "weight_NET", "weight_net")),
        "weight_gross": _to_decimal(_lookup(rec, "weight_GROSS", "weight_gross")),
        "volume": _to_decimal(_lookup(rec, "volume")),
        "part_status": _to_str(_lookup(rec, "part_STATUS", "part_status")),
        "item_mes_type": _to_str(_lookup(rec, "itemMESType", "itemmestype")),
        "raw_payload": rec,
        "erp_rowversion": _parse_dt(_lookup(rec, "rowversion", "modifiedtime")),
    }


def _map_supplier(rec: dict) -> dict:
    code = _lookup(rec, "suppliercode")
    if code in (None, ""):
        raise KeyError("suppliercode")
    return {
        "erp_supplier_code": str(code),
        "supplier_name": _to_str(_lookup(rec, "suppliername")) or "",
        "supplier_address": _to_str(_lookup(rec, "supplieraddress")),
        "supplier_tel": _to_str(_lookup(rec, "suppliertel")),
        "supplier_fax": _to_str(_lookup(rec, "supplierfax")),
        "supplier_type": _to_str(_lookup(rec, "suppliertype")),
        "raw_payload": rec,
        "erp_rowversion": _parse_dt(_lookup(rec, "rowversion", "modifiedtime")),
    }


def _map_person(rec: dict) -> dict:
    code = _lookup(rec, "personcode")
    if code in (None, ""):
        raise KeyError("personcode")
    return {
        "erp_person_code": str(code),
        "person_name": _to_str(_lookup(rec, "personname")) or "",
        "company_code": _to_str(_lookup(rec, "companycode")),
        "company_name": _to_str(_lookup(rec, "companyname")),
        "department_code": _to_str(_lookup(rec, "departmentcode")),
        "department_name": _to_str(_lookup(rec, "departmentname")),
        "is_valid": str(_lookup(rec, "isvalid") or "1") == "1",
        "pk_psndoc": _to_str(_lookup(rec, "pk_psndoc")),
        "raw_payload": rec,
        "erp_rowversion": _parse_dt(_lookup(rec, "modifiedtime", "rowversion")),
    }


_KIND_CONFIG: dict[str, dict] = {
    "material": {
        "model": ErpMaterial,
        "unique_col": "erp_part_no",
        "mapper": _map_material,
        "fetch_attr": "fetch_materials",
    },
    "supplier": {
        "model": ErpSupplier,
        "unique_col": "erp_supplier_code",
        "mapper": _map_supplier,
        "fetch_attr": "fetch_suppliers",
    },
    "person": {
        "model": ErpPerson,
        "unique_col": "erp_person_code",
        "mapper": _map_person,
        "fetch_attr": "fetch_persons",
    },
}


async def _write_state(
    db: AsyncSession, *, kind: str, status: str, message: str,
    last_ts: datetime | None, row_count: int,
) -> None:
    now = datetime.now(timezone.utc)
    stmt = pg_insert(ErpSyncState).values(
        kind=kind,
        last_ts=last_ts,
        last_synced_at=now,
        last_status=status,
        last_message=message,
        last_row_count=row_count,
        updated_at=now,
    ).on_conflict_do_update(
        index_elements=["kind"],
        set_=dict(
            last_ts=last_ts,
            last_synced_at=now,
            last_status=status,
            last_message=message,
            last_row_count=row_count,
            updated_at=now,
        ),
    )
    await db.execute(stmt)


async def sync_kind(
    db: AsyncSession,
    kind: Kind,
    *,
    full: bool = False,
    client: ErpClient | None = None,
) -> dict:
    cfg = _KIND_CONFIG[kind]

    state = await db.get(ErpSyncState, kind)
    if full or state is None or state.last_ts is None:
        ts = _EPOCH
        mode = "full"
    else:
        ts = state.last_ts
        mode = "incremental"

    # Fetch from ERP
    try:
        if client is None:
            async with ErpClient() as c:
                records = await getattr(c, cfg["fetch_attr"])(ts)
        else:
            records = await getattr(client, cfg["fetch_attr"])(ts)
    except ErpError as e:
        await _write_state(db, kind=kind, status="failed", message=str(e),
                           last_ts=state.last_ts if state else None, row_count=0)
        await db.commit()
        raise

    model = cfg["model"]
    mapper = cfg["mapper"]
    unique_col = cfg["unique_col"]
    now = datetime.now(timezone.utc)
    inserted = 0
    updated = 0
    max_rowversion: datetime | None = None

    for rec in records:
        try:
            row = mapper(rec)
        except KeyError:
            continue
        row["synced_at"] = now

        exists_q = select(getattr(model, unique_col)).where(
            getattr(model, unique_col) == row[unique_col]
        )
        existed = (await db.execute(exists_q)).scalar_one_or_none() is not None

        stmt = pg_insert(model).values(**row).on_conflict_do_update(
            index_elements=[unique_col],
            set_={k: v for k, v in row.items() if k != unique_col},
        )
        await db.execute(stmt)

        if existed:
            updated += 1
        else:
            inserted += 1

        rv = row.get("erp_rowversion")
        if rv is not None and (max_rowversion is None or rv > max_rowversion):
            max_rowversion = rv

    new_last_ts = max_rowversion or (state.last_ts if state else None) or now
    await _write_state(
        db, kind=kind, status="success", message="ok",
        last_ts=new_last_ts, row_count=len(records),
    )
    await db.commit()

    return {
        "kind": kind,
        "mode": mode,
        "total": len(records),
        "inserted": inserted,
        "updated": updated,
        "last_ts": new_last_ts.isoformat(),
        "status": "success",
        "message": "ok",
    }
