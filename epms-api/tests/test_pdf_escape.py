"""User text prints literally on every document PDF (PRD PDF-ESC-001, ISS-0095).

ReportLab's Paragraph parses its content as mini-XML: a title such as
`B3 & <tag> "quote"` lost "<tag>" on the GR PDF (taken for markup and dropped),
and a line break in notes collapsed into a space. Each generator is fed the
same awkward text in every field a person types, and the visible text of the
PDF must contain it unchanged.
"""
import uuid
from decimal import Decimal

from app.models.gr import GoodsReceipt, GrLineItem
from app.models.pa import PaLineItem, PaymentApplication
from app.models.po import PoLineItem, PurchaseOrder
from app.models.pr import PrLineItem, PurchaseRequest
from app.services.pdf_gr import generate_gr_pdf
from app.services.pdf_pa import generate_pa_pdf
from app.services.pdf_po import generate_po_pdf
from app.services.pdf_pr import generate_pr_pdf
from app.services.pdf_template import esc
from tests.test_pr_pdf_budget_account import _shown_text

T = 'B3 & <tag> "quote"'
TEMPLATES = {k: {"show_logo": False, "show_terms": True, "terms_text": f"Terms {T}",
                 "header_note": f"Header {T}", "footer_note": f"Footer {T}"}
             for k in ("pr", "po", "gr", "pa")}


def _assert_literal(shown: str, *texts: str) -> None:
    # ReportLab emits "<" / ">" / "&" as text runs of their own, which
    # _shown_text joins with a space; compare with all whitespace removed.
    flat = "".join(shown.split())
    for t in texts:
        assert "".join(t.split()) in flat, f"{t!r} not printed literally"


def test_esc_keeps_markup_characters_and_line_breaks():
    assert esc('a & <b> "c"') == 'a &amp; &lt;b&gt; "c"'
    assert esc("one\r\ntwo\nthree") == "one<br/>two<br/>three"
    assert esc(None) == ""


def test_gr_pdf_prints_angle_brackets():
    gr = GoodsReceipt(
        number="GR-ESC-1", title=f"Title {T}", po_id=uuid.uuid4(), po_number="PO-1",
        vendor_id=uuid.uuid4(), vendor_name=f"Vendor {T}", gr_type="physical",
        currency="CAD", status="confirmed", created_by=uuid.uuid4(),
        storage_location=f"Bay {T}", notes=f"Note {T}\nsecond line",
        line_items=[GrLineItem(
            description=f"Line {T}", material_id=f"MAT {T}", qty_ordered=Decimal("1"),
            qty_received=Decimal("1"), unit="<ea>", unit_price=Decimal("1"),
            line_total=Decimal("1"), condition="good", sort_order=0)],
    )
    shown = _shown_text(generate_gr_pdf(
        gr, f"Co {T}", TEMPLATES, None, created_by_name=f"Creator {T}"))
    _assert_literal(shown, f"Title {T}", f"Vendor {T}", f"Bay {T}", f"Note {T}",
                    "second line", f"Line {T}", f"MAT {T}", "<ea>", f"Creator {T}",
                    f"Co {T}", f"Terms {T}", f"Header {T}", f"Footer {T}")


def test_pa_pdf_prints_angle_brackets():
    pa = PaymentApplication(
        pa_number="PA-ESC-1", title=f"Title {T}", vendor_id=uuid.uuid4(),
        vendor_name=f"Vendor {T}", pa_type="regular", subtotal=Decimal("1"),
        tax_amount=Decimal("0"), other_charges=Decimal("1"),
        other_charges_note=f"Fee {T}", payment_amount=Decimal("2"), currency="CAD",
        status="approved", created_by=uuid.uuid4(), notes=f"Note {T}",
        line_items=[PaLineItem(description=f"Line {T}", qty=Decimal("1"), unit="<ea>",
                               unit_price=Decimal("1"), line_total=Decimal("1"), sort_order=0)],
    )
    shown = _shown_text(generate_pa_pdf(
        pa, f"Co {T}", TEMPLATES, None, requester_name=f"Applicant {T}",
        approvals=[{"role": f"Role {T}", "name": f"Approver {T}", "at": None}]))
    _assert_literal(shown, f"Title {T}", f"Vendor {T}", f"Fee {T}", f"Note {T}",
                    f"Line {T}", "<ea>", f"Applicant {T}", f"Role {T}", f"Approver {T}",
                    f"Co {T}", f"Terms {T}")


def test_pr_pdf_prints_angle_brackets():
    pr = PurchaseRequest(
        number="PR-ESC-1", title=f"Title {T}", type=2, status="approved",
        amount=Decimal("1"), currency="CAD", vendor_name=f"Vendor {T}",
        department_name=f"Dept {T}", budget_code="CRM00901", created_by=uuid.uuid4(),
        notes=f"Note {T}", delivery_address=f"Dock {T}",
        line_items=[PrLineItem(description=f"Line {T}", qty=Decimal("1"), unit="<ea>",
                               unit_price=Decimal("1"), line_total=Decimal("1"), sort_order=0,
                               supplier_item_id=f"SUP {T}")],
    )
    shown = _shown_text(generate_pr_pdf(
        pr, f"Co {T}", TEMPLATES, None, requester_name=f"Requester {T}",
        budget_account_name=f"Food&Beverage {T}"))
    _assert_literal(shown, f"Title {T}", f"Vendor {T}", f"Dept {T}", f"Note {T}",
                    f"Dock {T}", f"Line {T}", f"SUP {T}", "<ea>", f"Requester {T}",
                    "CRM00901", f"Food&Beverage {T}", f"Co {T}")


def test_po_pdf_prints_angle_brackets():
    po = PurchaseOrder(
        id=uuid.uuid4(), number="PO-ESC-1", title="t", type=2, status="issued",
        currency="CAD", vendor_id=uuid.uuid4(), vendor_name=f"Vendor {T}",
        delivery_address=f"Dock {T}", subtotal=Decimal("1"), tax_rate=Decimal("0"),
        tax_amount=Decimal("0"), total=Decimal("1"), created_by=uuid.uuid4(),
    )
    po.line_items = [PoLineItem(
        id=uuid.uuid4(), po_id=po.id, description=f"Line {T}", qty=Decimal("1"),
        unit="<ea>", unit_price=Decimal("1"), line_total=Decimal("1"),
        received_qty=Decimal("0"), sort_order=0, material_id=f"MAT {T}")]
    shown = _shown_text(generate_po_pdf(po, f"Co {T}", TEMPLATES))
    _assert_literal(shown, f"Vendor {T}", f"Dock {T}", f"Line {T}", f"MAT {T}", "<ea>",
                    f"Co {T}", f"Terms {T}")
