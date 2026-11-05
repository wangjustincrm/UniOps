"""The shadow comparison (§5.4): what is ignored, what counts."""
import pytest

from app.domain.shadow import MASK, compare, ignore_rules, masked, reference_940
from app.domain.x12 import X12Error


def x12(body: list[str], ctl="000000001", date="261102", time="0922") -> bytes:
    isa = (f"ISA*00*          *00*          *ZZ*CANADAROYALMILK*12*9055651212     *{date}*{time}*U*00401*{ctl}*0*P*>")
    segs = [isa, f"GS*OW*CANADAROYALMILK*9055651212*20{date}*{time}*{ctl}*X*006030", f"ST*940*{ctl}", *body,
            f"SE*{len(body) + 2}*{ctl}", f"GE*1*{ctl}", f"IEA*1*{ctl}"]
    return ("~\n".join(segs) + "~\n").encode()


BODY = ["W05*N*9471", "N1*ST*A B", "G62*10*20261102", "N9*E1*CRM-940_000000023", "W01*6*EA**VN*CF0102", "W76*6*12.18*LB"]


def test_control_numbers_dates_and_own_numbers_are_ignored():
    zoho = x12(["W05*N*000012345*PO-1", *BODY[1:2], "G62*10*20261030*Y*1200", "N9*E1*CRM-940_000012345_Walmart_PO-1", *BODY[4:]],
               ctl="000016468", date="261030", time="1500")
    sales = x12(["W05*N*000000023*PO-1", *BODY[1:2], "G62*10*20261102", "N9*E1*CRM-940_000000023_Walmart_PO-1", *BODY[4:]])
    c = compare(zoho, sales)
    assert c.differences == 0 and c.signature is None and all(r["same"] for r in c.rows)


def test_trailing_empty_elements_are_the_same_segment():
    assert compare(x12(["N9*DP*", *BODY]), x12(["N9*DP", *BODY])).differences == 0


def test_a_quantity_a_po_or_a_missing_line_counts():
    base = x12(BODY)
    c = compare(x12([*BODY[:4], "W01*12*EA**VN*CF0102", BODY[5]]), base)
    assert c.differences == 1
    [row] = [r for r in c.rows if not r["same"]]
    assert row == {"zoho": "W01*12*EA**VN*CF0102", "sales": "W01*6*EA**VN*CF0102", "same": False}
    assert compare(x12(["W05*N*9471*PO-1", *BODY[1:]]), x12(["W05*N*9471*PO-2", *BODY[1:]])).differences == 1
    c = compare(x12([*BODY, "W01*1*EA**VN*CRMMARK002"]), base)
    assert [(r["zoho"], r["sales"]) for r in c.rows if not r["same"]] == [
        ("W01*1*EA**VN*CRMMARK002", None), ("SE*9*000000001", "SE*8*000000001")]  # the line, and the count it changes


def test_the_signature_follows_the_differences_not_the_volatile_fields():
    a = compare(x12([*BODY[:4], "W01*12*EA**VN*CF0102", BODY[5]], ctl="000000007"), x12(BODY))
    b = compare(x12([*BODY[:4], "W01*12*EA**VN*CF0102", BODY[5]], ctl="000000099", date="261101"), x12(BODY, ctl="000000005"))
    c = compare(x12([*BODY[:4], "W01*18*EA**VN*CF0102", BODY[5]]), x12(BODY))
    assert a.signature == b.signature != c.signature


def test_the_ignore_list_is_explicit():
    assert masked(["ISA", "00", "", "00", "", "ZZ", "X", "12", "Y", "261102", "0922", "U", "00401", "000000001", "0"])[9:15] == \
        [MASK, MASK, "U", "00401", MASK, "0"]
    assert masked(["G62", "01", "20261105"]) == ["G62", "01", "20261105"]  # the requested delivery date counts
    assert "G62*10: G6202, G6203, G6204" in ignore_rules() and "ISA ISA09, ISA10, ISA13" in ignore_rules()


def test_reference_keys_and_what_is_not_a_940():
    r = reference_940(x12(["W05*N*000012345*PO-1", *BODY[1:]]))
    assert (r.order_number, r.po, r.sets) == ("000012345", "PO-1", 1)
    assert reference_940(x12(BODY)).po is None
    with pytest.raises(X12Error):
        reference_940(x12(BODY).replace(b"ST*940", b"ST*943"))
    with pytest.raises(X12Error):
        reference_940(b"not x12")
