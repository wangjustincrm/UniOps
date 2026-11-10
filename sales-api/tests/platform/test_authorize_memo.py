"""A permission is asked once per unit of work (2026-11-10)."""
import uuid
from types import SimpleNamespace

import pytest

from app.platform.authorization import PermissionDenied, authorize


async def test_each_key_is_asked_once_per_unit_of_work_and_a_denial_stays_one():
    asked: list[str] = []

    async def checker(session, actor, key):
        asked.append(key)
        return key == "sales.read"
    uow = SimpleNamespace(actor=SimpleNamespace(is_system=False, user_id=uuid.uuid4(), role="x"), session=None,
                          permissions=checker, granted={})
    for _ in range(3):
        await authorize(uow, "sales.read")
        with pytest.raises(PermissionDenied):
            await authorize(uow, "sales.config")
    assert asked == ["sales.read", "sales.config"]
    fresh = SimpleNamespace(**{**vars(uow), "granted": {}})  # the next request asks again
    await authorize(fresh, "sales.read")
    assert asked == ["sales.read", "sales.config", "sales.read"]
