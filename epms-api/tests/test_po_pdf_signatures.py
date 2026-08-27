"""The PO PDF's signature block, once sign-off has actually signed it.

Same approach as test_po_pdf_buyer_details.py: build ORM rows in memory, call
generate_po_pdf() synchronously, and assert on the rendered PDF — including
real x coordinates, because the column widths changed here (80/10/80 →
70/30/70) and alignment defects live in the interaction between a table's
padding and the frame's, which no structural assertion would catch.
"""
import base64
import re
import uuid
import zlib
from decimal import Decimal

from app.models.po import PoLineItem, PurchaseOrder
from app.services.pdf_po import generate_po_pdf

# A real 1x1 PNG. Has to decode: the renderer measures it to scale it.
_PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)


def _text_of(pdf_bytes: bytes) -> str:
    out = bytearray()
    for match in _STREAM_RE.finditer(pdf_bytes):
        raw = match.group(1).strip(b"\r\n")
        try:
            data = raw.rstrip()[:-2] if raw.rstrip().endswith(b"~>") else raw
            out += zlib.decompress(base64.a85decode(data))
        except Exception:
            continue
    return out.decode("latin-1")


def _abs_x_of(pdf_bytes: bytes, prefixes: list[str]) -> dict:
    """Absolute x (points) of the first text run starting with each prefix.

    Copied from test_po_pdf_buyer_details.py — the q/Q graphics-state stack has
    to be tracked or nested table cells report nonsense.
    """
    out = bytearray()
    for m in _STREAM_RE.finditer(pdf_bytes):
        raw = m.group(1).strip(b"\r\n")
        try:
            data = raw.rstrip()[:-2] if raw.rstrip().endswith(b"~>") else raw
            out += zlib.decompress(base64.a85decode(data))
        except Exception:
            continue
    stream = out.decode("latin-1")
    tok = re.compile(
        r"(?P<q>\bq\b)|(?P<Q>\bQ\b)"
        r"|1 0 0 1 (?P<cx>[-\d.]+) [-\d.]+ cm"
        r"|1 0 0 1 (?P<tx>[-\d.]+) [-\d.]+ Tm"
        r"|\((?P<s>.*?)\) Tj",
        re.DOTALL,
    )
    stack, ctm_x, tm_x, found = [], 0.0, 0.0, {}
    for m in tok.finditer(stream):
        if m.group("q"):
            stack.append(ctm_x)
        elif m.group("Q"):
            ctm_x = stack.pop() if stack else 0.0
        elif m.group("cx") is not None:
            ctm_x += float(m.group("cx"))
        elif m.group("tx") is not None:
            tm_x = float(m.group("tx"))
        elif m.group("s") is not None:
            for pref in prefixes:
                if m.group("s").startswith(pref) and pref not in found:
                    found[pref] = round(ctm_x + tm_x, 2)
    return found


def _image_count(pdf_bytes: bytes) -> int:
    """How many images are DRAWN, counted from the content stream's Do ops.

    Not from /Subtype /Image: a PNG with an alpha channel produces two image
    objects (the image and its soft mask), and two identical signatures are
    deduplicated into one XObject that is then drawn twice. Neither count
    answers "how many signatures appear on the page" — the Do operators do.
    No logo is passed in these tests, so every Do is a signature.
    """
    return len(re.findall(r"/\S+\s+Do\b", _text_of(pdf_bytes)))


def _po(**kw) -> PurchaseOrder:
    defaults = dict(
        id=uuid.uuid4(), number="PO-NC-0001", title="NC order", type=1,
        status="nc_pending", currency="CAD", vendor_id=uuid.uuid4(),
        vendor_name="Acme", subtotal=Decimal("100.00"), tax_rate=Decimal("0"),
        tax_amount=Decimal("0"), total=Decimal("100.00"), created_by=uuid.uuid4(),
    )
    defaults.update(kw)
    po = PurchaseOrder(**defaults)
    po.line_items = [PoLineItem(
        id=uuid.uuid4(), po_id=po.id, description="Widget", qty=Decimal("10"),
        unit="EA", unit_price=Decimal("10.00"), line_total=Decimal("100.00"),
        received_qty=Decimal("0"), sort_order=0,
    )]
    return po


def _sigs(*slots) -> list[dict]:
    return [{"sig_slot": slot, "signer_name": name, "signature_image": _PNG}
            for slot, name in slots]


# ── unsigned ──────────────────────────────────────────────────────────────

def test_an_unsigned_po_prints_the_blank_block_as_before():
    """The paper route still has to work: nothing signed, nothing drawn."""
    pdf = generate_po_pdf(_po(), company_name="Canada Royal Milk",
                          signatory_name="Laura Sivers")
    text = _text_of(pdf)
    assert "Laura Sivers" in text
    assert "Operations Manager" in text
    # No logo passed either, so any image at all would be a signature.
    assert _image_count(pdf) == 0


def test_signatures_are_absent_when_the_list_is_empty():
    assert _image_count(generate_po_pdf(_po(), signatures=[])) == 0


# ── signed ────────────────────────────────────────────────────────────────

def test_both_slots_are_drawn():
    pdf = generate_po_pdf(
        _po(), company_name="Canada Royal Milk", signatory_name="Laura Sivers",
        signatures=_sigs(("initials", "Peter Chan"), ("signature", "Laura Sivers")))
    assert _image_count(pdf) == 2


