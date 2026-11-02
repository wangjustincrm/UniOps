"""The composition root: fakes in tests, guarded real gateways elsewhere, with
the two prod-only switches (design §5.5 / §13.14)."""
from types import SimpleNamespace

import pytest

from app.container import EXTERNAL_EMAIL_SWITCH, PARTNER_WRITES_SWITCH, build_container
from app.core.config import Settings
from app.integrations.email import ExternalEmail, FakeEmail
from app.integrations.sftp import FakeSftp
from app.platform.guard import GuardedGateway, Suppressed

MSG = ExternalEmail(to=("a@partner.example",), cc=("b@partner.example",), subject="PO 123", body_text="hello")


def test_switches_default_to_false():
    s = Settings(JWT_SECRET_KEY="x")
    assert getattr(s, EXTERNAL_EMAIL_SWITCH) is False and getattr(s, PARTNER_WRITES_SWITCH) is False


def test_test_environment_uses_fakes():
    c = build_container(SimpleNamespace(ENVIRONMENT="test"))
    assert isinstance(c.sftp, FakeSftp) and isinstance(c.email, FakeEmail)


async def _sink_noop(record):
    pass


async def test_outside_tests_a_gateway_not_built_yet_fails_on_first_use():
    """The app starts (it did not, on dev2, while this raised at build time);
    using a gateway a later phase builds fails loudly, before anything goes out."""
    c = build_container(SimpleNamespace(ENVIRONMENT="development", **{EXTERNAL_EMAIL_SWITCH: True}),
                        refusal_sink=_sink_noop)
    with pytest.raises(NotImplementedError, match="SFTP gateway arrives in P2"):
        await c.sftp.list(None, "/in")
    with pytest.raises(NotImplementedError, match="email gateway arrives in P6/P8"):
        await c.email.send_external(None)


async def test_the_runtime_builds_outside_tests_with_the_real_sftp_gateway():
    """What the lifespan does on a dev or prod stack."""
    from app.integrations.sftp import SftpRefused
    from app.runtime import build_runtime
    s = SimpleNamespace(ENVIRONMENT="development", SALES_PARTNER_WRITES_ENABLED=False,
                        SALES_EXTERNAL_EMAIL_ENABLED=False, sftp_allowed_hosts=frozenset({"sftp-test"}))
    rt = build_runtime(s, uow_factory=None)
    assert repr(rt.container.sftp) == "<GuardedGateway sftp>"
    assert rt.intake.sftp is rt.container.sftp and rt.cycle.intake is rt.intake
    # The real gateway with this stack's host list, refusing before any traffic:
    with pytest.raises(SftpRefused, match="not in SALES_SFTP_ALLOWED_HOSTS"):
        await rt.container.sftp.list(SimpleNamespace(host="sftp.truecommerce.com", host_key="k"), "/in")


def test_outside_tests_a_refusal_sink_is_required():
    with pytest.raises(TypeError, match="refusal_sink"):
        build_container(SimpleNamespace(ENVIRONMENT="development"), real_sftp=FakeSftp, real_email=FakeEmail)


RADIANT = "commhub.example"


def _prod_like(email_on, writes_on, write_hosts=frozenset({RADIANT})):
    inner = {"sftp": FakeSftp(), "email": FakeEmail()}
    records = []

    async def sink(r):
        records.append(r)

    s = SimpleNamespace(ENVIRONMENT="production", sftp_write_hosts=write_hosts,
                        **{EXTERNAL_EMAIL_SWITCH: email_on, PARTNER_WRITES_SWITCH: writes_on})
    c = build_container(s, real_sftp=lambda: inner["sftp"], real_email=lambda: inner["email"], refusal_sink=sink)
    return s, c, inner, records


