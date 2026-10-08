"""The reconciliation report is the document the auditors get, and NC's bank
names, voucher summaries and payee names are Chinese. Before this, Helvetica
drew every one of them as a black box — the header read "1033760/RBC________".

These tests are about glyphs, so they check the bytes of the PDF, not that the
call returned something.
"""
from datetime import date

import pytest

from app.services import bank_recon_report as rpt

pypdf = pytest.importorskip("pypdf")

BANK = "1033760/RBC加拿大皇家银行"
PAYEE = "上海丰科生物科技股份有限公司"


def _snapshot():
    return {
        "account": BANK,
        "period_start": "2026-08-01",
        "period_end": "2026-08-31",
        "currency": "CAD",
        "prepared_by": "王静",
        "summary": {
            "statement_opening": "709868.72", "cleared_debit_total": "638728.00",
            "cleared_credit_total": "500836.83", "statement_closing": "454660.91",
            "book_closing": "454660.91", "difference": "0",
        },
        "cleared_payments": [
            {"date": "2026-08-04", "type": "付款单", "ref": "PV-0001",
             "payee": PAYEE, "amount": "1234.56"},
            {"date": "2026-08-05", "type": "Cheque", "ref": "1042",
             "payee": "Smith & Sons Ltd.", "amount": "78.90"},
        ],
        "cleared_deposits": [],
        "outstanding_bank": [],
        "outstanding_book": [],
    }


def _text(pdf: bytes) -> str:
    reader = pypdf.PdfReader(__import__("io").BytesIO(pdf))
    return "\n".join(p.extract_text() or "" for p in reader.pages)


def test_chinese_survives_into_the_pdf():
    pdf = rpt.render_pdf(_snapshot(), date(2026, 9, 24), "王静")
    text = _text(pdf)
    assert BANK in text, "the bank name is what the user saw as boxes"
    assert PAYEE in text, "payee names come from NC and are Chinese too"


def test_english_still_renders():
    """A positive control. If the Chinese assertions above ever pass because
    extraction returns everything-and-anything, this would not be able to fail.
    It also guards the escaping: '&' in a payee must not swallow the rest."""
    text = _text(rpt.render_pdf(_snapshot(), date(2026, 9, 24), "J. Wang"))
    assert "RECONCILIATION REPORT" in text
    assert "Statement beginning balance" in text
    assert "Smith & Sons Ltd." in text


def test_the_cjk_font_is_embedded():
    """Referencing a font by name leaves the auditor's reader to find one. The
    point of shipping the TTF in the image is that it travels with the file."""
    font = rpt._cjk_font()
    assert font != "Helvetica", (
        "no Chinese-capable font registered — check fonts-wqy-zenhei is in the image"
    )
    pdf = rpt.render_pdf(_snapshot(), date(2026, 9, 24), "王静")
    assert b"/FontFile2" in pdf, "the TrueType font was referenced, not embedded"


def test_ascii_cells_stay_plain_strings():
    """Only cells with Chinese become Paragraphs; the all-English report has to
    keep the layout the auditors already accept."""
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph

    style = ParagraphStyle("t")
    assert rpt._cell("Smith & Sons", style) == "Smith & Sons"
    assert rpt._cell(None, style) == ""
    assert isinstance(rpt._cell("加拿大皇家银行", style), Paragraph)
