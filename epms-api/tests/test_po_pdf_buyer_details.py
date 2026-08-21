"""PO PDF carries Incoterms, Buyer Notes and the Sample column.

Follows tests/test_pr_pdf_department.py: build ORM rows in memory, call
generate_po_pdf() synchronously, and assert on the decompressed content
streams. All fixture text is ASCII so reportlab writes it literally into the
Tj/TJ operators (non-ASCII would go through font subsetting and defeat a raw
substring check).
"""
import base64
import re
import uuid
import zlib
from datetime import date
from decimal import Decimal

from app.models.po import PoLineItem, PurchaseOrder
from app.services.pdf_po import generate_po_pdf

_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)


def _text_of(pdf_bytes: bytes) -> str:
    out = bytearray()
    for match in _STREAM_RE.finditer(pdf_bytes):
        raw = match.group(1).strip(b"\r\n")
        try:
            data = raw
            if data.rstrip().endswith(b"~>"):
                data = data.rstrip()[:-2]
            out += zlib.decompress(base64.a85decode(data))
        except Exception:
            continue
    return out.decode("latin-1")


def _po(**kw) -> PurchaseOrder:
    line_kw = kw.pop("line_kw", {})
    defaults = dict(
        id=uuid.uuid4(), number="PO-NC-0001", title="NC order", type=1,
        status="issued", currency="CAD", vendor_id=uuid.uuid4(), vendor_name="Acme",
        subtotal=Decimal("100.00"), tax_rate=Decimal("0"), tax_amount=Decimal("0"),
        total=Decimal("100.00"), created_by=uuid.uuid4(),
    )
    defaults.update(kw)
    po = PurchaseOrder(**defaults)
    po.line_items = [PoLineItem(
        id=uuid.uuid4(), po_id=po.id, description="Widget", qty=Decimal("10"),
        unit="EA", unit_price=Decimal("10.00"), line_total=Decimal("100.00"),
        received_qty=Decimal("0"), sort_order=0, **line_kw,
    )]
    return po


def test_incoterms_and_buyer_notes_render_in_order():
    po = _po(source="nc", incoterms="FOB Shanghai",
             buyer_notes="Ship in one lot.", notes="nc memo [NC Paid]")
    text = _text_of(generate_po_pdf(po))
    assert "FOB Shanghai" in text
    assert "Ship in one lot." in text
    # Incoterms must sit BEFORE Buyer Notes, per the spec's PDF ordering.
    assert text.index("FOB Shanghai") < text.index("Ship in one lot.")


def test_nc_notes_never_leak_into_the_vendor_facing_pdf():
    """An NC PO with no buyer_notes must NOT fall back to `notes` — that column
    holds NC's internal [NC Paid] / [NC Closed] markers."""
    po = _po(source="nc", buyer_notes=None, notes="nc memo [NC Paid]")
    text = _text_of(generate_po_pdf(po))
    assert "NC Paid" not in text
    assert "nc memo" not in text
    assert "BUYER NOTES" not in text, "no empty Buyer Notes heading"
    # Positive control: if _text_of() ever silently decoded to an empty string
    # (every per-stream exception is swallowed), the three assertions above
    # would pass vacuously. Prove the PDF actually has content.
    assert "Widget" in text


def test_empty_incoterms_renders_no_label():
    """A PO without Incoterms must not carry a stranded 'Incoterms' label."""
    po = _po(incoterms=None)
    text = _text_of(generate_po_pdf(po))
    assert "Incoterms" not in text
    # Positive control — see test_nc_notes_never_leak_into_the_vendor_facing_pdf.
    assert "PO-NC-0001" in text


def test_non_nc_po_falls_back_to_notes():
    """Ordinary POs collect buyer text in `notes` via the Create PO page's
    'Buyer Notes / Terms & Conditions' box, which never reached the PDF before."""
    po = _po(source=None, buyer_notes=None, notes="Deliver to dock 3.")
    text = _text_of(generate_po_pdf(po))
    assert "Deliver to dock 3." in text


