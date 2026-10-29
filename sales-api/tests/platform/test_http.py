"""crud_router and the error shape, through HTTP. The router has no
permission check of its own: the service's declared keys decide (the
X-Perms header stands in for the role matrix)."""
import pytest_asyncio
from fastapi import FastAPI, Header
from httpx import ASGITransport, AsyncClient

from app.platform.crud_router import crud_router
from app.platform.errors import BusinessError, install_error_handlers
from tests.platform.conftest import WidgetService, actor_with


@pytest_asyncio.fixture
async def http(uow_factory):
    async def uow(x_perms: str = Header(default="")):
        async with uow_factory(actor_with(*[k for k in x_perms.split(",") if k])) as u:
            yield u

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(crud_router("/widgets", WidgetService, uow=uow))

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret detail")

    @app.get("/refuse")
    async def refuse():
        raise BusinessError("demo.refused", "no", {"why": "rule"})

    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as c:
        yield c


RW = {"X-Perms": "pt.read,pt.config"}
RO = {"X-Perms": "pt.read"}


async def test_crud_round_trip(http):
    r = await http.post("/widgets", json={"code": "A", "name": "Alpha", "email": "a@x.com"}, headers=RW)
    assert r.status_code == 201, r.text
    w = r.json()
    r = await http.put(f"/widgets/{w['id']}?version={w['version']}", json={"email": None}, headers=RW)
    assert r.status_code == 200 and r.json()["email"] is None and r.json()["name"] == "Alpha"
    r = await http.get("/widgets?sort=-code&filter=code:eq:A&page_size=10", headers=RO)
    assert r.json()["total"] == 1 and r.json()["items"][0]["code"] == "A"
    r = await http.post(f"/widgets/{w['id']}/deactivate", headers=RW)
    assert r.json()["active"] is False
    r = await http.get("/widgets/export.csv", headers=RO)
    assert r.text.startswith("code,name,email,price,active,version\nA,Alpha,,0.00,false,3")


async def test_write_needs_the_write_permission_and_read_the_read_one(http):
    r = await http.post("/widgets", json={"code": "A", "name": "a"}, headers=RO)
    assert r.status_code == 403 and r.json()["error"] == {
        "code": "auth.forbidden", "message": "you may not do this (needs pt.config)",
        "details": {"permission": "pt.config"}}
    assert (await http.get("/widgets", headers={"X-Perms": "pt.config"})).status_code == 403
    for path in ("/widgets/1", "/widgets/export.csv"):
        assert (await http.get(path, headers={})).status_code == 403
    assert (await http.get("/widgets", headers=RO)).status_code == 200  # the positive half
    assert (await http.get("/widgets", headers=RO)).json()["total"] == 0  # the denied POST wrote nothing


async def test_import_dry_run_then_write(http):
    files = {"file": ("w.csv", b"code,name\nA,Alpha\n", "text/csv")}
    r = await http.post("/widgets/import.csv?dry_run=true", files=files, headers=RW)
    assert r.json()["ok"] and not r.json()["written"] and r.json()["counts"]["create"] == 1
    assert (await http.get("/widgets", headers=RO)).json()["total"] == 0
    r = await http.post("/widgets/import.csv?dry_run=false", files=files, headers=RW)
    assert r.json()["written"]
    assert (await http.get("/widgets", headers=RO)).json()["total"] == 1


async def test_error_shapes(http):
    r = await http.get("/widgets?sort=nope", headers=RO)
    assert r.status_code == 422 and r.json()["error"]["code"] == "query.unknown_field"
    r = await http.get("/widgets/999", headers=RO)
    assert r.status_code == 404 and r.json()["error"]["code"] == "entity.not_found"
    r = await http.get("/refuse")
    assert r.status_code == 409 and r.json() == {"error": {"code": "demo.refused", "message": "no", "details": {"why": "rule"}}}
    r = await http.get("/boom")
    assert r.status_code == 500
    body = r.json()["error"]
    assert body["code"] == "internal" and len(body["details"]["correlation_id"]) == 32
    assert "secret" not in r.text


async def test_stale_update_is_409(http):
    w = (await http.post("/widgets", json={"code": "A", "name": "Alpha"}, headers=RW)).json()
    await http.put(f"/widgets/{w['id']}?version=1", json={"name": "B"}, headers=RW)
    r = await http.put(f"/widgets/{w['id']}?version=1", json={"name": "C"}, headers=RW)
    assert r.status_code == 409 and r.json()["error"]["code"] == "entity.stale"
