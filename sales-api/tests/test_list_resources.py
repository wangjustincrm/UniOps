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
APP = pathlib.Path(__file__).resolve().parents[1] / "app"
FEATURES = pathlib.Path(__file__).resolve().parents[2] / "sales" / "src" / "features"
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
    files = list(APP.rglob("*.py"))
    assert len(files) > 100, APP
    assert [str(p) for p in files if "__wrapped__(" in p.read_text()] == []


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


def test_a_record_s_activity_is_declared_by_its_list_and_by_nothing_else():
    """Both ways, and apart from attachments: an invoice has an Activity and no attachments (user 2026-11-24)."""
    from app.services.record_files import RECORDS, TIMELINES
    on = {c: c.resolved().timeline for c in LISTS if c.resolved().timeline is not None}
    assert sorted(map(id, on.values())) == sorted(map(id, TIMELINES.values()))
    for c, k in on.items():
        assert k.model is c.repo_cls.model and "timeline" in c.resolved().detail.model_fields, c.__name__
    for c in LISTS:  # a detail that carries one declares it
        if c.resolved().detail is not None and "timeline" in c.resolved().detail.model_fields:
            assert c in on, c.__name__
    for name, r in RECORDS.items():  # what can be attached to has an Activity, of the same record
        assert (TIMELINES[name].model, TIMELINES[name].line_model, TIMELINES[name].line_fk) == (r.model, r.line_model, r.line_fk)
    assert {c.__name__ for c in LISTS if c.resolved().attachments is not None} == {"OrderService", "TransferService"}


def test_every_labelled_list_has_its_page_compared():
    assert set(PAGES) == {c.__name__ for c in LABELLED}


@pytest.mark.parametrize("svc", LABELLED, ids=lambda c: c.__name__)
def test_all_fields_never_says_less_than_the_page(svc):
    page = (FEATURES / PAGES[svc.__name__]).read_text()
    block = page[page.index("olumns: ColumnDef<"):]
    block = block[:block.index("\n]")]
    ids = set(re.findall(r"accessorKey: '(\w+)'", block)) | set(re.findall(r"\bid: '(\w+)'", block)) | set(
        re.findall(r"\bcol\w*\.\w+\('(\w+)'", block))
    assert len(ids) > 4, ids
    assert {i for i in ids - set(full_export(svc)) if (svc.__name__, i) not in OWN_CELLS} == set()


def test_a_list_kept_from_bulk_export_is_kept():
    off = {c.resolved().path for c in LISTS if not c.resolved().export}
    assert off == {"/documents", "/runs", "/config/notification-rules", "/log", "/audit-logs", "/entity-changes", "/notifications"}


# The single-record routes that went with stage 3 (l): one record and many are the record action, nothing beside it.
GONE = ("/orders/{id}/send-940", "/orders/{order_id}/shopify-fulfil", "/orders/manual/{id}/submit", "/orders/manual/{id}/cancel", "/orders/{id}/settle",
        "/orders/{id}/unsettle", "/orders/{id}/resolve-review", "/orders/{id}/cancel", "/orders/{id}/release-hold", "/invoices/confirm-bulk",
        "/invoices/{id}/send", "/transfers/{id}/ready", "/transfers/{id}/cancel", "/transfers/{id}/resend", "/transfers/{id}/release",
        "/documents/{id}/read-again", "/documents/{id}/ignore", "/documents/{id}/send-again", "/inventory/counts/{count_id}/apply",
        "/inventory/counts/{count_id}/undo")


def test_no_single_route_comes_back_beside_its_record_action():
    from app.main import app
    paths = set(app.openapi()["paths"])
    assert not paths & {f"/api/v1{p}" for p in GONE}
    for lst in ("orders", "invoices", "transfers", "documents", "inventory/counts"):
        assert f"/api/v1/{lst}/actions/{{action_id}}" in paths  # and the one that stands in their place is there


# ── files a record (or the list) gives (stage 4, Resource.files) ─────────────
BY_HAND = {"/orders/{id}/shipments/{shipment_id}/packing-list.pdf"}  # two ids: a shipment of its order


def file_path(svc, f) -> str:
    return svc.resolved().path + ("" if f.over == "list" else "/{id}") + "/" + f.path


def test_the_files_we_know_are_declared():
    assert {file_path(c, f): (f.media_type, f.inline) for c in LISTS for f in c.resolved().files} == {
        "/orders/{id}/po.pdf": ("application/pdf", True), "/orders/{id}/packet.zip": ("application/zip", False),
        "/invoices/{id}/invoice.pdf": ("application/pdf", True), "/transfers/{id}/print.pdf": ("application/pdf", True),
        "/settlements/report/settlements.pdf": ("application/pdf", True), "/settlements/{id}/statement.pdf": ("application/pdf", True),
        "/documents/{id}/raw": ("application/octet-stream", False)}


def test_a_declared_file_is_mounted_by_the_router_and_no_file_is_by_hand():
    from app.platform.files import FileResult  # noqa: F401 — what they answer with
    by_router = {r.path.removeprefix(PREFIX) for r in app.routes if getattr(r, "endpoint", None) and r.endpoint.__module__ == "app.platform.crud_router"
                 and r.endpoint.__name__ in ("record_file", "list_file")}
    assert by_router == {file_path(c, f) for c in LISTS for f in c.resolved().files}
    files = {p for m, p in ROUTES if m == "GET" and re.search(r"\.(pdf|zip)$|/raw$", p)}
    assert files - by_router == BY_HAND


@pytest.mark.parametrize(("svc", "f"), [(c, f) for c in LISTS for f in c.resolved().files], ids=lambda x: getattr(x, "path", getattr(x, "__name__", "")))
def test_a_declared_file_s_method_is_there_and_checks_the_permission_first(svc, f):
    method = getattr(svc, f.method)
    assert getattr(method, "__requires__", None) == (svc.read_permission, False), "its own @requires: the router checks nothing"


def test_a_list_s_file_is_never_taken_for_an_id():
    assert all("/" in f.path for c in LISTS for f in c.resolved().files if f.over == "list")


def test_what_a_person_sends_is_read_bounded_in_one_place():
    """platform/files.read_upload: at most a byte past the limit, never the whole of a huge file."""
    from pathlib import Path
    root = Path(app_dir())
    hand = [str(p.relative_to(root)) for d in ("api", "platform") for p in (root / d).rglob("*.py")
            if re.search(r"await \w+\.read\((?!\))", p.read_text()) and p.name != "files.py"]
    assert hand == []
    uses = sorted(str(p.relative_to(root)) for p in root.rglob("*.py") if re.search(r"await read_upload\(", p.read_text()))
    assert uses == ["api/v1/attachments.py", "api/v1/inventory.py", "platform/crud_router.py"]


def app_dir() -> str:
    import app as package
    return package.__path__[0]
