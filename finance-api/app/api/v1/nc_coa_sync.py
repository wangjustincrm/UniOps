"""NC65 COA + aux sync — preview/apply, gated on finance.coa.manage.

Synchronous by design: 360 accounts + 205 aux rows read in ~1-2s. The voucher
sync's worker/run-table/polling machinery exists for volume this does not have,
and preview->confirm already needs two calls.

Same lock as the COA page's writes (including CSV import, which can overwrite
the whole chart): the blast radius is identical and this sync's source is NC
rather than a hand-edited spreadsheet. A stricter gate here would only mean
finance_manager reaches the same end via CSV while the sync button 403s.
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.coa import ChartOfAccount, CoaAuxItem
from app.models.coa_sync import CoaSyncRun
from app.services import nc_coa_sync as svc

router = APIRouter(prefix="/coa-sync", tags=["coa-sync"])

# test seams — monkeypatched in tests
_fetch = svc.fetch_coa_from_nc
_worker_dsn = svc._pg_dsn

_MANAGE_KEY = "finance.coa.manage"          # same key coa.py gates its writes on
_manage_gate = require_permission(_MANAGE_KEY)


async def _can_manage(db: AsyncSession, user: dict) -> bool:
    try:
        await _manage_gate(user, db)
        return True
    except HTTPException:
        return False


async def _require_manage(db: AsyncSession, user: dict) -> None:
    if not await _can_manage(db, user):
        raise HTTPException(status_code=403,
                            detail="Insufficient permission to sync the chart of accounts")


def _require_configured() -> None:
    if not svc.nc_configured():
        raise HTTPException(status_code=503, detail="NC connection is not configured")


async def _read_db_state(db: AsyncSession) -> tuple[list[dict], list[dict]]:
    accts = (await db.execute(select(ChartOfAccount))).scalars().all()
    fields = svc.NC_OWNED_FIELDS + ("code", "is_active")
    db_accounts = [{f: getattr(a, f) for f in fields} for a in accts]
    items = (await db.execute(select(CoaAuxItem))).scalars().all()
    db_aux = [{"account_code": i.account_code, "dim_code": i.dim_code,
               "seq": i.seq, "required": i.required} for i in items]
    return db_accounts, db_aux


async def _build_or_503():
    loop = asyncio.get_running_loop()
    try:
        extract = await loop.run_in_executor(None, _fetch)
        return svc.build(extract)
    except svc.NcMappingError as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    except Exception as e:                       # oracledb / network
        raise HTTPException(status_code=503, detail=f"NC read failed: {e}") from None


@router.get("/status")
async def status(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    last = (await db.execute(select(CoaSyncRun)
                             .order_by(CoaSyncRun.started_at.desc()).limit(1))
            ).scalars().first()
    return {
        "can_sync": await _can_manage(db, user),
        "configured": svc.nc_configured(),
        "last_run": None if last is None else {
            "id": str(last.id),
            "started_at": last.started_at.isoformat() if last.started_at else None,
            "finished_at": last.finished_at.isoformat() if last.finished_at else None,
            "accounts_inserted": last.accounts_inserted,
            "accounts_updated": last.accounts_updated,
            "accounts_deactivated": last.accounts_deactivated,
            "aux_items_inserted": last.aux_items_inserted,
            "aux_items_deleted": last.aux_items_deleted,
            "error": last.error,
        },
    }


@router.post("/preview")
async def preview(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    _require_configured()
    nc_accounts, nc_aux = await _build_or_503()
    db_accounts, db_aux = await _read_db_state(db)
    d = svc.diff(nc_accounts, db_accounts)
    ad = svc.diff_aux(nc_aux, db_aux)

    # Warn (never block) when a to-be-deactivated account is still mapped.
    codes = [r["code"] for r in d.to_deactivate]
    mapped = []
    if codes:
        from app.models.coa import AccountMapping
        rows = (await db.execute(select(AccountMapping)
                                 .where(AccountMapping.account_code.in_(codes)))
                ).scalars().all()
        mapped = [{"account_code": m.account_code, "mapping_type": m.mapping_type,
                   "source_code": m.source_code} for m in rows]
    return {
        "accounts": {
            "to_insert": len(d.to_insert), "to_update": len(d.to_update),
            "to_deactivate": len(d.to_deactivate), "unchanged": d.unchanged,
            "updates": [{"code": u["code"], "reactivated": u["reactivated"],
                         "changes": {k: [v[0], v[1]] for k, v in u["changes"].items()}}
                        for u in d.to_update],
            "deactivations": d.to_deactivate,
        },
        "aux_items": {"to_insert": len(ad.to_insert), "to_delete": len(ad.to_delete),
                      "unchanged": ad.unchanged},
        "referenced_by_mappings": mapped,
    }


@router.post("/apply")
async def apply(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    await _require_manage(db, user)
    _require_configured()
    # Re-read NC and recompute: never let the client tell the server what to write.
    nc_accounts, nc_aux = await _build_or_503()
    db_accounts, db_aux = await _read_db_state(db)
    d = svc.diff(nc_accounts, db_accounts)
    ad = svc.diff_aux(nc_aux, db_aux)
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, svc.apply, d, nc_aux, ad, _worker_dsn(), user["sub"])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"apply failed: {e}") from None
