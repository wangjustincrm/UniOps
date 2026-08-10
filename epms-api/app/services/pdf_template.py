"""Shared PDF template utilities — applied uniformly to all document PDFs."""
import base64
from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Image, Paragraph, Spacer, Table, TableStyle

_GRAY = colors.HexColor("#737373")
_PRIMARY = colors.HexColor("#0A7C7C")
_DARK = colors.HexColor("#1A1A1A")
_LIGHT = colors.HexColor("#F5F5F5")


def _s(name: str, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, **kw)


def build_logo(logo_data_url: str | None, size_mm: float = 18.0) -> Image | None:
    """Decode a base64 data-URL logo and return a ReportLab Image preserving aspect ratio.

    The longest dimension is capped at size_mm; the other side scales proportionally
    so the logo is never distorted.
    """
    if not logo_data_url:
        return None
    try:
        if "," in logo_data_url:
            _, data = logo_data_url.split(",", 1)
        else:
            data = logo_data_url
        img_bytes = base64.b64decode(data)
        img = Image(BytesIO(img_bytes))
        # img.imageWidth / imageHeight are the natural pixel dimensions
        nat_w = img.imageWidth or 1
        nat_h = img.imageHeight or 1
        max_dim = size_mm * mm
        if nat_w >= nat_h:
            img.drawWidth  = max_dim
            img.drawHeight = max_dim * nat_h / nat_w
        else:
            img.drawHeight = max_dim
            img.drawWidth  = max_dim * nat_w / nat_h
        return img
    except Exception:
        return None


def header_note_element(text: str) -> list:
    """Return flowable elements for the header note banner."""
    if not text or not text.strip():
        return []
    style = _s("hdr_note", fontSize=8, textColor=_PRIMARY, fontName="Helvetica",
               backColor=colors.HexColor("#E8F8F8"), borderPadding=(4, 6, 4, 6))
    return [Paragraph(text.strip(), style), Spacer(1, 3 * mm)]


def footer_note_element(text: str) -> list:
    """Return flowable elements for the footer note."""
    if not text or not text.strip():
        return []
    style = _s("ftr_note", fontSize=8, textColor=_GRAY, fontName="Helvetica-Oblique")
    return [Spacer(1, 3 * mm), Paragraph(text.strip(), style)]


def terms_element(text: str) -> list:
    """Return flowable elements for the Terms & Conditions section."""
    if not text or not text.strip():
        return []
    sec_style = _s("terms_sec", fontSize=9, textColor=_PRIMARY, fontName="Helvetica-Bold", spaceAfter=2)
    body_style = _s("terms_body", fontSize=8, textColor=_DARK, fontName="Helvetica", leading=11)
    return [
        Spacer(1, 5 * mm),
        HRFlowable(width="100%", thickness=0.5, color=_GRAY),
        Spacer(1, 2 * mm),
        Paragraph("Terms & Conditions", sec_style),
        Paragraph(text.strip(), body_style),
    ]


def approvals_element(approvals: list[dict] | None, W: float) -> list:
    """Approval-history table shared by the PR and PA PDFs.

    `approvals` comes from app/crud/signatories.approval_signatories(); each entry
    is {"role", "name", "at"}. Returns [] when there is nothing to show so the
    section disappears entirely rather than printing an empty header.
    """
    if not approvals:
        return []
    sec_style = _s("appr_sec", fontSize=10, textColor=_PRIMARY,
                   fontName="Helvetica-Bold", spaceAfter=4)
    th_style = _s("appr_th", fontSize=8, textColor=colors.white, fontName="Helvetica-Bold")
    td_style = _s("appr_td", fontSize=8, textColor=_DARK, fontName="Helvetica")

    rows = [[Paragraph(h, th_style) for h in ("Step", "Approved By", "Date")]]
    for entry in approvals:
        at = entry.get("at")
        rows.append([
            Paragraph(entry.get("role") or "—", td_style),
            Paragraph(entry.get("name") or "—", td_style),
            Paragraph(at.strftime("%Y-%m-%d") if at else "—", td_style),
        ])

    tbl = Table(rows, colWidths=[W * 0.30, W * 0.45, W * 0.25], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0), _PRIMARY),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _LIGHT]),
        ("TOPPADDING",     (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 4),
        ("LEFTPADDING",    (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",   (0, 0), (-1, -1), 4),
        ("VALIGN",         (0, 0), (-1, -1), "TOP"),
    ]))
    return [Spacer(1, 5 * mm), Paragraph("Approvals", sec_style), tbl]


def get_tmpl(cfg_pdf_templates: dict | None, doc_type: str) -> dict:
    """Extract template settings for a document type from CompanyConfig.pdf_templates."""
    if not cfg_pdf_templates:
        return {}
    return cfg_pdf_templates.get(doc_type, {})
