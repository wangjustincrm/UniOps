from datetime import date
from decimal import Decimal

from app.services.wms_sync.transform import transform_lot

MAPPING = {"01": "hold", "02": "available", "04": "hold"}


def _raw(**over):
    base = {"warehouseid": "CANADA", "sku": "CF0086", "lotnum": "HGC1976532",
            "qty": Decimal("420"), "qtyallocated": Decimal("0"), "qtyonhold": Decimal("0"),
            "lotatt01": "2025-01-03", "lotatt02": "2027-01-02", "lotatt03": "2025-01-20",
            "lotatt05": "20250103 291041001", "lotatt08": "02", "lotatt13": "0000131",
            "lotatt14": "CASN2502100006*189", "edittime": None}
    base.update(over)
    return base


def test_release_lot_maps_available():
    row = transform_lot(_raw(), MAPPING, today=date(2026, 8, 3))
    assert row["mapped_status"] == "available"
    assert row["expiry_date"] == date(2027, 1, 2) and row["material_code"] == "CF0086"


def test_expired_lot_overrides_to_expired():
    row = transform_lot(_raw(lotatt02="2026-01-01"), MAPPING, today=date(2026, 8, 3))
    assert row["mapped_status"] == "expired"


def test_unknown_status_defaults_hold():
    row = transform_lot(_raw(lotatt08="99"), MAPPING, today=date(2026, 8, 3))
    assert row["mapped_status"] == "hold"


def test_blank_dates_tolerated():
    row = transform_lot(_raw(lotatt01=None, lotatt02=None), MAPPING, today=date(2026, 8, 3))
    assert row["expiry_date"] is None and row["mapped_status"] == "available"
