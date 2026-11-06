"""Vendor payment terms: any net<N> day count, not just the form presets.

Pure schema tests — no DB. The same rule lives in mdm-api (the owner) and in
epms/src/lib/paymentTerms.ts; finance-api's AP cash-flow reads N out of the
stored code, so what is accepted here must be exactly `net<N>`/cod/prepayment.
"""
import pytest
from pydantic import ValidationError

from app.schemas.vendor import (
    ErpVendorImportDefaults, VendorCreate, VendorCsvRow, VendorUpdate,
)

_BASE = {"code": "CIN", "name": "Cintas", "category": "Sundry"}


@pytest.mark.parametrize("raw,stored", [
    ("net30", "net30"),
    ("net21", "net21"),          # the reason this exists
    ("net14", "net14"),
    ("Net 21", "net21"),         # hand-made CSV spelling
    (" NET45 ", "net45"),
    ("net007", "net7"),
    ("cod", "cod"),
    ("Prepayment", "prepayment"),
])
def test_accepts_and_normalises(raw, stored):
    assert VendorCreate(**_BASE, payment_terms=raw).payment_terms == stored
    assert VendorUpdate(payment_terms=raw).payment_terms == stored
    assert VendorCsvRow(**_BASE, payment_terms=raw).payment_terms == stored
    assert ErpVendorImportDefaults(payment_terms=raw).payment_terms == stored


@pytest.mark.parametrize("raw", [
    "net0", "net366", "net21.5", "net", "21", "prepaid", "net45x", "",
])
def test_rejects_codes_the_forecast_cannot_read(raw):
    # "prepaid" is what the ERP-import drawer used to offer; the cash-flow
    # forecast never recognised it, so those vendors silently went undated.
    with pytest.raises(ValidationError):
        VendorCreate(**_BASE, payment_terms=raw)
    with pytest.raises(ValidationError):
        VendorUpdate(payment_terms=raw)


def test_update_without_terms_leaves_them_alone():
    assert VendorUpdate(name="x").payment_terms is None


def test_default_is_net30():
    assert VendorCreate(**_BASE).payment_terms == "net30"
