import pytest
from sqlalchemy import select

from app.platform.errors import InvalidInput
from app.platform.settings_registry import Setting, SettingsRegistry, as_bool, minimum
from tests.platform.conftest import AuditRow, SettingRow


class Tick:
    t = 0.0

    def __call__(self):
        return self.t


def _registry(clock):
    reg = SettingsRegistry(SettingRow, ttl_seconds=5, clock=clock)
    interval = reg.declare(Setting("interval_minutes", 5, int, minimum(1), safe=None))
    flag = reg.declare(Setting("feature_on", False, as_bool))
    return reg, interval, flag


async def test_default_set_read_and_audit(uow_factory, person):
    reg, interval, flag = _registry(Tick())
    async with uow_factory(person) as uow:
        assert await reg.get(uow.session, interval) == 5
        await reg.set(uow.session, flag, "true")
        await reg.set(uow.session, interval, "10")
        await uow.commit()
    async with uow_factory(person) as uow:
        assert await reg.get(uow.session, interval) == 10 and await reg.get(uow.session, flag) is True
        audit = (await uow.session.execute(select(AuditRow.entity, AuditRow.action))).all()
    assert [tuple(a) for a in audit] == [("pt_settings", "create"), ("pt_settings", "create")]


async def test_invalid_value_refused(uow_factory, person):
    reg, interval, _ = _registry(Tick())
    async with uow_factory(person) as uow:
        with pytest.raises(InvalidInput):
            await reg.set(uow.session, interval, 0)
        with pytest.raises(InvalidInput):
            await reg.set(uow.session, interval, "abc")


async def test_cache_expires_so_changes_are_seen_without_restart(uow_factory, person):
    clock = Tick()
    reg, interval, _ = _registry(clock)
    async with uow_factory(person) as uow:
        assert await reg.get(uow.session, interval) == 5
        uow.session.add(SettingRow(key="interval_minutes", value=7))  # another process wrote it
        await uow.commit()
    async with uow_factory(person) as uow:
        assert await reg.get(uow.session, interval) == 5  # still cached
        clock.t = 6
        assert await reg.get(uow.session, interval) == 7


def test_declaring_twice_or_with_a_bad_default_is_a_bug():
    reg = SettingsRegistry(SettingRow)
    reg.declare(Setting("a", 1, int, safe=0))
    with pytest.raises(ValueError):
        reg.declare(Setting("a", 1, int, safe=0))
    with pytest.raises(InvalidInput):
        reg.declare(Setting("b", 0, int, minimum(1), safe=None))
    with pytest.raises(InvalidInput):
        reg.declare(Setting("c", 5, int, minimum(1), safe=0))  # a safe value must be valid too
    with pytest.raises(ValueError, match="safe"):
        reg.declare(Setting("d", 5, int))  # not a boolean: safe must be explicit


async def test_bad_stored_value_is_safe_off_never_the_on_default(uow_factory, person):
    reported = []

    async def on_invalid(session, setting, raw, err):
        reported.append((setting.key, raw))

    reg = SettingsRegistry(SettingRow, on_invalid=on_invalid)
    feature = reg.declare(Setting("send_enabled", True, as_bool))  # default ON
    interval = reg.declare(Setting("interval_minutes", 5, int, minimum(1), safe=None))
    async with uow_factory(person) as uow:
        uow.session.add(SettingRow(key="send_enabled", value="maybe"))
        uow.session.add(SettingRow(key="interval_minutes", value=0))
        await uow.commit()
    async with uow_factory(person) as uow:
        assert await reg.get(uow.session, feature) is False
        assert await reg.get(uow.session, interval) is None  # the caller treats None as paused
    assert reported == [("send_enabled", "maybe"), ("interval_minutes", 0)]


async def test_a_failing_on_invalid_never_fails_the_read(uow_factory, person):
    async def broken(*a):
        raise RuntimeError("task service down")

    reg = SettingsRegistry(SettingRow, on_invalid=broken)
    feature = reg.declare(Setting("send_enabled", True, as_bool))
    async with uow_factory(person) as uow:
        uow.session.add(SettingRow(key="send_enabled", value=[1]))
        await uow.commit()
    async with uow_factory(person) as uow:
        assert await reg.get(uow.session, feature) is False


async def test_a_change_is_seen_right_after_commit_and_not_before(uow_factory, person):
    reg = SettingsRegistry(SettingRow, ttl_seconds=3600)
    interval = reg.declare(Setting("interval_minutes", 5, int, minimum(1), safe=None))
    async with uow_factory(person) as writer:
        await reg.set(writer.session, interval, 9)
        async with uow_factory(person) as reader:
            assert await reg.get(reader.session, interval) == 5  # not committed yet
        await writer.commit()
    async with uow_factory(person) as reader:
        assert await reg.get(reader.session, interval) == 9  # despite the hour-long TTL


async def test_a_rolled_back_change_leaves_the_cache_alone(uow_factory, person):
    reg = SettingsRegistry(SettingRow, ttl_seconds=3600)
    interval = reg.declare(Setting("interval_minutes", 5, int, minimum(1), safe=None))
    async with uow_factory(person) as writer:
        await reg.set(writer.session, interval, 9)
    async with uow_factory(person) as reader:
        assert await reg.get(reader.session, interval) == 5
