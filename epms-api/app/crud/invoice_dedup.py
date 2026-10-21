"""One rule for "this invoice is already in the system" (PRD INV-004).

Every path that gives an invoice row a vendor and a vendor invoice # goes
through here: POST /invoices (PO, agreement and Direct uploads), PATCH
/invoices/{id} when the number changes, the Freight courier upload, and the
upload form's live check (GET /invoices/duplicate-check). The database holds
the same rule as a partial unique index, so two uploads racing each other, a
script, or a path added later without calling this still cannot store a
second copy.

The rule
--------
Same vendor + same invoice key, against EVERY invoice whatever its status and
whoever can see it. Until 2026-10 the only check was the upload form, and it
compared against the invoices its user could see — a PO-scoped requester could
not see AP's unmatched uploads, and nine duplicate pairs got in that way.

* Invoice key = the number with everything but letters and digits removed,
  upper-cased, leading zeros dropped: "INV 000123", "inv-123" and "INV123"
  are one invoice; so are "000001" and "0000001". The expression is
  INVOICE_KEY_SQL below and nowhere else — the generated column, the unique
  index and every lookup are built from it, so Python and the database cannot
  disagree about what "the same number" is.
* A number whose key is empty ("-", "0", "N/A"…) is refused outright: it
  identifies nothing, so it could never be checked.
* Same vendor = same vendor id, or the same vendor name (case and surrounding
  spaces ignored). Vendor master has duplicate rows for one supplier, and the
  name catches an upload made against the other row. The database index can
  only hold the id half; the name half lives here.
* OA's own invoices (expense_invoices, same database) are checked too, by
  vendor id: Finance AP keeps one bill per vendor + number across both
  systems and silently drops the second one it is sent.

A vendor that genuinely reuses a number (WTC's monthly "000010") is entered
with a suffix ("000010-OCT"); there is deliberately no override.

Rows that were already duplicates when the index was introduced carry
dedup_exempt = true (migration id01). The index skips them so they could
stay; this lookup does NOT — they still block a new upload of that number.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import func, literal, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import INVOICE_KEY_SQL, UQ_VENDOR_INVOICE_KEY, Invoice


@dataclass(frozen=True)
class DuplicateHit:
    source: str            # "epms" | "oa"
    ref: str               # INV-2026-0123, or the OA PA number / id
    status: str | None
    vendor_invoice_number: str

    def describe(self) -> str:
        where = "EPMS" if self.source == "epms" else "OA"
        state = f", {self.status}" if self.status else ""
        return f"{where} {self.ref} ({self.vendor_invoice_number}{state})"


class DuplicateInvoice(Exception):
    """→ HTTP 409. `hit` is None when only the database index caught it."""

    def __init__(self, vendor_name: str, number: str, hit: DuplicateHit | None):
        self.hit = hit
        found = hit.describe() if hit else "another invoice"
        super().__init__(
            f"Duplicate invoice — {number.strip()} from {vendor_name} is already in the system: "
            f"{found}. If this is a different invoice that reuses the number, add a suffix "
            f"(e.g. {number.strip()}-OCT).")


class UncheckableInvoiceNumber(ValueError):
    """→ HTTP 422. The number has no letter or digit, so it identifies nothing."""

    def __init__(self, number: str):
        super().__init__(
            f"Vendor invoice # '{number}' has no letters or digits — enter the number "
            "printed on the invoice.")


def key_expr(value):
    """INVOICE_KEY_SQL applied to a column or a bound value."""
    return func.ltrim(func.upper(func.regexp_replace(value, "[^[:alnum:]]", "", "g")), "0")


async def invoice_key(db: AsyncSession, number: str) -> str:
    # Computed by the database, never in Python: [:alnum:] follows the
    # database's locale, and the stored column is computed the same way.
    return (await db.execute(select(key_expr(literal(number))))).scalar_one()


async def find_duplicate(
    db: AsyncSession,
    vendor_id: uuid.UUID,
    vendor_name: str,
    number: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> DuplicateHit | None:
    """The existing invoice `number` from this vendor duplicates, or None.

    Raises UncheckableInvoiceNumber when the number has no key."""
    key = await invoice_key(db, number)
    if not key:
        raise UncheckableInvoiceNumber(number)

    q = select(Invoice.internal_ref, Invoice.status, Invoice.vendor_invoice_number).where(
        Invoice.vendor_invoice_key == key,
        or_(Invoice.vendor_id == vendor_id,
            func.lower(func.btrim(Invoice.vendor_name)) == vendor_name.strip().lower()),
    )
    if exclude_id is not None:
        q = q.where(Invoice.id != exclude_id)
    row = (await db.execute(q.order_by(Invoice.created_at).limit(1))).first()
    if row is not None:
        return DuplicateHit("epms", row.internal_ref, row.status, row.vendor_invoice_number)

    # expense_invoices belongs to expense-api (same database); it is absent from
    # this service's test schema, hence the to_regclass guard.
    if (await db.execute(text("SELECT to_regclass('expense_invoices')"))).scalar_one() is not None:
        oa = (await db.execute(text(
            "SELECT coalesce(pa_number, id::text) AS ref, status, invoice_number "
            "FROM expense_invoices WHERE vendor_id = :v AND invoice_number IS NOT NULL "
            f"AND {INVOICE_KEY_SQL.format('invoice_number')} = :k "
            "ORDER BY created_at LIMIT 1"),
            {"v": vendor_id, "k": key})).first()
        if oa is not None:
            return DuplicateHit("oa", oa.ref, oa.status, oa.invoice_number)
    return None


async def ensure_unique(
    db: AsyncSession,
    vendor_id: uuid.UUID,
    vendor_name: str,
    number: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> None:
    hit = await find_duplicate(db, vendor_id, vendor_name, number, exclude_id=exclude_id)
    if hit is not None:
        raise DuplicateInvoice(vendor_name, number, hit)


def is_key_violation(exc: IntegrityError) -> bool:
    return UQ_VENDOR_INVOICE_KEY in str(exc.orig)


@asynccontextmanager
async def key_violation_as_duplicate(vendor_name: str, number: str):
    """Turn the unique index firing inside the block into DuplicateInvoice.

    This is the race the lookup cannot close: two uploads of one invoice both
    pass ensure_unique before either commits; the second one's flush waits on
    the first and then hits the index. Wrap the write that flushes the row."""
    try:
        yield
    except IntegrityError as e:
        if is_key_violation(e):
            raise DuplicateInvoice(vendor_name, number, None) from e
        raise
