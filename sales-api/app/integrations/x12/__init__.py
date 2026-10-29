"""X12 toolkit (design §13.10).

- `Segment` renders one segment; separator characters inside an element are
  replaced by a space (X12 has no escape sequence). Text is written as ASCII:
  accented and common letters are transliterated (é → e, ß → ss) and each
  change is noted; a character with no ASCII equivalent raises
  `UnmappableCharacter` naming the element and the character, so the caller
  sends the order to review — "?" is never written (product owner,
  2026-10-28).
- `Envelope` wraps transaction sets in ISA/GS/ST…SE/GE/IEA with the values the
  business code supplies (sender/receiver ids, versions, usage indicator).
- `read(raw)` parses an interchange, taking the separators from the ISA.
- `ControlSequence` draws control numbers from a database sequence whose name
  the business code supplies.
- `pad_control` / `same_control` are the zero-padding rules used by both
  building and matching (partners pad some control numbers and not others).
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text


class X12Error(ValueError):
    pass


class UnmappableCharacter(X12Error):
    def __init__(self, element: str, char: str, value: str) -> None:
        super().__init__(
            f"{element}: character {char!r} (U+{ord(char):04X}) has no ASCII equivalent in {value!r}; "
            "edit the value and build again")
        self.element = element
        self.char = char
        self.value = value


@dataclass(frozen=True)
class Separators:
    element: str = "*"
    component: str = ">"
    segment: str = "~"
    # Written after each segment terminator (readers ignore it).
    newline: str = "\n"

    @property
    def reserved(self) -> set[str]:
        return {self.element, self.component, self.segment}


DEFAULT = Separators()

# Letters NFKD does not decompose, and punctuation a keyboard or a web shop
# puts into addresses.
_TRANSLIT = {
    "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ø": "o", "Ø": "O", "ł": "l", "Ł": "L",
    "đ": "d", "Đ": "D", "ð": "d", "Ð": "D", "þ": "th", "Þ": "TH", "ı": "i",
    "‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
    "–": "-", "—": "-", "‐": "-", "‑": "-", "−": "-", "…": "...", "•": "-", "·": ".",
    "\u00a0": " ", "\u2007": " ", "\u202f": " ", "\u2009": " ", "№": "No",
}


def to_ascii(value: str, element: str) -> tuple[str, bool]:
    """(ASCII text, changed?). Raises UnmappableCharacter for anything with no
    ASCII equivalent."""
    if value.isascii():
        return value, False
    out = []
    for ch in value:
        if ch.isascii():
            out.append(ch)
            continue
        rep = _TRANSLIT.get(ch)
        if rep is None:
            base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
            rep = base if base and base.isascii() else None
        if rep is None:
            raise UnmappableCharacter(element, ch, value)
        out.append(rep)
    return "".join(out), True


def clean(value: object, seps: Separators = DEFAULT, *, element: str = "value", notes: list[str] | None = None) -> str:
    s = "" if value is None else str(value)
    ascii_s, changed = to_ascii(s, element)
    if changed and notes is not None:
        notes.append(f"{element}: {s!r} written as {ascii_s!r}")
    for ch in seps.reserved | {"\r", "\n"}:
        ascii_s = ascii_s.replace(ch, " ")
    return ascii_s


@dataclass(frozen=True)
class Segment:
    tag: str
    elements: tuple[str, ...]

    def __init__(self, tag: str, *elements: object) -> None:
        if not re.fullmatch(r"[A-Z0-9]{2,3}", tag):
            raise X12Error(f"bad segment tag {tag!r}")
        object.__setattr__(self, "tag", tag)
        object.__setattr__(self, "elements", tuple("" if e is None else str(e) for e in elements))

    def render(self, seps: Separators = DEFAULT, notes: list[str] | None = None) -> str:
        parts = [self.tag, *(clean(e, seps, element=f"{self.tag}{i:02d}", notes=notes)
                             for i, e in enumerate(self.elements, start=1))]
        while len(parts) > 1 and parts[-1] == "":  # no trailing empty elements
            parts.pop()
        return seps.element.join(parts) + seps.segment


def pad_control(n: int | str, width: int = 9) -> str:
    s = str(int(str(n).strip()))
    if len(s) > width:
        raise X12Error(f"control number {n} is wider than {width} digits")
    return s.zfill(width)


def same_control(a: str | int | None, b: str | int | None) -> bool:
    """`000016468` == `16468`; anything non-numeric compares as text."""
    if a is None or b is None:
        return False
    sa, sb = str(a).strip(), str(b).strip()
    if sa.isdigit() and sb.isdigit():
        return int(sa) == int(sb)
    return sa == sb


def _fixed(value: str, width: int, what: str, seps: Separators) -> str:
    value = clean(value, seps, element=what)
    if len(value) > width:
        raise X12Error(f"{what} {value!r} is longer than {width} characters")
    return value.ljust(width)


@dataclass(frozen=True)
class EnvelopeParams:
    sender_qualifier: str  # ISA05
    sender_id: str  # ISA06 / GS02
    receiver_qualifier: str  # ISA07
    receiver_id: str  # ISA08 / GS03
    functional_id: str  # GS01
    isa_version: str = "00401"  # ISA12
    gs_version: str = "004010"  # GS08
    usage: str = "P"  # ISA15: P production, T test
    ack_requested: str = "0"  # ISA14
    repetition: str = "U"  # ISA11


@dataclass
class TransactionSet:
    code: str  # ST01, the three-digit transaction set id
    body: list[Segment] = field(default_factory=list)  # between ST and SE


class Envelope:
    """One interchange with one functional group (what outbound files need).
    ISA13, GS06 and every ST02 use the same control number by default."""

    def __init__(self, params: EnvelopeParams, seps: Separators = DEFAULT) -> None:
        self.p = params
        self.seps = seps

    def render(
        self,
        sets: Sequence[TransactionSet],
        *,
        control: int,
        at: datetime,
        group_control: int | None = None,
        set_controls: Sequence[int] | None = None,
        notes: list[str] | None = None,
    ) -> bytes:
        """`notes` (optional) receives one line per transliterated value."""
        if not sets:
            raise X12Error("an interchange needs at least one transaction set")
        p, s = self.p, self.seps
        gctl = group_control if group_control is not None else control
        sctls = list(set_controls) if set_controls is not None else [control] * len(sets)
        if len(sctls) != len(sets):
            raise X12Error("one ST control number per transaction set")
        isa = Segment(
            "ISA", "00", " " * 10, "00", " " * 10,
            p.sender_qualifier, _fixed(p.sender_id, 15, "ISA06", s),
            p.receiver_qualifier, _fixed(p.receiver_id, 15, "ISA08", s),
            at.strftime("%y%m%d"), at.strftime("%H%M"), p.repetition, p.isa_version,
            pad_control(control), p.ack_requested, p.usage, s.component,
        )
        # ISA is fixed-width: render it verbatim (no trailing-element trimming,
        # and ISA16 is the component separator itself).
        out = [s.element.join([isa.tag, *isa.elements]) + s.segment]
        out.append(Segment(
            "GS", p.functional_id, p.sender_id.strip(), p.receiver_id.strip(),
            at.strftime("%Y%m%d"), at.strftime("%H%M"), pad_control(gctl), "X", p.gs_version,
        ).render(s, notes))
        for ts, sctl in zip(sets, sctls):
            stc = pad_control(sctl)
            body = [Segment("ST", ts.code, stc), *ts.body]
            out.extend(seg.render(s, notes) for seg in body)
            out.append(Segment("SE", len(body) + 1, stc).render(s))
        out.append(Segment("GE", len(sets), pad_control(gctl)).render(s))
        out.append(Segment("IEA", 1, pad_control(control)).render(s))
        # Strict: everything above is ASCII already; never a "?".
        return s.newline.join(out).encode("ascii") + s.newline.encode()


# ── reading ──────────────────────────────────────────────────────────────────


@dataclass
class ParsedSegment:
    tag: str
    elements: list[str]

    def get(self, i: int, default: str = "") -> str:
        """1-based like the X12 reference numbers (W06-02 → get(2))."""
        return self.elements[i - 1] if 0 < i <= len(self.elements) else default


@dataclass
class Transaction:
    code: str
    control: str
    segments: list[ParsedSegment]  # between ST and SE
    declared_count: int | None  # SE01

    @property
    def count_ok(self) -> bool:
        return self.declared_count == len(self.segments) + 2


@dataclass
class Group:
    functional_id: str
    control: str
    version: str
    transactions: list[Transaction]


@dataclass
class Interchange:
    separators: Separators
    sender_id: str
    receiver_id: str
    control: str
    usage: str
    groups: list[Group]

    def transactions(self) -> Iterable[Transaction]:
        for g in self.groups:
            yield from g.transactions


def read(raw: bytes | str) -> Interchange:
    data = raw.decode("ascii", errors="replace") if isinstance(raw, bytes) else raw
    data = data.lstrip("﻿ \r\n\t")
    if not data.startswith("ISA") or len(data) < 106:
        raise X12Error("not an X12 interchange (no ISA header)")
    element, component, terminator = data[3], data[104], data[105]
    seps = Separators(element=element, component=component, segment=terminator)
    segments: list[ParsedSegment] = []
    for chunk in data.split(terminator):
        chunk = chunk.strip("\r\n\t ")
        if not chunk:
            continue
        parts = chunk.split(element)
        segments.append(ParsedSegment(parts[0], parts[1:]))

    isa = segments[0]
    if isa.tag != "ISA" or len(isa.elements) < 16:
        raise X12Error("malformed ISA segment")
    ic = Interchange(seps, isa.get(6).strip(), isa.get(8).strip(), isa.get(13).strip(), isa.get(15), [])
    group: Group | None = None
    current: Transaction | None = None
    for seg in segments[1:]:
        if seg.tag == "GS":
            group = Group(seg.get(1), seg.get(6), seg.get(8), [])
            ic.groups.append(group)
        elif seg.tag == "ST":
            if group is None:
                raise X12Error("ST outside a functional group")
            current = Transaction(seg.get(1), seg.get(2), [], None)
            group.transactions.append(current)
        elif seg.tag == "SE":
            if current is None:
                raise X12Error("SE without ST")
            current.declared_count = int(seg.get(1)) if seg.get(1).isdigit() else None
            current = None
        elif seg.tag in ("GE", "IEA"):
            continue
        elif current is not None:
            current.segments.append(seg)
    if current is not None:
        raise X12Error(f"transaction set {current.code} {current.control} has no SE")
    return ic


class ControlSequence:
    """Control numbers from one PostgreSQL sequence (name supplied by the
    business code; created by its migration)."""

    def __init__(self, sequence_name: str) -> None:
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", sequence_name):
            raise ValueError(f"bad sequence name {sequence_name!r}")
        self.sequence_name = sequence_name

    async def next(self, session) -> int:
        return int((await session.execute(text(f"SELECT nextval('{self.sequence_name}')"))).scalar_one())
