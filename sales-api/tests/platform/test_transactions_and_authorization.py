"""STEP 1 (2026-10-29): one transaction per operation, and authorization at
the service.

- Only the outermost caller commits (an endpoint, once; a job's unit). An
  operation that calls two services and fails in the second leaves nothing —
  no row, no audit row, no outbox row.
- A service checks the keys it declares before it reads or writes anything;
  the system actor passes only where that is declared.
"""
import ast
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI, Header
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select

from app.platform.actors import SYSTEM_ACTOR
from app.platform.authorization import PermissionDenied, authorize, requires
from app.platform.errors import install_error_handlers
from app.platform.outbox import Outbox
from app.platform.uow import UnitOfWorkFactory
from tests.platform.conftest import (
    AuditRow, OutboxRow, Thing, Widget, WidgetCreate, WidgetService, actor_with,
)
from tests.platform.test_crud_service import Finish, FinishCmd

OUTBOX = Outbox(OutboxRow)


async def _count(uow_factory, model):
    async with uow_factory(SYSTEM_ACTOR) as uow:
        return len((await uow.session.execute(select(model))).scalars().all())


async def _thing(uow_factory, person, status="open"):
    async with uow_factory(person) as uow:
        t = Thing(status=status)
        uow.session.add(t)
        await uow.commit()
        return t.id


# ── one transaction per operation ────────────────────────────────────────────


@pytest_asyncio.fixture
async def op_http(uow_factory):
    """An endpoint that runs two services in one unit of work and commits
    once at the end — the shape every write endpoint has."""
    async def uow(x_perms: str = Header(default="")):
        async with uow_factory(actor_with(*[k for k in x_perms.split(",") if k], name="Op")) as u:
            yield u

    app = FastAPI()
    install_error_handlers(app)

    from typing import Annotated

    from fastapi import Depends

    @app.post("/op2/{thing_id}")
    async def op2(thing_id: int, u: Annotated[object, Depends(uow)], name: str = "fine"):
        await Finish(u).execute(FinishCmd(aggregate_id=thing_id))  # first: a command (audit row)
        OUTBOX.add(u.session, "notify", {"t": thing_id})
        await WidgetService(u).create(WidgetCreate(code="W2", name=name))  # second: may refuse
        await u.commit()
        return {"ok": True}

    @app.post("/op/{thing_id}")
    async def op(thing_id: int, u: Annotated[object, Depends(uow)], refuse: bool = False):
        await WidgetService(u).create(WidgetCreate(code="W1", name="made first"))  # flushes; audit row
        OUTBOX.add(u.session, "notify", {"w": "W1"})
        await u.session.flush()
        await Finish(u).execute(FinishCmd(aggregate_id=thing_id, refuse=refuse))  # the second service
        await u.commit()
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as c:
        yield c


ALL = {"X-Perms": "pt.read,pt.config,pt.operate"}


async def test_failure_in_the_second_service_leaves_nothing_written(op_http, uow_factory, person):
    tid = await _thing(uow_factory, person)
    r = await op_http.post(f"/op/{tid}?refuse=true", headers=ALL)
    assert r.status_code == 409 and r.json()["error"]["code"] == "thing.refused"
    assert await _count(uow_factory, Widget) == 0
    assert await _count(uow_factory, AuditRow) == 0
    assert await _count(uow_factory, OutboxRow) == 0
    # The positive half: the same operation without the failure keeps all three.
    r = await op_http.post(f"/op/{tid}", headers=ALL)
    assert r.status_code == 200
    assert (await _count(uow_factory, Widget), await _count(uow_factory, OutboxRow)) == (1, 1)
    assert await _count(uow_factory, AuditRow) >= 2  # the widget create + the thing transition


async def test_a_command_first_then_a_failing_service_leaves_nothing(op_http, uow_factory, person):
    tid = await _thing(uow_factory, person)
    r = await op_http.post(f"/op2/{tid}?name=forbidden", headers=ALL)
    assert r.status_code == 409 and r.json()["error"]["code"] == "widget.name_forbidden"
    async with uow_factory(person) as uow:
        assert (await uow.session.get(Thing, tid)).status == "open"  # the command's change is gone too
    assert (await _count(uow_factory, Widget), await _count(uow_factory, AuditRow),
            await _count(uow_factory, OutboxRow)) == (0, 0, 0)
    assert (await op_http.post(f"/op2/{tid}", headers=ALL)).status_code == 200  # positive half
    async with uow_factory(person) as uow:
        assert (await uow.session.get(Thing, tid)).status == "done"


async def test_a_denied_second_service_also_leaves_nothing(op_http, uow_factory, person):
    tid = await _thing(uow_factory, person)
    r = await op_http.post(f"/op/{tid}", headers={"X-Perms": "pt.read,pt.config"})  # no pt.operate
    assert r.status_code == 403
    assert (await _count(uow_factory, Widget), await _count(uow_factory, AuditRow),
            await _count(uow_factory, OutboxRow)) == (0, 0, 0)


async def test_a_command_service_on_its_own_commits_nothing(uow_factory, person):
    tid = await _thing(uow_factory, person)
    async with uow_factory(person) as uow:
        out = await Finish(uow).execute(FinishCmd(aggregate_id=tid))
        assert out.status == "done"  # changed and flushed inside the unit …
    async with uow_factory(person) as uow:
        assert (await uow.session.get(Thing, tid)).status == "open"  # … but never committed
    assert await _count(uow_factory, AuditRow) == 0