def test_the_person_who_signed_names_the_block():
    """Not the OPM the role lookup resolved — whoever actually signed."""
    pdf = generate_po_pdf(
        _po(), company_name="Canada Royal Milk", signatory_name="Laura Sivers",
        signatures=_sigs(("signature", "Amrit Singh")))
    text = _text_of(pdf)
    assert "Amrit Singh" in text
    assert "Laura Sivers" not in text


def test_a_step_without_a_slot_is_not_drawn():
    """A configured extra step is still signed, but the PDF has two places."""
    sigs = _sigs(("signature", "Laura Sivers"))
    sigs.append({"sig_slot": None, "signer_name": "Third Signer",
                 "signature_image": _PNG})
    pdf = generate_po_pdf(_po(), signatures=sigs)
    assert _image_count(pdf) == 1
    assert "Third Signer" not in _text_of(pdf)


def test_the_initials_slot_alone_leaves_our_block_named_by_the_role():
    """Purchasing Manager signs first; the OPM block is still the resolved name."""
    pdf = generate_po_pdf(
        _po(), company_name="Canada Royal Milk", signatory_name="Laura Sivers",
        signatures=_sigs(("initials", "Peter Chan")))
    assert _image_count(pdf) == 1
    assert "Laura Sivers" in _text_of(pdf)


# ── failing soft ──────────────────────────────────────────────────────────

def test_an_unreadable_signature_costs_only_that_signature():
    """A corrupt image must not take the whole PO PDF down with it."""
    pdf = generate_po_pdf(_po(), company_name="Canada Royal Milk",
                          signatory_name="Laura Sivers", signatures=[
                              {"sig_slot": "signature", "signer_name": "Laura Sivers",
                               "signature_image": "data:image/png;base64,notbase64!!"},
                              {"sig_slot": "initials", "signer_name": "Peter Chan",
                               "signature_image": _PNG},
                          ])
    assert pdf.startswith(b"%PDF")
    assert _image_count(pdf) == 1          # the good one still drew
    assert "Laura Sivers" in _text_of(pdf)  # and the block is still named


def test_a_non_data_url_is_ignored():
    pdf = generate_po_pdf(_po(), signatures=[
        {"sig_slot": "signature", "signer_name": "X",
         "signature_image": "https://example.com/sig.png"}])
    assert pdf.startswith(b"%PDF")
    assert _image_count(pdf) == 0


# ── layout ────────────────────────────────────────────────────────────────

def test_the_widened_middle_column_keeps_the_block_on_the_page_left_edge():
    """70/30/70 must not disturb the shared left edge.

    Same measurement as test_po_pdf_buyer_details.py's alignment test: every
    W-wide table here sits 6pt left of the frame's content edge and lines up
    with the story paragraphs only because ReportLab's default 6pt cell padding
    puts the contents back.
    """
    po = _po(vendor_name="Acme Vendor Co", buyer_notes="see attached")
    pdf = generate_po_pdf(po, company_name="Canada Royal Milk",
                          signatory_name="Laura Sivers",
                          signatures=_sigs(("initials", "Peter Chan"),
                                           ("signature", "Laura Sivers")))
    xs = _abs_x_of(pdf, ["LINE ITEMS", "BUYER NOTES", "Vendor", "Name:", "Title:"])
    # Positive control — an empty dict would make the equality below vacuous.
    assert set(xs) == {"LINE ITEMS", "BUYER NOTES", "Vendor", "Name:", "Title:"}, xs
    assert len(set(xs.values())) == 1, f"left edges disagree: {xs}"


def test_a_signed_and_an_unsigned_po_put_the_name_row_at_the_same_height():
    """The signing band is fixed-height, so signing must not move the rows."""
    def _name_y(pdf_bytes: bytes) -> float:
        stream = _text_of(pdf_bytes)
        # Track the y translations the same way _abs_x_of tracks x.
        tok = re.compile(
            r"(?P<q>\bq\b)|(?P<Q>\bQ\b)"
            r"|1 0 0 1 [-\d.]+ (?P<cy>[-\d.]+) cm"
            r"|1 0 0 1 [-\d.]+ (?P<ty>[-\d.]+) Tm"
            r"|\((?P<s>.*?)\) Tj", re.DOTALL)
        stack, ctm_y, tm_y = [], 0.0, 0.0
        for m in tok.finditer(stream):
            if m.group("q"):
                stack.append(ctm_y)
            elif m.group("Q"):
                ctm_y = stack.pop() if stack else 0.0
            elif m.group("cy") is not None:
                ctm_y += float(m.group("cy"))
            elif m.group("ty") is not None:
                tm_y = float(m.group("ty"))
            elif m.group("s") is not None and m.group("s").startswith("Title:"):
                return round(ctm_y + tm_y, 2)
        raise AssertionError("Title: row not found")

    unsigned = generate_po_pdf(_po(), company_name="CRM", signatory_name="Laura Sivers")
    signed = generate_po_pdf(_po(), company_name="CRM", signatory_name="Laura Sivers",
                             signatures=_sigs(("initials", "Peter Chan"),
                                              ("signature", "Laura Sivers")))
    assert _name_y(unsigned) == _name_y(signed)
