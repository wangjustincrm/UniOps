"""Composition root (design §13.14): every gateway is built here once and the
same objects are handed to the API and the scheduler.

In the test environment every gateway is an in-memory fake. Elsewhere the
real gateway sits behind the platform guard with the two prod-only switches
(design §5.5). Real gateways arrive with the phases that use them — SFTP in
P2, email in P6/P8 — until then building a container outside tests fails
loudly instead of sending anything.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.platform.container import gateway
from app.platform.gateways import FakeEmail, FakeSftp

EXTERNAL_EMAIL_SWITCH = "ORDERS_EXTERNAL_EMAIL_ENABLED"
PARTNER_WRITES_SWITCH = "ORDERS_PARTNER_WRITES_ENABLED"

SFTP_WRITES = {"put": PARTNER_WRITES_SWITCH, "move": PARTNER_WRITES_SWITCH, "delete": PARTNER_WRITES_SWITCH}
EMAIL_SENDS = {"send_external": EXTERNAL_EMAIL_SWITCH}


def sftp_live_only(method: str, args: tuple, kwargs: dict) -> str | None:  # noqa: ARG001
    """SFTP writes also need the connection in LIVE mode (design §5.4)."""
    conn = args[0] if args else kwargs.get("conn")
    return None if getattr(conn, "mode", None) == "LIVE" else "connection not LIVE"


def _not_built_yet(name: str, phase: str):
    def factory() -> Any:
        raise NotImplementedError(f"the real {name} gateway arrives in {phase}")
    return factory


@dataclass
class Container:
    sftp: Any
    email: Any


def build_container(settings: Any, *, real_sftp=None, real_email=None) -> Container:
    return Container(
        sftp=gateway(
            settings, real=real_sftp or _not_built_yet("SFTP", "P2"), fake=FakeSftp,
            guarded=SFTP_WRITES, allow=sftp_live_only, name="sftp",
        ),
        email=gateway(
            settings, real=real_email or _not_built_yet("email", "P6/P8"), fake=FakeEmail,
            guarded=EMAIL_SENDS, name="email",
        ),
    )
