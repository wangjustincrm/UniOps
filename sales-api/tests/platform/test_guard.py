"""The outbound guard (design §5.5): negative and positive pairs, so
"everything refused" cannot pass by accident."""
from types import SimpleNamespace

import pytest

from app.integrations.email import ExternalEmail, FakeEmail
from app.integrations.sftp import FakeSftp
from app.platform.guard import GuardedGateway, Suppressed

MSG = ExternalEmail(to=("a@partner.example",), subject="s", body_text="b")


def _settings(**kw):
    return SimpleNamespace(**{"WRITES": False, "MAIL": False, **kw})


def _sftp(settings, refused, inner=None):
    return GuardedGateway(
        inner if inner is not None else FakeSftp({"/out/a.txt": b"x"}), settings=settings,
        guarded={"put": "WRITES", "move": "WRITES", "delete": "WRITES"}, reads=("list", "get"), name="sftp",
        allow=lambda m, a, k: None if a[0].can_write else "writing is off for this connection",
        on_refused=refused.append,
    )


def _email(settings, inner, **kw):
    return GuardedGateway(inner, settings=settings, guarded={"send_external": "MAIL"}, **kw)


LIVE, TEST = SimpleNamespace(can_write=True), SimpleNamespace(can_write=False)  # Write tick on / off


async def test_sftp_writes_refused_while_switch_off():
    refused, inner = [], FakeSftp({"/out/a.txt": b"x"})
    g = _sftp(_settings(), refused, inner)
    for call in (lambda: g.put(LIVE, "/in", "f.edi", b"data"), lambda: g.move(LIVE, "/out/a.txt", "/arch/a.txt"),
                 lambda: g.delete(LIVE, "/out/a.txt")):
        with pytest.raises(Suppressed):
            await call()
    assert inner.files == {"/out/a.txt": b"x"} and inner.calls == []
    assert [(r.method, r.reason) for r in refused] == [("put", "switch off"), ("move", "switch off"), ("delete", "switch off")]
    assert refused[0].summary["args"][3] == "<4 bytes>"


async def test_sftp_put_happens_with_switch_on_and_write_on():
    refused, inner = [], FakeSftp()
    g = _sftp(_settings(WRITES=True), refused, inner)
    await g.put(LIVE, "/in", "f.edi", b"data")
    assert inner.files["/in/f.edi"] == b"data" and refused == []


async def test_sftp_put_refused_with_write_off_even_with_switch_on():
    refused, inner = [], FakeSftp()
    g = _sftp(_settings(WRITES=True), refused, inner)
    with pytest.raises(Suppressed) as e:
        await g.put(TEST, "/in", "f.edi", b"data")
    assert "/in/f.edi" not in inner.files and refused[0].reason == "writing is off for this connection"
    assert e.value.refused is refused[0]


async def test_reads_pass_through_with_switch_off():
    g = _sftp(_settings(), [])
    assert await g.get(LIVE, "/out/a.txt") == b"x"
    assert [f.name for f in await g.list(LIVE, "/out")] == ["a.txt"]


async def test_email_refused_then_sent_when_switch_flips():
    s, refused, inner = _settings(), [], FakeEmail()
    g = _email(s, inner, on_refused=refused.append)
    with pytest.raises(Suppressed):
        await g.send_external(MSG)
    assert inner.sent == [] and len(refused) == 1
    s.MAIL = True  # read at call time, not at construction
    await g.send_external(MSG)
    assert inner.sent == [MSG] and len(refused) == 1


async def test_only_literal_true_opens_the_switch():
    inner = FakeEmail()
    g = _email(_settings(MAIL="true"), inner)
    with pytest.raises(Suppressed):
        await g.send_external(MSG)
    assert inner.sent == []


def test_guarding_a_missing_method_is_a_wiring_error():
    with pytest.raises(TypeError):
        GuardedGateway(FakeEmail(), settings=_settings(), guarded={"send": "MAIL"})
    with pytest.raises(TypeError):
        GuardedGateway(FakeSftp(), settings=_settings(), guarded={}, reads=("_call",))
    with pytest.raises(TypeError):
        GuardedGateway(FakeSftp(), settings=_settings(), guarded={"put": "WRITES"}, reads=("put",))


def test_anything_not_allow_listed_is_refused():
    g = _sftp(_settings(), [])
    for attr in ("_inner", "_call", "files", "calls", "fail", "_methods", "__dict__", "rename"):
        with pytest.raises(AttributeError):
            getattr(g, attr)
    with pytest.raises(AttributeError):
        g.put = None
    assert dir(g) == ["delete", "get", "list", "move", "put"]
    # A guarded method does not lead back to the wrapped client either.
    assert not hasattr(g.put, "__wrapped__") and not hasattr(g.put, "__self__")


def test_an_email_gateway_exposes_only_its_send():
    g = _email(_settings(), FakeEmail())
    with pytest.raises(AttributeError):
        g.sent  # noqa: B018
    with pytest.raises(AttributeError):
        g.attempts  # noqa: B018


async def test_async_on_refused_is_awaited():
    seen = []

    async def record(r):
        seen.append(r.method)

    g = _email(_settings(), FakeEmail(), on_refused=record)
    with pytest.raises(Suppressed):
        await g.send_external(MSG)
    assert seen == ["send_external"]
