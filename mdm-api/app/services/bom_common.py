"""Shared BOM version-selection helpers.

Extracted from `app/api/v1/boms.py` (MRP phase0 task 5's `GET /effective`)
so `app/services/bom_explode.py` (task 5's multi-level explosion service)
can reuse the *exact same* version-ordering and line-effective-window logic
level by level, instead of re-implementing a second copy that could drift
out of sync (e.g. a second, buggy string-comparison version sort). Moving
these out of `boms.py` also breaks what would otherwise be a circular
import: `boms.py` needs `explode_bom` for the new `/explode` endpoint, and
`bom_explode.py` needs these two helpers — both can safely depend on this
leaf module instead of on each other.

Behavior is unchanged from the original `boms.py` copies; only the names
lost their leading underscore since they are now a public cross-module
surface. `boms.py` keeps its own module-level aliases so its existing call
sites needed no edits.
"""
from datetime import date as date_type

from app.models.bom import BomLine


def version_key(version: str | None) -> tuple:
    """Numeric HVERSION ordering: '1.10' > '1.9' > '1.0'. Falls back to
    (0,) for blank/unparsable versions so they sort lowest, never crash."""
    if not version:
        return (0,)
    parts: list[int] = []
    for segment in str(version).split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def covers_date(line: BomLine, on_date: date_type) -> bool:
    if line.effective_from is not None and line.effective_from > on_date:
        return False
    if line.effective_to is not None and line.effective_to < on_date:
        return False
    return True
