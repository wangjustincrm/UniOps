"""The company logo on Safety's printouts.

Round 9: the in-house certificate; round 10, question 7: "the logo should also
go on the other printouts — the printed training register, the JHSC minutes,
the incident report, permits." One logo, the one set under Admin → Company
Settings, read here and drawn by each printout. A logo that cannot be drawn
(an SVG, a broken upload) is left off: a printout without the logo is still the
record; one that fails to print is not.
"""
from __future__ import annotations

import base64
import io

from reportlab.lib.utils import ImageReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mirrors import CompanyConfig


async def logo_data_url(db: AsyncSession) -> str | None:
    return (await db.execute(select(CompanyConfig.logo_data_url).limit(1))).scalar_one_or_none()


def logo_reader(data_url: str | None) -> ImageReader | None:
    if not data_url or not data_url.startswith("data:image/") or "," not in data_url:
        return None
    try:
        reader = ImageReader(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1])))
        reader.getSize()
        return reader
    except Exception:  # noqa: BLE001 — any undecodable image means "no logo"
        return None


def fit(reader: ImageReader, max_w: float, max_h: float) -> tuple[float, float]:
    """Width and height to draw it at, keeping its shape."""
    w, h = reader.getSize()
    scale = min(max_w / w, max_h / h)
    return w * scale, h * scale


def logo_stream(data_url: str | None) -> io.BytesIO | None:
    """The image bytes, for layouts that take a file (platypus Image)."""
    if logo_reader(data_url) is None:
        return None
    return io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))
