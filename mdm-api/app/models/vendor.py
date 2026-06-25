"""Vendor — compatibility re-export (Phase 0-B3).

The vendors table was superseded by business_partners (suppliers are rows
with is_supplier=true). All mdm read paths now go through BusinessPartner;
this alias keeps existing imports working. BusinessPartner is a column
superset of the old Vendor model, so every schema still validates.
"""
from app.models.business_partner import BusinessPartner as Vendor  # noqa: F401
