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

NC batch-scale fields (2026-09-03, reported as a "BOM Explorer 精度问题"):
every non-root node also reports `qty_per_batch` / `parent_batch_output_qty`
(NC's own `NITEMNUM` and the parent header's `HNPARENTNUM`) and, when it has
a BOM of its own, `batch_output_qty`. These are REPORTING fields only — no
accumulation reads them, and adding them must not move a single planning
number.

They exist because `qty_per` alone cannot be reconciled against the NC BOM
screen: NC shows a whole-BATCH quantity (S0147's CS0147 line: 610 per a
3000 kg batch), the explorer shows the normalized quotient (0.2033333333),
and the planner is left multiplying a rounded number in their head. Worse,
the on-screen quotient is rounded again for display, so the hand-check
lands ~0.1 off and reads like a precision bug. Reporting NC's own pair
removes the arithmetic entirely.

`qty_per_batch` prefers `BomLine.qty_per_batch` — NC's `NITEMNUM` stored
verbatim by migration 0018/transform.py PATCH 7 — and falls back to
`qty_per * batch_output_qty` for rows synced before 0018. The fallback is
deliberately lossy-but-present rather than None: it is right to within the
10dp quotient's resolution (measured live: max absolute error 1e-7, max
relative 5.3e-6), which beats a blank column, but it is NOT good enough to
be the primary source — CS0081's CR0214 line is 0.00375508 in NC and
reconstructs as 0.0037551. Re-run `POST /boms/sync` after deploying 0018
and every row uses the exact value.
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
    the root, where the root's own qty_accumulated is defined as 1.

    The three NC batch-scale fields exist purely so a planner can reconcile
    a row against the NC BOM screen without re-deriving anything (see the
    module docstring); no planning math reads them:
      - `qty_per_batch`: this component's quantity per ONE BATCH of its
        immediate parent's BOM — i.e. NC's own `BD_BOM_B.NITEMNUM`, the
        un-divided numerator `qty_per` was derived from. None on the root
        (it has no parent line).
      - `parent_batch_output_qty`: the denominator that number is stated
        against — the PARENT BOM header's `HNPARENTNUM`. None on the root.
      - `batch_output_qty`: this node's OWN selected BOM header batch size,
        i.e. the denominator its own children's `qty_per_batch` values are
        stated against. None for a leaf/missing-BOM node, which has no
        selected BOM of its own."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    material_code: str
    name: str | None = None
    bom_type: str | None = None
    level: int
    qty_per: Decimal
    qty_accumulated: Decimal
    uom: str | None = None
    # NC-native batch-scale reconciliation triplet (2026-09-03) — see the
    # "NC batch-scale fields" section of this module's docstring.
    qty_per_batch: Decimal | None = None
    parent_batch_output_qty: Decimal | None = None
    batch_output_qty: Decimal | None = None
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


def _nc_batch_qty(line: BomLine, batch_output_qty: Decimal | None) -> Decimal | None:
    """NC's own `BD_BOM_B.NITEMNUM` for `line` — the whole-batch quantity a
    planner sees on the NC BOM screen (reporting only; nothing in the
    explosion reads it).

    Prefers the value stored verbatim by migration 0018 / transform.py
    PATCH 7. Rows synced before 0018 have it NULL, and fall back to
    reconstructing it as `qty_per * batch_output_qty` — correct only to the
    10dp quotient's own resolution (max relative error 5.3e-6 measured
    live), which is why it is a fallback and not the source. Returns None
    only when BOTH are unavailable (no stored value AND no batch size to
    reconstruct against), never a silently wrong 0.
    """
    stored = line.qty_per_batch
    if stored is not None:
        return stored
    if batch_output_qty is None:
        return None
    return line.qty_per * batch_output_qty


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
            # This node's own batch size — the denominator every child's
            # `qty_per_batch` below is stated against (reporting only).
            node.batch_output_qty = selected.batch_output_qty

            children: list[ExplodeNode] = []
            for ln in selected_lines:
                child_code = ln.component_material_code
                child_scrap = ln.scrap_rate if ln.scrap_rate is not None else Decimal(0)
                # NC-native pair for this line, reporting only — see the
                # module docstring's "NC batch-scale fields" for why the
                # stored raw value is preferred over the reconstruction.
                nc_batch = {
                    "qty_per_batch": _nc_batch_qty(ln, selected.batch_output_qty),
                    "parent_batch_output_qty": selected.batch_output_qty,
                }
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
                        **nc_batch,
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
                    **nc_batch,
                )
                children.append(child)
                if not is_cycle:
                    next_frontier.append((child, ancestors | {child_code}))
            node.children = children

        frontier = next_frontier
        level += 1

    return root