async def test_guarded_outside_tests_refused_while_off_and_logged():
    _, c, inner, records = _prod_like(False, False)
    assert isinstance(c.email, GuardedGateway) and isinstance(c.sftp, GuardedGateway)
    with pytest.raises(Suppressed):
        await c.email.send_external(MSG)
    with pytest.raises(Suppressed):
        await c.sftp.put(SimpleNamespace(mode="LIVE", id=7, host=RADIANT), "/in", "a.edi", b"xyz")
    assert inner["email"].sent == [] and inner["sftp"].files == {}
    mail, put = records
    assert mail.message.startswith("would have sent: email.send_external")
    assert mail.context["recipients"] == ["a@partner.example", "b@partner.example"]
    assert mail.context["subject"] == "PO 123" and len(mail.context["body_sha256"]) == 64
    assert "hello" not in repr(mail.context)  # the body itself is not logged
    assert put.message.startswith("would have written: sftp.put")
    assert put.context | {"sha256": None} == {
        "gateway": "sftp", "method": "put", "switch": PARTNER_WRITES_SWITCH, "reason": "switch off",
        "connection": 7, "mode": "LIVE", "size": 3, "sha256": None, "folder": "/in", "name": "a.edi",
    }


async def test_guarded_outside_tests_allowed_when_on_and_live_only():
    _, c, inner, records = _prod_like(True, True)
    await c.email.send_external(MSG)
    with pytest.raises(Suppressed):
        await c.sftp.put(SimpleNamespace(mode="TEST", host=RADIANT), "/in", "a", b"x")
    await c.sftp.put(SimpleNamespace(mode="LIVE", host=RADIANT), "/in", "b", b"x")
    assert inner["email"].sent == [MSG] and list(inner["sftp"].files) == ["/in/b"]
    assert [r.context["reason"] for r in records] == ["connection not LIVE"]


async def test_a_host_readable_but_not_writable_is_refused_before_any_traffic():
    """The third guard (P4): switch on and LIVE are not enough — the host must
    be in SALES_SFTP_WRITE_HOSTS. Read at call time; empty = no write anywhere."""
    s, c, inner, records = _prod_like(False, True, write_hosts=frozenset({"sftp-test"}))
    truecommerce = SimpleNamespace(mode="LIVE", id=3, host="sftp.truecommerce.example")
    for call in (c.sftp.put(truecommerce, "/Import", "a.json", b"x"), c.sftp.move(truecommerce, "/Export/a", "/Export/done/a"),
                 c.sftp.delete(truecommerce, "/Export/a")):
        with pytest.raises(Suppressed):
            await call
    assert inner["sftp"].calls == [] and inner["sftp"].files == {}  # nothing reached the gateway
    assert {r.context["reason"] for r in records} == {"host not in SALES_SFTP_WRITE_HOSTS"}
    await c.sftp.put(SimpleNamespace(mode="LIVE", id=1, host="SFTP-TEST"), "/in", "ok.edi", b"x")  # case does not matter
    assert list(inner["sftp"].files) == ["/in/ok.edi"]
    s.sftp_write_hosts = frozenset()  # emptied at run time: every write refused at once
    with pytest.raises(Suppressed):
        await c.sftp.put(SimpleNamespace(mode="LIVE", id=1, host="sftp-test"), "/in", "no.edi", b"x")
    assert list(inner["sftp"].files) == ["/in/ok.edi"]


def test_write_hosts_parse_fail_closed():
    from app.core.config import Settings
    assert Settings(SALES_SFTP_WRITE_HOSTS="").sftp_write_hosts == frozenset()
    assert Settings(SALES_SFTP_WRITE_HOSTS=" sftp-test , ,Radiant.Example ").sftp_write_hosts == {"sftp-test", "radiant.example"}


def test_task_notification_follows_the_external_email_switch():
    s, c, _, _ = _prod_like(False, False)
    assert c.notify_tasks() is False
    setattr(s, EXTERNAL_EMAIL_SWITCH, True)
    assert c.notify_tasks() is True
    assert build_container(SimpleNamespace(ENVIRONMENT="test")).notify_tasks() is False