def test_buyer_notes_with_markup_and_newlines_render_safely():
    """Buyer Notes is free text from a multi-line textarea, shown to the buyer
    as whitespace-pre-wrap and handed to ReportLab's Paragraph, which parses
    its content as mini-XML. Unescaped input previously: dropped a newline
    into a single run ("line1\\nline2" -> "line1 line2"), silently ate
    bracketed text ("ship <see attached>" -> "ship "), and raised a
    ValueError on a bare "&" (500ing the regenerate endpoint). All three must
    now survive."""
    po = _po(source="nc", buyer_notes="Line1\nLine2 <see attached> A & B")
    text = _text_of(generate_po_pdf(po))   # must not raise
    assert "Line1" in text
    assert "Line2" in text
    # ReportLab splits the run into separate Tj operators around the "<"/">"
    # characters, so the raw content stream doesn't contain one contiguous
    # "<see attached>" substring — check the fragments the old, unescaped
    # code silently dropped instead.
    assert "see attached" in text
    assert "A & B" in text


def test_sample_column_appears_only_when_a_line_has_one():
    with_sample = _text_of(generate_po_pdf(_po(line_kw={"sample": "500 g"})))
    assert "Sample" in with_sample
    assert "500 g" in with_sample

    without = _text_of(generate_po_pdf(_po(line_kw={"sample": None})))
    assert "Sample" not in without, "existing POs must keep their original layout"


def test_pdf_delivery_falls_back_to_earliest_line_date_when_header_is_unset():
    """expected_delivery is NULL on every NC-synced PO (the ERP has no header
    delivery date; rule 1 forbids ever backfilling it there). The vendor PDF
    must not print '—' for Delivery on those orders — it should show the
    earliest planned_arrival_date across the PO's lines. The PDF is
    vendor-facing, so no internal "from ERP" provenance marker is printed."""
    po = _po(source="nc", expected_delivery=None)
    po.line_items = [
        PoLineItem(
            id=uuid.uuid4(), po_id=po.id, description="Widget A", qty=Decimal("10"),
            unit="EA", unit_price=Decimal("10.00"), line_total=Decimal("50.00"),
            received_qty=Decimal("0"), sort_order=0,
            planned_arrival_date=date(2026, 9, 20),
        ),
        PoLineItem(
            id=uuid.uuid4(), po_id=po.id, description="Widget B", qty=Decimal("10"),
            unit="EA", unit_price=Decimal("10.00"), line_total=Decimal("50.00"),
            received_qty=Decimal("0"), sort_order=1,
            planned_arrival_date=date(2026, 9, 5),
        ),
    ]
    text = _text_of(generate_po_pdf(po))
    assert "2026-09-05" in text, "the earlier line date must win"
    assert "2026-09-20" not in text, "the later line date must not appear"
    assert "from ERP" not in text, "no internal provenance marker on the vendor-facing PDF"
    # Positive control — see test_nc_notes_never_leak_into_the_vendor_facing_pdf.
    assert "Widget A" in text


def test_material_id_supplier_item_id_description_appear_in_column_order():
    """LINE ITEMS columns must read #, Material ID, Supplier Item ID,
    Description, Qty, UOM, Unit Price, Line Total, [Sample] — left to right."""
    po = _po()
    po.line_items = [PoLineItem(
        id=uuid.uuid4(), po_id=po.id, description="Distinctive Widget Description",
        qty=Decimal("10"), unit="EA", unit_price=Decimal("10.00"),
        line_total=Decimal("100.00"), received_qty=Decimal("0"), sort_order=0,
        material_id="MAT-7788", supplier_item_id="SUP-99XY",
    )]
    text = _text_of(generate_po_pdf(po))
    assert "MAT-7788" in text
    assert "SUP-99XY" in text
    assert "Distinctive Widget Description" in text
    # Header labels must appear in the mandated order. "Supplier Item ID" wraps
    # onto two lines inside its 20mm column (ReportLab emits "Supplier" and
    # "Item ID" as separate Tj runs around a T* newline operator), so anchor
    # on "Supplier" rather than the full contiguous label.
    assert "Item ID" in text
    assert text.index("Material ID") < text.index("Supplier") < text.index("Description")
    # Body cell values must follow the same left-to-right column order.
    assert text.index("MAT-7788") < text.index("SUP-99XY") < text.index("Distinctive Widget Description")