class WhereUsedResult(BaseModel):
    """One upward path from a queried component to a top-level product that
    is not itself used anywhere further (design spec §6.6's where-used
    mode). `path` runs component-first, top-last — `[component, ..., top]`
    — matching the brief's worked example literally (`CR0031 → CS0026 →
    CW0001 → S0093`). `qty_accumulated` is the SAME kind of number
    `ExplodeNode.qty_accumulated` reports (how much of `path[0]` one unit of
    `top_product` needs), computed by multiplying the identical
    `qty_per * (1 + scrap_rate)` factors `explode_bom` would multiply
    walking the same edges downward — see this function's docstring for why
    that makes the two directions provably agree, not just "usually agree".
    `levels` is the number of BOM levels climbed (`len(path) - 1`; 0 means
    the queried component IS the top itself — nobody uses it further)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    top_product: str
    path: list[str]
    qty_accumulated: Decimal
    levels: int
    cycle_detected: bool = False


async def find_where_used(
    db: AsyncSession,
    component_code: str,
    on_date: date_type,
    max_depth: int = 10,
    max_nodes: int = 5000,
) -> list[WhereUsedResult]:
    """Reverse of `explode_bom`: starting from `component_code`, walk UP the
    same canonical bom_lines/boms tables, breadth-first, collecting every
    root-to-top path until each branch reaches a product that is itself used
    by nobody (a genuine top-level finished good) — or is stopped early by
    the same two guards `explode_bom` uses (`max_depth`; `max_nodes`, a
    running budget on total frontier entries expanded, shared across all
    branches so a wide fan-in graph can't blow up combinatorially even
    within the depth limit).

    Version/date selection MUST exactly match `/effective` and `explode_bom`
    (same `version_key`/`covers_date` helpers) or forward and reverse would
    silently disagree — e.g. a component could reverse-resolve to a top
    product whose CURRENT effective BOM (the one `/explode` would actually
    walk down) doesn't reference that component at all, because a NEWER,
    date-covering version superseded the one that does. So a raw
    `component_material_code` match is only a CANDIDATE edge: for every
    product touched by a candidate line, this replicates the exact same
    "highest version, tiebroken by nc_source_pk, whose lines cover on_date"
    selection `explode_bom`/`GET /effective` run, and only follows the edge
    if it belongs to that product's WINNING bom+line — never a losing/
    shadowed version's line, even if that line's own dates cover on_date.

    Accumulation walks the exact same qty_per*(1+scrap_rate) factors
    `explode_bom` would multiply walking the resulting path downward — just
    applied in ascending order. Multiplication is commutative, so the two
    directions produce the identical number for the identical path, not
    independently-computed values that happen to usually agree.

    Cycle guard: identical shape to `explode_bom`'s — each branch tracks the
    set of codes already visited on ITS OWN path (root component inclusive);
    a parent edge that lands back on one of those codes is reported (with
    `cycle_detected=True`, so a genuine cycle is visible rather than
    silently dropped) but NOT expanded further, exactly mirroring
    `explode_bom`'s "flag it, stop there" contract for A->B->A loops NC data
    can contain."""
    results: list[WhereUsedResult] = []
    total_nodes = 1  # the queried component itself counts against the budget too
    # frontier entries: (code, path-from-component-to-here inclusive, qty_accumulated, ancestor codes on this path)
    frontier: list[tuple[str, list[str], Decimal, frozenset[str]]] = [
        (component_code, [component_code], Decimal(1), frozenset({component_code}))
    ]

    level = 0
    while frontier and level < max_depth:
        codes = {code for code, _, _, _ in frontier}

        # Candidate upward edges: any APPROVED bom_line whose component is
        # one of this level's codes, date-effective. Each is only a
        # CANDIDATE until confirmed against its product's winning bom/version
        # below — see docstring.
        line_rows = (await db.execute(
            select(BomLine, Bom)
            .join(Bom, BomLine.bom_id == Bom.id)
            .where(BomLine.component_material_code.in_(codes), Bom.status == "approved")
        )).all()
        candidate_lines = [(ln, b) for ln, b in line_rows if covers_date(ln, on_date)]

        # For every product touched by a candidate, resolve ITS OWN winning
        # bom+lines the identical way explode_bom/GET /effective do — needs
        # every approved candidate for that product (not just the ones
        # holding our target component), since a higher, non-matching
        # version can still be the one that wins and shadows the candidate.
        products = {b.product_material_code for _, b in candidate_lines}
        winner_by_product: dict[str, tuple[Bom, set]] = {}
        if products:
            all_candidates = (await db.execute(
                select(Bom).where(Bom.product_material_code.in_(products), Bom.status == "approved")
            )).scalars().all()
            candidates_by_product: dict[str, list[Bom]] = defaultdict(list)
            for b in all_candidates:
                candidates_by_product[b.product_material_code].append(b)

            cand_bom_ids = [b.id for b in all_candidates]
            lines_by_bom: dict = defaultdict(list)
            if cand_bom_ids:
                all_lines = (await db.execute(
                    select(BomLine).where(BomLine.bom_id.in_(cand_bom_ids))
                )).scalars().all()
                for ln in all_lines:
                    lines_by_bom[ln.bom_id].append(ln)

            for product, cands in candidates_by_product.items():
                ordered = sorted(cands, key=lambda b: b.nc_source_pk)
                ordered.sort(key=lambda b: version_key(b.version), reverse=True)
                for cand in ordered:
                    eff_ids = {ln.id for ln in lines_by_bom.get(cand.id, []) if covers_date(ln, on_date)}
                    if eff_ids:
                        winner_by_product[product] = (cand, eff_ids)
                        break

        next_frontier: list[tuple[str, list[str], Decimal, frozenset[str]]] = []
        for code, path, qty_acc, ancestors in frontier:
            found_parent = False
            for ln, b in candidate_lines:
                if ln.component_material_code != code:
                    continue
                winner = winner_by_product.get(b.product_material_code)
                if winner is None or winner[0].id != b.id or ln.id not in winner[1]:
                    continue  # shadowed by a higher version — not a real edge
                found_parent = True
                scrap = ln.scrap_rate if ln.scrap_rate is not None else Decimal(0)
                new_qty = qty_acc * ln.qty_per * (Decimal(1) + scrap)
                parent_code = b.product_material_code
                new_path = path + [parent_code]
                if parent_code in ancestors:
                    results.append(WhereUsedResult(
                        top_product=parent_code, path=new_path,
                        qty_accumulated=new_qty, levels=len(path), cycle_detected=True,
                    ))
                    continue
                if total_nodes >= max_nodes:
                    results.append(WhereUsedResult(
                        top_product=parent_code, path=new_path,
                        qty_accumulated=new_qty, levels=len(path),
                    ))
                    continue
                total_nodes += 1
                next_frontier.append((parent_code, new_path, new_qty, ancestors | {parent_code}))
            if not found_parent:
                # Nobody uses `code` further (as of on_date) — it IS the top
                # of this branch, possibly the queried component itself.
                results.append(WhereUsedResult(
                    top_product=code, path=list(path), qty_accumulated=qty_acc, levels=len(path) - 1,
                ))

        frontier = next_frontier
        level += 1

    # max_depth exhausted with branches still live: truncated, not genuinely
    # top-level, but still reported so a caller can see the branch exists
    # rather than have it silently vanish.
    for code, path, qty_acc, _ in frontier:
        results.append(WhereUsedResult(
            top_product=code, path=list(path), qty_accumulated=qty_acc, levels=len(path) - 1,
        ))

    return results
