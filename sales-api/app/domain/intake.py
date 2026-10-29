"""What an inbound file is, from its content only (FR-OB-75, design §4.3).

JSON by its top-level key, X12 by ST01 — never by file name. The kinds we
read inbound are 850 (TrueCommerce) and 945 / 944 / 997 (Radiant); anything
else that is recognised — our own 940s and 943s reflected back in Radiant's
folder, an 810, other JSON — is *not ours*: counted, never read as inbound.
A file that cannot be read at all is ours to look at: it is stored as it is
and raises a task (FR-OB-70; 37 of 73 archived 850s are not valid JSON).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from app.integrations import x12

# Inbound kinds and the system that sends them.
INBOUND = {"850_JSON": "TRUECOMMERCE", "945_EDI": "RADIANT", "944_EDI": "RADIANT", "997_EDI": "RADIANT"}
JSON_KEYS = {"PurchaseOrder": "850_JSON", "Invoice": "810_JSON"}
X12_CODES = {"940", "943", "944", "945", "997"}


@dataclass(frozen=True)
class Classified:
    kind: str
    ours: bool
    readable: bool
    system: str | None = None
    problem: str | None = None  # why it could not be read
    isa13: str | None = None
    gs06: str | None = None
    st02: str | None = None


def decode(data: bytes) -> str:
    """Tolerant UTF-8 (FR-OB-70): accented characters kept, a bad byte replaced."""
    return data.decode("utf-8", errors="replace")


def classify(data: bytes) -> Classified:
    text = decode(data).lstrip("﻿ \r\n\t")
    if text.startswith("ISA"):
        return _x12(data)
    if text.startswith(("{", "[")):
        return _json(text)
    return Classified("OTHER", ours=True, readable=False, problem="neither JSON nor X12")


def _x12(data: bytes) -> Classified:
    try:
        ic = x12.read(data)
        txn = next(iter(ic.transactions()), None)
    except x12.X12Error as e:
        return Classified("OTHER", ours=True, readable=False, problem=f"X12 cannot be read: {e}")
    if txn is None:
        return Classified("OTHER", ours=True, readable=False, problem="X12 interchange without a transaction set")
    kind = f"{txn.code}_EDI" if txn.code in X12_CODES else "OTHER"
    group = ic.groups[0]
    return Classified(
        kind, ours=kind in INBOUND, readable=True, system=INBOUND.get(kind),
        isa13=ic.control[:9] or None, gs06=(group.control or None) and group.control[:9], st02=txn.control[:9] or None,
    )


def _json(text: str) -> Classified:
    try:
        body = json.loads(text)
    except ValueError as e:
        return Classified("OTHER", ours=True, readable=False, problem=f"not valid JSON: {e}")
    key = next(iter(body), None) if isinstance(body, dict) else None
    kind = JSON_KEYS.get(key, "OTHER") if isinstance(key, str) else "OTHER"
    return Classified(kind, ours=kind in INBOUND, readable=True, system=INBOUND.get(kind))
