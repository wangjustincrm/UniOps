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


def test_outside_tests_real_gateways_are_not_built_yet():
    with pytest.raises(NotImplementedError, match="P2"):
        build_container(SimpleNamespace(ENVIRONMENT="development"), refusal_sink=_sink_noop)


def test_outside_tests_a_refusal_sink_is_required():
    with pytest.raises(TypeError, match="refusal_sink"):
        build_container(SimpleNamespace(ENVIRONMENT="development"), real_sftp=FakeSftp, real_email=FakeEmail)


def _prod_like(email_on, writes_on):
    inner = {"sftp": FakeSftp(), "email": FakeEmail()}
    records = []

    async def sink(r):
        records.append(r)

    s = SimpleNamespace(ENVIRONMENT="production", **{EXTERNAL_EMAIL_SWITCH: email_on, PARTNER_WRITES_SWITCH: writes_on})
    c = build_container(s, real_sftp=lambda: inner["sftp"], real_email=lambda: inner["email"], refusal_sink=sink)
    return s, c, inner, records


async def test_guarded_outside_tests_refused_while_off_and_logged():
    _, c, inner, records = _prod_like(False, False)
    assert isinstance(c.email, GuardedGateway) and isinstance(c.sftp, GuardedGateway)
    with pytest.raises(Suppressed):
        await c.email.send_external(MSG)
    with pytest.raises(Suppressed):
        await c.sftp.put(SimpleNamespace(mode="LIVE", id=7), "/in", "a.edi", b"xyz")
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
        await c.sftp.put(SimpleNamespace(mode="TEST"), "/in", "a", b"x")
    await c.sftp.put(SimpleNamespace(mode="LIVE"), "/in", "b", b"x")
    assert inner["email"].sent == [MSG] and list(inner["sftp"].files) == ["/in/b"]
    assert [r.context["reason"] for r in records] == ["connection not LIVE"]


def test_task_notification_follows_the_external_email_switch():
    s, c, _, _ = _prod_like(False, False)
    assert c.notify_tasks() is False
    setattr(s, EXTERNAL_EMAIL_SWITCH, True)
    assert c.notify_tasks() is True
    assert build_container(SimpleNamespace(ENVIRONMENT="test")).notify_tasks() is False
