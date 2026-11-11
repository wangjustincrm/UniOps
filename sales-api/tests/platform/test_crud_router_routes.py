"""crud_router offers only the routes the table supports: a route that can only fail is never mounted."""
from app.main import app


def _routes() -> set[tuple[str, str]]:
    return {(m, r.path) for r in app.routes for m in getattr(r, "methods", set())}


def test_deactivate_only_for_a_table_with_active():
    routes = _routes()
    assert ("POST", "/api/v1/config/partners/{id}/deactivate") in routes
    assert ("POST", "/api/v1/config/partner-codes/{id}/deactivate") not in routes  # a buyer part number has no `active`


def test_csv_only_for_a_service_that_declares_it():
    routes = _routes()
    assert ("POST", "/api/v1/config/items/import.csv") in routes
    assert ("GET", "/api/v1/config/items/export.csv") in routes
    assert ("POST", "/api/v1/config/notification-rules/import.csv") not in routes
