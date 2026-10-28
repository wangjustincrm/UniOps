"""app/platform never imports business code (design §13): walk the AST of
every module under app/platform and refuse any `app.` import outside it."""
import ast
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[2] / "app" / "platform"


def business_imports(source: str, module: str) -> list[str]:
    """`module` is the dotted name of the file (for resolving relative imports)."""
    bad = []
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
        for n in names:
            if (n == "app" or n.startswith("app.")) and not (n == "app.platform" or n.startswith("app.platform.")):
                bad.append(n)
    return bad


def _modules():
    for p in sorted(PLATFORM.rglob("*.py")):
        rel = p.relative_to(PLATFORM.parent.parent).with_suffix("")
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


def test_no_order_bridge_names_in_platform_code():
    """design §13: no partner, order, 940 or Radiant identifiers, no switch names."""
    forbidden = ("order_bridge", "orders_", "obr_", "radiant", "shopify", "truecommerce", "940", "ORDERS_")
    hits = {}
    for p, mod in _modules():
        text = p.read_text()
        found = [w for w in forbidden if w.lower() in text.lower()]
        if found:
            hits[mod] = found
    assert hits == {}
