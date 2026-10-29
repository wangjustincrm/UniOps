"""Configuration → Partners end to end through HTTP (P1): the app's own
router, services, audit trail and error handlers against the migrated
sales_test schema. Only two things are replaced: the unit of work's session
factory (the test database) and the role matrix (a fixed grant table, since
the identity tables are not in sales_test — the app's checker itself is
covered in tests/platform/test_transactions_and_authorization.py)."""
import csv
import io

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.deps import get_uow_factory
from app.main import app
from app.models.records import AuditRecord
from app.platform.audit import AuditTrail
from app.platform.uow import UnitOfWorkFactory
from tests.conftest import make_token

ALL = {"sales.read", "sales.order.operate", "sales.file.retry", "sales.transfer.manage",
       "sales.inventory.adjust", "sales.invoice.manage", "sales.config"}
GRANTS = {"sales_operator": ALL, "sales_reader": {"sales.read"}, "requester": set()}


async def fake_matrix(session, actor, key):  # noqa: ARG001
    return key in GRANTS.get(actor.role or "", set())


@pytest_asyncio.fixture
async def http(db_engine):
    async with db_engine.begin() as conn:
        await conn.execute(sa.text("TRUNCATE sales_partners, sales_audit RESTART IDENTITY CASCADE"))
    factory = UnitOfWorkFactory(
        async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False),
        permissions=fake_matrix, audit=AuditTrail(AuditRecord),
    )
    app.dependency_overrides[get_uow_factory] = lambda: factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    app.dependency_overrides.clear()


def as_(role):
    return {"Authorization": f"Bearer {make_token(role)}"}


OP, READER, NOBODY = as_("sales_operator"), as_("sales_reader"), as_("requester")
BASE = "/api/v1/config/partners"


def partner(code="WMT", **over):
    body = {
        "code": code, "name": "Walmart Canada", "channel": "TRUECOMMERCE",
        "settlement_mode": "INVOICE", "sending_mode": "AUTOMATIC", "delay_minutes": 0,
        "invoice_method": "810_JSON", "dc_routing_required": True,
    }
    return {**body, **over}


async def make(http, **kw):
    r = await http.post(BASE, json=partner(**kw), headers=OP)
    assert r.status_code == 201, r.text
    return r.json()


async def audit_rows(db_engine):
    async with db_engine.connect() as c:
        return (await c.execute(sa.text(
            "SELECT action, entity, entity_id, user_name, reason, before, after FROM sales_audit ORDER BY id"
        ))).mappings().all()


# ── permission ───────────────────────────────────────────────────────────────

async def test_writes_need_sales_config_and_reads_sales_read(http):
    r = await http.post(BASE, json=partner(), headers=READER)
    assert r.status_code == 403 and r.json()["error"]["code"] == "auth.forbidden"
    assert r.json()["error"]["details"]["permission"] == "sales.config"
    assert (await http.get(BASE, headers=NOBODY)).status_code == 403
    # the admitted halves
    assert (await http.get(BASE, headers=READER)).json() == {"items": [], "total": 0}  # nothing was written
    p = await make(http)
    assert (await http.get(f"{BASE}/{p['id']}", headers=READER)).status_code == 200
    for path in (f"{BASE}/{p['id']}", f"{BASE}/export.csv", f"{BASE}/{p['id']}/audit"):
        assert (await http.get(path, headers=NOBODY)).status_code == 403, path
    r = await http.post(f"{BASE}/{p['id']}/deactivate", headers=READER)
    assert r.status_code == 403
    assert (await http.get(f"{BASE}/{p['id']}", headers=READER)).json()["active"] is True


async def test_no_token_is_401(http):
    assert (await http.get(BASE)).status_code in (401, 403)


# ── CRUD, list query, optimistic lock ───────────────────────────────────────

async def test_create_read_update_deactivate(http):
    p = await make(http, code="  WMT  ", carrier_defaults={"carrier": "CHR", "mode": "LTL"})
    assert p["code"] == "WMT" and p["version"] == 1 and p["active"] is True
    assert p["carrier_defaults"] == {"carrier": "CHR", "mode": "LTL"}
    assert p["created_by_name"] and p["updated_at"] is None

    r = await http.put(f"{BASE}/{p['id']}?version=1", json={"name": "Walmart", "payment_terms": "Net 30"}, headers=OP)
    assert r.status_code == 200, r.text
    u = r.json()
    assert (u["name"], u["payment_terms"], u["version"]) == ("Walmart", "Net 30", 2)
    assert u["channel"] == "TRUECOMMERCE"  # untouched
    r = await http.put(f"{BASE}/{p['id']}?version=2", json={"payment_terms": None}, headers=OP)
    assert r.json()["payment_terms"] is None  # an optional field sent as null is cleared

    r = await http.post(f"{BASE}/{p['id']}/deactivate", headers=OP)
    assert r.status_code == 200 and r.json()["active"] is False
    r = await http.put(f"{BASE}/{p['id']}?version={r.json()['version']}", json={"active": True}, headers=OP)
    assert r.json()["active"] is True


