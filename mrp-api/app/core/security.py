"""JWT token decoding (verification only — mrp-api does not issue tokens)."""
from typing import Any

from jose import jwt

from app.core.config import settings


def decode_token(token: str) -> dict[str, Any]:
    """Raises JWTError on invalid/expired tokens."""
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
