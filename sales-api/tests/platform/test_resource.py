"""What a list resource offers (platform/resource.py): declared at three levels, mounted as declared, said by /meta;
`may` asks without refusing."""
import csv
import io
import uuid
from types import SimpleNamespace

import pytest
from fastapi import APIRouter

from app.platform import authorization
from app.platform.authorization import PermissionDenied, authorize, may, on_denied
from app.platform.crud_router import resource_router
from app.platform.options import ResourceOptions, configure, reset
from app.platform.query import QuerySpec
from app.platform.resource import Computed, Resource, resolve
from tests.platform.conftest import WidgetCreate, WidgetService, actor_with

RW = actor_with("pt.read", "pt.config")


@pytest.fixture(autouse=True)
def defaults():
    yield
    reset()


def test_a_field_comes_from_the_service_else_its_base_else_the_platform():
    class Base:
        resource = Resource(tags=("t",), export=False, suggest=False)

    class Own(Base):
        resource = Resource(path="/own", suggest=True)

    r = resolve(Own)
    assert (r.path, r.tags, r.export, r.suggest, r.get) == ("/own", ("t",), False, True, True)
    configure(ResourceOptions(get=False, export=True))
    assert resolve(Own).get is False and resolve(Own).export is False  # the base's word is nearer than the platform's
    assert resolve(Own).file_name == "own.csv" and resolve(WidgetService).file_name == "widgets.csv"
    assert resolve(type("Nested", (), {"resource": Resource(path="/a/b")})).file_name == "a-b.csv"


def paths(service, **kw):
    r = resource_router(service, uow=lambda: None, **kw)
    return [(sorted(x.methods)[0], x.path) for x in r.routes]


def test_only_what_is_declared_is_mounted_and_the_id_route_is_last_of_the_gets():
    assert ("GET", "/widgets/export.csv") in paths(WidgetService) and ("GET", "/widgets/{id}") in paths(WidgetService)
    off = type("Off", (WidgetService,), {"resource": Resource(export=False, suggest=False, get=False)})
    assert not {("GET", "/widgets/export.csv"), ("GET", "/widgets/suggest"), ("GET", "/widgets/{id}")} & set(paths(off))
    custom = APIRouter(prefix="/widgets")

    @custom.get("/special")
    async def special(): ...

    gets = [p for m, p in paths(WidgetService, custom=custom) if m == "GET"]
    assert gets.index("/widgets/special") < gets.index("/widgets/{id}") and gets[-1] == "/widgets/{id}"
    with pytest.raises(ValueError):
        resource_router(WidgetService, uow=lambda: None, custom=APIRouter(prefix="/other"))
    with pytest.raises(TypeError):
        resource_router(type("NoPath", (WidgetService,), {"resource": Resource(path=None)}), uow=lambda: None)


async def test_meta_says_what_the_list_offers_and_whether_this_person_may(uow_factory):
    svc = type("Kept", (WidgetService,), {"resource": Resource(export_permission="pt.config", computed={"shout": Computed("Shout", lambda r: r.name.upper())})})
    async with uow_factory(RW) as u:
        m = await svc(u).meta()
        assert m["capabilities"] == {"export": {"allowed": True, "computed": [{"id": "shout", "label": "Shout"}]}, "suggest": False, "actions": []}
    async with uow_factory(actor_with("pt.read")) as u:
        assert (await svc(u).meta())["capabilities"]["export"]["allowed"] is False
        with pytest.raises(PermissionDenied):
            await svc(u).export_csv(QuerySpec())  # the operation checks, whatever a page shows
    off = type("Off", (WidgetService,), {"resource": Resource(export=False)})
    async with uow_factory(RW) as u:
        assert (await off(u).meta())["capabilities"]["export"] is None


async def test_the_export_writes_computed_columns_and_labels_where_declared(uow_factory):
    computed = {"shout": Computed("Shout", lambda r: r.name.upper())}
    labelled = type("Labelled", (WidgetService,), {"resource": Resource(computed=computed, export_headers="labels", export_fields=("code", "shout"))})
    async with uow_factory(RW) as u:
        await labelled(u).create(WidgetCreate(code="A", name="Ann"))
        rows = lambda b: list(csv.reader(io.StringIO(b.decode("utf-8-sig"))))  # noqa: E731
        assert rows(await labelled(u).export_csv(QuerySpec())) == [["Code", "Shout"], ["A", "ANN"]]
        assert rows(await labelled(u).export_csv(QuerySpec(), ["shout", "name"])) == [["Shout", "Name"], ["ANN", "Ann"]]
        assert rows(await WidgetService(u).export_csv(QuerySpec()))[0][:2] == ["code", "name"]  # what an import reads back


async def test_may_asks_without_a_security_event_and_a_later_refusal_is_still_one():
    asked, heard = [], []

    async def checker(session, actor, key):
        asked.append(key)
        return key == "x.read"

    async def listener(uow, key):
        heard.append(key)
    uow = SimpleNamespace(actor=SimpleNamespace(is_system=False, user_id=uuid.uuid4(), role="x"), session=None, permissions=checker, granted={})
    on_denied(listener)
    try:
        assert await may(uow, "x.read") is True and await may(uow, "x.config") is False
        assert heard == []  # a question is not a refusal
        with pytest.raises(PermissionDenied):
            await authorize(uow, "x.config")
        assert heard == ["x.config"] and asked == ["x.read", "x.config"]  # the refusal is heard, the answer asked once
        system = SimpleNamespace(**{**vars(uow), "actor": SimpleNamespace(is_system=True)})
        assert await may(system, "x.read") is False
    finally:
        authorization._denied_listeners.remove(listener)
