"""Each list's capabilities are declared on its service and mounted as declared, both ways (user 2026-11-24): a
capability without its route, or a route nobody declared, fails here — Orders and Transfers declared `suggestable`
fields for weeks with no /suggest."""
import importlib
import inspect
import pathlib
import pkgutil
import re
from types import SimpleNamespace

import pytest

import app.services as services
from app.main import app
from app.platform.authorization import PermissionDenied
from app.platform.services import CrudService, ReadOnlyService
from app.services.shadow import ShadowMatchService

PREFIX = "/api/v1"
NOT_MOUNTED = {ShadowMatchService: "the shadow comparison: read through the saved views' check only"}


def lists():
    out = set()
    for m in pkgutil.iter_modules(services.__path__):
        mod = importlib.import_module(f"app.services.{m.name}")
        out |= {c for _, c in inspect.getmembers(mod, inspect.isclass)
                if c.__module__ == mod.__name__ and issubclass(c, ReadOnlyService) and hasattr(c, "repo_cls")}
    return sorted(out, key=lambda c: c.__name__)


LISTS = [c for c in lists() if c not in NOT_MOUNTED]
ROUTES = {(m, r.path.removeprefix(PREFIX)) for r in app.routes for m in getattr(r, "methods", ())}
CAPABILITIES = {"suggest": "/suggest", "export": "/export.csv", "get": "/{id}", "actions": "/actions/{action_id}", "import": "/import.csv",
                "history": "/{id}/audit"}


def declared(svc) -> set[str]:
    r = svc.resolved()
    return {name for name, on in {"suggest": r.suggest, "export": r.export, "get": r.get, "actions": r.actions is not None, "history": r.history,
                                  "import": issubclass(svc, CrudService) and bool(svc.csv_columns and svc.csv_key)}.items() if on}


def mounted(svc) -> set[str]:
    p = svc.resolved().path
    method = {"actions": "POST", "import": "POST"}
    return {name for name, suffix in CAPABILITIES.items() if (method.get(name, "GET"), p + suffix) in ROUTES}


def test_there_are_the_lists_we_know():
    assert len(LISTS) == 24, [c.__name__ for c in LISTS]
    assert all(c.resolved().path for c in LISTS), [c.__name__ for c in LISTS if not c.resolved().path]
    assert all(c.resolved().path is None for c in NOT_MOUNTED)


@pytest.mark.parametrize("svc", LISTS, ids=lambda c: c.__name__)
def test_what_a_list_declares_is_mounted_and_nothing_else(svc):
    p = svc.resolved().path
    assert {("GET", p), ("GET", p + "/meta")} <= ROUTES
    assert mounted(svc) == declared(svc)


@pytest.mark.parametrize("svc", LISTS, ids=lambda c: c.__name__)
def test_every_suggestable_field_has_its_route(svc):
    if svc.repo_cls.suggestable:
        assert ("GET", svc.resolved().path + "/suggest") in ROUTES


def test_no_list_route_is_mounted_by_hand():
    paths = {c.resolved().path for c in LISTS}
    stray = [p for m, p in ROUTES if m == "GET" and re.search(r"/(meta|suggest|export\.csv)$", p) and p.rsplit("/", 1)[0] not in paths]
    assert stray == []


LABELLED = [c for c in LISTS if c.resolved().export and c.resolved().export_headers == "labels"]
PAGES = {"OrderService": "orders/OrdersPage.tsx", "TransferService": "transfers/TransfersPage.tsx", "InvoiceService": "invoices/InvoicesPage.tsx",
         "StockCountService": "inventory/CountsView.tsx"}
# A page's own cell made from fields the full export has: nothing is lost, only the wording.
OWN_CELLS = {("StockCountService", "state"): "Applied / Report only: from Applied (applied_at)"}


def full_export(svc) -> list[str]:
    r = svc.resolved()
    return list(r.export_fields or svc.repo_cls.list_fields)


def exportable(svc) -> set[str]:
    return {f["name"] for f in svc.repo_cls.field_meta(svc.read_schema) if f["in_row"]} | set(svc.resolved().computed)


