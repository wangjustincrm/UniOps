"""Canonical BOM endpoints (MRP phase0 task 5).

GET  /boms/effective — single effective BOM (header + nested lines +
     substitutes) for a product code as of a date.
POST /boms/sync      — refresh nc_bom mirror (Task 4) then transform into
     boms/bom_lines/bom_substitutes (Task 5).

Reads (/effective, /explode, /where-used, /sync-state): gated
`mrp.report.view` — NOT authentication-only. A BOM is a trade secret (full
formulation of every finished product); `mrp.report.view` is literally
defined as "...BOM browsing" (identity-api/scripts/seed_authz.py), so any
role without it (e.g. requester, vendor_manager, auditor) must be refused,
not merely required to be logged in. (Materials/UOM reads elsewhere in this
service are intentionally open to any authenticated role — BOMs are not.)
Writes (sync): gated on require_any_permission("data_maintenance",
"mdm.bom.write") — the pre-existing broad `data_maintenance` key (so
existing EPMS Data Maintenance admins keep working unchanged) OR the
narrower `mdm.bom.write` key seeded for MRP phase-0 governance
(identity-api/scripts/seed_authz.py). `mdm.bom.write` is not granted to any
role by default; an admin must be explicitly granted it via the Portal
Access Control matrix to use it as an alternative to `data_maintenance`.

Version-selection rule for /effective (survey recommendation, since
multiple approved HVERSIONs can coexist per product — survey §8, e.g.
CS0026 has 7 approved versions 1.0-1.6): among APPROVED boms for the
product (optionally narrowed to one NC org via `factory_code` — see below),
walk versions from highest to lowest (numeric HVERSION ordering, e.g.
'1.10' > '1.9'); the first version that has at least one line whose
CBEGINPERIOD/CENDPERIOD-derived effective window covers the query date
wins. Only that version's date-covering lines are returned — a bom's other,
not-yet/no-longer-effective lines are excluded from the response.

`factory_code` (optional query param, <- NC PK_ORG, survey §294 — opaque NC
org pk, no human-readable map yet): when omitted, candidates span all 6 NC
orgs a product code can appear under, which is only correct if the same
product code never has genuinely different BOMs per org; when NC does
maintain per-org variants, the caller MUST pass `factory_code` to disambiguate.
Deterministic tiebreak (both with and without `factory_code`): when two
candidate boms share the exact same `_version_key` (e.g. two rows both
parsing to version '1.0' — malformed/blank HVERSIONs all collapse to the
same (0,) key), the one with the lexicographically SMALLEST `nc_source_pk`
(CBOMID) wins — a stable, reproducible choice instead of whatever order
Postgres happens to return rows in (which is otherwise unspecified: no
ORDER BY in the query, and dict/set iteration order guarantees don't apply
to SQL result order).
"""
import uuid
from datetime import date as date_type
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.authz import require_any_permission, require_permission
from app.db.base import get_db
from app.models.bom import Bom, BomLine
from app.models.sync_state import NcSyncState
from app.services.bom_common import covers_date, version_key
from app.services.bom_explode import ExplodeNode, WhereUsedResult, explode_bom, find_where_used
from app.services.nc_bom_sync.canonical_sync import BomSyncInProgress, sync_boms
from app.services.nc_bom_sync.reader import nc_configured

router = APIRouter(prefix="/boms", tags=["boms"])

SyncDep = Annotated[dict, Depends(require_any_permission("data_maintenance", "mdm.bom.write"))]
# GET /sync-state, /effective, /explode and /where-used all share this same
# gate — `mrp.report.view`, matching mrp-api's own report endpoints
# (consignment.py/forecast.py/inventory.py's ReadDep). /sync-state has
# always used it (design spec §6.6); /effective|/explode|/where-used were
# previously authentication-only (any logged-in role, via CurrentUser) —
# see the module docstring's "Reads" paragraph for why that was a gap.
ReportViewDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]


class BomSubstituteResponse(BaseModel):
    id: uuid.UUID
    substitute_material_code: str
    priority: int
    mode: str

    model_config = {"from_attributes": True}