def test_material_id_blank_not_none_when_absent():
    """A line with no material_id must render an empty cell, not the string
    'None' (a naive f-string/str() of a NULL column would print that)."""
    po = _po()  # default line's material_id is unset (None)
    text = _text_of(generate_po_pdf(po))
    assert "None" not in text
    # Positive control — see test_nc_notes_never_leak_into_the_vendor_facing_pdf.
    assert "Widget" in text


def test_type1_po_signature_block_renders_company_vendor_and_signatory():
    """Type 1 POs get a countersigned-document signature block: our company's
    name, the vendor's name, the resolved OPM name, and the fixed literal
    'Operation Manager' title — never derived from the user's actual role."""
    po = _po(type=1, vendor_name="Acme Vendor Co")
    text = _text_of(generate_po_pdf(po, company_name="Canada Royal Milk",
                                     signatory_name="Laura Sivers"))
    assert "Canada Royal Milk" in text
    assert "Acme Vendor Co" in text
    assert "Laura Sivers" in text
    assert "Operation Manager" in text


def test_type2_po_has_no_signature_block():
    """A non-Type-1 PO must render none of the signature-block text."""
    po = _po(type=2, vendor_name="Acme Vendor Co")
    text = _text_of(generate_po_pdf(po, company_name="Canada Royal Milk",
                                     signatory_name="Laura Sivers"))
    assert "Operation Manager" not in text
    # Positive control — see test_nc_notes_never_leak_into_the_vendor_facing_pdf.
    assert "Widget" in text


def test_type1_po_signature_block_blank_name_when_signatory_missing():
    """When the caller resolves no unique OPM holder (zero or multiple active
    holders), signatory_name is None — the block must still render with the
    Title line, and must never print the literal string 'None'."""
    po = _po(type=1, vendor_name="Acme Vendor Co")
    text = _text_of(generate_po_pdf(po, company_name="Canada Royal Milk",
                                     signatory_name=None))
    assert "Operation Manager" in text
    assert "Acme Vendor Co" in text
    assert "None" not in text


def test_signature_block_introduces_no_date_row():
    """The signature block must carry no Date row on either side. The meta
    grid already renders a 'PO Date' label, so assert on something the block
    would uniquely introduce rather than a bare 'Date' substring."""
    po = _po(type=1, vendor_name="Acme Vendor Co")
    text = _text_of(generate_po_pdf(po, company_name="Canada Royal Milk",
                                     signatory_name="Laura Sivers"))
    assert "Operation Manager" in text  # positive control: block did render
    assert "Signature Date" not in text
    assert "Date:" not in text


def test_pdf_header_expected_delivery_wins_over_line_dates():
    """A human-entered expected_delivery is a deliberate override (rule 3) — it
    must win over any line-level ERP date, everywhere it's displayed."""
    po = _po(source="nc", expected_delivery=date(2026, 10, 1),
             line_kw={"planned_arrival_date": date(2026, 9, 5)})
    text = _text_of(generate_po_pdf(po))
    assert "2026-10-01" in text, "the human-entered header value must win"
    assert "2026-09-05" not in text, "the line date must not override the header value"
    assert "from ERP" not in text, "a header value is not ERP-derived, so no marker"
    # Positive control — see test_nc_notes_never_leak_into_the_vendor_facing_pdf.
    assert "Widget" in text
