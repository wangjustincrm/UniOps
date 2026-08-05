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

Accumulation formula (2026-08-04, PATCH 6 follow-up — read this before
"fixing" the missing `÷ yield_rate` the design spec's §6.5/§408 formula
mentions):

    child.qty_accumulated = parent.qty_accumulated * child.qty_per * (1 + child.scrap_rate)

DELIBERATELY does NOT divide by `bom.yield_rate`, even though
docs/superpowers/specs/2026-08-03-mrp-subsystem-design.md §6.5/§408 wrote
the formula as `qty_per × (1+scrap_rate) ÷ yield_rate` (that generic
MRP-textbook term is now corrected there too, pointing back here).
`yield_rate` (<- NC `HVCHANGERATE`) is NOT a production-yield/loss factor —
per the survey (docs/superpowers/specs/2026-08-03-nc-bom-survey.md:54-55),
it is 头级用量换算比 "输出/输入": a UNIT-OF-MEASURE CONVERSION RATIO between
the header's own primary and secondary UOM, with `HNPARENTNUM`/
`HNASSPARENTNUM` as its numerator/denominator. It is dimensionally not a
yield term at all, and its *value* is not the same as either divisor
individually — e.g. S0093's yield_rate=4.2 equals neither HNPARENTNUM=420
nor HNASSPARENTNUM=100, it's their quotient (420/100). Confirmed against
all 46 distinct live (HVCHANGERATE, HNPARENTNUM, HNASSPARENTNUM)
combinations across 911 live headers on 2026-08-04, zero exceptions.

Why this matters for THIS formula: `qty_per` is already normalized against
the very same `HNPARENTNUM`/`HNASSPARENTNUM` pair at sync time
(`nc_bom_sync/transform.py`'s PATCH 6). `yield_rate` is NOT always 1 — live
values include 4.2 (S0093) and as high as 1000 (one real combination is
HNPARENTNUM=1000/HNASSPARENTNUM=1) — so dividing the already-normalized
`qty_per` by it again would not just double-count some abstract factor, it
would reintroduce a live variant of the exact qty_per bug PATCH 6 fixed, at
up to 1000x. This is not a one-time hand-verified assumption left to rot:
`nc_bom_sync/transform.py`'s `_check_yield_rate_invariant()` re-checks
`yield_rate == HNPARENTNUM/HNASSPARENTNUM` on every sync and emits a
counted `warnings` entry naming the BOM if NC ever starts populating
`HVCHANGERATE` differently — so a real divergence surfaces immediately
instead of silently corrupting every explosion downstream.

Worked example (S0093, real data): header HNPARENTNUM=420,
HNASSPARENTNUM=100 (yield_rate=4.2). Its CW0001 line carries NITEMNUM=420,
which `transform()` already normalizes to `qty_per=1.0` (420/420). If this
function ALSO divided by `yield_rate` (=4.2), the accumulated quantity for
CW0001 would come out `1.0 / 4.2 ≈ 0.238` per kg of S0093 — wrong; the
correct, already-normalized answer is `1.0` per kg (1 kg of dry-mix powder
per kg of finished product). `bom.yield_rate` is still stored (and still
worth keeping, since it's the header's real primary/secondary UOM
conversion ratio), it is simply not an input to THIS formula.
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
    node_limit_reached: bool = False
    children: list["ExplodeNode"] = []


ExplodeNode.model_rebuild()


async def explode_bom(
    db: AsyncSession,
    product_code: str,
    on_date: date_type,
    max_depth: int = 10,
    max_nodes: int = 5000,
) -> ExplodeNode:
    """Explode `product_code`'s effective BOM cascade as of `on_date`,
    breadth-first, down to at most `max_depth` levels below the root
    (root itself is level 0; a node at `level == max_depth` is returned but
    never expanded — its own `children` stays empty and its own BOM lookup
    is simply never attempted, distinct from `missing_bom`/`cycle_detected`,
    which both mean "we looked and here's what we found").

    `max_nodes` is a second, independent hard stop on the TOTAL number of
    nodes materialized in the tree (root inclusive), on top of `max_depth`:
    a diamond-heavy graph (several components sharing several sub-
    components) can blow up combinatorially breadth-wise well before
    `max_depth` levels down, cycle guard notwithstanding (the cycle guard
    only catches an ancestor repeating on ONE root-to-node path — it does
    nothing to bound fan-out across many different paths). Once the budget
    is spent, every subsequently-discovered node is still returned (so a
    caller can see the component exists) but flagged
    `node_limit_reached=True` and never expanded further — same "we found
    it, we just didn't explore past it" contract as `max_depth` truncation,
    just with its own explicit flag instead of being inferred from `level`."""
    root = ExplodeNode(
        material_code=product_code,
        level=0,
        qty_per=Decimal(1),
        qty_accumulated=Decimal(1),
    )
    total_nodes = 1  # root counts against the budget too
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
                # No `÷ bom.yield_rate` here — see this module's docstring
                # ("Accumulation formula") for why that would double-count
                # the batch-scale normalization transform.py's PATCH 6
                # already applied to `qty_per` at sync time.
                child_accumulated = node.qty_accumulated * ln.qty_per * (Decimal(1) + child_scrap)
                if total_nodes >= max_nodes:
                    # Budget spent: still report the component (with its
                    # correct qty_per/qty_accumulated) but stop right here —
                    # never expanded, never added to next_frontier. See
                    # `max_nodes`'s own docstring paragraph above.
                    children.append(ExplodeNode(
                        material_code=child_code,
                        level=node.level + 1,
                        qty_per=ln.qty_per,
                        qty_accumulated=child_accumulated,
                        uom=ln.uom,
                        qty_per_secondary=ln.qty_per_secondary,
                        uom_secondary=ln.uom_secondary,
                        scrap_rate=child_scrap,
                        node_limit_reached=True,
                    ))
                    continue
                total_nodes += 1
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
