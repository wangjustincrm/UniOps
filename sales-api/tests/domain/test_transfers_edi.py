"""943 (A.4) and 944 (A.7) on the real pair: transfer 00104 / 944 16468."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.domain.edi943 import Line943, Party943, Transfer943, build_943
from app.domain.edi944 import LineIn, allocate, parse_944
from app.domain.shadow import compare
from app.domain.x12 import X12Error

ZOHO_943 = b"""ISA*00*          *00*          *ZZ*CANADAROYALMILK*12*9055651212     *261007*1009*U*00401*000000104*0*P*>~
GS*OW*CANADAROYALMILK*9055651212*20261007*1009*000000104*X*006030~
ST*943*000000104~
W06*F*00104*20261007*00104~
N1*SF*Canada Royal Milk ULC*94*CRM~
N3*1680 Venture Dr~
N4*Kingston*ON*K7P 0E9*CA~
N1*ST*Radiant Coleraine Warehouse*94*CLN02~
N3*12480 Coleraine Drive~
N4*Caledon*ON*L7E 4M2~
NTE*OTH*Transfer Order - TO-00104_CRM-943_000000104_TRID_00104_.EDI~
W27*M*LT*PP~
W04*8400.00*EA**VN*CF0092~
N9*LI*1~
W04*7200.00*EA**VN*CF0093~
N9*LI*2~
W03*15600.00*34908.00*LB~
SE*16*000000104~
GE*1*000000104~
IEA*1*000000104~
"""

KINGSTON = Party943("Canada Royal Milk ULC", "94", "CRM", "1680 Venture Dr", "Kingston", "ON", "K7P 0E9", "CA")
CALEDON = Party943("Radiant Coleraine Warehouse", "94", "CLN02", "12480 Coleraine Drive", "Caledon", "ON", "L7E 4M2", None)
# per-EA weights that give the sample's 34,908.00 LB: 8,400 × 2.27 + 7,200 × 2.20
T104 = Transfer943("00104", KINGSTON, CALEDON, (Line943(1, "CF0092", 8400, Decimal("2.27")), Line943(2, "CF0093", 7200, Decimal("2.20"))))


def real_944() -> bytes:
    head = ["ISA*00*          *00*          *12*9055651212     *ZZ*CANADAROYALMILK*261008*0624*U*00401*000016468*0*P*>",
            "GS*RE*9055651212*CANADAROYALMILK*20261008*0624*16468*X*006030", "ST*944*16468", "W17*F*20261008*00104*00104",
            "N1*SF*CANADA ROYAL MILK ULC*94*CRM-CLN02", "N3*1680 VENTURE DR", "N4*KINGSTON*ON**CA", "N1*WH*WAREHOUSE*93*CLN02",
            "G62*BS*20261008"]
    pallets = (["LX*1", "W07*600*EA*628347940140*VN*CF0092", "G69*NIURISS STAGE 1", "N9*LT*26C253125110231"] * 14
               + ["LX*2", "W07*600*EA*628347940362*VN*CF0103", "G69*NIURISS SMART 2", "N9*LT*26E248121010211"] * 12)
    tail = ["W14*15600", "SE*113*16468", "GE*1*16468", "IEA*1*000016468"]
    return ("~\n".join(head + pallets + tail) + "~\n").encode()


def test_ac64_the_943_is_the_a4_file_but_for_its_own_control_number_and_dates():
    built = build_943(T104, 777, datetime(2026, 11, 2, 9, 30))
    assert built.ok, built.errors
    assert built.file_name == "CRM-943_000000777_TRID_00104_.EDI"
    c = compare(ZOHO_943, built.content)
    # The NTE names the file, whose control number differs; nothing else may.
    assert [(r["zoho"], r["sales"]) for r in c.rows if not r["same"]] == [
        ("W06*F*00104*20261007*00104", "W06*F*00104*20261102*00104"),
        ("NTE*OTH*Transfer Order - TO-00104_CRM-943_000000104_TRID_00104_.EDI",
         "NTE*OTH*Transfer Order - TO-00104_CRM-943_000000777_TRID_00104_.EDI")]


def test_a_943_without_weights_or_addresses_fails_its_checks():
    bad = Transfer943("00105", replace(CALEDON, zip=None), KINGSTON, (Line943(1, "CF0092", 10, None),))
    built = build_943(bad, 1, datetime(2026, 11, 2))
    assert not built.ok and any("no gross weight" in e for e in built.errors) and any("zip is empty" in e for e in built.errors)


def test_ac75_the_14_pallets_of_cf0092_are_summed_and_the_total_is_w14():
    r = parse_944(real_944())
    assert (r.transfer_key, r.receipt_date, r.total, len(r.rows)) == ("00104", date(2026, 10, 8), 15600, 26)
    a = allocate([LineIn(1, "CF0092", 8400), LineIn(2, "CF0093", 7200)], r.rows, r.total)
    assert a.received[1] == 8400 and a.by_lot[(1, "26C253125110231")] == 8400  # 14 × 600, none overwritten
    assert sum(x.qty for x in r.rows) == r.total == 15600


def test_ac76_the_real_pair_is_a_discrepancy_line_1_received():
    r = parse_944(real_944())
    a = allocate([LineIn(1, "CF0092", 8400), LineIn(2, "CF0093", 7200)], r.rows, r.total)
    assert a.outcome == "Discrepancy"
    assert (a.line_status[1], a.line_status[2]) == ("Received", "Open")
    assert "CF0103 7200 EA received but not on the transfer" in a.discrepancies
    assert "line 2 CF0093: 0 of 7200 EA received" in a.discrepancies


def test_received_partially_received_and_over_received():
    lines = [LineIn(1, "CF0092", 1200)]
    from app.domain.edi944 import Row944
    row = lambda q, lot="L1", lx="1": Row944(1, lx, "CF0092", None, q, "EA", lot)  # noqa: E731
    assert allocate(lines, [row(600), row(600)], 1200).outcome == "Received"
    assert allocate(lines, [row(600)], 600).outcome == "Partially Received"
    over = allocate(lines, [row(600), row(600), row(600)], 1800)
    assert over.outcome == "Discrepancy" and over.line_status[1] == "Discrepancy"
    # two lines of one item: LX first, then the lot, then the first with room
    two = [LineIn(1, "CF0092", 600), LineIn(2, "CF0092", 600)]
    a = allocate(two, [row(600, "A", "2"), row(600, "B", "")], 1200)
    assert a.row_line == [2, 1] and a.outcome == "Received"


def test_what_is_not_a_944():
    with pytest.raises(X12Error):
        parse_944(real_944().replace(b"ST*944", b"ST*945"))
    with pytest.raises(X12Error, match="transfer number"):
        parse_944(real_944().replace(b"W17*F*20261008*00104*00104", b"W17*F*20261008"))
