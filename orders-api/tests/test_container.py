"""The composition root: fakes in tests, guarded real gateways elsewhere, with
the two prod-only switches (design §5.5 / §13.14)."""
from types import SimpleNamespace

import pytest

from app.container import EXTERNAL_EMAIL_SWITCH, PARTNER_WRITES_SWITCH, build_container
from app.core.config import Settings
from app.platform.gateways import ExternalEmail, FakeEmail, FakeSftp, GuardedGateway

MSG = ExternalEmail(to=("a@partner.example",), subject="s", body_text="b")


def test_switches_default_to_false():
    s = Settings(JWT_SECRET_KEY="x")
    assert getattr(s, EXTERNAL_EMAIL_SWITCH) is False and getattr(s, PARTNER_WRITES_SWITCH) is False


def test_test_environment_uses_fakes():
    c = build_container(SimpleNamespace(ENVIRONMENT="test"))
    assert isinstance(c.sftp, FakeSftp) and isinstance(c.email, FakeEmail)


def test_outside_tests_real_gateways_are_not_built_yet():
    with pytest.raises(NotImplementedError, match="P2"):
        build_container(SimpleNamespace(ENVIRONMENT="development"))


def _prod_like(email_on, writes_on):
    s = SimpleNamespace(ENVIRONMENT="production", **{EXTERNAL_EMAIL_SWITCH: email_on, PARTNER_WRITES_SWITCH: writes_on})
    return s, build_container(s, real_sftp=FakeSftp, real_email=FakeEmail)


async def test_guarded_outside_tests_refused_while_off():
    _, c = _prod_like(False, False)
    assert isinstance(c.email, GuardedGateway) and isinstance(c.sftp, GuardedGateway)
    await c.email.send_external(MSG)
    await c.sftp.put(SimpleNamespace(mode="LIVE"), "/in", "a", b"x")
    assert c.email._inner.sent == [] and c.sftp._inner.files == {}


async def test_guarded_outside_tests_allowed_when_on_and_live_only():
    _, c = _prod_like(True, True)
    await c.email.send_external(MSG)
    await c.sftp.put(SimpleNamespace(mode="TEST"), "/in", "a", b"x")
    await c.sftp.put(SimpleNamespace(mode="LIVE"), "/in", "b", b"x")
    assert c.email._inner.sent == [MSG] and list(c.sftp._inner.files) == ["/in/b"]
