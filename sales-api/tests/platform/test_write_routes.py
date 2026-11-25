"""A write request commits by itself (platform/routes.py, stage 5): once, after its endpoint returned and before anything is
answered; nothing on an error; a read never. Real units of work on the test database, the app as Starlette runs it."""
import asyncio
from typing import Annotated, Any

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.platform.actors import SYSTEM_ACTOR
from app.platform.audit import AuditTrail
from app.platform.auditing import begin_scope
from app.platform.errors import BusinessError, install_error_handlers
from app.platform.events import DomainEvent, HandlerRegistry
from app.platform.routes import Router, UnitOfWorkRoute, own_commit
from app.platform.uow import SqlAlchemyUnitOfWork, UnitOfWorkFactory
from tests.platform.conftest import AuditRow, Widget, WidgetCreate, WidgetService, actor_with, fake_permissions

ACTOR = actor_with("pt.read", "pt.config", "pt.operate", name="Op")


class Pinged(DomainEvent):
    pass


class Noted(SqlAlchemyUnitOfWork):
    """Writes down, in order, what happened to it."""
    trail: list[str]

    async def commit(self) -> None:
        self.trail.append("commit")
        await super().commit()
        self.trail.append("committed")

    async def __aexit__(self, *exc: Any) -> None:
        await super().__aexit__(*exc)
        self.trail.append("closed")


class World:
    def __init__(self, session_factory) -> None:
        self.trail: list[str] = []
        self.handlers = HandlerRegistry()
        self.session_factory = session_factory
        self.app = FastAPI()
        install_error_handlers(self.app)

    def unit(self) -> Noted:
        u = Noted(self.session_factory, ACTOR, handlers=self.handlers, audit=AuditTrail(AuditRow), permissions=fake_permissions)
        u.trail = self.trail
        return u

    async def uow(self):
        async with self.unit() as u:
            yield u

    @property
    def Uow(self):
        return Annotated[Any, Depends(self.uow)]

    async def call(self, method: str, path: str):
        """As the server sees it: the trail says when the answer starts and ends."""
        async def asgi(scope, receive, send):
            async def noted(message):
                if message["type"] == "http.response.start":
                    self.trail.append(f"answer {message['status']}")
                await send(message)
            await self.app(scope, receive, noted)
        async with AsyncClient(transport=ASGITransport(app=asgi, raise_app_exceptions=False), base_url="http://t") as c:
            return await c.request(method, path)

    async def codes(self) -> list[str]:
        async with UnitOfWorkFactory(self.session_factory)(SYSTEM_ACTOR) as u:
            return sorted((await u.session.execute(select(Widget.code))).scalars())


@pytest.fixture
def world(session_factory, uow_factory):  # noqa: ARG001 — uow_factory: the tables
    return World(session_factory)


async def add(u, code: str) -> None:
    await WidgetService(u).create(WidgetCreate(code=code, name=code))


def mount(world, endpoint, method="post", router=None, path="/w"):
    r = router or Router()
    getattr(r, method)(path)(endpoint)
    world.app.include_router(r)
    return r


# ── once, after the endpoint, before the answer ──────────────────────────────

@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
async def test_a_write_is_committed_once_after_its_endpoint_and_before_anything_is_answered(world, method):
    U = world.Uow

    async def endpoint(u: U):
        await add(u, "A")
        world.trail.append("endpoint done")
        return {"ok": True}
    mount(world, endpoint, method)
    r = await world.call(method.upper(), "/w")
    assert (r.status_code, r.json()) == (200, {"ok": True}) and await world.codes() == ["A"]
    i = world.trail.index
    assert world.trail.count("commit") == 1
    assert i("endpoint done") < i("commit") < i("committed") < i("answer 200")  # kept before a byte is sent


async def test_a_response_made_by_the_endpoint_too(world):
    U = world.Uow

    async def endpoint(u: U):
        await add(u, "A")
        return Response(status_code=204)
    mount(world, endpoint, "delete")
    assert (await world.call("DELETE", "/w")).status_code == 204
    assert await world.codes() == ["A"] and world.trail.index("committed") < world.trail.index("answer 204")


