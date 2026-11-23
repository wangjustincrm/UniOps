"""Route guards (review 2026-11-23, ≈ ABP's [Authorize] by default + strict input): every route needs a signed-in
person except the health checks; every request body refuses unknown fields; every record action has a permission
and is run only through run_action (which authorizes once, first)."""
import inspect

from fastapi.routing import APIRoute
from pydantic import BaseModel

from app.main import app

PUBLIC = {"/health", "/health/db", "/api/v1/health", "/api/v1/health/db"}  # docker / check-health.sh, no data


def _calls(dependant):
    for d in dependant.dependencies:
        yield d.call
        yield from _calls(d)


def _routes():
    return [r for r in app.routes if isinstance(r, APIRoute)]


def test_every_route_needs_a_signed_in_person_except_the_health_checks():
    open_ = {r.path for r in _routes() if "get_current_user_payload" not in {getattr(c, "__name__", "") for c in _calls(r.dependant)}}
    assert open_ == PUBLIC


def test_every_request_body_refuses_unknown_fields():
    loose = set()
    for r in _routes():
        for p in r.dependant.body_params:
            t = p.field_info.annotation
            if inspect.isclass(t) and issubclass(t, BaseModel) and t.model_config.get("extra") != "forbid":
                loose.add(f"{r.path}: {t.__name__}")
    assert loose == set()


def test_every_record_action_has_a_permission():
    import app.services as services
    from app.platform.bulk_actions import ActionRegistry
    import importlib
    import pkgutil
    registries = []
    for m in pkgutil.iter_modules(services.__path__):
        mod = importlib.import_module(f"app.services.{m.name}")
        registries += [v for v in vars(mod).values() if isinstance(v, ActionRegistry)]
    assert registries, "the guard sees the registries"
    for reg in registries:
        for a in reg.actions.values():
            assert a.permission.startswith("sales.") and a.permission != "sales.read", (a.id, a.permission)
    action_routes = [r for r in _routes() if r.path.endswith("/actions/{action_id}")]
    assert action_routes and all(r.endpoint.__module__ == "app.platform.bulk_actions" for r in action_routes)
