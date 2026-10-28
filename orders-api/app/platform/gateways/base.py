"""Gateway protocols shared by any integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class RemoteFile:
    path: str
    name: str
    size: int
    modified_at: datetime | None = None


class SftpGateway(Protocol):
    # `conn` is whatever connection record the business code keeps (host,
    # credentials, LIVE/TEST mode); the gateway only reads what it needs.
    async def list(self, conn: Any, folder: str) -> list[RemoteFile]: ...
    async def get(self, conn: Any, path: str) -> bytes: ...
    async def put(self, conn: Any, folder: str, name: str, data: bytes) -> None: ...
    async def move(self, conn: Any, src: str, dst: str) -> None: ...
    async def delete(self, conn: Any, path: str) -> None: ...


@dataclass(frozen=True)
class ExternalEmail:
    to: tuple[str, ...]
    subject: str
    body_text: str
    body_html: str | None = None
    cc: tuple[str, ...] = ()
    reply_to: str | None = None
    attachments: tuple[tuple[str, bytes, str], ...] = field(default_factory=tuple)  # (name, data, mime)


class EmailGateway(Protocol):
    async def send_external(self, msg: ExternalEmail) -> None:
        """Raises outbox.NotAccepted when the server did not take the message
        (connection failure, refusal before DATA completed) and
        outbox.AcceptedThenFailed when it did and something later failed."""