async def test_stale_version_is_409_and_changes_nothing(http):
    p = await make(http)
    assert (await http.put(f"{BASE}/{p['id']}?version=1", json={"name": "A"}, headers=OP)).status_code == 200
    r = await http.put(f"{BASE}/{p['id']}?version=1", json={"name": "B"}, headers=OP)
    assert r.status_code == 409 and r.json()["error"]["code"] == "entity.stale"
    assert r.json()["error"]["details"] == {"current_version": 2, "sent_version": 1}
    assert (await http.get(f"{BASE}/{p['id']}", headers=OP)).json()["name"] == "A"
    r = await http.put(f"{BASE}/{p['id']}", json={"name": "C"}, headers=OP)
    assert r.status_code == 422 and r.json()["error"]["code"] == "entity.version_required"


async def test_list_search_sort_filter(http):
    await make(http, code="WMT", name="Walmart Canada")
    await make(http, code="SHOP", name="Shopify Store", channel="SHOPIFY", settlement_mode="PREPAID",
               dc_routing_required=False)
    await make(http, code="JC", name="Jean Coutu", invoice_method="EMAIL", invoice_email="ap@jeancoutu.example")
    codes = lambda r: [i["code"] for i in r.json()["items"]]  # noqa: E731
    assert codes(await http.get(BASE, headers=READER)) == ["JC", "SHOP", "WMT"]  # default sort: code
    assert codes(await http.get(f"{BASE}?sort=-name", headers=READER)) == ["WMT", "SHOP", "JC"]
    assert codes(await http.get(f"{BASE}?search=coutu", headers=READER)) == ["JC"]
    assert codes(await http.get(f"{BASE}?filter=channel:in:SHOPIFY|MANUAL", headers=READER)) == ["SHOP"]
    assert codes(await http.get(f"{BASE}?filter=settlement_mode:eq:INVOICE&page_size=1", headers=READER)) == ["JC"]
    r = await http.get(f"{BASE}?filter=settlement_mode:eq:INVOICE&page_size=1", headers=READER)
    assert r.json()["total"] == 2
    r = await http.get(f"{BASE}?sort=invoice_email", headers=READER)
    assert r.status_code == 422 and r.json()["error"]["code"] == "query.unknown_field"


# ── validation and rules ─────────────────────────────────────────────────────

async def test_shape_errors_name_their_field(http):
    r = await http.post(BASE, json=partner(channel="FAX"), headers=OP)
    assert r.status_code == 422
    e = r.json()["error"]
    assert e["code"] == "request.invalid" and e["details"]["field"] == "channel"
    r = await http.post(BASE, json=partner(delay_minutes=-1, invoice_email="not-an-email"), headers=OP)
    fields = {x["field"] for x in r.json()["error"]["details"]["errors"]}
    assert fields == {"delay_minutes", "invoice_email"}
    p = await make(http)
    r = await http.put(f"{BASE}/{p['id']}?version=1", json={"name": None}, headers=OP)
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "name"  # required: not clearable
    r = await http.put(f"{BASE}/{p['id']}?version=1", json={"code": "OTHER"}, headers=OP)
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "code"  # not changed after creation
    assert (await http.get(f"{BASE}/{p['id']}", headers=OP)).json()["version"] == 1


async def test_email_invoicing_needs_an_address_fr_ob_33(http):
    r = await http.post(BASE, json=partner(invoice_method="EMAIL"), headers=OP)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "partner.invoice_email_required"
    assert r.json()["error"]["details"]["field"] == "invoice_email"
    p = await make(http)  # 810_JSON without an address is fine
    r = await http.put(f"{BASE}/{p['id']}?version=1", json={"invoice_method": "EMAIL"}, headers=OP)
    assert r.status_code == 422 and r.json()["error"]["code"] == "partner.invoice_email_required"
    r = await http.put(f"{BASE}/{p['id']}?version=1",
                       json={"invoice_method": "EMAIL", "invoice_email": "ap@walmart.example"}, headers=OP)
    assert r.status_code == 200
    r = await http.put(f"{BASE}/{p['id']}?version=2", json={"invoice_email": None}, headers=OP)
    assert r.status_code == 422  # cannot clear it while the method is EMAIL


