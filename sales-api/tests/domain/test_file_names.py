"""One name for every PDF of an order (user 2026-11-26), as the EDI files: <Doc>-<partner abbrev>-<order number>-<PO>.pdf."""
from types import SimpleNamespace

from app.services.print_docs import INVOICE, PACKING_LIST, PO, doc_name


def order(number, po, partner="X"):
    return SimpleNamespace(order_number=number, source_order_number=po, partner_code=partner)


def test_a_retailer_order_a_shopify_order_and_a_manual_one_with_and_without_its_reference():
    assert doc_name(INVOICE, "JC", order(1008728, "599091")) == "Invoice-JC-1008728-599091.pdf"
    assert doc_name(PO, "JC", order(1008728, "599091")) == "PO-JC-1008728-599091.pdf"
    assert doc_name(PACKING_LIST, "JC", order(1008728, "599091")) == "PackingList-JC-1008728-599091.pdf"
    assert doc_name(INVOICE, "SHP", order(1018786, "9649")) == "Invoice-SHP-1018786-9649.pdf"
    assert doc_name(INVOICE, "MAN", order(1018828, None)) == "Invoice-MAN-1018828.pdf"
    assert doc_name(INVOICE, "MAN", order(1018828, "")) == "Invoice-MAN-1018828.pdf"
    assert doc_name(INVOICE, "MAN", order(1018828, "SHP-9439")) == "Invoice-MAN-1018828-SHP-9439.pdf"


def test_one_945_among_several_carries_its_place():
    assert doc_name(PACKING_LIST, "SHP", order(1018786, "9649"), 2) == "PackingList-SHP-1018786-9649-2.pdf"
    assert doc_name(PACKING_LIST, "SHP", order(1018786, "9649"), None) == "PackingList-SHP-1018786-9649.pdf"


def test_odd_characters_in_a_po_and_no_abbreviation():
    assert doc_name(PO, "WM", order(5, 'Käse "8"/1 #2')) == "PO-WM-5-Käse_8_1_2.pdf"  # its letters stay; platform/files writes the header
    assert doc_name(PO, "WM", order(5, "PO-订单-é")) == "PO-WM-5-PO-订单-é.pdf"
    assert doc_name(PO, "WM", order(5, "../../x\r\ny")) == "PO-WM-5-.._.._x_y.pdf"
    assert doc_name(PO, "WM", order(5, "#9649")) == "PO-WM-5-9649.pdf"
    assert doc_name(PO, "WM", order(5, "///")) == "PO-WM-5.pdf"     # nothing left of it: left out
    assert doc_name(PO, None, order(5, "A1", partner="Walmart")) == "PO-Walmart-5-A1.pdf"
    assert doc_name(PO, "  ", order(5, "A1", partner="Walmart")) == "PO-Walmart-5-A1.pdf"
