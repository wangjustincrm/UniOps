"""PDF text extraction for bank documents — decryption, page text, hashing.

Kept apart from the two parsers because the decryption step is a trap they share:
RBC's statement export is **AES-encrypted with an empty user password**. pypdf
will not even report the page count until `decrypt("")` is called, and without
the `cryptography` package it raises DependencyError rather than anything that
reads as "encrypted". Both packages are pinned in requirements.txt for this.
"""
import hashlib
import logging

log = logging.getLogger(__name__)


class PdfUnreadable(ValueError):
    """The file is not a PDF we can open, or it needs a password we don't have."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reader(data: bytes):
    import io

    from pypdf import PdfReader
    from pypdf.errors import DependencyError, PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
    except PdfReadError as exc:
        raise PdfUnreadable(f"Not a readable PDF: {exc}") from exc

    if reader.is_encrypted:
        # An empty user password is how RBC ships statements: the file is
        # AES-encrypted so it cannot be edited, but anyone can open it.
        try:
            if reader.decrypt("") == 0:
                raise PdfUnreadable(
                    "This PDF is password-protected. Remove the password and upload again.")
        except DependencyError as exc:
            # Loud on purpose: a missing dependency must never look like a bad file.
            raise RuntimeError(
                "This PDF is AES-encrypted and the `cryptography` package is missing "
                "from finance-api — it cannot be read until that is installed. "
                "This is not a problem with the file."
            ) from exc
    return reader


LAYOUT = "layout"
PLAIN = "plain"


def page_texts(data: bytes, mode: str = PLAIN) -> list[str]:
    """Per-page text, decrypting first when needed.

    `mode` is not a preference, it is a per-document fact. Measured 2026-09-22 on
    the real files with pypdf 5.1.0:

      RBC statement    plain 1661 chars / 47 lines   layout **0 chars**
      payment file     plain 1389 chars /  1 line    layout 31958 chars / 61 lines

    So: payment files are only readable in `layout` mode (plain returns the whole
    page as one unsplittable blob with vendor names run together), and the
    statement is only readable in `plain` mode (layout yields nothing at all,
    which is also why the statement's debit/credit column CANNOT be recovered
    positionally — see bank_statement_parse for the arithmetic gate that replaces
    it).

    `layout` falls back to plain per page, so a page that yields nothing
    positional still yields something.
    """
    reader = _reader(data)
    out = []
    for i, page in enumerate(reader.pages):
        text = ""
        try:
            if mode == LAYOUT:
                text = page.extract_text(extraction_mode="layout") or ""
            if not text.strip():
                text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 — one bad page must not lose the rest
            log.warning("bank pdf: page %d text extraction failed: %s", i + 1, exc)
        out.append(text)
    return out


def full_text(data: bytes, mode: str = PLAIN) -> str:
    return "\n".join(page_texts(data, mode=mode))


def page_count(data: bytes) -> int:
    return len(_reader(data).pages)


def is_encrypted(data: bytes) -> bool:
    import io

    from pypdf import PdfReader
    try:
        return bool(PdfReader(io.BytesIO(data)).is_encrypted)
    except Exception:  # noqa: BLE001
        return False
