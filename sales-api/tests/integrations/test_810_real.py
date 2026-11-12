"""Confirm and send to the end: a confirmed invoice leaves through the outbox and
the real asyncssh gateway, and its 810 lands on the throwaway SFTP container
(SALES_TEST_SFTP_*). The 945 and the 940 before it use the fakes: their real
round trip is exercised elsewhere. Never a partner's server."""
import json
import os
from datetime import date

import asyncssh
import pytest
import sqlalchemy as sa

from app.integrations.sftp import AsyncsshSftp
from app.models.config import Connection
from app.platform.actors import SYSTEM_ACTOR
from app.schemas.invoices import ConfirmIn
from app.services.invoice_views import InvoiceService
from app.services.settings import INVOICE_CONNECTION_ID, SETTINGS
from tests.test_invoicing import inv_status, p6, shipped_850  # noqa: F401 — p6 is a fixture
from tests.test_orders import OP, env  # noqa: F401
from tests.test_outbound import edi as p4_edi  # noqa: F401

HOST = os.getenv("SALES_TEST_SFTP_HOST")
pytestmark = pytest.mark.skipif(not HOST, reason="no throwaway SFTP container given (SALES_TEST_SFTP_*)")
PORT = int(os.getenv("SALES_TEST_SFTP_PORT", "22"))
USER = os.getenv("SALES_TEST_SFTP_USER", "radiant")
PASSWORD = os.getenv("SALES_TEST_SFTP_PASSWORD", "")


async def remote_810s() -> list[bytes]:
    async with asyncssh.connect(HOST, port=PORT, username=USER, password=PASSWORD, known_hosts=None) as ssh, \
            ssh.start_sftp_client() as s:
        out = []
        for name in await s.listdir("/in"):
            if name.startswith("810-"):
                async with s.open(f"/in/{name}", "rb") as f:
                    out.append(await f.read())
                await s.remove(f"/in/{name}")
        return out


async def test_confirm_and_send_puts_the_810_on_the_server(p6):  # noqa: F811
    o = await shipped_850(p6, "PO-810")
    await p6.inv.advance()
    gateway = AsyncsshSftp(allowed_hosts=frozenset({HOST}), secret=lambda n: PASSWORD if n == "SFTP_PW" else None, connect_timeout=10)
    async with p6.factory(SYSTEM_ACTOR) as u:
        conn = Connection(name="sftp real", kind="SFTP", host=HOST, port=PORT, username=USER, secret_name="SFTP_PW", can_write=True,
                          inbound_folder="/processed", outbound_folder="/in", processed_folder="/processed", error_folder="/error",
                          after_read="LEAVE", stable_seconds=0, active=True, version=1)
        u.session.add(conn)
        await u.session.flush()
        await SETTINGS.set(u.session, INVOICE_CONNECTION_ID, conn.id)
        snapshot = (await u.session.execute(sa.select(Connection).where(Connection.id == conn.id))).scalar_one()
        snapshot.host_key = (await gateway.host_key(snapshot)).line
        await u.commit()
    p6.inv.sftp = gateway

    async with p6.factory(OP) as u:
        await InvoiceService(u).confirm(p6.inv, ConfirmIn(order_id=o.id, confirmed_date=date(2026, 11, 5), send=True))
        await u.commit()
    assert await inv_status(p6, o.id) == "Queued"
    await p6.worker.run_once()

    assert await inv_status(p6, o.id) == "Sent"
    [data] = await remote_810s()
    text = data.decode()
    assert "PO-810" in text and json.loads(text)
