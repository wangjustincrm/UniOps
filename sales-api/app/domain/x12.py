"""X12 basics (Appendix A.2, FR-OB-26, FR-OB-29a): reading any interchange
with the separators its ISA declares, and writing ours.

Writing: element `*`, segment `~` followed by a newline, sub-element `>`
(A.2). Every value written is plain ASCII: accented letters are
transliterated (é → e) and the change is reported; a character with no ASCII
form, or one of our separators inside a value, is an error naming the field
and the character — a "?" is never written (product owner, 2026-10-28).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

ELEMENT, SEGMENT, SUB_ELEMENT = "*", "~", ">"
_SEPARATORS = {ELEMENT, SEGMENT, SUB_ELEMENT}


@dataclass
class TextIssues:
    """What writing the values found: transliterations (noted on the
    document) and errors (the file is not sent)."""
    changed: list[str] = field(default_factory=list)   # "ship_to.name: Montréal → Montreal"
    errors: list[str] = field(default_factory=list)    # "ship_to.name: '北' has no ASCII form"


# Punctuation a phone keyboard types in an address ("Val-d’Or"): it has a plain form, NFKD just does not know it.
_TYPOGRAPHIC = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def ascii_value(name: str, value: object, issues: TextIssues) -> str:
    """`value` as plain ASCII X12 text (FR-OB-29a)."""
    text = "" if value is None else str(value).strip()
    text = re.sub(r"\s+", " ", text)
    text = text.translate(_TYPOGRAPHIC)
    out = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    if out != text:
        lost = [c for c in text if not unicodedata.normalize("NFKD", c).encode("ascii", "ignore")
                and not unicodedata.combining(c)]
        if lost:
            issues.errors.append(f"{name}: {lost[0]!r} has no ASCII form — edit the value")
            return out
        issues.changed.append(f"{name}: {text} → {out}")
    bad = sorted({c for c in out if c in _SEPARATORS})
    if bad:
        issues.errors.append(f"{name}: {bad[0]!r} is an EDI separator and cannot be sent — edit the value")
    return out


def segment(*elements: object) -> str:
    """One segment, trailing empty elements dropped (X12 style)."""
    parts = ["" if e is None else str(e) for e in elements]
    while len(parts) > 1 and parts[-1] == "":
        parts.pop()
    return ELEMENT.join(parts)


def interchange(segments: list[str]) -> bytes:
    return ("".join(f"{s}{SEGMENT}\n" for s in segments)).encode("ascii")


# ── reading (FR-OB-26) ───────────────────────────────────────────────────────
class X12Error(ValueError):
    """Not an X12 interchange this code can read."""


@dataclass(frozen=True)
class Separators:
    element: str
    segment: str
    sub_element: str


def separators(data: bytes | str) -> Separators:
    """From the ISA: the element separator is the 4th character, the
    sub-element separator ISA16 (position 104) and the segment terminator the
    character after it (FR-OB-26: never hard-coded)."""
    text = data.decode("ascii", "replace") if isinstance(data, bytes) else data
    text = text.lstrip("﻿ \r\n\t")
    if not text.startswith("ISA") or len(text) < 106:
        raise X12Error("not an X12 interchange (no ISA header)")
    return Separators(element=text[3], sub_element=text[104], segment=text[105])


def segments(data: bytes | str) -> list[list[str]]:
    """Every segment as a list of elements (the segment id first)."""
    text = data.decode("ascii", "replace") if isinstance(data, bytes) else data
    text = text.lstrip("﻿ \r\n\t")
    sep = separators(text)
    return [s.strip("\r\n ").split(sep.element) for s in text.split(sep.segment) if s.strip("\r\n ")]


def control(value: object) -> str:
    """A control number for matching: digits, leading zeros dropped (A.2)."""
    return str(int(str(value).strip())) if str(value).strip().isdigit() else str(value).strip()
