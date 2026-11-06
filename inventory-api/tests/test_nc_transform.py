"""Row shaping: NC's padding, '~' and string dates must not leak into the mirror."""
from datetime import date, datetime
from decimal import Decimal

from app.services.nc_sync import transform as t
from app.services.nc_sync.service import shape_extract


def test_clean_str_undoes_char_padding_and_tilde():
    assert t.clean_str("IA                  ") == "IA"
    assert t.clean_str("~") is None
    assert t.clean_str("~   ") is None
    assert t.clean_str("   ") is None
    assert t.clean_str(None) is None
    # A real value that merely contains a tilde is kept.
    assert t.clean_str("A~B") == "A~B"


def test_parse_nc_datetime_handles_both_shapes_and_garbage():
    assert t.parse_nc_datetime("2026-08-31 23:15:00") == datetime(2026, 8, 31, 23, 15)
    assert t.parse_nc_datetime("2026-08-31") == datetime(2026, 8, 31)
    assert t.parse_nc_datetime("2026-08-31 00:00:00   ") == datetime(2026, 8, 31)
    assert t.parse_nc_datetime("~") is None
    assert t.parse_nc_datetime("31/08/2026") is None  # kept raw, never fails the sync
    assert t.parse_nc_date("2025-04-20 00:00:00") == date(2025, 4, 20)


def test_ic_flow_keeps_raw_date_and_parses_wall_clock():
    r = t.ic_flow({"pk_flow": "F1", "dbizdate": "2026-10-14 21:15:00", "ninnum": Decimal("5"),
                   "noutnum": None, "fbillflag": 2, "vbatchcode": "~"})
    assert r["dbizdate"] == "2026-10-14 21:15:00"
    assert r["biz_time"] == datetime(2026, 10, 14, 21, 15)  # naive, NC's own clock
    assert r["vbatchcode"] is None
    assert r["fbillflag"] == 2


def test_material_name_prefers_english_and_falls_back():
    assert t.material({"pk_material": "M", "code": "CR0025", "name": "乳糖", "ename": "Lactose"})["name"] == "Lactose"
    m = t.material({"pk_material": "M", "code": "M0438", "name": "零件", "ename": None})
    assert m["name"] == "零件" and m["name_local"] == "零件"


def test_warehouse_costed_flag_only_explicit_n_excludes():
    assert t.warehouse({"pk_stordoc": "W", "code": "SC16", "iscalculatedinvcost": "N"})["iscalculatedinvcost"] is False
    assert t.warehouse({"pk_stordoc": "W", "code": "SC11", "iscalculatedinvcost": "Y"})["iscalculatedinvcost"] is True
    assert t.warehouse({"pk_stordoc": "W", "code": "SC11", "iscalculatedinvcost": "~"})["iscalculatedinvcost"] is True


def test_gl_line_unposted_is_null_tallydate():
    r = t.gl_line({"pk_detail": "D", "pk_voucher": "V", "year": "2026", "period": "10",
                   "account_code": "1404", "tallydate": "~                  ",
                   "pk_system": "IA                  ", "prepareddate": "2026-10-03 00:00:00"})
    assert r["tallydate"] is None
    assert r["pk_system"] == "IA"
    assert r["prepared_time"] == datetime(2026, 10, 3)


def test_shape_extract_counts_unusable_and_duplicate_rows_instead_of_dropping_silently():
    raw = {
        "tables": {
            "warehouse": [
                {"pk_stordoc": "W1", "code": "SC11"},
                {"pk_stordoc": "W2", "code": "~"},        # unusable: no code
                {"pk_stordoc": "W1", "code": "SC11"},     # duplicate pk
            ],
        },
        "filtered": {"ia_ledger_deleted": 7},
    }
    shaped, filtered = shape_extract(raw)
    assert [w["code"] for w in shaped["warehouse"]] == ["SC11"]
    assert filtered["warehouse_unusable"] == 1
    assert filtered["warehouse_duplicate_pk"] == 1
    assert filtered["ia_ledger_deleted"] == 7   # extract-time counts pass through
    assert shaped["ic_flow"] == []              # absent tables become empty, not KeyError
