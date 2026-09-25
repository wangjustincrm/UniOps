"""Drawing a name that is not written in Latin letters.

ReportLab's built-in faces are Type 1 and carry no CJK glyphs. Asked to draw
一位同事的名字 in Helvetica it produces a row of black boxes — no exception, no
warning, a PDF that looks fine until somebody who can read it looks at it. A
certificate's whole point is the name on it.

★ The font is *embedded*, from the TTC the image installs. A CID reference by
name (STSong-Light and friends) relies on the reader having the font and does
not survive being emailed. ★ Noto CJK is not a substitute: its outlines are CFF
and ReportLab cannot embed them.

The face is only applied to text that needs it. Latin text stays in Helvetica,
which is what the rest of these documents are set in and what they should look
like.
"""
from __future__ import annotations

import logging
import os

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

log = logging.getLogger(__name__)

CJK_FONT = "wqy-zenhei"
_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
)

_registered: bool | None = None


def has_cjk(text: str | None) -> bool:
    """Whether this string needs a font Helvetica cannot provide.

    Kana and Hangul are in here as well as Chinese: wqy-zenhei carries all
    three, and a name written in any of them draws as boxes without it. CJK
    extension A falls inside the radicals-to-ideographs range already.
    """
    if not text:
        return False
    return any(
        "ᄀ" <= ch <= "ᇿ"      # Hangul jamo
        or "⺀" <= ch <= "鿿"   # radicals, kana, and unified ideographs
        or "가" <= ch <= "힯"   # Hangul syllables
        or "＀" <= ch <= "￯"   # full-width forms
        for ch in text
    )


def cjk_available() -> bool:
    """Register the embedded face once, and say whether it is there.

    Returns False rather than raising when the image has not been rebuilt with
    the font: a certificate with boxes in it is bad, and no certificate at all
    is worse.
    """
    global _registered
    if _registered is not None:
        return _registered

    for path in _CANDIDATES:
        if not os.path.exists(path):
            continue
        try:
            # subfontIndex=0 because these are TrueType *collections*; without
            # it ReportLab reads the container rather than a face.
            pdfmetrics.registerFont(TTFont(CJK_FONT, path, subfontIndex=0))
            _registered = True
            return True
        except Exception:  # noqa: BLE001 — a broken font must not stop a document
            log.warning("Could not embed %s; documents with CJK text will "
                        "fall back to Helvetica", path, exc_info=True)
    _registered = False
    log.warning("No CJK font is installed, so non-Latin text in generated PDFs "
                "will not render. Rebuild the image with fonts-wqy-zenhei.")
    return False


def font_for(text: str | None, *, bold: bool = False) -> str:
    """The face to draw this string in.

    wqy-zenhei has no separate bold cut, so emphasis on CJK text is carried by
    size rather than weight — better than a synthetic bold that smears the
    strokes of a small glyph.
    """
    if has_cjk(text) and cjk_available():
        return CJK_FONT
    return "Helvetica-Bold" if bold else "Helvetica"
