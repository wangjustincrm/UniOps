"""A settings form's service, declared once (2026-11-10): which settings its
fields are, who may read and change it, and the checks — the reads, the
partial update, the audit reason and the answer are the same for every group.

    class EdiSettingsService(SettingsGroup):
        registry = SETTINGS
        read_schema = EdiSettings
        fields = {"connection_id": EDI_CONNECTION_ID, "ack_timeout_minutes": ACK_TIMEOUT_MINUTES}
        read_permission, write_permission = READ, CONFIG
        reason = "EDI settings"

An update writes only the fields sent (`exclude_unset`), and of those only the
ones whose value changes (no audit row for a save that changes nothing). The
answer is the values as stored (parsed), so it is what the next read gives.
"""
from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel

from app.platform.authorization import authorize
from app.platform.settings_registry import Setting, SettingsRegistry


class SettingsGroup:
    registry: ClassVar[SettingsRegistry]
    read_schema: ClassVar[type[BaseModel]]
    # The read schema's field → the setting it is. Fields of the schema not here come from extra().
    fields: ClassVar[dict[str, Setting]]
    read_permission: ClassVar[str]
    write_permission: ClassVar[str]
    reason: ClassVar[str] = "settings"

    def __init__(self, uow) -> None:
        self.uow = uow

    # ── hooks ────────────────────────────────────────────────────────────────
    async def extra(self, values: dict[str, Any]) -> dict[str, Any]:
        """Read-only fields of the answer, given the settings' values (e.g. whether this server may run something)."""
        return {}

    async def check(self, changes: dict[str, Any]) -> None:
        """Refuses changes that the fields' own types cannot (raise InvalidInput)."""

    def reason_for(self, changes: dict[str, Any]) -> str:
        return self.reason

    async def after(self, changes: dict[str, Any]) -> dict[str, Any]:
        """Work that follows a change, in the same unit; what it returns is added to the answer."""
        return {}

    # ── the operations ──────────────────────────────────────────────────────
    async def _values(self) -> dict[str, Any]:
        s = self.uow.session
        return {name: await self.registry.get(s, st) for name, st in self.fields.items()}

    async def _answer(self, values: dict[str, Any], **more: Any) -> BaseModel:
        return self.read_schema(**values, **await self.extra(values), **more)

    async def get(self) -> BaseModel:
        await authorize(self.uow, self.read_permission)
        return await self._answer(await self._values())

    async def update(self, body: BaseModel) -> BaseModel:
        await authorize(self.uow, self.write_permission)
        sent = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k in self.fields}
        now = await self._values()
        changes = {k: v for k, v in sent.items() if v != now[k]}
        if not changes:
            return await self._answer(now)
        await self.check(changes)
        self.uow.set_reason(self.reason_for(changes))
        stored = {name: await self.registry.set(self.uow.session, self.fields[name], value) for name, value in changes.items()}
        # The registry's cache is emptied at commit: the answer is the values as written.
        return await self._answer({**now, **stored}, **await self.after(changes))
