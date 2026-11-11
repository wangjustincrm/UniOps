from pathlib import Path

from app.scripts.import_old_files import envelope_at, from_us, order_key, set_type

ISA = ["ISA", "00", "", "00", "", "ZZ", "CANROYALMILK   ", "ZZ", "RADIANT", "250305", "1530", "U", "00401", "000005049", "0", "P", ">"]


def test_a_shopify_940_is_found_by_its_file_name_else_by_w05():
    segs = [ISA, ["GS", "OW", "CANROYALMILK"], ["ST", "940", "0001"], ["W05", "N", "5049"]]
    assert order_key(Path("CRM-940_000005049_Shopify_5049.EDI"), segs) == ("SHOPIFY", "5049")
    assert order_key(Path("CRM-940_000000020.EDI"), [ISA, ["W05", "N", "0001082"]]) == ("SHOPIFY", "1082")
    assert order_key(Path("x.edi"), [ISA, ["W05", "N", "1000053", "4885104791"]]) == ("EDI_850", "4885104791")
    assert order_key(Path("x.edi"), [ISA]) is None


def test_envelope_time_type_and_sender():
    segs = [ISA, ["GS", "FA", "1234567890"], ["ST", "997", "0001"]]
    assert envelope_at(segs).strftime("%Y-%m-%d %H:%M") == "2025-03-05 15:30"
    assert set_type(segs) == "997"
    assert from_us(segs)
    assert not from_us([["ISA", "", "", "", "", "", "RADIANT"], ["GS", "OW", "RADIANT"]])