# ── the rule "a write endpoint commits exactly once" (static check) ──────────

APP = Path(__file__).resolve().parents[2] / "app"
WRITE_METHODS = {"post", "put", "patch", "delete"}


def endpoint_commit_counts(source: str) -> dict[str, int]:
    """Every function decorated with `<router>.post/put/patch/delete(...)` →
    how many `.commit()` calls its body contains."""
    out = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        writes = any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in WRITE_METHODS
            for d in node.decorator_list
        )
        if writes:
            out[node.name] = sum(
                1 for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "commit"
            )
    return out


def test_every_write_endpoint_commits_exactly_once():
    files = [APP / "platform" / "crud_router.py", *sorted((APP / "api").rglob("*.py"))]
    counts = {f"{p.relative_to(APP)}:{name}": n for p in files for name, n in endpoint_commit_counts(p.read_text()).items()}
    assert len(counts) >= 4, counts  # crud_router's import, create, update, deactivate
    assert {k: n for k, n in counts.items() if n != 1} == {}


def test_the_endpoint_check_flags_missing_and_double_commits():
    src = '''
@router.post("/a")
async def none(u): await svc(u).create()
@router.put("/b")
async def twice(u):
    await u.commit()
    await u.commit()
@router.post("/c")
async def once(u):
    await svc(u).create()
    await u.commit()
@router.get("/d")
async def read(u): return 1
'''
    assert endpoint_commit_counts(src) == {"none": 0, "twice": 2, "once": 1}


# ── authorization at the service ─────────────────────────────────────────────


async def test_denied_without_the_key_and_allowed_with_it(uow_factory, person, stranger):
    async with uow_factory(stranger) as uow:
        with pytest.raises(PermissionDenied) as e:
            await WidgetService(uow).create(WidgetCreate(code="X", name="x"))
        assert e.value.status_code == 403 and e.value.details == {"permission": "pt.config"}
        with pytest.raises(PermissionDenied):
            await WidgetService(uow).list(__import__("app.platform.query", fromlist=["QuerySpec"]).QuerySpec())
    async with uow_factory(person) as uow:  # the positive admission test
        out = await WidgetService(uow).create(WidgetCreate(code="X", name="x"))
        await uow.commit()
    assert out.code == "X" and await _count(uow_factory, Widget) == 1


async def test_a_denied_call_flushes_nothing(uow_factory, stranger):
    async with uow_factory(stranger) as uow:
        flushes = []
        event.listen(uow.session.sync_session, "before_flush", lambda *a: flushes.append(1))
        with pytest.raises(PermissionDenied):
            await WidgetService(uow).create(WidgetCreate(code="X", name="x"))
        with pytest.raises(PermissionDenied):
            await Finish(uow).execute(FinishCmd(aggregate_id=1))
        assert flushes == [] and not uow.session.new and not uow.session.dirty
    assert await _count(uow_factory, AuditRow) == 0


class SystemWidgets(WidgetService):
    system_allowed = True


async def test_the_system_actor_passes_only_where_declared(uow_factory):
    async with uow_factory(SYSTEM_ACTOR) as uow:
        with pytest.raises(PermissionDenied) as e:
            await WidgetService(uow).create(WidgetCreate(code="S", name="s"))
        assert "system actor" in e.value.message
        await SystemWidgets(uow).create(WidgetCreate(code="S", name="s"))  # declared: allowed
        await uow.commit()
    assert await _count(uow_factory, Widget) == 1


class Jobs:
    def __init__(self, uow):
        self.uow = uow

    @requires("pt.operate")
    async def by_hand(self):
        return "ran"

    @requires("pt.operate", system=True)
    async def scheduled(self):
        return "ran"


async def test_requires_decorator_for_application_services(uow_factory, person, stranger):
    async with uow_factory(SYSTEM_ACTOR) as uow:
        assert await Jobs(uow).scheduled() == "ran"
        with pytest.raises(PermissionDenied):
            await Jobs(uow).by_hand()
    async with uow_factory(stranger) as uow:
        for fn in (Jobs(uow).by_hand, Jobs(uow).scheduled):
            with pytest.raises(PermissionDenied):
                await fn()
    async with uow_factory(person) as uow:
        assert await Jobs(uow).by_hand() == "ran" and await Jobs(uow).scheduled() == "ran"
    assert Jobs.scheduled.__requires__ == ("pt.operate", True)


async def test_a_unit_of_work_without_a_checker_refuses_people_loudly(session_factory, person):
    async with UnitOfWorkFactory(session_factory)(person) as uow:
        with pytest.raises(RuntimeError, match="permission checker"):
            await authorize(uow, "pt.read")


async def test_the_app_checker_follows_the_role_matrix(monkeypatch):
    import app.core.deps as deps
    from app.platform.actors import Actor

    calls = []

    async def fake_has_permission(db, user_id, role, key):
        calls.append((role, key))
        return role == "sales_operator" and key == "sales.read"

    monkeypatch.setattr(deps, "has_permission", fake_has_permission)
    op = Actor(__import__("uuid").uuid4(), "Op", role="sales_operator")
    assert await deps.check_permission(None, op, "sales.read") is True
    assert await deps.check_permission(None, op, "sales.config") is False
    assert await deps.check_permission(None, SYSTEM_ACTOR, "sales.read") is False  # never through the matrix
    assert await deps.check_permission(None, Actor(op.user_id, "No role"), "sales.read") is False
    assert calls == [("sales_operator", "sales.read"), ("sales_operator", "sales.config")]
