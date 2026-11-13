"""The company's logo for printed documents: epms-api's public branding, the logo the module's
sidebar shows (one place to change it). Read at most every 10 minutes (a failure for a minute);
unreachable or not an image → None, and the document prints without it — never a failed PDF."""
from __future__ import annotations

import base64
import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

TTL_SECONDS = 600
RETRY_SECONDS = 60
_cache: dict[str, tuple[float, bytes | None]] = {}


async def company_logo() -> bytes | None:
    url = settings.SALES_BRANDING_URL.strip()
    if not url:
        return None
    hit = _cache.get(url)
    if hit is not None and time.monotonic() < hit[0]:
        return hit[1]
    logo: bytes | None = None
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(url)
            r.raise_for_status()
            data = str(r.json().get("logo_data_url") or "")
        if data.startswith("data:image/") and ";base64," in data:
            logo = base64.b64decode(data.split(",", 1)[1], validate=True)
    except Exception as e:  # noqa: BLE001 — a document never fails for its logo
        logger.warning("company logo not read (%s): documents print without it", type(e).__name__)
    _cache[url] = (time.monotonic() + (TTL_SECONDS if logo else RETRY_SECONDS), logo)
    return logo
