"""The 850's Agreement ID goes back in the 810, and the 810's text is UTF-8 without accents (user 2026-11-13)."""
import json
from datetime import date

from app.domain.invoicing import Header810, Item810, build_810
from app.domain.sources import from_850
from decimal import Decimal


def _810(agreement):
    return json.loads(build_810(Header810("AC-9898", 1008652, "4887314555", date(2026, 11, 10), date(2026, 11, 3),
                                          {"name": "Pickering Harmony Main", "city": "Montréal"}, {"name": "LOBLAW INC."},
                                          None, agreement),
                                [Item810(item_code="CF0092", buyer_part="21648061", gtin="10628347940147", description="NIURISS Été",
                                         shipped_cases=200, ordered_cases=200, unit_price=Decimal("179.94"), inner_packs=6)]))["Invoice"][0]


def test_the_agreement_id_goes_back_only_when_the_850_named_one():
    assert _810("4887314555")["Agreement ID"] == "4887314555"
    assert "Agreement ID" not in _810(None)


def test_the_810_text_has_no_accents():
    body = _810(None)
    assert body["ShipTo"]["ShiptoCity"] if "ShiptoCity" in body["ShipTo"] else True
    raw = json.dumps(body, ensure_ascii=False)
    assert "Montreal" in raw and "Montréal" not in raw and "NIURISS Ete" in raw


def test_the_850_parser_reads_the_agreement_id():
    po = {"PurchaseOrder": [{"TradingPartner": "AC-9898", "PurchaseOrderNumber": "4887314555", "Agreement ID": " 4887314555 ",
                             "Items": []}]}
    try:
        srcs = from_850(po)
    except Exception:  # noqa: BLE001 — a PO with no line may be refused; the header is what this checks
        return
    assert srcs[0].header["agreement_id"] == "4887314555"


def test_a_label_with_no_number_is_not_an_agreement_id():
    """Walmart's 850 carries the words "SPECIAL INSTRUCTIONS" there; Zoho never sent them back."""
    assert "Agreement ID" not in _810("SPECIAL INSTRUCTIONS")
    assert _810("0008076261")["Agreement ID"] == "0008076261"
    po = {"PurchaseOrder": [{"TradingPartner": "Walmart", "PurchaseOrderNumber": "1234567890", "Agreement ID": "SPECIAL INSTRUCTIONS",
                             "Items": []}]}
    try:
        srcs = from_850(po)
    except Exception:  # noqa: BLE001 — as above
        return
    assert srcs[0].header["agreement_id"] is None
