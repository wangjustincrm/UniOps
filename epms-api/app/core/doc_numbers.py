"""Document numbers as people type them, turned into the form the system stores.

"pr20260928-0001" and "20260928-0001" are PR-20260928-0001 to anyone reading
them, but an exact match on either found nothing and the Assistant said the
document did not exist (ISS-0024). The formats are the ones the numbering code
writes (crud/pr, gr, pa, po, agreement and invoice `_next_number`); the PRD
states the same ones (PR-006, GR-P-005, §3.12.5, §3.15.5, AGR-001, INV-005).

Only a value that fits a format is rewritten. Anything else — a legacy PMS
number, an NC number with a `-VOID1` suffix — is compared exactly as typed
(outer spaces trimmed): numbers outside these formats are not guaranteed to
be upper-case, so changing their case could make a real one miss.
"""
from __future__ import annotations

import re

# prefix -> pattern over the upper-cased, space-free value, and the canonical form.
_FORMATS: dict[str, tuple[re.Pattern, str]] = {
    "PR": (re.compile(r"^(?:PR)?-?(\d{8})-?(\d{4})$"), "PR-{0}-{1}"),
    "GR": (re.compile(r"^(?:GR)?-?(\d{8})-?(\d{4})$"), "GR-{0}-{1}"),
    "PA": (re.compile(r"^(?:PA)?-?(\d{8})-?(\d{4})$"), "PA-{0}-{1}"),
    "AGR": (re.compile(r"^(?:AGR)?-?(\d{6})-?(\d{4})$"), "AGR-{0}-{1}"),
    "INV": (re.compile(r"^(?:INV)?-?(\d{4})-?(\d{4,})$"), "INV-{0}-{1}"),
    # PO-<vendor POID>-YYMM-NN, NC collisions suffixed -2, -3 (NC-005). The
    # vendor code may itself be digits, so the prefix is required here and only
    # its missing hyphen is restored.
    "PO": (re.compile(r"^PO-?([A-Z0-9]+?)-(\d{4})-(\d{2,})((?:-\d+)?)$"),
           "PO-{0}-{1}-{2}{3}"),
}
PREFIXES = tuple(_FORMATS)


def normalize(prefix: str, value) -> str:
    """The stored form of a typed document number, or the value as typed."""
    pattern, canonical = _FORMATS[prefix]
    match = pattern.match(re.sub(r"\s+", "", str(value)).upper())
    return canonical.format(*match.groups()) if match else str(value).strip()


def digit_groups(value) -> list[str]:
    """The runs of digits in a typed number, for a "did you mean" search."""
    return re.findall(r"\d{2,}", str(value))


# The same formats, found inside a sentence. Only a complete number with its
# prefix counts: inside a question "20261015-0006" could be a PR, a GR or a PA.
_IN_TEXT = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"(?P<PR>PR-\d{8}-\d{4})|(?P<GR>GR-\d{8}-\d{4})|(?P<PA>PA-\d{8}-\d{4})"
    r"|(?P<AGR>AGR-\d{6}-\d{4})|(?P<INV>INV-\d{4}-\d{4,})"
    r"|(?P<PO>PO-[A-Za-z0-9]+-\d{4}-\d{2,}(?:-\d+)?)"
    r")(?![A-Za-z0-9])", re.I)


def find_all(text) -> list[tuple[str, str, int, int]]:
    """(prefix, number in its stored form, start, end) for every document
    number written in `text`, in order."""
    out = []
    for m in _IN_TEXT.finditer(str(text or "")):
        prefix = m.lastgroup
        out.append((prefix, normalize(prefix, m.group(0)), m.start(), m.end()))
    return out
