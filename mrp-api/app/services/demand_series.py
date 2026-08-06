"""Continuous demand series: read grid + the single guarded write path +
freezing a window into an outlook snapshot (Continuous Sales Forecast
redesign, plan doc
docs/superpowers/plans/2026-08-06-continuous-sales-forecast.md, Tasks 2 & 4).

`mrp_demand_series` (Task 1, `app/models/demand_series.py`) replaces the
version-scoped `ForecastLine` grid as the one living demand table: one row
per (material_code, absolute 'YYYY-MM' month), sparse (a row exists only for
a non-zero cell), unbounded in time — no version_id. `ForecastVersion`/
`ForecastLine` are now frozen "outlook snapshot" tables (Task 4's
`freeze_outlook`, below) — the living series is the input, a version is one
immutable, timestamped copy of a window of it.

Three entry points:

- `read_series_grid(db, from_month, to_month)` — same `GridResponse` shape
  `app/api/v1/forecast.py`'s `GET .../grid` returns (`months`, `rows` with
  `name`/`cells`/`total`, `column_totals`, `grand_total`), just sourced from
  `mrp_demand_series` filtered to the requested month range instead of a
  version's lines. `name` is resolved the same batched, never-raises way via
  `resolve_material_names` — see that module's docstring for why the
  try/except lives on the worker thread inside it, not here.

- `upsert_cells(db, cells, current_month, changed_by, source)` — **the single
  write path** onto `mrp_demand_series`. It enforces the two invariants the
  continuous grid depends on:

    - past-month-read-only: any cell whose `month < current_month` raises
      `PastMonthError` (already-elapsed months are historical fact, not
      editable forecast).
    - KG-only: any cell whose `uom != 'KG'` raises `UomError` (mirrors
      `net_requirement.py`'s `PLANNING_UOM` invariant — this is the write-side
      enforcement of the same rule).

  Both checks run over the **whole batch before any row is touched** — one
  bad cell rejects everything, nothing partially written (no half-applied
  batch to explain to a user or reconcile later).

  For each cell that survives validation, the old value (the current
  `mrp_demand_series.qty` for that (material_code, month), or `None` if no
  row exists — never coerced to 0 for the log) is compared against the new
  value. A cell whose value doesn't actually change (new == old, treating a
  missing row as old=0 for this comparison only) writes nothing and logs
  nothing — a no-op re-save must not spam the change log. A cell that does
  change: qty!=0 inserts/updates the series row, qty==0 deletes it (sparse
  table — a zero cell has no row), and in both cases exactly one
  `mrp_forecast_change_log` row is appended (old_qty=<previous value or
  None>, new_qty=<new value>). This is one DB transaction — `upsert_cells`
  commits itself (same "service owns the write's atomicity" pattern
  `app/services/wms_sync/service.py` uses), so a caller never has to
  remember to commit for the guarantees above to hold.

- `freeze_outlook(db, anchor_month, horizon_months, created_by)` —
  "Generate Outlook": copies the `[anchor_month, anchor_month +
  horizon_months)` slice of `mrp_demand_series` into a brand-new
  `ForecastVersion` (`status='confirmed'` from creation) + its
  `ForecastLine` rows, one per series cell in that window. Every call
  produces an independent, immutable snapshot; it never touches a
  previously frozen version's lines and never changes any other version's
  status — several outlook snapshots (and hand-built drafts confirmed via
  `app/api/v1/forecast.py`) coexist as `confirmed` at once (design §4.3).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.demand_series import MrpDemandSeries, MrpForecastChangeLog
from app.models.forecast import ForecastLine, ForecastVersion
from app.services.mdm_client import resolve_material_names

_ZERO = Decimal("0")


# ── Month helpers (plain 'YYYY-MM' string arithmetic, no date lib) ─────────


def generate_month_range(from_month: str, to_month: str) -> list[str]:
    """Inclusive 'YYYY-MM' list from `from_month` to `to_month`. Returns an
    empty list if `to_month` is before `from_month` (never raises — an
    empty/reversed range is simply an empty grid, same as an out-of-range
    version horizon elsewhere in this service)."""
    y1, m1 = (int(p) for p in from_month.split("-"))
    y2, m2 = (int(p) for p in to_month.split("-"))
    start_index = y1 * 12 + (m1 - 1)
    end_index = y2 * 12 + (m2 - 1)
    months = []
    for idx in range(start_index, end_index + 1):
        y, m = divmod(idx, 12)
        months.append(f"{y:04d}-{m + 1:02d}")
    return months


# ── Read: GridResponse over mrp_demand_series ───────────────────────────────


async def read_series_grid(
    db: AsyncSession, from_month: str, to_month: str, token: str = "",
) -> dict:
    """`GridResponse` dict (`months`, `rows`, `column_totals`, `grand_total`)
    over every `mrp_demand_series` row whose month falls in
    [from_month, to_month]. `token` forwards the caller's bearer token to
    `resolve_material_names` (optional/blank here since this module has no
    HTTP layer yet — Task 3's `GET /series` endpoint passes the real token);
    that lookup never raises, so mdm-api trouble degrades every row's `name`
    to None rather than breaking this read (same contract as
    `app/api/v1/forecast.py`'s `GET .../grid`)."""
    months = generate_month_range(from_month, to_month)
    month_index = set(months)

    rows = (await db.execute(
        select(MrpDemandSeries).where(MrpDemandSeries.month.in_(month_index))
    )).scalars().all()

    by_material: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        by_material.setdefault(row.material_code, {})[row.month] = row.qty

    # One batched mdm-api round trip for the whole grid (never per row) —
    # skipped entirely when there are no rows to name.
    names = await resolve_material_names(token) if by_material else {}

    column_totals: dict[str, Decimal] = {m: _ZERO for m in months}
    grid_rows: list[dict] = []
    grand_total = _ZERO
    for material_code in sorted(by_material):
        material_cells = by_material[material_code]
        cells = {m: material_cells.get(m, _ZERO) for m in months}
        total = sum(cells.values(), _ZERO)
        grid_rows.append({
            "material_code": material_code,
            "name": names.get(material_code),
            "cells": cells,
            "total": total,
        })
        for m in months:
            column_totals[m] += cells[m]
        grand_total += total

    return {
        "months": months,
        "rows": grid_rows,
        "column_totals": column_totals,
        "grand_total": grand_total,
    }


# ── Write: the single guarded upsert path ───────────────────────────────────


@dataclass(frozen=True)
class CellChange:
    material_code: str
    month: str
    qty: Decimal
    uom: str = "KG"


@dataclass(frozen=True)
class UpsertResult:
    upserted: int
    changed: int


class PastMonthError(ValueError):
    """Raised by `upsert_cells` when any cell's month is before
    `current_month` — elapsed months are read-only history, never editable."""


class UomError(ValueError):
    """Raised by `upsert_cells` when any cell's `uom` isn't the KG planning
    UOM (mirrors `app/services/net_requirement.py`'s `PLANNING_UOM`
    invariant — this is the write-side enforcement of the same rule)."""


async def upsert_cells(
    db: AsyncSession,
    cells: list[CellChange],
    current_month: str | None = None,
    changed_by: uuid.UUID | None = None,
    source: str = "manual",
) -> UpsertResult:
    """The single write path onto `mrp_demand_series`. See module docstring
    for the full contract (past-month/KG guards validated over the whole
    batch before any row is touched; no-op cells write and log nothing;
    qty==0 deletes the sparse row). Commits internally — one transaction for
    the whole batch, whether it's one cell or a thousand.

    `current_month` defaults to `datetime.now(timezone.utc)` formatted
    'YYYY-MM' when omitted (real callers, e.g. Task 3's API endpoint); tests
    pass a fixed value so the past-month guard is deterministic."""
    if current_month is None:
        current_month = datetime.now(timezone.utc).strftime("%Y-%m")

    if not cells:
        return UpsertResult(upserted=0, changed=0)

    # Validate the WHOLE batch before mutating anything — one bad cell
    # rejects the entire request atomically (no query/db.add happens above
    # this loop, so raising here leaves the session untouched).
    for cell in cells:
        if cell.month < current_month:
            raise PastMonthError(
                f"cannot write {cell.material_code}/{cell.month}: month is before "
                f"current_month {current_month!r} — past months are read-only"
            )
        if cell.uom != "KG":
            raise UomError(
                f"cannot write {cell.material_code}/{cell.month}: uom {cell.uom!r} "
                "is not the KG planning UOM"
            )

    material_codes = {c.material_code for c in cells}
    months = {c.month for c in cells}
    existing_rows = (await db.execute(
        select(MrpDemandSeries).where(
            MrpDemandSeries.material_code.in_(material_codes),
            MrpDemandSeries.month.in_(months),
        )
    )).scalars().all()
    existing_by_key = {(r.material_code, r.month): r for r in existing_rows}

    upserted = 0
    changed = 0
    for cell in cells:
        key = (cell.material_code, cell.month)
        existing = existing_by_key.get(key)
        old_qty = existing.qty if existing is not None else None
        # A missing row reads as 0 for the "did this actually change"
        # comparison only — the log's old_qty stays the raw None so a
        # brand-new cell's log entry reads old=None, not old=0.
        effective_old = old_qty if old_qty is not None else _ZERO
        new_qty = cell.qty

        if effective_old == new_qty:
            continue  # true no-op: writes nothing, logs nothing

        if new_qty == _ZERO:
            if existing is not None:
                await db.delete(existing)
                del existing_by_key[key]
        elif existing is not None:
            existing.qty = new_qty
        else:
            new_row = MrpDemandSeries(
                material_code=cell.material_code, month=cell.month,
                qty=new_qty, uom=cell.uom,
            )
            db.add(new_row)
            existing_by_key[key] = new_row  # dedupe repeated cells in the same batch

        db.add(MrpForecastChangeLog(
            material_code=cell.material_code, month=cell.month,
            old_qty=old_qty, new_qty=new_qty,
            source=source, changed_by=changed_by,
        ))
        upserted += 1
        changed += 1

    await db.commit()
    return UpsertResult(upserted=upserted, changed=changed)


# ── Freeze: snapshot a window into an immutable ForecastVersion ────────────


def _add_months(month: str, offset: int) -> str:
    """`month` shifted forward `offset` (>=0) whole months, same 'YYYY-MM'
    string arithmetic as `generate_month_range`/`_generate_months`
    (app/api/v1/forecast.py) — used only to turn `(anchor_month,
    horizon_months)` into the `to_month` `generate_month_range` needs."""
    y, m = (int(p) for p in month.split("-"))
    idx = y * 12 + (m - 1) + offset
    y2, m2 = divmod(idx, 12)
    return f"{y2:04d}-{m2 + 1:02d}"


async def freeze_outlook(
    db: AsyncSession,
    anchor_month: str,
    horizon_months: int,
    created_by: uuid.UUID | None = None,
) -> ForecastVersion:
    """Freeze the living `mrp_demand_series` slice `[anchor_month,
    anchor_month + horizon_months)` into a brand-new, already-`confirmed`
    `ForecastVersion` snapshot (+ one `ForecastLine` per series cell in that
    window — the sparse series table's own invariant already guarantees
    every row is non-zero, see `upsert_cells`'s qty==0-deletes-the-row
    contract, so no extra qty!=0 filter is needed here).

    This is "Generate Outlook": every call mints an independent, immutable
    snapshot — it never mutates a prior snapshot's lines (the series table
    it reads from may keep changing after the fact; that's exactly the
    point of freezing) and never touches any other `ForecastVersion`'s
    status. Several outlook snapshots — and hand-built drafts confirmed via
    `app/api/v1/forecast.py`'s `/confirm` — coexist as `confirmed` at once
    (design §4.3); there is no more "exactly one confirmed version
    system-wide" invariant to protect.
    """
    to_month = _add_months(anchor_month, horizon_months - 1)
    months = generate_month_range(anchor_month, to_month)

    rows = (await db.execute(
        select(MrpDemandSeries).where(MrpDemandSeries.month.in_(months))
    )).scalars().all()

    version = ForecastVersion(
        version_no=f"FCV-{anchor_month}-{uuid.uuid4().hex[:6].upper()}",
        status="confirmed",
        horizon_start_month=anchor_month,
        horizon_months=horizon_months,
        source_anchor_month=anchor_month,
        created_by=created_by,
        confirmed_at=datetime.now(timezone.utc),
    )
    db.add(version)
    await db.flush()  # assign version.id for the ForecastLine FK below

    for row in rows:
        db.add(ForecastLine(
            version_id=version.id,
            material_code=row.material_code,
            month=row.month,
            qty=row.qty,
            uom=row.uom,
        ))

    await db.commit()
    await db.refresh(version)
    return version
