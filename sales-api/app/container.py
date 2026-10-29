"""Composition root (design §13.14): every gateway is built here once and the
same objects are handed to the API and the scheduler.

In the test environment every gateway is an in-memory fake. Elsewhere the
real gateway sits behind the platform guard with the two prod-only switches
(design §5.5). Real gateways arrive with the phases that use them — SFTP in
P2, email in P6/P8 — until then building a container outside tests fails
loudly instead of sending anything.

A refused write raises `Suppressed` (the outbox row ends SUPPRESSED) and is
handed to the refusal sink as a `RefusalRecord`: the "would have sent /
written" row of §5.5 (recipients, subject and body hash for email). The sink
writes `sales_log`; it is wired by P0, which creates that table.
"""
from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.platform.container import gateway, is_test
from app.platform.gateways import ExternalEmail, FakeEmail, FakeSftp, Refused

EXTERNAL_EMAIL_SWITCH = "SALES_EXTERNAL_EMAIL_ENABLED"
PARTNER_WRITES_SWITCH = "SALES_PARTNER_WRITES_ENABLED"

SFTP_WRITES = {"put": PARTNER_WRITES_SWITCH, "move": PARTNER_WRITES_SWITCH, "delete": PARTNER_WRITES_SWITCH}
SFTP_READS = ("list", "get")
EMAIL_SENDS = {"send_external": EXTERNAL_EMAIL_SWITCH}


def sftp_live_only(method: str, args: tuple, kwargs: dict) -> str | None:  # noqa: ARG001
    """SFTP writes also need the connection in LIVE mode (design §5.4)."""
    conn = args[0] if args else kwargs.get("conn")
    return None if getattr(conn, "mode", None) == "LIVE" else "connection not LIVE"


def _sha256(data: bytes | str | None) -> str | None:
    if data is None:
        return None
    return hashlib.sha256(data.encode() if isinstance(data, str) else bytes(data)).hexdigest()


def email_summary(method: str, args: tuple, kwargs: dict) -> dict[str, Any]:  # noqa: ARG001
    msg: ExternalEmail = args[0] if args else kwargs["msg"]
    return {
        "recipients": [*msg.to, *msg.cc],
        "subject": msg.subject,
        "body_sha256": _sha256(msg.body_text),
        "body_html_sha256": _sha256(msg.body_html),
        "attachments": [{"name": n, "size": len(d), "sha256": _sha256(d)} for n, d, _ in msg.attachments],
    }


def sftp_summary(method: str, args: tuple, kwargs: dict) -> dict[str, Any]:
    names = {"put": ("conn", "folder", "name", "data"), "move": ("conn", "src", "dst"), "delete": ("conn", "path")}
    a = dict(zip(names[method], args)) | kwargs
    conn = a.pop("conn", None)
    out: dict[str, Any] = {"connection": getattr(conn, "id", None), "mode": getattr(conn, "mode", None)}
    data = a.pop("data", None)
    if data is not None:
        out |= {"size": len(data), "sha256": _sha256(data)}
    return out | {k: str(v) for k, v in a.items()}


@dataclass(frozen=True)
class RefusalRecord:
    """One "would have sent / written" log row (§5.5)."""
    message: str
    context: dict[str, Any] = field(default_factory=dict)


def refusal_record(r: Refused) -> RefusalRecord:
    verb = "sent" if r.gateway == "email" else "written"
    return RefusalRecord(
        message=f"would have {verb}: {r.gateway}.{r.method} ({r.switch}: {r.reason})",
        context={"gateway": r.gateway, "method": r.method, "switch": r.switch, "reason": r.reason, **r.summary},
    )


RefusalSink = Callable[[RefusalRecord], Awaitable[None]]


def _not_built_yet(name: str, phase: str):
    def factory() -> Any:
        raise NotImplementedError(f"the real {name} gateway arrives in {phase}")
    return factory


@dataclass
class Container:
    sftp: Any
    email: Any
    # Task Inbox mail (product owner, 2026-10-28): while external email is off,
    # tasks are written as already notified, so the epms notifier never mails
    # them from a dev or test stack.
    notify_tasks: Callable[[], bool]


def build_container(settings: Any, *, real_sftp=None, real_email=None, refusal_sink: RefusalSink | None = None) -> Container:
    if not is_test(settings) and refusal_sink is None:
        raise TypeError("outside tests a refusal_sink is required: refused writes must be logged (§5.5)")

    async def on_refused(r: Refused) -> None:
        await refusal_sink(refusal_record(r))

    return Container(
        sftp=gateway(
            settings, real=real_sftp or _not_built_yet("SFTP", "P2"), fake=FakeSftp,
            guarded=SFTP_WRITES, reads=SFTP_READS, allow=sftp_live_only, name="sftp",
            summarise=sftp_summary, on_refused=on_refused,
        ),
        email=gateway(
            settings, real=real_email or _not_built_yet("email", "P6/P8"), fake=FakeEmail,
            guarded=EMAIL_SENDS, name="email", summarise=email_summary, on_refused=on_refused,
        ),
        notify_tasks=lambda: getattr(settings, EXTERNAL_EMAIL_SWITCH, False) is True,
    )