async def test_the_answer_reads_what_is_kept_and_the_unit_is_closed_after(world):
    U = world.Uow

    async def endpoint(u: U):
        await add(u, "A")
        return {"committed_yet": u.committed}
    mount(world, endpoint)
    assert (await world.call("POST", "/w")).json() == {"committed_yet": False}  # the endpoint itself never commits
    assert world.trail.index("committed") < world.trail.index("closed")


# ── nothing on an error ──────────────────────────────────────────────────────

@pytest.mark.parametrize(("error", "status"), [(BusinessError("widget.refused", "no"), 409), (HTTPException(418), 418), (RuntimeError("boom"), 500),
                                               (asyncio.TimeoutError(), 500)], ids=["business", "http", "bug", "timeout"])
async def test_an_endpoint_that_raises_keeps_nothing(world, error, status):
    U = world.Uow

    async def endpoint(u: U):
        await add(u, "A")
        raise error
    mount(world, endpoint)
    assert (await world.call("POST", "/w")).status_code == status
    assert "commit" not in world.trail and await world.codes() == []


async def test_a_refused_body_never_runs_the_endpoint(world):
    U = world.Uow

    async def endpoint(u: U, n: int):
        await add(u, "A")
    mount(world, endpoint)
    assert (await world.call("POST", "/w?n=x")).status_code == 422
    assert "commit" not in world.trail and await world.codes() == []


# ── a commit that is refused is answered, as any refusal ─────────────────────

async def test_a_commit_that_is_refused_is_the_answer_and_nothing_is_kept(world):
    U = world.Uow

    async def refuses(event, u):  # an event's handler, in the transaction
        raise BusinessError("widget.handler", "the handler refuses")
    world.handlers.on(Pinged, refuses)

    async def endpoint(u: U):
        await add(u, "A")
        u.events.raise_(Pinged(actor=u.actor))
        return {"ok": True}
    mount(world, endpoint)
    r = await world.call("POST", "/w")
    assert r.status_code == 409 and r.json()["error"]["code"] == "widget.handler"
    assert await world.codes() == [] and "committed" not in world.trail and "answer 200" not in world.trail


async def test_an_event_s_handler_runs_in_the_transaction_and_its_writes_are_kept_with_it(world):
    U = world.Uow

    async def follows(event, u):
        await add(u, "B")
        world.trail.append("handler")
    world.handlers.on(Pinged, follows)

    async def endpoint(u: U):
        await add(u, "A")
        u.events.raise_(Pinged(actor=u.actor))
        world.trail.append("endpoint done")
    mount(world, endpoint)
    assert (await world.call("POST", "/w")).status_code == 200
    i = world.trail.index
    assert i("endpoint done") < i("commit") < i("handler") < i("committed") < i("answer 200") and await world.codes() == ["A", "B"]


async def test_a_database_that_refuses_at_the_commit_is_a_500_and_never_a_success(world):
    U = world.Uow

    async def endpoint(u: U):
        u.session.add(Widget(code="A", name="A", version=1))
        u.session.add(Widget(code="A", name="twice", version=1))  # unique, seen at the commit's flush
        return {"ok": True}
    mount(world, endpoint)
    assert (await world.call("POST", "/w")).status_code == 500
    assert await world.codes() == [] and "answer 200" not in world.trail


async def test_the_audit_log_s_scope_sees_the_changes_and_the_error_as_the_request_s(world):
    U = world.Uow

    async def endpoint(u: U, bad: bool = False):
        await add(u, "B" if bad else "A")
        if bad:
            raise BusinessError("widget.refused", "no")
    mount(world, endpoint)
    async with begin_scope("request", "t") as scope:
        assert (await world.call("POST", "/w")).status_code == 200
    assert scope.log.entity_changes == 1 and scope.log.error_code is None
    async with begin_scope("request", "t") as scope:
        assert (await world.call("POST", "/w?bad=true")).status_code == 409
    assert scope.log.error_code == "widget.refused" and await world.codes() == ["A"]


# ── a read never commits ─────────────────────────────────────────────────────

@pytest.mark.parametrize("method", ["get", "head", "options"])
async def test_a_read_never_commits_whatever_was_flushed(world, method):
    U = world.Uow

    async def endpoint(u: U):
        await add(u, "A")  # flushed
        return {"ok": True}
    mount(world, endpoint, method)
    assert (await world.call(method.upper(), "/w")).status_code == 200
    assert "commit" not in world.trail and await world.codes() == []


