"""The prepayment cap that applies to a vendor (PRD PP-001, PP-003, VND-006).

One definition for the code that refuses a prepayment (api/v1/pa.py
_assert_prepayment_cap) and the Assistant that says what a vendor may be
prepaid: asked about a vendor with no Max Prepayment % of its own, the
Assistant answered "not set, no prepayment policy" instead of the system cap
that applies (ISS-0021 #6, ISS-0066).
"""
from __future__ import annotations

from decimal import Decimal


def effective_cap(vendor_pct, system_pct) -> tuple[Decimal, str]:
    """The cap and where it comes from: ``("vendor", ...)`` when the vendor
    has its own Max Prepayment %, else ``("system", ...)`` — Admin Panel →
    Prepayment Config. Never above the system cap (PP-003). 0 = cannot be
    prepaid (PP-001)."""
    system = Decimal(str(system_pct or 0))
    if vendor_pct is None or vendor_pct == "":
        return system, "system"
    return min(Decimal(str(vendor_pct)), system), "vendor"
