"""Query helpers for the PA ↔ PO association (`pa_po_links`).

A PA may settle several POs. `payment_applications.po_id` is only the PRIMARY
one, so ANY question of the form "which POs does this PA pay" or "which POs
already have a PA" must be asked here — reading `po_id` alone silently ignores
every PO after the first, which is how a paid PO keeps nagging "Create Payment
Application" and how a PO detail page ends up showing no payment at all.

Each helper unions the link table with the header column rather than trusting
the link table alone. Migration `pa01_pa_po_links` backfilled a row for every
existing PA, and crud.pa.create writes one for every new PA, so in practice the
union is redundant — but a PA inserted by anything that bypasses that path (a
data migration, a repair script, an older importer) would otherwise vanish from
every one of these answers, and the failure is silent: a real payment stops
counting as a payment. The union costs a scan of an indexed column and removes
that entire class of failure, so it is not an optimisation worth reclaiming.
"""
import uuid

from sqlalchemy import Select, select

from app.models.pa import PaymentApplication
from app.models.pa_po_link import PaPoLink


def po_ids_with_pa(*conditions) -> Select:
    """PO ids covered by a PA, optionally narrowed by PA-level predicates
    (status, pa_type, ...)."""
    links = select(PaPoLink.po_id)
    header = select(PaymentApplication.po_id).where(PaymentApplication.po_id.is_not(None))
    if conditions:
        links = links.join(
            PaymentApplication, PaymentApplication.id == PaPoLink.pa_id
        ).where(*conditions)
        header = header.where(*conditions)
    return links.union(header)


def pa_ids_for_po(po_id: uuid.UUID) -> Select:
    """PA ids that pay this PO — primary or not."""
    return select(PaPoLink.pa_id).where(PaPoLink.po_id == po_id).union(
        select(PaymentApplication.id).where(PaymentApplication.po_id == po_id)
    )


def pa_ids_for_pos(po_ids) -> Select:
    """PA ids that pay any PO in `po_ids` (a list or a sub-select)."""
    return select(PaPoLink.pa_id).where(PaPoLink.po_id.in_(po_ids)).union(
        select(PaymentApplication.id).where(PaymentApplication.po_id.in_(po_ids))
    )


def po_ids_of_pa(pa_id: uuid.UUID) -> Select:
    """PO ids on one PA."""
    return select(PaPoLink.po_id).where(PaPoLink.pa_id == pa_id).union(
        select(PaymentApplication.po_id).where(
            PaymentApplication.id == pa_id, PaymentApplication.po_id.is_not(None)
        )
    )


def po_ids_of_pas(pa_ids) -> Select:
    """PO ids on any of `pa_ids` (a list or a sub-select)."""
    return select(PaPoLink.po_id).where(PaPoLink.pa_id.in_(pa_ids)).union(
        select(PaymentApplication.po_id).where(
            PaymentApplication.id.in_(pa_ids), PaymentApplication.po_id.is_not(None)
        )
    )