# ── a route that decides for itself ──────────────────────────────────────────

async def test_a_route_that_decides_for_itself_is_left_to_it(world):
    U = world.Uow

    @own_commit("a dry run keeps nothing")
    async def endpoint(u: U, dry: bool):
        await add(u, "A")
        if not dry:
            await u.commit()
    mount(world, endpoint)
    assert (await world.call("POST", "/w?dry=true")).status_code == 200 and await world.codes() == [] and "commit" not in world.trail
    assert (await world.call("POST", "/w?dry=false")).status_code == 200 and await world.codes() == ["A"] and world.trail.count("commit") == 1


def test_it_says_why():
    for reason in ("", "  "):
        with pytest.raises(ValueError):
            own_commit(reason)


async def test_an_endpoint_that_commits_without_saying_so_is_not_committed_again(world, caplog):
    U = world.Uow

    async def endpoint(u: U):
        await add(u, "A")
        await u.commit()
    mount(world, endpoint)
    assert (await world.call("POST", "/w")).status_code == 200
    assert world.trail.count("commit") == 1 and any("without saying so" in r.getMessage() for r in caplog.records)


# ── how it is mounted ────────────────────────────────────────────────────────

async def test_a_route_included_again_and_again_commits_once(world):
    U = world.Uow

    async def endpoint(u: U):
        await add(u, "A")
    inner, middle, outer = Router(), APIRouter(prefix="/m"), APIRouter(prefix="/o")
    inner.post("/w")(endpoint)
    middle.include_router(inner)
    outer.include_router(middle)
    world.app.include_router(outer)
    assert (await world.call("POST", "/o/m/w")).status_code == 200
    assert world.trail.count("commit") == 1 and await world.codes() == ["A"]
    [route] = [r for r in world.app.routes if getattr(r, "path", "") == "/o/m/w"]
    assert isinstance(route, UnitOfWorkRoute) and route.endpoint is endpoint


async def test_a_plain_router_s_write_is_not_committed(world):
    U = world.Uow

    async def endpoint(u: U):
        await add(u, "A")
    mount(world, endpoint, router=APIRouter())
    assert (await world.call("POST", "/w")).status_code == 200
    assert await world.codes() == []  # why the guard refuses one (tests/test_write_routes.py)


@pytest.mark.parametrize("units", [0, 2])
async def test_a_write_without_one_unit_of_work_never_runs(world, units):
    ran, U = [], world.Uow

    async def none():
        ran.append(1)

    async def both(a: U, b: Annotated[Any, Depends(world.uow, use_cache=False)]):
        ran.append(1)
    mount(world, none if units == 0 else both)
    assert (await world.call("POST", "/w")).status_code == 500
    assert ran == [] and "commit" not in world.trail


def test_a_write_endpoint_is_async(world):
    U = world.Uow

    def endpoint(u: U):
        return {}
    with pytest.raises(TypeError, match="async"):
        mount(world, endpoint)


def test_the_route_says_nothing_else_to_the_world(world):
    """The OpenAPI of a Router is that of an APIRouter: the endpoint's signature, name, words and dependencies are its own."""
    U = world.Uow

    def build(router):
        async def made(u: U, id: int, body: WidgetCreate, n: int | None = None) -> dict:
            """Make one."""
            return {}

        async def seen(u: U, id: int) -> dict:
            return {}
        router.post("/w/{id}", status_code=201, tags=["w"])(made)
        router.get("/w/{id}")(seen)
        app = FastAPI()
        app.include_router(router, prefix="/api")
        return app
    ours, plain = build(Router(prefix="/p")), build(APIRouter(prefix="/p"))
    assert ours.openapi() == plain.openapi()
    pairs = [[r for r in a.routes if getattr(r, "path", "") == "/api/p/w/{id}"] for a in (ours, plain)]
    for a, b in zip(*pairs):
        assert [d.call for d in a.dependant.dependencies] == [d.call for d in b.dependant.dependencies]
        assert (a.name, a.unique_id, a.description) == (b.name, b.unique_id, b.description)
