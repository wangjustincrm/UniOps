"""Who did it. Scheduler work runs as SYSTEM_ACTOR and is recorded as such,
never as a person (design §13.13)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID | None
    name: str
    # The base role from the access token (UniOps role matrix); None for the
    # system actor.
    role: str | None = None

    @property
    def is_system(self) -> bool:
        return self.user_id is None


SYSTEM_ACTOR = Actor(user_id=None, name="SYSTEM")


def actor_from_token(payload: dict[str, Any]) -> Actor:
    """The person behind a verified access-token payload."""
    sub = payload.get("sub")
    if not sub:
        raise ValueError("token payload has no subject")
    name = payload.get("full_name") or payload.get("name") or payload.get("email") or str(sub)
    return Actor(user_id=uuid.UUID(str(sub)), name=str(name), role=payload.get("role") or None)
