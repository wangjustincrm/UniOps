"""Headers that carry a name somebody might actually have.

★ HTTP headers are latin-1. A `Content-Disposition` built by interpolating a
person's name or a product name raises when the name is written in Chinese —
and it raises inside the response, so the download answers 500 for exactly the
people and products whose names needed the care in the first place.

RFC 5987 is the way out: an ASCII name that every client understands, and the
real one beside it for the clients that read the extended form, which is all of
them made this decade.
"""
from __future__ import annotations

from urllib.parse import quote


def content_disposition(stem: str, *, extension: str = "pdf",
                        inline: bool = True) -> str:
    """A `Content-Disposition` value that survives a non-Latin name.

    `stem` is the filename without its extension, in whatever script it is
    written in.
    """
    filename = f"{stem}.{extension}"
    # Stripped rather than transliterated: a name reduced to "??" helps nobody,
    # and the extended form beside it is what any modern browser will use.
    #
    # ★ Quotes and backslashes come out too. A product called
    # `Pipe sealant 3/4"` would close the quoted string early and leave a
    # malformed header — truncated by some clients, refused by strict proxies.
    # The extended form is safe already because `quote` escapes both.
    ascii_stem = (stem.encode("ascii", "ignore").decode()
                  .replace('"', "").replace("\\", "").strip(" -_") or "download")
    return (f"{'inline' if inline else 'attachment'}; "
            f'filename="{ascii_stem}.{extension}"; '
            f"filename*=UTF-8''{quote(filename)}")