@pytest.mark.parametrize("svc", LABELLED, ids=lambda c: c.__name__)
def test_all_fields_never_says_less_than_a_view_shows(svc):
    """User 2026-11-24: every exportable column a system view names is in the full export, and each header once."""
    named = {c for p in svc.repo_cls.presets for c in p.state.get("columns", [])}
    assert (named & exportable(svc)) - set(full_export(svc)) == set()
    labels = svc.export_labels(full_export(svc))
    assert sorted({x for x in labels if labels.count(x) > 1}) == []


def test_the_lists_with_a_history_are_the_configuration_tables():
    """The eight that had one before it was declared; a record's history names values of every field of it."""
    assert {c.__name__ for c in LISTS if c.resolved().history} == {
        "PartnerService", "ItemService", "PartnerCodeService", "DcRoutingService", "WarehouseService", "CarrierService",
        "ConnectionService", "NotificationRuleService"}


DETAILED = [c for c in LISTS if c.resolved().detail is not None]


def test_a_declared_detail_is_the_list_s_own_and_goes_through_the_one_check():
    assert {c.__name__ for c in DETAILED} == {"OrderService", "TransferService", "InvoiceService"}
    for c in DETAILED:
        assert c.detail is ReadOnlyService.detail and c._detail is not ReadOnlyService._detail, c.__name__


@pytest.mark.parametrize("svc", DETAILED, ids=lambda c: c.__name__)
async def test_a_detail_is_refused_before_anything_is_read(svc):
    async def nobody(session, actor, key):  # noqa: ARG001
        return False
    s = svc.__new__(svc)  # no session at all: a read before the check would fail otherwise
    s.uow = SimpleNamespace(actor=SimpleNamespace(is_system=False, user_id=None, role="x"), permissions=nobody, granted={}, session=None)
    with pytest.raises(PermissionDenied):
        await s.detail(1)


def test_no_service_calls_round_its_check():
    root = pathlib.Path("app")
    assert [str(p) for p in root.rglob("*.py") if "__wrapped__(" in p.read_text()] == []


def test_a_record_s_attachments_are_declared_by_its_list_and_by_nothing_else():
    """Both ways: every kind the attachment routes serve is a list's, and every list that declares one is served."""
    from app.services.record_files import RECORDS
    on = {c: c.resolved().attachments for c in LISTS if c.resolved().attachments is not None}
    assert {k.entity_type: k for k in on.values()} == RECORDS and len(on) == len(RECORDS)
    for c, k in on.items():
        assert k.model is c.repo_cls.model and k.read == c.read_permission, c.__name__
        assert all(k.accepts(f"x.{e.upper()}") for e in k.extensions) and not k.accepts("x.exe") and not k.accepts("pdf")
    for m, p in (("GET", ""), ("POST", ""), ("POST", "/{document_id}/remove")):
        assert (m, "/attachments/{entity_type}/{entity_id}" + p) in ROUTES


def test_every_labelled_list_has_its_page_compared():
    assert set(PAGES) == {c.__name__ for c in LABELLED}


@pytest.mark.parametrize("svc", LABELLED, ids=lambda c: c.__name__)
def test_all_fields_never_says_less_than_the_page(svc):
    page = (pathlib.Path("../sales/src/features") / PAGES[svc.__name__]).read_text()
    block = page[page.index("olumns: ColumnDef<"):]
    block = block[:block.index("\n]")]
    ids = set(re.findall(r"accessorKey: '(\w+)'", block)) | set(re.findall(r"\bid: '(\w+)'", block)) | set(
        re.findall(r"\bcol\w*\.\w+\('(\w+)'", block))
    assert len(ids) > 4, ids
    assert {i for i in ids - set(full_export(svc)) if (svc.__name__, i) not in OWN_CELLS} == set()


def test_a_list_kept_from_bulk_export_is_kept():
    off = {c.resolved().path for c in LISTS if not c.resolved().export}
    assert off == {"/documents", "/runs", "/config/notification-rules", "/log", "/audit-logs", "/entity-changes", "/notifications"}
