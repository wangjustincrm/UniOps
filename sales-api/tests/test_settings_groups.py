"""A settings form's routes come from what its group declares (stage 4, platform/settings_router.py): declared is mounted and
nothing by hand; reading and changing each need their own permission; a save of nothing changes nothing."""
import importlib
import pkgutil

import pytest

import app.services as services
from app.main import app as application
from app.platform.settings_group import SettingsGroup
from app.platform.settings_router import settings_router
from tests.test_orders import env  # noqa: F401 — a fixture
from tests.test_orders_api import CONFIG, NOBODY, READER, api  # noqa: F401 — api is a fixture

PREFIX = "/api/v1"
NO_ROUTES = {"ShadowSettingService"}  # the shadow run's switch: no page turns it (the runs' scripts do)


def groups() -> list[type[SettingsGroup]]:
    out = set()
    for m in pkgutil.iter_modules(services.__path__):
        mod = importlib.import_module(f"app.services.{m.name}")
        out |= {c for c in vars(mod).values() if isinstance(c, type) and issubclass(c, SettingsGroup) and c is not SettingsGroup}
    return sorted(out, key=lambda c: c.__name__)


MOUNTED = [g for g in groups() if g.path]
ROUTES = {(m, r.path) for r in application.routes for m in getattr(r, "methods", ())}


def test_there_are_the_groups_we_know_and_one_without_routes():
    assert [g.__name__ for g in MOUNTED] == ["EdiSettingsService", "ManualOrderSettingsService", "SchedulerSettingsService"]
    assert {g.__name__ for g in groups()} - {g.__name__ for g in MOUNTED} == NO_ROUTES
    with pytest.raises(TypeError):
        settings_router(next(g for g in groups() if not g.path), uow=lambda: None)


@pytest.mark.parametrize("group", MOUNTED, ids=lambda g: g.__name__)
def test_what_a_group_declares_is_mounted_by_the_router_and_nothing_else_is(group):
    mine = [r for r in application.routes if getattr(r, "path", None) == PREFIX + group.path]
    assert sorted(m for r in mine for m in r.methods) == ["GET", "PUT"]
    assert {r.endpoint.__module__ for r in mine} == {"app.platform.settings_router"}  # not by hand
    put = next(r for r in mine if "PUT" in r.methods)
    assert [p.field_info.annotation for p in put.dependant.body_params] == [group.update_schema or group.read_schema]
    assert all(r.response_model is group.read_schema for r in mine)


@pytest.mark.parametrize("group", MOUNTED, ids=lambda g: g.__name__)
async def test_reading_and_changing_need_their_own_permission_and_nothing_sent_changes_nothing(api, group):  # noqa: F811
    url = PREFIX + group.path
    assert (group.read_permission, group.write_permission) == ("sales.read", "sales.config")
    assert (await api.get(url, headers=NOBODY)).status_code == 403
    before = await api.get(url, headers=READER)
    assert before.status_code == 200
    body = {} if group.update_schema else {k: v for k, v in before.json().items() if k in group.fields}
    audited = len(await api.env.q("SELECT 1 FROM sales_audit"))
    assert (await api.put(url, json=body, headers=READER)).status_code == 403
    r = await api.put(url, json=body, headers=CONFIG)
    assert r.status_code == 200 and r.json() == before.json()
    assert len(await api.env.q("SELECT 1 FROM sales_audit")) == audited  # a save of nothing: no audit row
    assert (await api.put(url, json={**body, "no_such_field": 1}, headers=CONFIG)).status_code == 422


async def test_a_change_is_kept_and_answered_as_stored(api):  # noqa: F811
    url = PREFIX + "/config/manual-order-settings"
    now = (await api.get(url, headers=READER)).json()
    r = await api.put(url, json={**now, "express_offered": not now["express_offered"]}, headers=CONFIG)
    assert r.status_code == 200 and r.json()["express_offered"] is (not now["express_offered"])
    assert (await api.get(url, headers=READER)).json() == r.json()  # committed
