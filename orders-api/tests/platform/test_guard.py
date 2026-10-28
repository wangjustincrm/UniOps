"""The outbound guard (design §5.5): negative and positive pairs, so
"everything refused" cannot pass by accident."""
from types import SimpleNamespace

import pytest

from app.platform.gateways import ExternalEmail, FakeEmail, FakeSftp, GuardedGateway

MSG = ExternalEmail(to=("a@partner.example",), subject="s", body_text="b")


def _settings(**kw):
    return SimpleNamespace(**{"WRITES": False, "MAIL": False, **kw})


def _sftp(settings, refused):
    return GuardedGateway(
        FakeSftp({"/out/a.txt": b"x"}), settings=settings,
        guarded={"put": "WRITES", "move": "WRITES", "delete": "WRITES"}, name="sftp",
        allow=lambda m, a, k: None if a[0].mode == "LIVE" else "connection not LIVE",
        on_refused=refused.append,
    )


LIVE, TEST = SimpleNamespace(mode="LIVE"), SimpleNamespace(mode="TEST")


async def test_sftp_writes_refused_while_switch_off():
    refused = []
    g = _sftp(_settings(), refused)
    assert await g.put(LIVE, "/in", "f.edi", b"data") is None
    await g.move(LIVE, "/out/a.txt", "/arch/a.txt")
    await g.delete(LIVE, "/out/a.txt")
    assert g._inner.files == {"/out/a.txt": b"x"}
    assert [(r.method, r.reason) for r in refused] == [("put", "switch off"), ("move", "switch off"), ("delete", "switch off")]
    assert refused[0].summary["args"][3] == "<4 bytes>"


async def test_sftp_put_happens_with_switch_on_and_live_connection():
    refused = []
    g = _sftp(_settings(WRITES=True), refused)
    await g.put(LIVE, "/in", "f.edi", b"data")
    assert g._inner.files["/in/f.edi"] == b"data" and refused == []


async def test_sftp_put_refused_on_test_connection_even_with_switch_on():
    refused = []
    g = _sftp(_settings(WRITES=True), refused)
    await g.put(TEST, "/in", "f.edi", b"data")
    assert "/in/f.edi" not in g._inner.files and refused[0].reason == "connection not LIVE"


async def test_reads_pass_through_with_switch_off():
    g = _sftp(_settings(), [])
    assert await g.get(LIVE, "/out/a.txt") == b"x"
    assert [f.name for f in await g.list(LIVE, "/out")] == ["a.txt"]


async def test_email_refused_then_sent_when_switch_flips():
    s, refused = _settings(), []
    g = GuardedGateway(FakeEmail(), settings=s, guarded={"send_external": "MAIL"}, on_refused=refused.append)
    await g.send_external(MSG)
    assert g._inner.sent == [] and len(refused) == 1
    s.MAIL = True  # read at call time, not at construction
    await g.send_external(MSG)
    assert g._inner.sent == [MSG] and len(refused) == 1


async def test_only_literal_true_opens_the_switch():
    g = GuardedGateway(FakeEmail(), settings=_settings(MAIL="true"), guarded={"send_external": "MAIL"})
    await g.send_external(MSG)
    assert g._inner.sent == []


def test_guarding_a_missing_method_is_a_wiring_error():
    with pytest.raises(TypeError):
        GuardedGateway(FakeEmail(), settings=_settings(), guarded={"send": "MAIL"})


async def test_async_on_refused_is_awaited():
    seen = []

    async def record(r):
        seen.append(r.method)

    g = GuardedGateway(FakeEmail(), settings=_settings(), guarded={"send_external": "MAIL"}, on_refused=record)
    await g.send_external(MSG)
    assert seen == ["send_external"]
