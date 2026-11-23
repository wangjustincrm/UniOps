"""Security events (≈ ABP's SecurityLog, user 2026-11-23): what the module decides about who may do what — a refusal
today — as one event shape, saved by a SecurityLogStore (the Strategy pattern) the composition root chooses. Logins,
MFA and role changes are identity's. The module's store is its own (nothing shared is written); a store writing to
identity's security log is the swap when the project decides it."""
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class SecurityEvent:
    action: str                     # e.g. "permission_denied"
    user_id: str | None = None
    user_name: str | None = None
    role: str | None = None
    permission: str | None = None
    route: str | None = None
    correlation_id: str | None = None
    client_ip: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class SecurityLogStore(Protocol):
    async def save(self, event: SecurityEvent, uow: Any) -> None: ...
