"""One-way layer rules (design §13): walk the AST of every module of a layer
and refuse imports the layer may not make.

    app/platform      imports nothing else of app (not integrations, domain,
                      services, api, core, db, models)
    app/integrations  may import platform and itself — never domain,
                      services or api (they sit above it)
"""
import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
PLATFORM = APP / "platform"
INTEGRATIONS = APP / "integrations"


def imported_app_modules(source: str, module: str) -> list[str]:
    """Every `app…` module a source imports; `module` is the file's dotted
    name (for resolving relative imports)."""
    found = []
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = module.split(".")[: -node.level]
                names = [".".join(base + ([node.module] if node.module else []))]
            else:
                names = [node.module or ""]
        found += [n for n in names if n == "app" or n.startswith("app.")]
    return found


def _inside(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


def business_imports(source: str, module: str) -> list[str]:
    """Platform rule: anything of app outside app.platform."""
    return [n for n in imported_app_modules(source, module) if not _inside(n, "app.platform")]


INTEGRATIONS_MAY_NOT = ("app.domain", "app.services", "app.api")


def integration_violations(source: str, module: str) -> list[str]:
    return [n for n in imported_app_modules(source, module)
            if any(_inside(n, layer) for layer in INTEGRATIONS_MAY_NOT)]


def _modules(root: Path = PLATFORM):
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(APP.parent).with_suffix("")
        yield p, ".".join(rel.parts)


def test_platform_imports_no_business_code():
    files = list(_modules())
    assert len(files) >= 15, "the walk found too few platform modules — is the path right?"
    offenders = {mod: bad for p, mod in files if (bad := business_imports(p.read_text(), mod))}
    assert offenders == {}


def test_checker_catches_absolute_and_relative_business_imports():
    # The positive half: the same checker flags what it must, so an empty
    # result above is not an accident of a broken walk.
    assert business_imports("from app.domain.status import FULFILLMENT", "app.platform.x") == ["app.domain.status"]
    assert business_imports("import app.core.config", "app.platform.x") == ["app.core.config"]
    assert business_imports("from ..core import config", "app.platform.x") == ["app.core"]
    assert business_imports("from . import errors", "app.platform.x") == []
    assert business_imports("from app.platform.errors import BusinessError", "app.platform.x") == []
    assert business_imports("import sqlalchemy", "app.platform.x") == []
    for layer in ("app.integrations.x12", "app.integrations", "app.domain.status", "app.services.orders",
                  "app.api.v1.health", "app.core.config", "app.db.session", "app.models"):
        assert business_imports(f"import {layer}", "app.platform.x") == [layer], layer
    assert business_imports("from ..integrations import sftp", "app.platform.x") == ["app.integrations"]


def test_integrations_import_only_platform_and_themselves():
    files = list(_modules(INTEGRATIONS))
    assert len(files) >= 4, "the walk found too few integration modules — is the path right?"
    offenders = {mod: bad for p, mod in files if (bad := integration_violations(p.read_text(), mod))}
    assert offenders == {}


def test_integration_checker_flags_upper_layers_and_allows_platform():
    m = "app.integrations.sftp"
    assert integration_violations("from app.services.orders import X", m) == ["app.services.orders"]
    assert integration_violations("import app.api.v1.health", m) == ["app.api.v1.health"]
    assert integration_violations("from app.domain import status", m) == ["app.domain"]
    assert integration_violations("from ..domain import status", m) == ["app.domain"]
    assert integration_violations("from app.platform.guard import Suppressed", m) == []
    assert integration_violations("from app.integrations.email import FakeEmail", m) == []


def test_no_sales_names_in_platform_code():
    """design §13: no module, partner, order, 940 or Radiant identifiers, no switch names.
    "sales" alone covers sales_, SALES_ and sales-api (compared case-insensitively);
    they are listed so the intent stays visible."""
    forbidden = ("sales", "sales_", "SALES_", "sales-api", "orders_", "radiant", "shopify", "truecommerce", "940")
    hits = {}
    for p, mod in _modules():
        text = p.read_text()
        found = [w for w in forbidden if w.lower() in text.lower()]
        if found:
            hits[mod] = found
    assert hits == {}
