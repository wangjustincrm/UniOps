"""The Zoho history import's reading of an export (app/scripts/import_zoho_history.py, 2026-11-10).
The whole import was run on Zoho's real export on a throwaway database; this keeps the pairing."""
import json
from datetime import datetime

from app.scripts.import_zoho_history import TOR, file_time, index

GS = b"ISA*00*~GS*OW*CANADAROYALMILK*9055651212*20260727*0943*1000003*X*006030~ST*940*1000003~"


def test_each_order_gets_its_source_940_997_945_and_810_in_the_order_zoho_sent_them(tmp_path):
    (tmp_path / "Radiant").mkdir(); (tmp_path / "TrueCommerce").mkdir(); (tmp_path / "Shopify").mkdir()
    r, t = tmp_path / "Radiant", tmp_path / "TrueCommerce"
    (r / "940-WM-1000003-PO1_20260727_094306.edi").write_bytes(GS)
    (r / "940-SHP-1000002-9001.edi").write_bytes(GS.replace(b"0943", b"0900"))      # no time in the name: the GS's
    (r / "940-WM-1000003-PO1_20260801_100000.edi").write_bytes(GS)                   # Zoho's resend: not a second order
    (r / "997-RADIANT-1000003-1_20260727_100000.edi").write_bytes(b"x")
    (r / "945-Walmart-1000003-1_20260728_100000.edi").write_bytes(b"x")
    (r / "945-Radiant-1000003-1_20260728_100000.edi").write_bytes(b"x")              # Radiant's own name: not an order's
    (t / "850-WM-1000003-PO1-20260727_090000.json").write_text("{}")
    (t / "810-WM-1000003-PO1-20260805_090000.json").write_text("{}")
    (tmp_path / "Shopify" / "page.json").write_text(json.dumps({"orders": [
        {"order_number": 9001, "updated_at": "2026-07-27T10:00:00-04:00"}, {"order_number": 9001, "updated_at": "2026-07-26T10:00:00-04:00"}]}))
    chains, notes = index(tmp_path)
    assert [c.label for c in chains] == ["SHP-9001", "WM-PO1"]  # by the time Zoho sent the 940
    shp, wm = chains
    assert shp.sent_at == datetime(2026, 7, 27, 9, 0, tzinfo=TOR) and [o["updated_at"][:10] for o in shp.snapshots] == ["2026-07-26", "2026-07-27"]
    assert (wm.zoho_number, wm.f850.name, wm.f810.name) == (1000003, "850-WM-1000003-PO1-20260727_090000.json", "810-WM-1000003-PO1-20260805_090000.json")
    assert [f.name for f in wm.f997] == ["997-RADIANT-1000003-1_20260727_100000.edi"]
    assert [f.name for f in wm.f945] == ["945-Walmart-1000003-1_20260728_100000.edi"]
    assert notes == {"940 sent again by Zoho": 1}


def test_a_file_time_is_toronto_local():
    assert file_time("940-WM-1-PO_20260727_094306.edi") == datetime(2026, 7, 27, 9, 43, 6, tzinfo=TOR)
    assert file_time("940-WM-1-PO.edi") is None