class BomLineResponse(BaseModel):
    id: uuid.UUID
    line_no: int
    component_material_code: str
    qty_per: Decimal
    uom: str | None
    qty_per_secondary: Decimal | None = None
    uom_secondary: str | None = None
    scrap_rate: Decimal
    effective_from: date_type | None
    effective_to: date_type | None
    substitutes: list[BomSubstituteResponse] = []

    model_config = {"from_attributes": True}


class BomEffectiveResponse(BaseModel):
    id: uuid.UUID
    product_material_code: str
    bom_type: str | None
    version: str | None
    factory_code: str | None
    status: str
    yield_rate: Decimal
    batch_output_qty: Decimal | None = None
    nc_source_pk: str
    lines: list[BomLineResponse]

    model_config = {"from_attributes": True}


class BomSyncResponse(BaseModel):
    boms: int
    lines: int
    substitutes: int
    skipped: int
    warnings: int
    tombstoned: int
    tombstone_skipped: list[str] = []


# Module-level aliases (logic moved to app/services/bom_common.py so
# app/services/bom_explode.py can reuse the exact same version-ordering and
# line-effective-window logic without a circular import — see that module's
# docstring). Kept as aliases so this file's existing call sites (and its
# module-level `_version_key`/`_covers` names, which the test suite may
# still reference via `boms_module._version_key`) needed no further edits.
_version_key = version_key
_covers = covers_date


@router.get("/effective", response_model=BomEffectiveResponse)
async def get_effective_bom(
    product: str = Query(..., description="product_material_code"),
    date: date_type = Query(..., description="query date, YYYY-MM-DD"),
    factory_code: str | None = Query(
        default=None,
        description="optional NC org (PK_ORG) filter; omit to search across all orgs",
    ),
    db: AsyncSession = Depends(get_db),
    _: ReportViewDep = ...,
):
    conditions = [Bom.product_material_code == product, Bom.status == "approved"]
    if factory_code is not None:
        conditions.append(Bom.factory_code == factory_code)
    candidates = list((await db.execute(select(Bom).where(*conditions))).scalars().all())
    # Deterministic tiebreak: sort by nc_source_pk ASC first (stable sort),
    # then by version DESC — ties on the version key keep their pre-sorted
    # nc_source_pk-ascending relative order instead of depending on
    # Postgres's unspecified row order. See module docstring.
    candidates.sort(key=lambda b: b.nc_source_pk)
    candidates.sort(key=lambda b: _version_key(b.version), reverse=True)

    for bom in candidates:
        all_lines = list((await db.execute(
            select(BomLine)
            .where(BomLine.bom_id == bom.id)
            .options(selectinload(BomLine.substitutes))
            .order_by(BomLine.line_no)
        )).scalars().all())
        effective_lines = [ln for ln in all_lines if _covers(ln, date)]
        if effective_lines:
            return BomEffectiveResponse(
                id=bom.id,
                product_material_code=bom.product_material_code,
                bom_type=bom.bom_type,
                version=bom.version,
                factory_code=bom.factory_code,
                status=bom.status,
                yield_rate=bom.yield_rate,
                batch_output_qty=bom.batch_output_qty,
                nc_source_pk=bom.nc_source_pk,
                lines=[BomLineResponse.model_validate(ln) for ln in effective_lines],
            )

    raise HTTPException(
        status_code=404,
        detail=f"No effective BOM found for product={product!r} date={date}",
    )


