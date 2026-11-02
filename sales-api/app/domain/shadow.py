"""The shadow-run comparison (design §5.4): a 940 Zoho sent against the 940
Sales would send for the same order, segment by segment.

Volatile fields are masked before comparing — the explicit list below; every
other element counts. Trailing empty elements are dropped first (X12 lets a
sender omit them, so `N9*DP*` and `N9*DP` are the same segment). The rows
keep the original segments for display; `signature` identifies the set of
differences, so a person's "expected difference" holds while it stays the same.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from difflib import SequenceMatcher

from app.domain.x12 import X12Error, segments

STATUSES = ("Match", "Differs", "Expected difference", "No Sales order", "Sales only", "Unreadable")
MASK = "<ignored>"

# segment id → element positions (1-based) that differ by design between two
# files for the same order.
IGNORED: dict[str, tuple[int, ...]] = {
    "ISA": (9, 10, 13),  # interchange date, time, control number
    "GS": (4, 5, 6),     # group date, time, control number
    "ST": (2,),          # transaction set control number
    "SE": (2,),
    "GE": (2,),
    "IEA": (2,),
    "W05": (2,),         # the sending system's own order number (the PO in W05 03 still counts)
}
# (segment id, qualifier in element 1) → positions: the qualified value is the
# day the file was made, or each system's own number.
IGNORED_QUALIFIED: dict[tuple[str, str], tuple[int, ...]] = {
    ("G62", "10"): (2, 3, 4),  # requested ship date (+ time) = the day the file is made
    ("G62", "38"): (2, 3, 4),  # ship-not-later-than = the day the file is made
    ("N9", "E1"): (2,),        # CRM-940_<9-digit>: each system's internal number
}


def ignore_rules() -> list[str]:
    """The masked fields, readable (shown on the page)."""
    out = [f"{seg} {', '.join(f'{seg}{p:02d}' for p in pos)}" for seg, pos in IGNORED.items()]
    out += [f"{seg}*{q}: {', '.join(f'{seg}{p:02d}' for p in pos)}" for (seg, q), pos in IGNORED_QUALIFIED.items()]
    return out


def _trim(seg: list[str]) -> list[str]:
    out = list(seg)
    while len(out) > 1 and out[-1].strip() == "":
        out.pop()
    return out


def masked(seg: list[str]) -> list[str]:
    seg = _trim(seg)
    positions = IGNORED.get(seg[0], ()) + IGNORED_QUALIFIED.get((seg[0], seg[1] if len(seg) > 1 else ""), ())
    out = [MASK if i in positions else v for i, v in enumerate(seg)]
    while len(out) > 1 and out[-1] == MASK:
        out.pop()  # an ignored element at the end may be present on one side only (a G62 time)
    return out


@dataclass(frozen=True)
class Comparison:
    rows: list[dict]          # [{"zoho": str | None, "sales": str | None, "same": bool}]
    differences: int
    signature: str | None     # None when the files are the same


def compare(zoho: bytes | str, sales: bytes | str) -> Comparison:
    """Raises X12Error when either side is not an X12 interchange."""
    z, s = [_trim(x) for x in segments(zoho)], [_trim(x) for x in segments(sales)]
    zk, sk = ["*".join(masked(x)) for x in z], ["*".join(masked(x)) for x in s]
    rows: list[dict] = []
    for op, i1, i2, j1, j2 in SequenceMatcher(None, zk, sk, autojunk=False).get_opcodes():
        if op == "equal":
            rows += [{"zoho": "*".join(z[i]), "sales": "*".join(s[j]), "same": True}
                     for i, j in zip(range(i1, i2), range(j1, j2))]
            continue
        # Inside a changed block, pair segments of the same id (W01 with W01),
        # so a missing line shows alone instead of next to the SE.
        zs, ss = list(range(i1, i2)), list(range(j1, j2))
        ids = SequenceMatcher(None, [z[i][0] for i in zs], [s[j][0] for j in ss], autojunk=False)
        for op2, a1, a2, b1, b2 in ids.get_opcodes():
            for k in range(max(a2 - a1, b2 - b1)):
                zi = zs[a1 + k] if a1 + k < a2 else None
                sj = ss[b1 + k] if b1 + k < b2 else None
                if op2 != "equal" and zi is not None and sj is not None:  # different ids: one row each
                    rows += [{"zoho": "*".join(z[zi]), "sales": None, "same": False},
                             {"zoho": None, "sales": "*".join(s[sj]), "same": False}]
                    continue
                rows.append({"zoho": "*".join(z[zi]) if zi is not None else None,
                             "sales": "*".join(s[sj]) if sj is not None else None, "same": False})
    diffs = [r for r in rows if not r["same"]]
    sig = None
    if diffs:
        key = [["*".join(masked(r["zoho"].split("*"))) if r["zoho"] else None,
                "*".join(masked(r["sales"].split("*"))) if r["sales"] else None] for r in diffs]
        sig = hashlib.sha256(json.dumps(key).encode()).hexdigest()
    return Comparison(rows, len(diffs), sig)


@dataclass(frozen=True)
class Reference940:
    order_number: str | None   # W05 02: Zoho's / Shopify's order number
    po: str | None             # W05 03: the retailer's PO
    sets: int


def reference_940(data: bytes) -> Reference940:
    """The keys of a Zoho 940 (raises X12Error when it is not one)."""
    segs = segments(data)
    sets = [s for s in segs if s[0] == "ST"]
    if not sets or any(s[1:2] != ["940"] for s in sets):
        raise X12Error("not a 940")
    w05 = next((s for s in segs if s[0] == "W05"), None)
    if w05 is None:
        raise X12Error("940 without W05")
    get = lambda i: (w05[i].strip() or None) if len(w05) > i else None  # noqa: E731
    return Reference940(get(2), get(3), len(sets))
