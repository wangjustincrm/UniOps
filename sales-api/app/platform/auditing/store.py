"""Where an audit log is saved (≈ ABP's IAuditingStore): a Strategy chosen by the composition root. The platform
gives a null store (nothing saved: the stdout line only) and an in-memory one (tests); the module gives its SQL store
on its own tables (models.py mixins), written in a session of its own (≈ ABP's UnitOfWorkManager.Begin(true)), so the
log survives the failed request it describes."""
from typing import Protocol

from app.platform.auditing.scope import AuditLogInfo


class AuditingStore(Protocol):
    async def save(self, log: AuditLogInfo) -> None: ...


class NullAuditingStore:
    async def save(self, log: AuditLogInfo) -> None:  # noqa: ARG002
        return None


class InMemoryAuditingStore:
    def __init__(self) -> None:
        self.logs: list[AuditLogInfo] = []

    async def save(self, log: AuditLogInfo) -> None:
        self.logs.append(log)


_store: AuditingStore = NullAuditingStore()


def use_store(store: AuditingStore) -> None:
    global _store
    _store = store


def current_store() -> AuditingStore:
    return _store
