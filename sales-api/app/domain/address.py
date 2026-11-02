"""Canadian address rules, offline (no address service; the user, 2026-11-02):
the province a postal code belongs to (its first letter) and a PO box.

A manual order's ship-to is refused on a mismatch (AddressIn); an imported
order (Shopify, 850) with one goes to Review before its 940 (FR-OB-29a). A PO
box is never refused: carriers may not deliver there, so it is a warning on
the manual form and a flag on an imported order's detail."""
from __future__ import annotations

import re
from typing import Any

CA_PROVINCES = ("AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT")
CA_POSTAL = re.compile(r"^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z] ?\d[ABCEGHJ-NPRSTV-Z]\d$")

# The first letter of a postal code → the province(s) it belongs to (X serves two territories).
POSTAL_PROVINCES: dict[str, tuple[str, ...]] = {
    "A": ("NL",), "B": ("NS",), "C": ("PE",), "E": ("NB",), "G": ("QC",), "H": ("QC",), "J": ("QC",),
    "K": ("ON",), "L": ("ON",), "M": ("ON",), "N": ("ON",), "P": ("ON",), "R": ("MB",), "S": ("SK",),
    "T": ("AB",), "V": ("BC",), "X": ("NT", "NU"), "Y": ("YT",),
}

# "PO Box 12", "P.O. Box 12", "Post Office Box", "CP 34" / "C.P. 34" (case postale), "Case postale", "Box 56".
PO_BOX = re.compile(r"\b(?:p\.?\s*o\.?\s*box|post\s+office\s+box|case\s+postale|c\.?\s*p\.?\s+\d+|box\s*#?\s*\d+)",
                    re.IGNORECASE)


def postal_mismatch(province: str | None, zip_code: str | None) -> str | None:
    """Why a Canadian postal code does not belong to the province, or None
    (also None when either is missing or the code is not a postal code)."""
    p, z = (province or "").strip().upper(), (zip_code or "").strip().upper()
    if not p or not z:
        return None
    allowed = POSTAL_PROVINCES.get(z[0])
    if allowed is None or p in allowed:
        return None
    return f"postal code {z} is in {'/'.join(allowed)}, not {p}"


def is_po_box(*lines: str | None) -> bool:
    return any(PO_BOX.search(x or "") for x in lines)


def _is_ca(a: dict[str, Any]) -> bool:
    return str(a.get("country") or a.get("country_code") or "CA").strip().upper() in ("CA", "CAN", "CANADA")


def address_problem(a: dict[str, Any] | None) -> str | None:
    """An imported ship-to that cannot go on a 940 as it is (Review): the postal code's province."""
    if not a or not _is_ca(a):
        return None
    return postal_mismatch(a.get("province") or a.get("province_code"), a.get("zip"))


def address_flags(a: dict[str, Any] | None) -> list[str]:
    """Non-blocking warnings shown with the address."""
    if a and is_po_box(a.get("address1"), a.get("address2")):
        return ["PO box: the carrier may not deliver there"]
    return []
