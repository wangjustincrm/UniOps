"""Each list's capabilities are declared on its service and mounted as declared, both ways (user 2026-11-24): a
capability without its route, or a route nobody declared, fails here — Orders and Transfers declared `suggestable`
fields for weeks with no /suggest."""
import importlib
import inspect
import pathlib
import pkgutil
import re

import pytest

import app.services as services
from app.main import app
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
CAPABILITIES = {"suggest": "/suggest", "export": "/export.csv", "get": "/{id}", "actions": "/actions/{action_id}", "import": "/import.csv"}


def declared(svc) -> set[str]:
    r = svc.resolved()
    return {name for name, on in {"suggest": r.suggest, "export": r.export, "get": r.get, "actions": r.actions is not None,
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
