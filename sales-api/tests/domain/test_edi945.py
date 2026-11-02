"""945 parsing (Appendix A.6, FR-OB-25b) and the order key (FR-OB-28)."""
from datetime import date
from decimal import Decimal

import pytest

from app.domain.edi945 import order_key, parse_945
from app.domain.x12 import X12Error


def x945(body: list[str], ctl="000000777") -> bytes:
    isa = ("ISA*00*          *00*          *12*9055651212     *ZZ*CANADAROYALMILK*261102*1000*U*00401*"
           f"{ctl}*0*P*>")
    segs = [isa, "GS*SW*9055651212*CANADAROYALMILK*20261102*1000*777*X*004010", "ST*945*0001", *body,
            f"SE*{len(body) + 2}*0001", "GE*1*777", f"IEA*1*{ctl}"]
    return ("~\n".join(segs) + "~\n").encode()


PALLET = ["W06*N*000001234*20261101*SH-9**PO-55", "N1*ST*DC 6080*UL*0012345000011", "N3*1 DC Road",
          "N4*Mississauga*ON*L5T 1A1*CA", "N9*BM*BOL-1", "N9*MB*PRO-7", "DTM*011*20261101", "G62*10*20261030",
          "W27*LT*WIFS", "LX*1", "MAN*GM*00077777770077782891",
          "W12*CP*300*298*0*EA*628347940140*VN*CF0092", "N9*LT*26C141125110131", "DTM*036*20280519",
          "W12*CP*300*2*0*EA*628347940140*VN*CF0092", "N9*LT*26E037125110051",
          "LX*2", "MAN*GM*00077777770077782907", "N9*2I*1Z999", "W12*CC*12*12*0*EA**VN*CF0010*LOT-X*18.5"]


def test_every_w12_under_every_lx_with_lots_expiry_and_sscc():
    [sh] = parse_945(x945(PALLET))
    assert (sh.control, sh.group_control, sh.order_key_raw, sh.po, sh.shipment_id) == ("1", "777", "000001234", "PO-55", "SH-9")
    assert [(r.lx_index, r.w12_index, r.sscc, r.item_code, r.qty_ordered, r.qty_shipped, r.lot) for r in sh.rows] == [
        (1, 1, "00077777770077782891", "CF0092", 300, 298, "26C141125110131"),   # AC-OB-34 / 35
        (1, 2, "00077777770077782891", "CF0092", 300, 2, "26E037125110051"),
        (2, 1, "00077777770077782907", "CF0010", 12, 12, "LOT-X")]              # W12-09 when no N9*LT
    assert sh.rows[0].expiry == date(2028, 5, 19) and sh.rows[1].expiry is None
    assert sh.rows[2].weight == Decimal("18.5") and sh.rows[2].tracking == "1Z999" and sh.rows[0].tracking is None
    assert (sh.mode, sh.carrier, sh.bol, sh.carrier_ref) == ("LT", "WIFS", "BOL-1", "PRO-7")
    assert sh.parties["ST"].id == "0012345000011" and sh.parties["ST"].zip == "L5T 1A1"


def test_ac77_ship_date_from_dtm_else_g62():
    [sh] = parse_945(x945(PALLET))
    assert (sh.ship_date, sh.ship_date_source) == (date(2026, 11, 1), "DTM")
    [sh] = parse_945(x945([s if not s.startswith("DTM*011") else "DTM*011*" for s in PALLET]))
    assert (sh.ship_date, sh.ship_date_source) == (date(2026, 10, 30), "G62")


def test_the_order_key_falls_back_to_w06_06_and_tracking_on_the_header():
    [sh] = parse_945(x945(["W06*N*****PO-9", "N9*2I*TRK-1", "LX*1", "W12*CC*1*1*0*EA**VN*CF0010"]))
    assert sh.order_key_raw == "PO-9" and sh.tracking == ["TRK-1"]


def test_what_is_not_a_945_or_cannot_be_applied():
    with pytest.raises(X12Error):
        parse_945(x945(PALLET).replace(b"ST*945", b"ST*940"))
    with pytest.raises(X12Error, match="no order key"):
        parse_945(x945(["W06*N", "LX*1", "W12*CC*1*1*0*EA**VN*CF0010"]))
    with pytest.raises(X12Error, match="fractional"):
        parse_945(x945(["W06*N*1", "LX*1", "W12*CC*1*1.5*0*EA**VN*CF0010"]))


def test_ac38_ac78_order_keys():
    assert (order_key("1000123a").base, order_key("1000123a").suffix) == ("1000123", "a")
    assert order_key("1000123B").base == "1000123"
    assert order_key("000001234").base == "1234" and order_key("000001234").numeric
    assert order_key("1000123ab").base is None      # two letters: review
    assert order_key("W-2").base == "W-2" and not order_key("W-2").numeric  # a PO as it is
