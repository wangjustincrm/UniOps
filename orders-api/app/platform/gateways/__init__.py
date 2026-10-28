"""Gateways to the outside (design §13.9): protocols, in-memory fakes, and
the guard that holds back every outbound write unless its switch is on.

Real implementations arrive with the phases that first use them (SFTP with
P2, email with P6/P8); API gateways specific to one partner are business code.
"""
from app.platform.gateways.base import (
    EmailGateway,
    ExternalEmail,
    RemoteFile,
    SftpGateway,
)
from app.platform.gateways.fakes import FakeEmail, FakeSftp
from app.platform.gateways.guard import GuardedGateway, Refused

__all__ = [
    "EmailGateway", "ExternalEmail", "FakeEmail", "FakeSftp", "GuardedGateway",
    "Refused", "RemoteFile", "SftpGateway",
]
