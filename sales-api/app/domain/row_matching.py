"""Which order line each 945 row belongs to, when an item sits on several lines
(FR-OB-25a; user 2026-11-04). LX is a pallet, never the match key.

Rows and lines of one item, largest first (ties keep their order): each row goes to
the first line with room for all of it, and that room shrinks. A row with no such
line goes to the largest line and is named in `did_not_fit`: a row links to one
line, so it cannot be split. What each line shipped is `lifecycle.spread` of the
item's total, so the quantities stay right either way.
"""
from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field


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
