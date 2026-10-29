"""External email (design §5.5 / §13.9): the gateway protocol, an in-memory
fake, and what the outbound guard needs — the one write and the "would have
sent" summary (recipients, subject, body hash; never the body).

The real aiosmtplib gateway arrives with P6/P8.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


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


WRITES = ("send_external",)
READS: tuple[str, ...] = ()


def guarded(switch: str) -> dict[str, str]:
    return {m: switch for m in WRITES}


def _sha256(data: bytes | str | None) -> str | None:
    if data is None:
        return None
    return hashlib.sha256(data.encode() if isinstance(data, str) else bytes(data)).hexdigest()


def summary(method: str, args: tuple, kwargs: dict) -> dict[str, Any]:  # noqa: ARG001
    msg: ExternalEmail = args[0] if args else kwargs["msg"]
    return {
        "recipients": [*msg.to, *msg.cc],
        "subject": msg.subject,
        "body_sha256": _sha256(msg.body_text),
        "body_html_sha256": _sha256(msg.body_html),
        "attachments": [{"name": n, "size": len(d), "sha256": _sha256(d)} for n, d, _ in msg.attachments],
    }


class FakeEmail:
    """`script` (optional) is called with the attempt number and may raise
    NotAccepted / AcceptedThenFailed to simulate the server's answer."""

    def __init__(self, script: Callable[[int], None] | None = None) -> None:
        self.sent: list[ExternalEmail] = []
        self.attempts = 0
        self.script = script

    async def send_external(self, msg: ExternalEmail) -> None:
        self.attempts += 1
        if self.script is not None:
            self.script(self.attempts)
        self.sent.append(msg)
