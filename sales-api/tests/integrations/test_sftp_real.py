"""The real asyncssh gateway against a throwaway SFTP container (atmoz/sftp),
started per test run by the runner and given here as SALES_TEST_SFTP_*.
Never a partner's server: the gateway's allow-list is the container alone.

Skipped when no container is given; the runner always gives one."""
import os
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import replace

import pytest
import pytest_asyncio

from app.integrations.sftp import AsyncsshSftp, SftpAuthFailed, SftpError, SftpRefused
from app.services.intake import ConnectionSnapshot
from tests.test_intake import env, x12  # noqa: F401 — env is a fixture

HOST = os.getenv("SALES_TEST_SFTP_HOST")
pytestmark = pytest.mark.skipif(not HOST, reason="no throwaway SFTP container given (SALES_TEST_SFTP_*)")
PORT = int(os.getenv("SALES_TEST_SFTP_PORT", "22"))
USER = os.getenv("SALES_TEST_SFTP_USER", "radiant")
PASSWORD = os.getenv("SALES_TEST_SFTP_PASSWORD", "")


def snapshot(**over) -> ConnectionSnapshot:
    base = dict(id=1, name="sftp-test", kind="SFTP", host=HOST, port=PORT, username=USER, secret_name="SFTP_PW",
                host_key=None, mode="TEST", inbound_folder="/in", processed_folder="/processed",
                error_folder="/error", after_read="LEAVE", stable_seconds=0, auth_failed_at=None)
    return replace(ConnectionSnapshot(**base), **over)


def gateway(hosts=None, password=PASSWORD):
    return AsyncsshSftp(allowed_hosts=frozenset({HOST} if hosts is None else hosts),
                        secret=lambda name: password if name == "SFTP_PW" else None, connect_timeout=10)


@pytest_asyncio.fixture
async def pinned():
    key = await gateway().host_key(snapshot())
    return snapshot(host_key=key.line)


@pytest_asyncio.fixture
async def area(pinned):
    """A fresh folder per test under each of the user's three folders."""
    import asyncssh
    tag = uuid.uuid4().hex[:8]
    async with asyncssh.connect(HOST, port=PORT, username=USER, password=PASSWORD, known_hosts=None) as ssh, \
            ssh.start_sftp_client() as s:
        for top in ("/in", "/processed", "/error"):
            await s.mkdir(f"{top}/{tag}")
    return replace(pinned, inbound_folder=f"/in/{tag}", processed_folder=f"/processed/{tag}",
                   error_folder=f"/error/{tag}")


# ── refusals before any login ────────────────────────────────────────────────

async def test_fail_closed_host_list_unpinned_key_and_missing_secret():
    with pytest.raises(SftpRefused, match="not in SALES_SFTP_ALLOWED_HOSTS"):
        await gateway(hosts=set()).list(snapshot(host_key="ssh-ed25519 AAAA"), "/in")  # empty list: nothing
    with pytest.raises(SftpRefused, match="not in SALES_SFTP_ALLOWED_HOSTS"):
        await gateway().list(snapshot(host="sftp.truecommerce.com", host_key="ssh-ed25519 AAAA"), "/in")
    with pytest.raises(SftpRefused, match="not pinned"):
        await gateway().list(snapshot(), "/in")  # no trust on first use
    with pytest.raises(SftpRefused, match="secret"):
        await gateway(password=None).list(snapshot(host_key="ssh-ed25519 AAAA"), "/in")


async def test_the_host_key_is_read_without_logging_in():
    key = await gateway(password=None).host_key(snapshot())
    assert key.line.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-")) and key.fingerprint.startswith("SHA256:")


async def test_a_key_other_than_the_pinned_one_is_refused(pinned):
    import asyncssh
    other = asyncssh.generate_private_key("ssh-ed25519").export_public_key("openssh").decode().strip()
    with pytest.raises(SftpError, match="other than the pinned"):
        await gateway().list(replace(pinned, host_key=other), "/in")
    assert (await gateway().list(pinned, "/in")) is not None  # the positive half


async def test_a_refused_login_is_an_auth_failure(pinned):
    with pytest.raises(SftpAuthFailed):
        await gateway(password="wrong-password").list(pinned, "/in")


# ── reads and writes ─────────────────────────────────────────────────────────

async def test_put_list_get_move_delete_and_the_check(area):
    gw = gateway()
    await gw.put(area, area.inbound_folder, "a.945", b"bytes \xc3\xa9")
    [f] = await gw.list(area, area.inbound_folder)
    assert (f.name, f.size, f.path) == ("a.945", 8, f"{area.inbound_folder}/a.945") and f.modified_at is not None
    assert await gw.get(area, f.path) == b"bytes \xc3\xa9"
    checks = await gw.check(area, [area.inbound_folder, area.processed_folder, "/no/such/folder"])
    assert [(c.ok, c.files) for c in checks] == [(True, 1), (True, 0), (False, None)]
    assert [x.name for x in await gw.list(area, area.inbound_folder)] == ["a.945"]  # the check changed nothing
    await gw.move(area, f.path, f"{area.processed_folder}/a.945")
    assert await gw.list(area, area.inbound_folder) == []
    await gw.delete(area, f"{area.processed_folder}/a.945")
    assert await gw.list(area, area.processed_folder) == []


async def test_an_intake_pass_over_the_real_server(area, env):  # noqa: F811 — the fixture
    """AC-OB-03 / 04 / 08 end to end: the real server, the app's intake, the
    sales_test document store. After-read MOVE runs unguarded here (the guard
    is tested on its own); only the files we read move."""
    gw = gateway()
    for name, data in (("a.945", x12("945", "000000001")), ("b.945", x12("945", "000000001")),
                       ("mine.940", x12("940", "000000002"))):
        await gw.put(area, area.inbound_folder, name, data)
    c = await env.add_conn(host=HOST, port=PORT, username=USER, secret_name="SFTP_PW", host_key=area.host_key,
                           inbound_folder=area.inbound_folder, processed_folder=area.processed_folder,
                           error_folder=area.error_folder, after_read="MOVE", stable_seconds=0)
    env.intake.sftp = gw
    env.clock.now = datetime.now(timezone.utc) + timedelta(seconds=5)  # real mtimes, real clock
    r = await env.intake.run_pass(c)
    assert (r.listed, r.stored, r.duplicates, r.not_ours, r.balanced) == (3, 1, 1, 1, True)
    assert [f.name for f in await gw.list(area, area.inbound_folder)] == ["mine.940"]
    assert sorted(f.name for f in await gw.list(area, area.processed_folder)) == ["a.945", "b.945"]
    [doc] = await env.q("SELECT content FROM sales_documents WHERE content IS NOT NULL")
    assert bytes(doc["content"]) == x12("945", "000000001")
    r = await env.intake.run_pass(c)  # the 940 stays, and is not downloaded again
    assert (r.listed, r.already_here) == (1, 1)


async def test_a_port_that_is_not_ssh_gives_readable_text():
    """What the dev2 Shopify test hit (SSH against an HTTPS port), on a real
    non-SSH port: the test database's."""
    from app.integrations.sftp import readable
    pg = int(os.getenv("TEST_PG_PORT", "5432"))
    with pytest.raises(Exception) as e:
        await AsyncsshSftp(allowed_hosts=frozenset({"127.0.0.1"}), secret=lambda n: None,
                           connect_timeout=5).host_key(snapshot(host="127.0.0.1", port=pg))
    assert readable(e.value) in ("The server did not answer as an SFTP server on that port.",
                                 "The server did not answer in time.")
