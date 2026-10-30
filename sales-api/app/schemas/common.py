"""Field validators shared by the configuration DTOs.

Their messages are fixed text: a refused value is never echoed back — not in
an error response, an import report or a log line — because what lands in
the wrong column is often a password or a token (2026-10-29: Connections CSV
rows with a Shopify access token and SFTP passwords in host_key).
"""
from __future__ import annotations

import base64
import re

from pydantic_core import PydanticCustomError

# OpenSSH public key types a server presents as its host key.
HOST_KEY_TYPES = (
    "ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com",
)
HOST_KEY_MESSAGE = (
    "host_key must be the server's public key line (e.g. 'ssh-ed25519 AAAA…'). Leave it empty and use "
    "Test connection → Pin this key. Passwords and tokens never go in the CSV; put them in the server "
    "environment under secret_name."
)
SECRET_NAME_MESSAGE = ("secret_name must be an environment variable name like SALES_SECRET_RADIANT "
                       "(capitals, digits, underscore)")
EXCEL_MESSAGE = "this looks like Excel changed a number; format the column as Text"
BARCODE_MESSAGE = ("must be 8, 12, 13 or 14 digits (Excel may have dropped a leading zero or changed the "
                   "number; format the column as Text)")

_SECRET_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
_EXCEL_NUMBER = re.compile(r"[+-]?\d+(\.\d+)?[eE][+-]?\d+")
_BARCODE = re.compile(r"\d{8}|\d{12,14}")


def host_key_line(v: str) -> str:
    """`<type> <base64>[ comment]`, the base64 carrying the same type name. The
    comment is dropped: what is pinned is the key."""
    parts = v.split()
    ok = len(parts) >= 2 and parts[0] in HOST_KEY_TYPES
    if ok:
        try:
            blob = base64.b64decode(parts[1], validate=True)
            n = int.from_bytes(blob[:4], "big")
            ok = blob[4:4 + n] == parts[0].encode()
        except ValueError:
            ok = False
    if not ok:
        raise PydanticCustomError("host_key", HOST_KEY_MESSAGE)
    return f"{parts[0]} {parts[1]}"


def secret_name(v: str) -> str:
    if not _SECRET_NAME.fullmatch(v):
        raise PydanticCustomError("secret_name", SECRET_NAME_MESSAGE)
    return v


def not_excel_number(v: str) -> str:
    """A code or user name Excel turned into scientific notation (1.22E+11)."""
    if _EXCEL_NUMBER.fullmatch(v.strip()):
        raise PydanticCustomError("excel_number", EXCEL_MESSAGE)
    return v


def barcode(v: str) -> str:
    """UPC / GTIN: the GTIN family's lengths, digits only (FR-OB-02)."""
    if not _BARCODE.fullmatch(v.strip()):
        raise PydanticCustomError("barcode", BARCODE_MESSAGE)
    return v.strip()
