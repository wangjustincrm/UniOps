"""JWT token decoding (verification only — vms-api does not issue tokens).

Tokens are issued by epms-api `/api/v1/auth/login`; vms-api validates locally
via the shared `JWT_SECRET_KEY` — the same pattern every other UniOps backend
uses (no remote introspection).
"""
from typing import Any

from jose import jwt

from app.core.config import settings


def decode_token(token: str) -> dict[str, Any]:
    """Raises JWTError on invalid / expired tokens."""
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
