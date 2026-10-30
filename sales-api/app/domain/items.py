"""Item Master rules (FR-OB-02, FR-OB-14)."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from app.platform.errors import InvalidInput

BASE_UNIT = "EA"


FACTOR_SCALE = Decimal("0.0001")  # sales_item_units.factor_ea is Numeric(12, 4)
TITLES_MAX = 500                  # sales_item_units.shopify_titles is String(500)
UNITS_HINT = "write units as UNIT=factor, separated by ; (e.g. EA=1; CASE=6 [Case (6 Cans), Case of 6])"


def normalise_unit(unit: str) -> str:
    return unit.strip().upper()


def factor(value: object) -> Decimal:
    return Decimal(str(value)).quantize(FACTOR_SCALE)


def title_key(s: str | None) -> str:
    """How a Shopify variant title and a unit name or alias are compared:
    lower-case letters and digits only (`2-Can` is `2can`)."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def barcode_key(value: object) -> str | None:
    """A barcode in GTIN-14 form: digits only, left-padded with zeros to 14
    (product owner, 2026-10-30). UPC-A 062639300012, EAN-13 0062639300012 and
    GTIN-14 00062639300012 are one code; a GTIN-14 with a packaging indicator
    (10062639300019, a case) is a different code from its each-level UPC.
    Fewer than 8 or more than 14 digits is no barcode."""
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return digits.zfill(14) if 8 <= len(digits) <= 14 else None


def titles_column(titles: list[str]) -> str | None:
    """The alias list as stored in sales_item_units.shopify_titles."""
    return ", ".join(titles) or None


def titles_list(column: str | None) -> list[str]:
    return [t.strip() for t in (column or "").split(",") if t.strip()]


def check_units(units: list[dict[str, Any]]) -> None:
    """Every item has an EA conversion of exactly 1; each unit once; every
    factor positive (AC-OB-14: a set without EA = 1 is refused)."""
    names = [u["unit"] for u in units]
    if len(names) != len(set(names)):
        raise InvalidInput("item.unit_twice", "a unit appears twice", {"field": "units"})
    ea = [u for u in units if u["unit"] == BASE_UNIT]
    if not ea or Decimal(ea[0]["factor_ea"]) != 1:
        raise InvalidInput("item.ea_required", "every item needs the unit EA with factor 1", {"field": "units"})
    if any(Decimal(u["factor_ea"]) <= 0 for u in units):
        raise InvalidInput("item.factor_positive", "every unit factor must be above 0", {"field": "units"})
    check_titles(units)


def check_titles(units: list[dict[str, Any]]) -> None:
    """Shopify titles (aliases) of one item: each names something (letters or
    digits), and no alias stands for two units — an alias equal to another
    unit's name or alias would make the title match a guess."""
    owner: dict[str, str] = {title_key(u["unit"]): u["unit"] for u in units}
    for u in units:
        titles = list(u.get("shopify_titles") or [])
        if len(titles_column(titles) or "") > TITLES_MAX:
            raise InvalidInput("item.titles_too_long", f"the Shopify titles of {u['unit']} are longer than "
                               f"{TITLES_MAX} characters", {"field": "units"})
        for title in titles:
            key = title_key(title)
            if not key:
                raise InvalidInput("item.title_empty", f"Shopify title {title!r} of {u['unit']} has no letters "
                                   "or digits", {"field": "units"})
            if owner.setdefault(key, u["unit"]) != u["unit"]:
                raise InvalidInput("item.title_twice", f"Shopify title {title!r} names both {owner[key]} and "
                                   f"{u['unit']}", {"field": "units"})


def units_text(units: list[dict[str, Any]]) -> str:
    """`EA=1; CASE=6 [Case (6 Cans)]` — how units appear in CSV and in the
    form; the bracket lists the unit's Shopify titles, when it has any."""
    def num(v: Any) -> str:
        d = Decimal(str(v)).normalize()
        return format(d, "f")

    def one(u: dict[str, Any]) -> str:
        titles = u.get("shopify_titles") or []
        return f"{u['unit']}={num(u['factor_ea'])}" + (f" [{', '.join(titles)}]" if titles else "")
    return "; ".join(one(u) for u in units)


_UNIT_PART = re.compile(r"^([^=\[\]]+)=([^=\[\]]+?)\s*(?:\[([^\[\]]*)\])?$")


def _split_outside_brackets(text: str) -> list[str]:
    parts, cur, depth = [], "", 0
    for ch in text:
        depth += {"[": 1, "]": -1}.get(ch, 0)
        if ch in ";," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    return [*parts, cur]


def parse_units(text: str) -> list[dict[str, Any]]:
    """The inverse of units_text. `;` (or `,`) separates units; inside the
    brackets `,` separates a unit's Shopify titles."""
    out = []
    for part in (p.strip() for p in _split_outside_brackets(text)):
        if not part:
            continue
        m = _UNIT_PART.match(part)
        if not m:
            raise ValueError(UNITS_HINT)
        unit, factor_text, titles = m.groups()
        out.append({"unit": normalise_unit(unit), "factor_ea": factor_text.strip(),
                    "shopify_titles": [t.strip() for t in (titles or "").split(",") if t.strip()]})
    return out
