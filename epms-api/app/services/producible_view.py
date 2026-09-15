"""How many of a product could we build from what is on hand right now.

Asked "基于S0102的配方的物料，计算下我们现存的原料能生产多少S0102", the
assistant listed the on-hand quantity of seven packaging components, said it was
missing the recipe, and totalled the seven numbers to 521,257 — a sum of pieces
of lid, pieces of label and pieces of carton, which is not a quantity of
anything. Three separate failures, and all three are structural:

  * it could not fetch the recipe. controlled_query.execute runs ONE query, and
    this question needs the BOM and the inventory and then arithmetic over both;
  * the seven components it found are the first level only. S0102's default BOM
    is the packaging stage, and one of its eight lines is CW0002, a dry mix with
    a recipe of its own, which in turn contains CS0070, which has fourteen. The
    materials that actually constrain production are three levels below the ones
    it reported, and it never saw them;
  * summing across materials. Adding a lid count to a carton count produces a
    number with no referent, and it read as a result.

So this module explodes the recipe, reads what is available, and does the
division in code. The model's only job is to say the answer out loud.

── What "available" means ───────────────────────────────────────────────────
Not invented here. mrp-api owns this definition and states it in one place
(mrp-api/app/services/net_requirement.py), and it is copied verbatim rather
than improved on:

    sum(qty - qty_onhold)
      where mapped_status = 'available'
        and (expiry_date IS NULL OR expiry_date >= today)

Note what it does NOT subtract: qty_allocated. That is deliberate in the
original and the docstring there says so — allocated stock is still physically
on the shelf. Reproducing the formula means reproducing that choice, not
quietly tightening it. test_producible_view asserts this module and that one
still agree.

Consignment stock, which net_requirement adds on top for opening stock, is left
out and said so in the payload: it is a hand-entered weekly count, it is not
configured for this site, and silently adding an empty table to the total would
make "we have none" indistinguishable from "we never counted".

── What this is and is not ──────────────────────────────────────────────────
It is a reference figure, and the payload says so. It divides what is on the
shelf by what the recipe asks for and takes the smallest result. It knows
nothing about what is already committed to other production, about minimum
batch sizes, about shelf life against a production date, or about what is on
order. Those are the planning run's job, not this.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import sqlalchemy as sa

# Units are named differently on the two sides — the recipe says EA and KGM,
# the warehouse says PIECES and KG — and they mean the same thing. Anything not
# in here is REFUSED rather than assumed equal: a recipe in litres divided by an
# on-hand figure in kilograms produces a number that looks perfectly reasonable
# and is wrong, which is the one outcome worth failing to avoid.
_UNIT_ALIASES = {
    "EA": "EA", "PIECES": "EA", "PCS": "EA", "PC": "EA", "PIECE": "EA",
    "KGM": "KG", "KG": "KG", "KGS": "KG",
    "LTR": "L", "L": "L", "LITRE": "L", "LITER": "L",
    "GRM": "G", "G": "G",
    "BOX": "BOX", "CTN": "CTN", "ROLL": "ROLL", "SET": "SET", "BAG": "BAG",
}

# A BOM that contains itself would recurse forever. Depth is capped as a second
# line of defence behind the visited-set; real trees here are four deep.
_MAX_DEPTH = 12


class AmbiguousRecipe(LookupError):
    """The product itself has more than one default recipe.

    Raised rather than resolved: picking one would answer a question about a
    recipe the asker never named, and the answer would look exactly like a
    correct one.
    """

    def __init__(self, product: str, options: list[str]):
        self.product, self.options = product, options
        super().__init__(f"{product} has {len(options)} default recipes: "
                         f"{', '.join(options)}")


def _canon_unit(u: str | None) -> str | None:
    return _UNIT_ALIASES.get((u or "").strip().upper())


@dataclass
class Component:
    """One leaf material and what it does to the answer."""
    material_code: str
    qty_per_unit: Decimal
    bom_uom: str | None
    available: Decimal | None = None
    stock_uom: str | None = None
    lots_seen: int = 0
    # None when it cannot be computed — no stock, or units that do not match.
    supports_units: Decimal | None = None
    problem: str | None = None
    reached_via: list[str] = field(default_factory=list)


async def _default_bom_index(db) -> tuple[dict[str, str], dict[str, list[str]]]:
    """product_material_code -> bom id, for the default version only.

    The default flag lives on the NC mirror rather than on `boms`, which is why
    this reads through nc_source_pk — the same expression the ontology's
    bom.is_default uses, kept identical on purpose.

    Returns the ambiguous ones separately rather than resolving them. Five
    products carry more than one default BOM (CS0081 has three: milling 1.1,
    1.4 and 1.5), and a dict comprehension over these rows picks whichever the
    database happened to return last — a different recipe, a different set of
    ingredients, and nothing in the answer showing a choice was made. The
    caller stops at such a material and says why.
    """
    rows = (await db.execute(sa.text(
        "SELECT b.product_material_code, b.id::text, b.bom_type, b.version "
        "FROM boms b "
        "WHERE b.nc_source_pk IN (SELECT nc_source_pk FROM nc_bom WHERE hbdefault = 'Y') "
        "ORDER BY b.product_material_code, b.bom_type, b.version"
    ))).all()
    by_code: dict[str, list[tuple[str, str, str]]] = {}
    for code, bom_id, bom_type, version in rows:
        by_code.setdefault(code, []).append((bom_id, bom_type, version))
    index = {c: v[0][0] for c, v in by_code.items() if len(v) == 1}
    ambiguous = {c: [f"{t}/{ver}" for _, t, ver in v]
                 for c, v in by_code.items() if len(v) > 1}
    return index, ambiguous


async def _lines_of(db, bom_id: str) -> list[tuple[str, Decimal, str | None]]:
    rows = (await db.execute(sa.text(
        "SELECT component_material_code, qty_per, uom FROM bom_lines "
        "WHERE bom_id = CAST(:b AS uuid) ORDER BY line_no"
    ), {"b": bom_id})).all()
    return [(r[0], Decimal(str(r[1])), r[2]) for r in rows]


async def explode(db, product: str, exclude: set[str] | None = None
                  ) -> tuple[list[Component], list[dict], list[str]]:
    """Flatten the recipe to leaf materials, with how much of each per product.

    `qty_per` on a bom_line is ALREADY per unit of the product, not per batch —
    the sync divides NITEMNUM by the header's HNPARENTNUM before storing it
    (mdm-api nc_bom_sync/transform.py, "PATCH 6"). Measured across all 286 BOMs:
    every one has a real batch_output_qty, so the divisor was always available
    and no row escaped the division. Multiplying back up by batch_output_qty
    here would overstate every requirement by the batch size.

    Returns (leaves, intermediates, excluded-subtree roots). An excluded
    material takes its whole subtree with it: dropping CR0059 has to drop CR0010
    too, since CR0010 is only reachable through it.
    """
    exclude = {e.strip().upper() for e in (exclude or set())}
    index, ambiguous = await _default_bom_index(db)

    leaves: dict[str, Component] = {}
    intermediates: list[dict] = []
    pruned: list[str] = []

    async def walk(code: str, multiplier: Decimal, uom: str | None,
                   path: list[str], seen: frozenset[str]) -> None:
        if code.upper() in exclude:
            if code not in pruned:
                pruned.append(code)
            return
        if code in seen or len(path) > _MAX_DEPTH:
            # A cycle, or deeper than any real tree. Recorded as a leaf with a
            # problem rather than dropped, so it cannot silently stop
            # constraining the answer.
            leaves.setdefault(code, Component(code, multiplier, uom))
            leaves[code].problem = "recipe loops back on itself — not expanded"
            return

        if code in ambiguous:
            # Not expanded, and not silently treated as a raw material either —
            # it is neither. Recorded with the reason so the answer says which
            # material it could not see through.
            leaf = leaves.setdefault(
                code, Component(code, multiplier, uom, reached_via=list(path)))
            leaf.problem = (f"has {len(ambiguous[code])} default recipes "
                            f"({', '.join(ambiguous[code])}) — cannot tell which "
                            f"applies, so what it is made of is not counted")
            return

        bom_id = index.get(code)
        if bom_id is None:
            leaf = leaves.get(code)
            if leaf is None:
                leaves[code] = Component(code, multiplier, uom, reached_via=list(path))
            else:
                # The same raw material reached through two branches. Its needs
                # add up; taking either alone would understate it.
                leaf.qty_per_unit += multiplier
            return

        intermediates.append({"material_code": code, "qty_per_unit": str(multiplier),
                              "reached_via": list(path)})
        for child, qty, child_uom in await _lines_of(db, bom_id):
            await walk(child, multiplier * qty, child_uom,
                       path + [code], seen | {code})

    if product in ambiguous:
        raise AmbiguousRecipe(product, ambiguous[product])
    root_bom = index.get(product)
    if root_bom is None:
        return [], [], []
    for child, qty, child_uom in await _lines_of(db, root_bom):
        await walk(child, qty, child_uom, [product], frozenset({product}))

    return list(leaves.values()), intermediates, pruned


async def _availability(db, codes: list[str]) -> dict[str, tuple[Decimal, str | None, int, int]]:
    """Available quantity per material, by mrp-api's definition — see the
    module docstring. Returns (available, uom, available_lots, total_lots);
    the two lot counts are what separates "we hold none" from "we hold some and
    none of it is usable", which are different problems with different fixes.
    """
    if not codes:
        return {}
    rows = (await db.execute(sa.text(
        "SELECT material_code, "
        "  COALESCE(SUM(qty - COALESCE(qty_onhold, 0)) FILTER ("
        "    WHERE mapped_status = 'available' "
        "      AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE)), 0), "
        "  MAX(uom) FILTER ("
        "    WHERE mapped_status = 'available' "
        "      AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE)), "
        "  COUNT(*) FILTER ("
        "    WHERE mapped_status = 'available' "
        "      AND (expiry_date IS NULL OR expiry_date >= CURRENT_DATE)), "
        "  COUNT(*) "
        "FROM wms_inventory_lots WHERE material_code = ANY(:codes) "
        "GROUP BY material_code"
    ), {"codes": codes})).all()
    return {r[0]: (Decimal(str(r[1])), r[2], int(r[3]), int(r[4])) for r in rows}


async def how_many_can_we_make(db, product: str,
                               exclude: set[str] | None = None) -> dict | None:
    """The whole answer, arithmetic included. None when there is no recipe."""
    leaves, intermediates, pruned = await explode(db, product, exclude)
    if not leaves and not pruned:
        # No recipe at all. Distinct from "everything in the recipe was
        # excluded", which reaches the caller as a normal result with nothing
        # left to divide — reporting that as "no recipe" would send someone
        # looking for a missing BOM that is not missing.
        return None

    stock = await _availability(db, [c.material_code for c in leaves])

    for c in leaves:
        got = stock.get(c.material_code)
        if got is None:
            c.available, c.lots_seen = Decimal(0), 0
            c.problem = "no stock records at all for this material"
            c.supports_units = Decimal(0)
            continue
        c.available, c.stock_uom, avail_lots, total_lots = got
        c.lots_seen = total_lots

        if c.qty_per_unit <= 0:
            c.problem = "recipe asks for zero of this — skipped"
            continue

        bom_u, stock_u = _canon_unit(c.bom_uom), _canon_unit(c.stock_uom)
        if c.stock_uom and bom_u and stock_u and bom_u != stock_u:
            # Refuse rather than divide. This is the failure that would look
            # like a perfectly ordinary number.
            c.problem = (f"recipe is in {c.bom_uom}, stock is in {c.stock_uom} — "
                         f"cannot compare without a conversion")
            continue
        if c.stock_uom and (bom_u is None or stock_u is None):
            c.problem = (f"unrecognised unit ({c.bom_uom} vs {c.stock_uom}) — "
                         f"not assuming they are the same")
            continue

        c.supports_units = c.available / c.qty_per_unit
        if c.available == 0 and total_lots > avail_lots:
            c.problem = ("stock exists but none of it is usable — expired, "
                         "on hold, or past its expiry date")

    usable = [c for c in leaves if c.supports_units is not None]
    blocked = [c for c in leaves if c.supports_units is None]
    limit = min((c.supports_units for c in usable), default=None)

    def row(c: Component) -> dict:
        return {
            "material_code": c.material_code,
            "needed_per_unit": str(c.qty_per_unit),
            "recipe_uom": c.bom_uom,
            "available": str(c.available) if c.available is not None else None,
            "stock_uom": c.stock_uom,
            "stock_lots": c.lots_seen,
            "supports_units": (str(c.supports_units.quantize(Decimal("1")))
                               if c.supports_units is not None else None),
            "is_the_constraint": (c.supports_units is not None and limit is not None
                                  and c.supports_units == limit),
            "reached_via": " → ".join(c.reached_via) if c.reached_via else None,
            "problem": c.problem,
        }

    ordered = sorted(usable, key=lambda c: c.supports_units) + blocked
    return {
        "product": product,
        "can_make": str(limit.quantize(Decimal("1"))) if limit is not None else None,
        # Named rather than left for the reader to spot in the table: "what is
        # stopping us" is the question behind the question.
        "limited_by": [c.material_code for c in usable if c.supports_units == limit],
        "materials": [row(c) for c in ordered],
        "levels_deep": max((len(c.reached_via) for c in leaves), default=0),
        "sub_recipes_expanded": [i["material_code"] for i in intermediates],
        "excluded": sorted(pruned),
        "could_not_compute": [c.material_code for c in blocked],
        "basis": (
            "Available stock is what the warehouse holds as available and "
            "unexpired, less anything on hold — mrp-api's own definition. "
            "Stock already allocated to other orders is INCLUDED, because it "
            "is still physically on the shelf. Consignment stock is not "
            "included. This is a rough reference figure: it does not consider "
            "minimum batch sizes, what is already committed to other "
            "production, shelf life against a production date, or anything on "
            "order."
        ),
    }
