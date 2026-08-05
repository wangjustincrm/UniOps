"""Multi-level BOM explosion — Phase 1A's BOM Explorer AND Phase 1C's MRP
requirements engine share this exact function (design spec §6.6: "这不只是
一个查看器——它内部就是 MRP 的 BOM 展开引擎"). `GET /mdm/v1/boms/explode`
(app/api/v1/boms.py) is a thin wrapper around `explode_bom()`; Phase 1C's
material-requirements calculation is expected to call it directly.

Version selection at every level matches `GET /boms/effective` exactly (same
`version_key`/`covers_date` helpers, see app/services/bom_common.py's
docstring for why they live there and not in boms.py): among APPROVED boms
for a component, walk versions highest-to-lowest (numeric HVERSION order,
tiebroken by smallest nc_source_pk), and take the first whose lines cover
the query date. `version_candidates_count` is the *total* approved-bom count
for that material code (selection transparency — survey §8: CS0026 has 7
approved versions coexisting), independent of how many of those candidates'
lines actually cover the date.

Two things a component can be that are NOT the same:
  - A **leaf** (raw material / purchased packaging item, e.g. CR*/CP*
    prefixes): legitimately has no BOM of its own. Not an error.
  - **`missing_bom`** (e.g. `-R` rework variants): its material code prefix
    (CS/CW/CF/S — see `nc_bom_sync.transform._bom_type`, reused here rather
    than re-derived) says it SHOULD be a manufactured item with its own
    approved BOM, but none was found for the query date. This must be
    flagged, never silently treated as a leaf (survey-confirmed real gap in
    NC data, brief hard-requirement #3).

Cycle guard: NC's BOM graph can contain loops (A -> B -> A). Descent tracks
the set of material codes on the current root-to-node path; a component that
repeats one of its own ancestors is flagged `cycle_detected=True` and NOT
expanded further (children stays empty) — this is what actually prevents
infinite recursion, not just a symptom flag.

N+1 guard: `explode_bom` walks the tree breadth-first, one query-pair
(Bom, then BomLine) per LEVEL for every code discovered at that level, not
one query pair per node. A 4-level real cascade (S0093 -> CW -> CS -> CR/CP)
costs on the order of 4 x 2 queries + 1 name lookup per level, not one query
per component (CS0026 alone can fan out into 16+ raw-material lines).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date as date_type
from decimal import Decimal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bom import Bom, BomLine
from app.models.material import Material
from app.services.bom_common import covers_date, version_key
from app.services.nc_bom_sync.transform import _bom_type as expected_bom_type


class ExplodeNode(BaseModel):
    """One node in the explosion tree. `qty_per` is this component's
    quantity per 1 unit of its immediate PARENT; `qty_accumulated` is the
    same component's quantity relative to 1 unit of the TOP-level product
    being exploded (the number planners actually need — design spec §6.6:
    "做 1 吨成品要多少脱脂奶粉"), computed as
    `parent.qty_accumulated * qty_per * (1 + scrap_rate)` walking down from
    the root, where the root's own qty_accumulated is defined as 1."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    material_code: str
    name: str | None = None
    bom_type: str | None = None
    level: int
    qty_per: Decimal
    qty_accumulated: Decimal
    uom: str | None = None
    qty_per_secondary: Decimal | None = None
    uom_secondary: str | None = None
    scrap_rate: Decimal = Decimal("0")
    version: str | None = None
    version_candidates_count: int = 0
    missing_bom: bool = False
    cycle_detected: bool = False
    children: list["ExplodeNode"] = []


ExplodeNode.model_rebuild()


async def explode_bom(
    db: AsyncSession,
    product_code: str,
    on_date: date_type,
    max_depth: int = 10,
) -> ExplodeNode:
    """Explode `product_code`'s effective BOM cascade as of `on_date`,
    breadth-first, down to at most `max_depth` levels below the root
    (root itself is level 0; a node at `level == max_depth` is returned but
    never expanded — its own `children` stays empty and its own BOM lookup
    is simply never attempted, distinct from `missing_bom`/`cycle_detected`,
    which both mean "we looked and here's what we found")."""
    root = ExplodeNode(
        material_code=product_code,
        level=0,
        qty_per=Decimal(1),
        qty_accumulated=Decimal(1),
    )
    # frontier: (node, ancestor codes on the path from root to node inclusive)
    frontier: list[tuple[ExplodeNode, frozenset[str]]] = [(root, frozenset({product_code}))]

    level = 0
    while frontier and level < max_depth:
        codes = {node.material_code for node, _ in frontier}

        materials = (await db.execute(select(Material).where(Material.code.in_(codes)))).scalars().all()
        name_by_code = {m.code: m.name for m in materials}
        for node, _ in frontier:
            node.name = name_by_code.get(node.material_code)

        boms = (await db.execute(
            select(Bom).where(Bom.product_material_code.in_(codes), Bom.status == "approved")
        )).scalars().all()
        boms_by_code: dict[str, list] = defaultdict(list)
        for b in boms:
            boms_by_code[b.product_material_code].append(b)

        bom_ids = [b.id for b in boms]
        lines_by_bom: dict = defaultdict(list)
        if bom_ids:
            lines = (await db.execute(
                select(BomLine).where(BomLine.bom_id.in_(bom_ids)).order_by(BomLine.line_no)
            )).scalars().all()
            for ln in lines:
                lines_by_bom[ln.bom_id].append(ln)

        next_frontier: list[tuple[ExplodeNode, frozenset[str]]] = []
        for node, ancestors in frontier:
            code = node.material_code
            candidates = boms_by_code.get(code, [])
            node.version_candidates_count = len(candidates)

            selected: Bom | None = None
            selected_lines: list[BomLine] = []
            if candidates:
                # Same tiebreak as GET /effective: sort by nc_source_pk ASC
                # (stable), then version DESC — ties keep nc_source_pk order.
                ordered = sorted(candidates, key=lambda b: b.nc_source_pk)
                ordered.sort(key=lambda b: version_key(b.version), reverse=True)
                for cand in ordered:
                    eff = [ln for ln in lines_by_bom.get(cand.id, []) if covers_date(ln, on_date)]
                    if eff:
                        selected = cand
                        selected_lines = eff
                        break

            if selected is None:
                # No approved-and-date-effective BOM found. Only an error
                # (missing_bom) if this material's own code prefix says it
                # SHOULD be manufactured (CS/CW/CF/S); otherwise it's a
                # legitimate purchased-leaf material (raw/packaging), never
                # expected to own a BOM.
                if expected_bom_type(code) != "unknown":
                    node.missing_bom = True
                continue

            node.bom_type = selected.bom_type
            node.version = selected.version

            children: list[ExplodeNode] = []
            for ln in selected_lines:
                child_code = ln.component_material_code
                child_scrap = ln.scrap_rate if ln.scrap_rate is not None else Decimal(0)
                child_accumulated = node.qty_accumulated * ln.qty_per * (Decimal(1) + child_scrap)
                is_cycle = child_code in ancestors
                child = ExplodeNode(
                    material_code=child_code,
                    level=node.level + 1,
                    qty_per=ln.qty_per,
                    qty_accumulated=child_accumulated,
                    uom=ln.uom,
                    qty_per_secondary=ln.qty_per_secondary,
                    uom_secondary=ln.uom_secondary,
                    scrap_rate=child_scrap,
                    cycle_detected=is_cycle,
                )
                children.append(child)
                if not is_cycle:
                    next_frontier.append((child, ancestors | {child_code}))
            node.children = children

        frontier = next_frontier
        level += 1

    return root
