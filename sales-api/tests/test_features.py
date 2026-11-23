"""The switches as features (user 2026-11-23, ≈ ABP Feature management): the server's environment is the ceiling — a
person can turn partner writes, external email or the scheduler OFF from Health (audited, with a reason), and back to
the server's value, never ON past it; the guards and the scheduler read the effective value."""
from types import SimpleNamespace

import pytest

from app.platform.features import FeatureDefinition, FeatureManager, FeatureSettingsView
from app.services.features import FEATURES
from app.services.settings import automatic_runs_on
from tests.test_order_payment import FINANCE, OPERATOR, api  # noqa: F401 — api is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture

W = "SALES_PARTNER_WRITES_ENABLED"


@pytest.fixture(autouse=True)
def clean():
    FEATURES.stored.values.clear()
    yield
    FEATURES.stored.values.clear()


def test_the_environment_is_the_ceiling_and_the_view_answers_the_switches():
    m = FeatureManager([FeatureDefinition("X_ON", "x"), FeatureDefinition("Y_FREE", "y", env_is_ceiling=False)])
    on, off = SimpleNamespace(X_ON=True, Y_FREE=False, other=7), SimpleNamespace(X_ON=False, Y_FREE=True)
    assert m.effective("X_ON", on) and not m.effective("X_ON", off)
    m.note_stored("X_ON", False)
    assert not m.effective("X_ON", on)          # a person turned it off
    m.note_stored("X_ON", True)
    assert not m.effective("X_ON", off)         # …but never on past the server
    m.note_stored("Y_FREE", True)
    assert m.effective("Y_FREE", SimpleNamespace(Y_FREE=False))  # a free feature: the stored value wins
    view = FeatureSettingsView(on, m)
    assert view.X_ON is True and view.other == 7


async def test_turned_off_from_health_audited_effective_and_back(api):  # noqa: F811
    from app.core.config import settings
    r = await api.post("/api/v1/config/features", json={"name": W, "value": False, "reason": "Radiant is down"}, headers=FINANCE)
    assert r.status_code == 403  # sales.config only
    r = await api.post("/api/v1/config/features", json={"name": W, "value": False, "reason": "Radiant is down"}, headers=OPERATOR)
    assert r.status_code == 200, r.text
    [w] = [f for f in r.json() if f["name"] == W]
    assert (w["stored"], w["effective"]) == (False, False)
    assert FeatureSettingsView(SimpleNamespace(**{W: True}), FEATURES).SALES_PARTNER_WRITES_ENABLED is False  # the guards
    [a] = await api.env.q("SELECT reason FROM sales_audit WHERE entity = 'sales_settings' AND reason LIKE 'Partner writes%'")
    assert a["reason"] == "Partner writes: off: Radiant is down"
    h = (await api.get("/api/v1/operations/health", headers=OPERATOR)).json()
    assert h["partner_writes_enabled"] is False and any(f["name"] == W and f["stored"] is False for f in h["features"])
    r = await api.post("/api/v1/config/features", json={"name": W, "value": True, "reason": "back up"}, headers=OPERATOR)
    [w] = [f for f in r.json() if f["name"] == W]
    assert w["stored"] is None and w["effective"] == bool(getattr(settings, W, False))  # back to the server's value
    r = await api.post("/api/v1/config/features", json={"name": "NOPE", "value": False, "reason": "x y z"}, headers=OPERATOR)
    assert r.status_code == 422


async def test_the_scheduler_switch_off_stops_automatic_runs(api):  # noqa: F811
    on = SimpleNamespace(SALES_SCHEDULER_ENABLED=True)
    from app.platform.actors import SYSTEM_ACTOR
    from app.services.settings import SCHEDULER_PAUSED, SETTINGS
    async with api.env.factory(SYSTEM_ACTOR) as u:
        await SETTINGS.set(u.session, SCHEDULER_PAUSED, False)  # the master switch on (it starts paused)
        SETTINGS.invalidate()
        assert await automatic_runs_on(u.session, on)
        FEATURES.note_stored("SALES_SCHEDULER_ENABLED", False)
        assert not await automatic_runs_on(u.session, on)
