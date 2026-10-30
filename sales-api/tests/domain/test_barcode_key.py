"""FR-OB-02 (product owner, 2026-10-30): barcodes compare in GTIN-14 form."""
import pytest

from app.domain.items import barcode_key


@pytest.mark.parametrize("value, key", [
    ("062639300012", "00062639300012"),     # UPC-A
    ("62639300012", "00062639300012"),      # the same, leading zero lost (Excel)
    ("0062639300012", "00062639300012"),    # EAN-13
    ("00062639300012", "00062639300012"),   # GTIN-14
    ("10062639300019", "10062639300019"),   # GTIN-14 with a packaging indicator: another code
    (" 0626-3930-0012 ", "00062639300012"), # digits only
    ("06263930", "00000006263930"),         # EAN-8
    ("1234567", None), ("123456789012345", None), ("", None), (None, None), ("n/a", None),
])
def test_barcode_key(value, key):
    assert barcode_key(value) == key
