"""Carriers (sales19): a parcel's tracking link from the carrier's template."""
from __future__ import annotations

from urllib.parse import quote

PLACEHOLDER = "{tracking}"


def tracking_url(template: str | None, number: str | None) -> str | None:
    """The template with the number put in, URL-encoded; none without either."""
    if not template or not number or PLACEHOLDER not in template:
        return None
    return template.replace(PLACEHOLDER, quote(number.strip(), safe=""))
