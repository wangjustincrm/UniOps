"""Composition root (design §13.14): every gateway is built here once and the
same objects are handed to the API and the scheduler.

In the test environment every gateway is an in-memory fake. Elsewhere the
real gateway sits behind the platform guard with the two prod-only switches
(design §5.5). Real gateways arrive with the phases that use them — SFTP in
P2 (app/runtime.py passes it in), email in P6/P8. Until then the app starts
with a stand-in for each missing one that fails loudly on its first use, so
nothing is ever sent by a gateway that does not exist yet.

A refused write raises `Suppressed` (the outbox row ends SUPPRESSED) and is
handed to the refusal sink as a `RefusalRecord`: the "would have sent /
written" row of §5.5 (recipients, subject and body hash for email). The sink
(app/runtime.py) writes it to `sales_log`.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.integrations import email, sftp, shopify
from app.integrations.email import FakeEmail
from app.integrations.sftp import FakeSftp
from app.integrations.shopify import FakeShopify
from app.platform.container import gateway, is_test
from app.platform.guard import Refused

EXTERNAL_EMAIL_SWITCH = "SALES_EXTERNAL_EMAIL_ENABLED"
PARTNER_WRITES_SWITCH = "SALES_PARTNER_WRITES_ENABLED"
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


class _NotBuiltYet:
    """Stands in for a real gateway a later phase builds: every call raises."""

    def __init__(self, name: str, phase: str) -> None:
        self._name, self._phase = name, phase

    def __getattr__(self, method: str) -> Any:
        if method.startswith("_"):
            raise AttributeError(method)

        async def refuse(*args: Any, **kwargs: Any) -> Any:
            raise NotImplementedError(f"the real {self._name} gateway arrives in {self._phase} ({method})")
        return refuse


def _not_built_yet(name: str, phase: str):
    return lambda: _NotBuiltYet(name, phase)


@dataclass
class Container:
    sftp: Any
    email: Any
    shopify: Any
    # Task Inbox mail (product owner, 2026-10-28): while external email is off,
    # tasks are written as already notified, so the epms notifier never mails
    # them from a dev or test stack.
    notify_tasks: Callable[[], bool]


def build_container(settings: Any, *, real_sftp=None, real_email=None, real_shopify=None,
                    refusal_sink: RefusalSink | None = None) -> Container:
    if not is_test(settings) and refusal_sink is None:
        raise TypeError("outside tests a refusal_sink is required: refused writes must be logged (§5.5)")

    async def on_refused(r: Refused) -> None:
        await refusal_sink(refusal_record(r))

    return Container(
        sftp=gateway(
            settings, real=real_sftp or _not_built_yet("SFTP", "P2"), fake=FakeSftp,
            guarded=sftp.guarded(PARTNER_WRITES_SWITCH), reads=sftp.READS, allow=sftp.live_only, name="sftp",
            summarise=sftp.summary, on_refused=on_refused,
        ),
        email=gateway(
            settings, real=real_email or _not_built_yet("email", "P6/P8"), fake=FakeEmail,
            guarded=email.guarded(EXTERNAL_EMAIL_SWITCH), reads=email.READS, name="email",
            summarise=email.summary, on_refused=on_refused,
        ),
        shopify=gateway(
            settings, real=real_shopify or _not_built_yet("Shopify", "P3"), fake=FakeShopify,
            guarded=shopify.guarded(PARTNER_WRITES_SWITCH), reads=shopify.READS, name="shopify",
            on_refused=on_refused,
        ),
        notify_tasks=lambda: getattr(settings, EXTERNAL_EMAIL_SWITCH, False) is True,
    )
