"""Which order line each 945 row belongs to, when an item sits on several lines
(FR-OB-25a; user 2026-11-04). LX is a pallet, never the match key.

Rows and lines of one item, largest first (ties keep their order): each row goes to
the first line with room for all of it, and that room shrinks. A row with no such
line goes to the largest line and is named in `did_not_fit`: a row links to one
line, so it cannot be split. What each line shipped is `lifecycle.spread` of the
item's total, so the quantities stay right either way.
"""
from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Sequence
from dataclasses import dataclass, field

from app.domain.lifecycle import spread


def identifiers(*values: object) -> set[str]:
    """Item code, UPC, GTIN or buyer part, compared without leading zeros."""
    return {str(v).strip().lstrip("0") for v in values if v}


def line_identifiers(item_code: str | None, source: dict | None, item_upc: str | None, item_gtin: str | None) -> set[str]:
    """What identifies an order line's item: its code, what the source sent, the Item Master's barcodes."""
    src = source or {}
    return identifiers(item_code, src.get("buyer_part"), src.get("upc"), src.get("gtin"), item_upc, item_gtin)


@dataclass
class Assignment:
    links: dict[Hashable, Hashable] = field(default_factory=dict)   # row → line
    did_not_fit: list[Hashable] = field(default_factory=list)


def assign_rows(rows: Sequence[tuple[Hashable, int]], lines: Sequence[tuple[Hashable, int]]) -> Assignment:
    """rows: (row, quantity); lines: (line, room left), in their own order."""
    out = Assignment()
    ranked = sorted(lines, key=lambda x: -x[1])
    room = dict(lines)
    for row, qty in sorted(rows, key=lambda x: -x[1]):
        line = next((ln for ln, _ in ranked if room[ln] >= qty), None)
        if line is None:
            line = ranked[0][0]
            out.did_not_fit.append(row)
        room[line] = max(room[line] - qty, 0)
        out.links[row] = line
    return out


@dataclass(frozen=True)
class Row:
    """A 945 row, read from its file or stored."""
    at: tuple[int, int]  # LX, W12
    item_code: str | None
    upc: str | None
    qty: int


@dataclass
class Group:
    """The rows of one item and the lines it sits on."""
    lines: list
    rows: list[Row]
    room: list[int] = field(default_factory=list)
    shipped: dict[int, int] = field(default_factory=dict)  # line id → EA of these rows
    links: dict[tuple[int, int], int] = field(default_factory=dict)
    fits: bool = True  # every row has a line with room for it

    @property
    def total(self) -> int:
        return sum(r.qty for r in self.rows)


def match_rows(rows: Iterable[Row], lines: Sequence, ids_of: Callable[[object], set[str]], room_of: Callable[[object], int]
               ) -> tuple[list[Group], list[Row]]:
    """(the 945's rows by item with the line of each and what each line got, the rows that match no line). Several lines of one
    item (a Shopify order's "Trial Can" and "Can", both CF0102) share their rows; what each shipped is the item's total spread
    over them, up to each line's room, and only what is left over is over-shipped."""
    groups: dict[tuple[int, ...], Group] = {}
    unmatched: list[Row] = []
    for r in rows:
        ident = identifiers(r.item_code, r.upc)
        cands = [ln for ln in lines if ident & ids_of(ln)]
        if not cands:
            unmatched.append(r)
            continue
        groups.setdefault(tuple(ln.id for ln in cands), Group(cands, [])).rows.append(r)
    got: dict[int, int] = {}
    for g in groups.values():
        g.room = [max(room_of(ln) - got.get(ln.id, 0), 0) for ln in g.lines]
        placed = assign_rows([(r.at, r.qty) for r in g.rows], [(ln.id, n) for ln, n in zip(g.lines, g.room)])
        g.links, g.fits = dict(placed.links), not placed.did_not_fit
        g.shipped = {ln.id: n for ln, n in zip(g.lines, spread(g.total, g.room))}
        for lid, n in g.shipped.items():
            got[lid] = got.get(lid, 0) + n
    return list(groups.values()), unmatched
