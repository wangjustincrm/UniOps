"""Every public async method of an application service checks its permission (@requires, or
self._authorize in its body), is a hook the checked base method calls, or belongs to an engine
that only the scheduler, the outbox or a checked facade call (review 2026-11-10). A new method
that answers a person without a check fails here instead of shipping; this test found
OrderService.packing_list (the customer's name and address) open to anyone signed in."""
import importlib
import inspect
import pathlib
import pkgutil
import re

import app.services as services

# CrudService / ReadOnlyService hooks: the base's checked create / update / list call them.
# enrich: fills rows already read by an authorised get / list / detail (ReadOnlyService._read_all).
# check / extra / after / reason_for: SettingsGroup hooks, called by its authorised get / update.
HOOKS = {"before_update", "validate_create", "apply_changes", "enrich", "check", "extra", "after", "reason_for"}

# Engines: they take the unit of work as a parameter and are reached only through the scheduler
# (cycle), the outbox, or a facade that checks first (InvoiceService, TransferService, OrderService,
# DocumentActions, HealthService). No router calls one directly (the second test).
ENGINES = {
    "cycle.ConnectionJob": "the scheduler's per-connection job",
    "cycle.SalesScheduler": "the scheduler",
    "cycle.SystemJob": "the scheduler's system job",
    "edi_intake.EdiIntake": "the scheduler (850 intake)",
    "intake.Intake": "the scheduler (SFTP intake)",
    "shopify_intake.ShopifyIntake": "the scheduler (Shopify intake)",
    "gateway_intake.AuthorizeNetIntake": "the scheduler (Authorize.Net settlements, read only)",
    "invoicing.Invoicing": "InvoiceService / DeliveryService check first; the scheduler advances",
    "transfers.Transfers": "TransferService checks first; the scheduler queues and reads 997 / 944",
    "outbound.Outbound940": "OrderService.send_940_now checks first; the scheduler queues; the outbox sends",
    "shipments.Shipments": "the scheduler (945s); OrderService checks before retry_shopify",
    "shadow.ShadowRun": "the scheduler (shadow comparison)",
    "orders.OrderSync": "intake and OrderService.resolve_review (checked)",
    "orders.Catalog": "a read helper of OrderSync",
    "orders.BarcodeLookup": "a read helper of OrderSync",
    "problems.SalesTasks": "the task gateway every engine raises problems through",
    "health.Health": "HealthService checks first",
}


def _checked_inline(fn) -> bool:
    try:
        # its own check, or the checked base method it extends (an enriching list() / get())
        return bool(re.search(r"\bself\._authorize\(|\bauthorize\(self\.uow|\bsuper\(\)\.(list|get)\(", inspect.getsource(fn)))
    except OSError:
        return False


def _unchecked() -> list[str]:
    out = []
    for m in pkgutil.iter_modules(services.__path__):
        mod = importlib.import_module(f"app.services.{m.name}")
        for cname, cls in inspect.getmembers(mod, inspect.isclass):
            if cls.__module__ != mod.__name__ or f"{m.name}.{cname}" in ENGINES:
                continue
            for name, fn in cls.__dict__.items():
                f = getattr(fn, "__func__", fn)
                if (name.startswith("_") or not inspect.iscoroutinefunction(f) or hasattr(f, "__requires__")
                        or name in HOOKS or _checked_inline(f)):
                    continue
                out.append(f"{m.name}.{cname}.{name}")
    return sorted(out)


def test_every_public_service_method_checks_a_permission():
    assert _unchecked() == []


def test_no_router_calls_an_engine_directly():
    api = pathlib.Path(services.__file__).parents[1] / "api"
    direct = [f"{p.name}: {m}" for p in api.rglob("*.py") for m in re.findall(r"\brt\.\w+\.\w+\(", p.read_text())]
    assert direct == []


def test_the_engine_list_names_classes_that_exist():
    names = set()
    for m in pkgutil.iter_modules(services.__path__):
        mod = importlib.import_module(f"app.services.{m.name}")
        names |= {f"{m.name}.{c}" for c, k in inspect.getmembers(mod, inspect.isclass) if k.__module__ == mod.__name__}
    assert sorted(set(ENGINES) - names) == []


# ── one base (step 3, 2026-11-18): every service a request reaches is a platform Service ──────────────────────
def _unbased(module_classes) -> list[str]:
    """Classes with a public coroutine (an operation a request may call) that are neither an engine nor built on
    platform Service. Data holders (dataclasses, no coroutine) and the platform's other bases are not services."""
    import dataclasses
    from app.platform.services import Service
    out = []
    for qual, cls in module_classes:
        if qual in ENGINES or dataclasses.is_dataclass(cls) or issubclass(cls, Service):
            continue
        ops = [n for n, f in cls.__dict__.items() if not n.startswith("_") and inspect.iscoroutinefunction(getattr(f, "__func__", f))]
        if ops:
            out.append(qual)
    return sorted(out)


def _service_classes():
    for m in pkgutil.iter_modules(services.__path__):
        mod = importlib.import_module(f"app.services.{m.name}")
        for cname, cls in inspect.getmembers(mod, inspect.isclass):
            if cls.__module__ == mod.__name__:
                yield f"{m.name}.{cname}", cls


# Platform bases of their own (a settings group, the saved views, the task gateway, a scheduled job): each
# checks its permission in the base, as the first test proves.
OTHER_BASES = ("SettingsGroup", "ViewService", "TaskService", "ScheduledJob")


def test_every_service_a_request_reaches_is_a_platform_service():
    classes = [(q, c) for q, c in _service_classes() if not any(b.__name__ in OTHER_BASES for b in c.__mro__[1:])]
    assert _unbased(classes) == []


def test_the_base_check_catches_a_service_built_by_hand():
    from app.platform.services import Service

    class Planted:
        async def do(self) -> None: ...

    class Based(Service):
        async def do(self) -> None: ...
    assert _unbased([("x.Planted", Planted), ("x.Based", Based)]) == ["x.Planted"]
