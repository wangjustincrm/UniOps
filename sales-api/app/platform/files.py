"""A file answered over HTTP, one way (user 2026-11-24): its name is data (a customer's PO number, a partner's file) and
may hold any character, while a header holds latin-1 only — a name outside it answered 500. RFC 6266 / 5987: an ASCII
`filename` for every reader, and `filename*` (UTF-8) beside it when the name needs it."""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

from fastapi import Response

# Never in a header or a file's name: controls (a line break would end the header) and path separators.
_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f/\\]+")


def content_disposition(name: str, *, inline: bool = False) -> str:
    """`attachment; filename="PO-1.pdf"`; a name beyond plain ASCII adds `filename*=UTF-8''…`."""
    clean = _UNSAFE.sub("_", name).strip() or "file"
    # é → e; what has no ASCII form, and a quote or a percent (read differently by browsers), → _
    plain = "".join(c for c in unicodedata.normalize("NFKD", clean) if not unicodedata.combining(c))
    ascii_name = re.sub(r'[^\x20-\x7e]+|["%]+', "_", plain)
    head = f'{"inline" if inline else "attachment"}; filename="{ascii_name}"'
    return head if ascii_name == clean else f"{head}; filename*=UTF-8''{quote(clean, safe='')}"


class FileResult(Response):
    def __init__(self, content: bytes, name: str, *, media_type: str, inline: bool = False) -> None:
        super().__init__(content, media_type=media_type, headers={"Content-Disposition": content_disposition(name, inline=inline)})
