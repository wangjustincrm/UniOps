"""Business rules of the partner configuration (FR-OB-03, FR-OB-33).

Shape (types, lengths, closed sets) is the DTOs' job; what needs more than one
field, or another record, is decided here and in the service.
"""
from __future__ import annotations

from typing import Any

from app.platform.errors import InvalidInput


def check_partner(values: dict[str, Any]) -> None:
    """The rules on a partner's full, would-be state (after a change is applied)."""
    # FR-OB-33: invoicing by email needs the address to send it to.
    if values.get("invoice_method") == "EMAIL" and not values.get("invoice_email"):
        raise InvalidInput(
            "partner.invoice_email_required",
            "an invoice email address is required when the invoice method is EMAIL",
            {"field": "invoice_email"},
        )
