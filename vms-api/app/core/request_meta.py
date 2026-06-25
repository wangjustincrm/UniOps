"""Helpers to extract caller metadata for audit logging + visibility scope.

`CurrentUserPayload` (from `app.core.deps`) carries only `sub` and `role`.
For audit log `user_name` and dept-manager visibility scope, we also need
`full_name` and `department_id` — these live in `public.users` (mirrored
read-only here via `app.models.user_mirror.User`).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_mirror import User


@dataclass(frozen=True)
class RequestMeta:
    """All caller info a CRUD operation needs to do audit + scope."""
    user_id: uuid.UUID
    user_name: str
    role: str
    department_id: uuid.UUID | None
    ip_address: str
    user_agent: str | None


async def load_request_meta(
    db: AsyncSession,
    payload: dict,
    request: Request,
) -> RequestMeta:
    """Pull caller meta from JWT payload + Request + the users mirror."""
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        ) from e

    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()
    if user is None:
        # JWT verifies the signature but the user might have been removed.
        # Don't 401 (token is valid); 403 is more precise here.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User no longer exists",
        )

    # IP: trust X-Forwarded-For (left-most) when present; fall back to
    # request.client.host. Capped at 45 chars to fit the audit_log column.
    fwd = request.headers.get("x-forwarded-for")
    ip = (fwd.split(",", 1)[0].strip() if fwd else None) or (
        request.client.host if request.client else "unknown"
    )

    return RequestMeta(
        user_id=user_id,
        user_name=user.full_name,
        role=payload.get("role") or user.role,
        department_id=user.department_id,
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
    )
