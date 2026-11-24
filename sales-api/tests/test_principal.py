"""An active principal (user 2026-11-24, ≈ ABP): a valid token of a user who is no longer active — or no longer
exists — is refused at once (within the cache's TTL), with a clear code and a security event; an active user is
admitted; the system actor and background work never pass the check."""
import uuid

import pytest
import sqlalchemy as sa

from app.integrations.epms_users import shared_users_table
from app.platform.principal import (
    ACTIVE, INACTIVE, AllPrincipalsActive, CachedPrincipalStatus, _listeners, ensure_active, on_inactive, use_principal_status,
)
from app.services.principals import UsersTablePrincipalStatus
from app.services.security_log import inactive_listener
from tests.conftest import make_token
from tests.test_order_payment import GRANTS, api  # noqa: F401 — api is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture

ACTIVE_ID, INACTIVE_ID = uuid.uuid4(), uuid.uuid4()


class Counting:
    def __init__(self, value: str) -> None:
        self.value, self.calls = value, 0

    async def status(self, user_id):  # noqa: ARG002
        self.calls += 1
        return self.value


async def test_the_cache_keeps_a_status_for_its_ttl_then_reads_again():
    now = [0.0]
    src = Counting(ACTIVE)
    cached = CachedPrincipalStatus(src, clock=lambda: now[0])
    u = uuid.uuid4()
    assert await cached.status(u) == ACTIVE and await cached.status(u) == ACTIVE and src.calls == 1
    src.value = INACTIVE  # deactivated meanwhile
    now[0] = 29.0
    assert await cached.status(u) == ACTIVE  # still within the TTL (30 s by default)
    now[0] = 31.0
    assert await cached.status(u) == INACTIVE and src.calls == 2  # within the TTL it takes effect


async def test_the_system_actor_and_unconfigured_runs_pass():
    use_principal_status(AllPrincipalsActive())
    await ensure_active(None)  # no user (the system actor never has a token)
    await ensure_active(uuid.uuid4())  # no source configured: a script, a test


@pytest.fixture
async def users(api):  # noqa: F811
    engine = api.env.factory.session_factory.kw["bind"]
    async with engine.begin() as c:
        await c.run_sync(lambda sc: shared_users_table(sa.MetaData()).create(sc, checkfirst=True))
        await c.execute(sa.text("DELETE FROM users WHERE id IN (:a, :b)"), {"a": ACTIVE_ID, "b": INACTIVE_ID})
        await c.execute(sa.text("INSERT INTO users (id, email, full_name, is_active) VALUES (:a, 'a@x.test', 'Ann Active', true), "
                                "(:b, 'i@x.test', 'Ian Inactive', false)"), {"a": ACTIVE_ID, "b": INACTIVE_ID})
    use_principal_status(CachedPrincipalStatus(UsersTablePrincipalStatus(api.env.factory.session_factory)))
    listener = on_inactive(inactive_listener(api.env.factory.session_factory))
    yield
    use_principal_status(AllPrincipalsActive())
    _listeners[:] = [x for x in _listeners if x is not listener]


async def test_an_active_user_is_admitted_an_inactive_or_missing_one_refused_with_a_security_event(api, users):  # noqa: F811
    GRANTS["reader2"] = {"sales.read"}
    auth = lambda sub: {"Authorization": f"Bearer {make_token('reader2', sub)}"}  # noqa: E731
    assert (await api.get("/api/v1/orders", headers=auth(ACTIVE_ID))).status_code == 200  # admitted
    r = await api.get("/api/v1/orders", headers=auth(INACTIVE_ID))
    assert r.status_code == 401 and r.json()["error"]["code"] == "auth.user_inactive"
    r = await api.get("/api/v1/orders", headers=auth(uuid.uuid4()))  # a user who does not exist (any more)
    assert r.status_code == 401 and r.json()["error"]["code"] == "auth.user_missing"
    rows = await api.env.q("SELECT message, context FROM sales_log WHERE step = 'security' ORDER BY id")
    assert [x["context"]["action"] for x in rows] == ["user_inactive", "user_missing"]
    assert rows[0]["context"]["user_id"] == str(INACTIVE_ID) and "token refused" in rows[0]["message"]
