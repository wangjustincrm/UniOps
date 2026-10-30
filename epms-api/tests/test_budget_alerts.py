"""ISS-0146 ③: budget threshold alert emails (email only, no Teams)."""
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm.attributes import flag_modified

import app.db.session as session_module
from app.crud import user as user_crud
from app.crud.config import get_or_create as get_config
from app.models.department import Department
from app.schemas.auth import RegisterRequest
from app.services import budget_client
from app.tasks import budget_alerts as mod



@pytest.fixture
def sessions(test_engine, monkeypatch):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(session_module, "AsyncSessionLocal", factory)
    return factory


@pytest.fixture
def mail(monkeypatch):
    out = {"to": [], "admin": []}

    async def _send(to, subject, html, **kw):
        out["to"].append((to, subject))

    async def _admin(subject, body, db=None):
        out["admin"].append(subject)
    monkeypatch.setattr("app.services.email.send_email", _send)
    monkeypatch.setattr(mod, "send_admin_alert", _admin)
    return out


@pytest.fixture
async def config(sessions):
    async with sessions() as db:
        cfg = await get_config(db)
        before = (dict(cfg.notification_settings or {}), dict(cfg.budget_admin_config or {}),
                  dict(cfg.dept_gm_opm_mapping or {}))
        await db.commit()

    async def _set(ns=None, gm_opm=None):
        async with sessions() as db:
            cfg = await get_config(db)
            cfg.notification_settings = {**before[0], **(ns or {})}
            cfg.budget_admin_config = {**before[1], "yellow_threshold_pct": 80, "red_threshold_pct": 100}
            cfg.dept_gm_opm_mapping = {**before[2], **(gm_opm or {})}
            for f in ("notification_settings", "budget_admin_config", "dept_gm_opm_mapping"):
                flag_modified(cfg, f)
            await db.commit()
    yield _set
    async with sessions() as db:
        cfg = await get_config(db)
        cfg.notification_settings, cfg.budget_admin_config, cfg.dept_gm_opm_mapping = before
        for f in ("notification_settings", "budget_admin_config", "dept_gm_opm_mapping"):
            flag_modified(cfg, f)
        await db.commit()


async def _user(db, role, dept=None):
    u = await user_crud.create(db, RegisterRequest(
        email=f"{role}-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name=f"T {role}", role=role))
    u.department_id = dept
    return u


async def _world(db):
    a = Department(code=f"DA{uuid.uuid4().hex[:5]}", name="Dept A")
    b = Department(code=f"DB{uuid.uuid4().hex[:5]}", name="Dept B (no manager)")
    db.add_all([a, b])
    await db.flush()
    people = {"dm": await _user(db, "dept_manager", a.id), "fm": await _user(db, "finance_manager"),
              "gm": await _user(db, "gm"), "admin": await _user(db, "system_admin")}
    await db.commit()
    return a, b, people


def _stub(monkeypatch, a, b, usage):
    """usage: {(cc, account): (budget, actual)} for cc in 'A','B'."""
    cc = {"A": str(uuid.uuid4()), "B": str(uuid.uuid4())}
    acct = {}

    async def _scope(token):
        return {"full_access": True, "cost_centers": [
            {"id": cc["A"], "code": "CC-A", "name": "A", "department_id": str(a.id)},
            {"id": cc["B"], "code": "CC-B", "name": "B", "department_id": str(b.id)}]}

    async def _summary(token, fy, cost_center_id=None, include_unposted=False):
        assert include_unposted is True
        name = "A" if str(cost_center_id) == cc["A"] else "B"
        rows = []
        for (c, account), (budget, actual) in usage.items():
            if c != name:
                continue
            aid = acct.setdefault((c, account), str(uuid.uuid4()))
            rows.append({"account_id": aid, "account_code": account, "account_name": account,
                         "l1_code": "L1", "annual_budget": str(budget), "committed": "0",
                         "actual_spent": str(actual)})
        return {"accounts": rows}
    monkeypatch.setattr(budget_client, "get_actuals_scope", _scope)
    monkeypatch.setattr(budget_client, "get_actuals_summary", _summary)


def test_level_of():
    from decimal import Decimal as D
    y, r = D(80), D(100)
    assert mod.level_of(D(100), D(79), y, r) is None
    assert mod.level_of(D(100), D(80), y, r) == "warning"
    assert mod.level_of(D(100), D(100), y, r) == "over"
    assert mod.level_of(D(0), D(5), y, r) == "over"       # spend, no budget
    assert mod.level_of(D(0), D(0), y, r) is None


@pytest.mark.asyncio
async def test_off_and_dry_run_email_nobody(sessions, mail, config, monkeypatch):
    async with sessions() as db:
        a, b, people = await _world(db)
    _stub(monkeypatch, a, b, {("A", "TRAVEL"): (100, 120)})
    await config(ns={"budget_alert_enabled": False})
    await mod.run_budget_alerts()
    assert mail == {"to": [], "admin": []}

    await config(ns={"budget_alert_enabled": True})      # dry run is the default
    await mod.run_budget_alerts()
    assert people["dm"].email not in [t for t, _ in mail["to"]]
    assert len(mail["admin"]) == 1 and "Dry run" in mail["admin"][0]
    await mod.run_budget_alerts()                         # nothing marked: listed again
    assert len(mail["admin"]) == 2


@pytest.mark.asyncio
async def test_crossings_are_emailed_once_and_rearm(sessions, mail, config, monkeypatch):
    async with sessions() as db:
        a, b, people = await _world(db)
    await config(ns={"budget_alert_enabled": True, "budget_alert_dry_run": False},
                 gm_opm={str(a.id): "gm"})
    usage = {("A", "TRAVEL"): (100, 85), ("A", "IT"): (100, 120), ("B", "OFFICE"): (100, 130)}
    _stub(monkeypatch, a, b, usage)

    await mod.run_budget_alerts()
    to = [t for t, _ in mail["to"]]
    # Dept A: DM (warning + over), FM (over), its GM (over). Dept B has no
    # manager → the FM gets B's line; B's line is over, so the FM anyway.
    assert people["dm"].email in to and people["fm"].email in to and people["gm"].email in to
    first = len(mail["to"])

    await mod.run_budget_alerts()                         # same usage: no new crossing
    assert len(mail["to"]) == first

    usage[("A", "TRAVEL")] = (100, 101)                   # warning → over: alerts again
    await mod.run_budget_alerts()
    assert len(mail["to"]) > first
    second = len(mail["to"])

    usage[("A", "IT")] = (100, 10)                        # drops back: mark cleared …
    await mod.run_budget_alerts()
    assert len(mail["to"]) == second
    usage[("A", "IT")] = (100, 150)                       # … and crossing again alerts
    await mod.run_budget_alerts()
    assert len(mail["to"]) > second


@pytest.mark.asyncio
async def test_department_without_manager_goes_to_finance_manager(sessions, mail, config, monkeypatch):
    async with sessions() as db:
        a, b, people = await _world(db)
    await config(ns={"budget_alert_enabled": True, "budget_alert_dry_run": False})
    _stub(monkeypatch, a, b, {("B", "OFFICE"): (100, 85)})   # warning only, dept B
    await mod.run_budget_alerts()
    to = [t for t, _ in mail["to"]]
    assert people["fm"].email in to and people["dm"].email not in to
