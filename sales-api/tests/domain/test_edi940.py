"""The 940 per Appendix A.2/A.3 and FR-OB-29a, and reading X12 (FR-OB-26) and
997s (A.5, FR-OB-22a). The shapes below were settled by the segment-by-segment
replay against Zoho's archived 940s (AC-OB-25, 2026-11-05)."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.domain.edi940 import Address, Line940, Order940, build_940
from app.domain.edi997 import parse_997
from app.domain.x12 import X12Error, separators, segments

NOW = datetime(2026, 10, 30, 14, 5)  # America/Toronto local
SHIP = Address(name="Ana Lévesque", address1="1 rue Saint-Denis", city="Montréal", province="QC", zip="H2X 1Y4", country="CA")


def line(no, code="CF0092", qty=6, held=True, weight="2.03", part=None):
    return Line940(line_no=no, item_code=code, qty_ea=qty, buyer_part=part, gtin="10062639300019", item_name="Formula 900 g",
                   weight_lb_per_ea=Decimal(weight) if weight else None, warehouse_held=held)


def shopify(**over):
    base = dict(order_id=1, order_number=104, retail=False, source_order_number="9302", source_reference="SHP-9302",
                partner_code="Shopify", walmart=False, label_code=None, dc_number=None, order_type=None, department=None,
                requested_delivery=None, ship_to=SHIP, bill_to=Address(), contact={"email": "a@b.example", "phone": "514 555 0100"},
                express=False, lines=(line(1),))
    return Order940(**(base | over))


def retail(**over):
    return shopify(**({"retail": True, "source_order_number": "PO-778", "source_reference": "PO-778", "partner_code": "LBL",
                       "requested_delivery": date(2026, 11, 3), "contact": {}} | over))


def seg(built, sid):
    return [s for s in segments(built.content) if s[0] == sid]


def test_a_shopify_940_follows_appendix_a3():
    b = build_940(shopify(lines=(line(1, qty=60),)), 17, NOW)  # AC-OB-14: 10 cases × 6 → 60 EA (computed at intake)
    assert b.ok, (b.errors, b.issues.errors)
    text = b.content.decode()
    assert text.startswith("ISA*00*          *00*          *ZZ*CANADAROYALMILK*12*9055651212     *261030*1405*U*00401*000000017*0*P*>~\n")
    assert "GS*OW*CANADAROYALMILK*9055651212*20261030*1405*000000017*X*006030~" in text
    assert seg(b, "W05") == [["W05", "N", "9302"]]
    assert seg(b, "W01") == [["W01", "60", "EA", "", "VN", "CF0092"]]
    assert ["N9", "8X", "BC"] in seg(b, "N9") and ["N9", "E1", "SHP-9302"] in seg(b, "N9")
    assert seg(b, "W66") == [["W66", "PP", "U", "", "", "GND", "", "", "", "", "PRLA"]]
    assert seg(b, "PER") == [["PER", "CN", "Ana Levesque", "EM", "a@b.example", "TE", "514 555 0100"]]
    assert seg(b, "G62") == [["G62", "10", "20261030"]]  # no requested date on a Shopify order: only "today"
    assert ["N1", "BT", "Ana Levesque"] in seg(b, "N1")  # an empty bill-to is the ship-to
    assert b.issues.changed and "Montréal → Montreal" in " ".join(b.issues.changed)  # noted on the document
    assert build_940(shopify(express=True), 18, NOW).content.count(b"*EXP*****PRLA") == 1


def test_the_shopify_customer_id_is_the_n1_id_and_the_contact_sits_between_the_parties():
    b = build_940(shopify(contact={"email": "a@b.example", "customer_id": "10995500000000"}), 19, NOW)
    order = [s[0] + (s[1] if s[0] == "N1" else "") for s in segments(b.content) if s[0] in ("N1", "N3", "N4", "PER")]
    assert order == ["N1FR", "N1WH", "N1ST", "N3", "N4", "PER", "N1BT", "N3", "N4"]
    assert ["N1", "ST", "Ana Levesque", "94", "10995500000000"] in seg(b, "N1")
    assert ["N1", "BT", "Ana Levesque", "94", "10995500000000"] in seg(b, "N1")


def test_retail_parties_lines_and_reference_as_zoho_sends_them():
    b = build_940(retail(partner_name="Overwaitea Food Group", ship_to=Address(
        name="DC", address1="1 Road", city="Toronto", province="ON", zip="M1M 1M1", country="CA", gln="0000000000028"),
        bill_to=Address(name="HQ", address1="2 Road", city="Ottawa", province="ON", zip="K1K 1K1", gln="0000000000099"),
        lines=(line(1, part="050825074"),)), 22, NOW)
    assert ["N1", "ST", "DC", "94", "0000000000028"] in seg(b, "N1") and ["N1", "BT", "HQ", "94", "0000000000099"] in seg(b, "N1")
    assert ["N9", "E1", "CRM-940_000000104_OverwaiteaFoodGroup_PO-778"] in seg(b, "N9")
    assert seg(b, "N9")[-4:] == [["N9", "E1", "Formula 900 g"], ["N9", "E2", "10062639300019"], ["N9", "L9", "050825074"],
                                 ["N9", "E3", "050825074"]]
    assert seg(b, "PER") == []


def test_a_retail_940_carries_lo_and_dp_even_empty_and_mr_only_for_walmart_0020():
    b = build_940(retail(), 21, NOW)
    assert b.ok, b.errors
    assert seg(b, "W05") == [["W05", "N", "000000104", "PO-778"]]
    n9 = seg(b, "N9")
    assert ["N9", "LO", ""] in n9 and ["N9", "DP", ""] in n9 and ["N9", "LB", "UCC_CRM_LBW"] in n9  # AC-OB-26
    assert not any(s[1] == "MR" for s in n9) and not seg(b, "PER")
    assert [s[1] for s in seg(b, "G62")] == ["01", "10", "38", "79"]
    assert seg(b, "W66") == [["W66", "PP", "LT", "", "", "", "", "", "", "", "WIFS"]]
    w = build_940(retail(walmart=True, dc_number="6080", order_type="0020", department="92"), 22, NOW)
    n9 = seg(w, "N9")
    assert ["N9", "LB", "UCC_CRM_WMT"] in n9 and ["N9", "LO", "6080"] in n9 and ["N9", "MR", "0020"] in n9 and ["N9", "DP", "92"] in n9
    assert not build_940(retail(walmart=True, dc_number=None), 23, NOW).ok  # a Walmart order needs its DC


def test_weight_is_per_ea_times_quantity_and_unheld_items_are_left_off():
    b = build_940(shopify(lines=(line(1, qty=2, weight="2.03"), line(2, "CF0010", qty=1, weight="2.48"),
                                 line(3, "CRMMARK002", qty=1, held=False, weight=None))), 30, NOW)
    assert b.ok, b.errors
    assert seg(b, "W76") == [["W76", "3", "6.54", "LB"]]  # AC-OB-27
    assert [s[5] for s in seg(b, "W01")] == ["CF0092", "CF0010"]  # AC-OB-28: no bib line
    assert [x["line_no"] for x in b.lines_sent] == [1, 2] and set(b.locked["lines"]) == {"1", "2"}


@pytest.mark.parametrize("over, error", [
    ({"ship_to": Address(name="X", address1="1 St", city="Ottawa", province="ON")}, "ship_to.zip is empty"),
    ({"lines": (line(1, held=False),)}, "no line the warehouse holds"),
    ({"lines": (line(1, weight=None),)}, "has no gross weight"),
    ({"lines": (line(1, qty=0),)}, "quantity 0 EA"),
])
def test_what_fails_validation_is_named(over, error):
    b = build_940(shopify(**over), 40, NOW)
    assert not b.ok and any(error in e for e in b.errors)


def test_text_is_ascii_and_never_a_question_mark():
    b = build_940(shopify(ship_to=Address(name="王小明", address1="1 Main", city="Toronto", province="ON", zip="M5V 1A1")), 41, NOW)
    assert not b.ok and b.issues.errors[0] == "ST.name: '王' has no ASCII form — edit the value"
    assert b"?" not in b.content
    b = build_940(shopify(ship_to=Address(name="A*B", address1="1 Main", city="Toronto", province="ON", zip="M5V 1A1")), 42, NOW)
    assert not b.ok and "EDI separator" in b.issues.errors[0]
    empty = build_940(shopify(ship_to=Address(name="A", address1="1 Main", city="Toronto", province="ON", zip="M5V 1A1", country="")), 43, NOW)
    assert ["N4", "Toronto", "ON", "M5V 1A1", "CA"] in seg(empty, "N4")  # AC-OB-30: empty country = Canada


def test_structure_is_checked():
    b = build_940(shopify(), 7, NOW)
    segs = segments(b.content)
    st, se = segs.index(next(s for s in segs if s[0] == "ST")), segs.index(next(s for s in segs if s[0] == "SE"))
    assert int(segs[se][1]) == se - st + 1 and segs[0][13] == segs[1][6] == segs[st][2] == segs[se][2] == "000000007"


A4_943 = (b"ISA*00*          *00*          *ZZ*CANADAROYALMILK*12*9055651212     *261007*1009*U*00401*000000104*0*P*>~\n"
          b"GS*OW*CANADAROYALMILK*9055651212*20261007*1009*000000104*X*006030~\nST*943*000000104~\n"
          b"W06*F*00104*20261007*00104~\nSE*16*000000104~\nGE*1*000000104~\nIEA*1*000000104~\n")


def test_separators_come_from_the_isa():
    assert separators(A4_943).element == "*" and separators(A4_943).segment == "~" and separators(A4_943).sub_element == ">"
    other = A4_943.replace(b"*", b"|").replace(b"~\n", b"'")
    assert [s[0] for s in segments(other)][:3] == ["ISA", "GS", "ST"]  # FR-OB-26: never hard-coded
    with pytest.raises(X12Error):
        segments(b"not edi")


def test_997_groups_sets_and_codes():
    raw = (b"ISA*00*          *00*          *12*9055651212     *ZZ*CANADAROYALMILK*261030*1500*U*00401*000016468*0*P*>~\n"
           b"GS*FA*9055651212*CANADAROYALMILK*20261030*1500*16468*X*006030~\nST*997*0001~\n"
           b"AK1*OW*17~\nAK2*940*000000017~\nAK5*R*5~\nAK9*R*1*1*0~\nSE*6*0001~\nGE*1*16468~\nIEA*1*000016468~\n")
    [g] = parse_997(raw)
    assert (g.functional_id, g.group_control, g.code, g.accepted) == ("OW", "17", "R", False)
    assert [(s.type, s.control, s.code, s.reasons, s.accepted) for s in g.sets] == [("940", "17", "R", ["5"], False)]
    ok = parse_997(raw.replace(b"AK5*R*5", b"AK5*E").replace(b"AK9*R", b"AK9*A"))[0]
    assert ok.accepted and ok.sets[0].accepted
    with pytest.raises(X12Error):
        parse_997(A4_943)