@router.get("/explode", response_model=ExplodeNode)
async def explode_bom_endpoint(
    product: str = Query(..., description="product_material_code to explode from the top"),
    date: date_type = Query(..., description="as-of date, YYYY-MM-DD"),
    max_depth: int = Query(default=10, ge=1, le=50, description="hard stop on tree depth"),
    max_nodes: int = Query(
        default=5000, ge=1, le=50000,
        description="hard stop on total nodes materialized (root inclusive) — guards diamond-heavy graphs",
    ),
    db: AsyncSession = Depends(get_db),
    _: ReportViewDep = ...,
):
    """Multi-level BOM explosion — the same engine Phase 1C's material
    requirements calculation reuses (see app/services/bom_explode.py). Gated
    `mrp.report.view`, matching /effective/where-used/sync-state — this
    exposes the complete formulation of every finished product, so
    authentication alone is not enough (see the I4 review finding: any
    logged-in employee, including a vendor_manager or auditor, could
    otherwise pull and reverse-map the entire product portfolio). Unlike
    /effective, a product with zero approved BOMs is not a 404 — it comes
    back as a single root node with `missing_bom`/`version_candidates_count=0`,
    since the caller asked to explode a tree and an empty tree is still an
    answer (the same reasoning `explode_bom` applies to every missing
    component node deeper in the tree, not just the root)."""
    return await explode_bom(db, product, date, max_depth=max_depth, max_nodes=max_nodes)


@router.get("/where-used", response_model=list[WhereUsedResult])
async def where_used_endpoint(
    component: str = Query(..., description="component_material_code to reverse-resolve"),
    date: date_type = Query(..., description="as-of date, YYYY-MM-DD"),
    max_depth: int = Query(default=10, ge=1, le=50, description="hard stop on levels climbed"),
    max_nodes: int = Query(
        default=5000, ge=1, le=50000,
        description="hard stop on total frontier entries expanded — guards wide fan-in graphs",
    ),
    db: AsyncSession = Depends(get_db),
    _: ReportViewDep = ...,
):
    """Reverse of /explode (see app/services/bom_explode.py's find_where_used
    docstring for the full version/date-selection-parity argument). Read
    gate matches /effective, /explode and /sync-state: `mrp.report.view` —
    reverse-resolving a component to every finished product that consumes it
    is exactly as trade-secret-sensitive as /explode, so it gets the same
    gate, not bare authentication. A component nobody uses (or that isn't a
    real material code at all) is not an error — it comes back as its own
    single-entry top (path=[component], levels=0), the same "empty tree is
    still an answer" reasoning /explode applies at its root."""
    return await find_where_used(db, component, date, max_depth=max_depth, max_nodes=max_nodes)


class BomSyncStateResponse(BaseModel):
    source: str
    last_success_at: str | None = None
    last_error: str | None = None
    last_stats: dict | None = None
    updated_at: str | None = None


@router.get("/sync-state", response_model=BomSyncStateResponse)
async def get_bom_sync_state(
    db: AsyncSession = Depends(get_db),
    _: ReportViewDep = ...,
):
    """Task 7: so the BOM Explorer page can show "Last synced: … (N hours
    ago)" instead of Sync being a blind button (design spec §6.6's flagged
    backend gap). No row yet (never synced in this environment) is not a
    404 — comes back with every field null so the UI can render "Never
    synced" instead of special-casing an error response."""
    state = await db.get(NcSyncState, "nc_bom")
    if state is None:
        return BomSyncStateResponse(source="nc_bom")
    return BomSyncStateResponse(
        source=state.source,
        last_success_at=state.last_success_at.isoformat() if state.last_success_at else None,
        last_error=state.last_error,
        last_stats=state.last_stats,
        updated_at=state.updated_at.isoformat() if state.updated_at else None,
    )


@router.post("/sync", response_model=BomSyncResponse)
async def trigger_bom_sync(
    db: AsyncSession = Depends(get_db),
    _: SyncDep = ...,
):
    # nc_configured() is defined (see reader.py) but was never enforced here
    # — an unconfigured NC connection would previously fall through to
    # fetch_nc_bom() and raise an opaque oracledb/DSN error as an
    # unhandled 500. Guard it the same way epms-api/app/api/v1/
    # nc_purchase_sync.py and finance-api/app/api/v1/nc_coa_sync.py gate
    # their own NC sync triggers.
    if not nc_configured():
        raise HTTPException(status_code=503, detail="NC connection is not configured")
    try:
        return await sync_boms(db)
    except BomSyncInProgress as e:
        # Non-blocking concurrency guard (Task 7, design spec §6.6): two
        # planners double-clicking Sync must not queue behind each other's
        # delete+insert transaction — the second gets an immediate, clear
        # answer instead of a hung request.
        raise HTTPException(status_code=409, detail=str(e))