async def test_codes_are_unique_ignoring_case_and_spaces(http):
    await make(http, code="WMT")
    r = await http.post(BASE, json=partner(code=" wmt "), headers=OP)
    assert r.status_code == 409 and r.json()["error"]["code"] == "partner.code_taken"
    assert r.json()["error"]["details"]["field"] == "code"
    assert (await http.post(BASE, json=partner(code="WMT2"), headers=OP)).status_code == 201


# ── audit ────────────────────────────────────────────────────────────────────

async def test_every_write_is_audited(http, db_engine):
    p = await make(http)
    await http.put(f"{BASE}/{p['id']}?version=1", json={"name": "Walmart", "delay_minutes": 30}, headers=OP)
    await http.post(f"{BASE}/{p['id']}/deactivate", headers=OP)
    await http.post(BASE, json=partner(channel="FAX"), headers=OP)  # refused: no row
    await http.post(BASE, json=partner(code="X"), headers=READER)   # denied: no row

    rows = await audit_rows(db_engine)
    assert [(r["action"], r["reason"]) for r in rows] == [("create", "create"), ("update", "update"), ("update", "deactivate")]
    assert all(r["entity"] == "sales_partners" and r["entity_id"] == str(p["id"]) for r in rows)
    assert all(r["user_name"] for r in rows)
    assert rows[0]["before"] is None and rows[0]["after"]["code"] == "WMT"
    assert rows[1]["before"] == {"name": "Walmart Canada", "delay_minutes": 0}
    assert rows[1]["after"] == {"name": "Walmart", "delay_minutes": 30}
    assert (rows[2]["before"], rows[2]["after"]) == ({"active": True}, {"active": False})

    r = await http.get(f"{BASE}/{p['id']}/audit", headers=READER)
    assert r.status_code == 200
    assert [e["reason"] for e in r.json()] == ["deactivate", "update", "create"]  # newest first


# ── CSV ──────────────────────────────────────────────────────────────────────

def _rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def _csv(rows):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue().encode()


async def _import(http, data, dry_run):
    r = await http.post(f"{BASE}/import.csv?dry_run={str(dry_run).lower()}",
                        files={"file": ("p.csv", data, "text/csv")}, headers=OP)
    assert r.status_code == 200, r.text
    return r.json()


async def test_csv_round_trip(http, db_engine):
    await make(http, code="WMT", carrier_defaults={"carrier": "CHR"})
    await make(http, code="JC", name="=Jean Coutu", invoice_method="EMAIL", invoice_email="ap@jc.example")
    r = await http.get(f"{BASE}/export.csv", headers=READER)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = _rows(r.text)
    assert [x["code"] for x in rows] == ["JC", "WMT"] and rows[0]["version"] == "1"
    assert rows[0]["name"] == "'=Jean Coutu"  # a formula-looking cell is defused on export
    assert rows[1]["carrier_defaults"] == '{"carrier": "CHR"}'  # JSON, so it reads back

    rep = await _import(http, r.content, dry_run=True)  # the export, unchanged, changes nothing
    assert rep["ok"] and rep["counts"] == {"create": 0, "update": 0, "unchanged": 2}

    rows[1]["name"] = "Walmart"
    rows.append({**rows[1], "code": "COSTCO", "name": "Costco", "version": "", "active": "false"})
    data = _csv(rows)
    rep = await _import(http, data, dry_run=True)
    assert rep["ok"] and not rep["written"] and rep["counts"] == {"create": 1, "update": 1, "unchanged": 1}
    assert (await http.get(BASE, headers=OP)).json()["total"] == 2  # a dry run writes nothing
    rep = await _import(http, data, dry_run=False)
    assert rep["ok"] and rep["written"]
    items = {p["code"]: p for p in (await http.get(BASE, headers=OP)).json()["items"]}
    assert items["WMT"]["name"] == "Walmart" and items["WMT"]["version"] == 2
    assert items["COSTCO"]["active"] is False and items["COSTCO"]["carrier_defaults"] == {"carrier": "CHR"}
    assert items["JC"]["name"] == "=Jean Coutu" and items["JC"]["version"] == 1
    reasons = [(r["action"], r["reason"]) for r in await audit_rows(db_engine)][2:]
    assert sorted(reasons) == [("create", "import.csv"), ("update", "import.csv")]


