"""Pure value-parsing helpers for the PMS → EPMS migration."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def to_decimal(value, default: Decimal = Decimal("0")) -> Decimal:
    """Parse a number that may arrive as float, int, or messy text ($1,234.50)."""
    if value is None or value == "":
        return default
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return default
    s = str(value).replace(",", "").strip()
    try:
        return Decimal(s)
    except InvalidOperation:
        m = _NUM_RE.search(s)
        if m:
            try:
                return Decimal(m.group(0))
            except InvalidOperation:
                return default
        return default


def to_dt(value) -> datetime | None:
    """Parse a SharePoint ISO timestamp into an aware datetime (UTC)."""
    if not value:
        return None
    s = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def to_date(value) -> date | None:
    dt = to_dt(value)
    return dt.date() if dt else None


def clip(value, length: int) -> str | None:
    if value is None:
        return None
    s = str(value)
    return s[:length]


def nz(value, fallback: str) -> str:
    """Non-empty string or fallback (for NOT NULL text columns)."""
    s = (str(value).strip() if value is not None else "")
    return s if s else fallback
