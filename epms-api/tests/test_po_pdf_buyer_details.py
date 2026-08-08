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
    po = PurchaseOrder(
        id=uuid.uuid4(), number="PO-NC-0001", title="NC order", type=1,
        status="issued", currency="CAD", vendor_id=uuid.uuid4(), vendor_name="Acme",
        subtotal=Decimal("100.00"), tax_rate=Decimal("0"), tax_amount=Decimal("0"),
        total=Decimal("100.00"), created_by=uuid.uuid4(), **kw,
    )
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