async def test_csv_import_of_a_stale_export_is_refused(http):
    p = await make(http, code="WMT")
    export = (await http.get(f"{BASE}/export.csv", headers=OP)).text
    await http.put(f"{BASE}/{p['id']}?version=1", json={"name": "Changed meanwhile"}, headers=OP)
    rows = _rows(export)
    rows[0]["name"] = "From the old file"
    rep = await _import(http, _csv(rows), dry_run=False)
    assert not rep["ok"] and not rep["written"]
    assert [(e["column"], e["code"]) for e in rep["errors"]] == [("version", "entity.stale")]
    assert (await http.get(f"{BASE}/{p['id']}", headers=OP)).json()["name"] == "Changed meanwhile"


async def test_csv_rows_are_validated_like_the_form(http):
    rows = [
        {**partner(code="A"), "invoice_method": "EMAIL"},  # FR-OB-33
        {**partner(code="B"), "channel": "FAX"},           # closed set
        {**partner(code="C")},                              # fine, but nothing is written
    ]
    rep = await _import(http, _csv(rows), dry_run=False)
    assert not rep["ok"] and not rep["written"]
    assert {(e["row"], e["code"]) for e in rep["errors"]} == {(1, "partner.invoice_email_required"), (2, "csv.invalid")}
    assert (await http.get(BASE, headers=OP)).json()["total"] == 0
    rep = await _import(http, _csv([{**partner(code="A")}]), dry_run=True)
    assert rep["ok"]


async def test_csv_import_needs_sales_config(http):
    r = await http.post(f"{BASE}/import.csv?dry_run=true",
                        files={"file": ("p.csv", _csv([partner()]), "text/csv")}, headers=READER)
    assert r.status_code == 403


@pytest.mark.parametrize("name", ["CHANNELS", "SETTLEMENT_MODES", "SENDING_MODES", "INVOICE_METHODS"])
def test_dto_closed_sets_match_the_domain(name):
    """The Literal types of the DTO and the CHECK constraints share one source."""
    from typing import get_args

    import app.domain.config as domain
    import app.schemas.partners as dto
    literal = {"CHANNELS": dto.Channel, "SETTLEMENT_MODES": dto.SettlementMode,
               "SENDING_MODES": dto.SendingMode, "INVOICE_METHODS": dto.InvoiceMethod}[name]
    assert get_args(literal) == getattr(domain, name)


def test_the_apps_own_factory_audits_and_checks_the_role_matrix():
    """The fixture above replaces the factory; this pins what it replaces."""
    import app.core.deps as deps
    assert deps.get_uow_factory() is deps.uow_factory
    assert isinstance(deps.uow_factory.audit, AuditTrail) and deps.uow_factory.audit.model is AuditRecord
    assert deps.uow_factory.permissions is deps.check_permission


async def test_the_database_refuses_a_code_differing_only_in_case(http, db_engine, monkeypatch):
    """Two creates racing past the service check: the index decides."""
    from sqlalchemy.exc import IntegrityError

    from app.domain.partners import check_partner
    from app.services.partners import PartnerService

    async def no_lookup(self, data):  # the service's own uniqueness check, skipped
        check_partner(data.model_dump())

    monkeypatch.setattr(PartnerService, "validate_create", no_lookup)
    await make(http, code="abc")
    r = await http.post(BASE, json=partner(code="ABC"), headers=OP)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "partner.code_taken"
    assert r.json()["error"]["details"]["field"] == "code"
    assert (await http.post(BASE, json=partner(code="ABD"), headers=OP)).status_code == 201  # the admitted half
    assert {p["code"] for p in (await http.get(BASE, headers=OP)).json()["items"]} == {"abc", "ABD"}
    # And below the API altogether:
    with pytest.raises(IntegrityError, match="uq_sales_partners_code_ci"):
        async with db_engine.begin() as c:
            await c.execute(sa.text(
                "INSERT INTO sales_partners (code, name, channel, settlement_mode, sending_mode, delay_minutes, "
                "invoice_method, dc_routing_required, active, version, created_by_name) "
                "VALUES ('Abc', 'x', 'MANUAL', 'INVOICE', 'MANUAL', 0, 'NONE', false, true, 1, 't')"))
