"""Every write route commits by itself, or says why it decides for itself (stage 5, platform/routes.py): held over the routes the
app really mounts (a router made another way is caught) and over the code (no endpoint commits unless it declares it)."""
import ast
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

import app as package
from app.main import app
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.platform.routes import SAFE, UnitOfWorkRoute
from app.platform.uow import SqlAlchemyUnitOfWork

ROOT = Path(package.__path__[0])
WRITES = [r for r in app.routes if isinstance(r, APIRoute) and r.methods - SAFE]
READS = [r for r in app.routes if isinstance(r, APIRoute) and not r.methods - SAFE]
# (module, endpoint) → why, and how often its code commits. Nothing else decides for itself.
OWN = {("app.api.v1.orders", "resolve_945_row"): ("reads the order again after the commit", 1),
       ("app.api.v1.orders", "manual_create"): ("reads the order again after the commit", 1),
       ("app.api.v1.orders", "manual_update"): ("reads the order again after the commit", 1),
       ("app.api.v1.orders", "reorder"): ("reads the order again after the commit", 1),
       ("app.api.v1.orders", "manual_preview"): ("reads only: the EA preview before saving (FR-OB-19a)", 0),
       ("app.api.v1.operations", "connection_test"): ("reads only: lists folders, never writes (FR-OB-74)", 0),
       ("app.api.v1.operations", "connection_run_now"): ("the connection's run is claimed in a unit of its own (ScheduledJob.claim)", 0),
       ("app.api.v1.operations", "run_now"): ("the run is claimed in a unit of its own (ScheduledJob.claim)", 0),
       ("app.api.v1.operations", "run_system_now"): ("the system run is claimed in a unit of its own (ScheduledJob.claim)", 0),
       ("app.platform.bulk_actions", "run"): ("a dry run keeps nothing", 1),
       ("app.platform.crud_router", "import_"): ("a dry run and a refused file keep nothing", 1)}
name = lambda r: f"{'/'.join(sorted(r.methods))} {r.path}"  # noqa: E731


class Counted(SqlAlchemyUnitOfWork):
    """A unit that only counts."""
    def __init__(self, fails: Exception | None = None) -> None:
        super().__init__(lambda: None, SYSTEM_ACTOR)
        self.commits, self.fails = 0, fails

    async def commit(self) -> None:
        self.commits += 1
        if self.fails:
            raise self.fails
        self.committed = True


def test_there_are_the_writes_we_know():
    assert len(WRITES) >= 60 and len(READS) >= 60, (len(WRITES), len(READS))


@pytest.mark.parametrize("route", WRITES, ids=name)
def test_every_write_route_is_the_platform_s_and_says_so_where_it_decides_for_itself(route):
    assert isinstance(route, UnitOfWorkRoute), "made with a plain APIRouter: its writes would never be kept"
    key = (route.endpoint.__module__, route.endpoint.__name__)
    assert getattr(route.endpoint, "__own_commit__", None) == (OWN[key][0] if key in OWN else None)
    assert route.commits is (key not in OWN)


@pytest.mark.parametrize("route", [r for r in WRITES if isinstance(r, UnitOfWorkRoute) and r.commits], ids=name)
async def test_what_fastapi_calls_commits_once_after_the_endpoint_and_never_when_it_raises(route, monkeypatch):
    """The call FastAPI makes for this very route, its endpoint replaced."""
    ran = []

    async def done(u):
        ran.append(u.commits)
        return "the answer"
    monkeypatch.setattr(route, "endpoint", done)
    u = Counted()
    assert await route.dependant.call(u=u) == "the answer" and ran == [0] and u.commits == 1

    async def refused(u):
        raise BusinessError("refused", "no")
    monkeypatch.setattr(route, "endpoint", refused)
    u = Counted()
    with pytest.raises(BusinessError):
        await route.dependant.call(u=u)
    assert u.commits == 0
    monkeypatch.setattr(route, "endpoint", done)
    u = Counted(fails=BusinessError("commit.refused", "no"))
    with pytest.raises(BusinessError) as e:  # answered by the error handlers
        await route.dependant.call(u=u)
    assert e.value.code == "commit.refused" and u.commits == 1


@pytest.mark.parametrize("route", [r for r in WRITES if not getattr(r, "commits", False)] + READS, ids=name)
def test_a_read_and_a_route_that_decides_for_itself_are_called_as_they_are(route):
    assert route.dependant.call is route.endpoint


@pytest.mark.parametrize("route", [r for r in WRITES if getattr(r, "commits", False)], ids=name)
def test_a_write_takes_one_unit_of_work(route):
    from app.core.deps import get_uow
    assert [d.call for d in route.dependant.dependencies].count(get_uow) == 1


# ── the code ─────────────────────────────────────────────────────────────────

METHODS = {"get", "head", "options", "trace", "post", "put", "patch", "delete"}


def endpoints(source: str) -> dict[str, tuple[bool, int, str | None]]:
    """Every function decorated with <router>.<method>(…) → (is a write, how often its body commits, own_commit's reason)."""
    out = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [d for d in node.decorator_list if isinstance(d, ast.Call)]
        verbs = {d.func.attr for d in calls if isinstance(d.func, ast.Attribute) and d.func.attr in METHODS}
        if verbs:
            own = next((d.args[0].value for d in calls if isinstance(d.func, ast.Name) and d.func.id == "own_commit"), None)
            commits = sum(1 for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("commit", "flush_and_commit"))
            out[node.name] = (bool(verbs - {"get", "head", "options", "trace"}), commits, own)
    return out


def test_no_endpoint_commits_unless_it_says_why():
    found = {}
    for p in [*sorted((ROOT / "api").rglob("*.py")), *sorted((ROOT / "platform").rglob("*.py"))]:
        mod = "app." + ".".join(p.relative_to(ROOT).with_suffix("").parts)
        found |= {(mod, n): v for n, v in endpoints(p.read_text()).items()}
    assert len(found) >= 60
    assert {k: v[1] for k, v in found.items() if v[1] and k not in OWN} == {}
    assert {k: (found[k][2], found[k][1]) for k in OWN} == OWN
    assert {k for k, v in found.items() if v[2] is not None} == set(OWN)
    assert all(found[k][0] for k in OWN)  # a read has nothing to decide


def test_a_route_that_reads_again_does_so_after_its_commit():
    """The order the four keep (orders.py): write, commit, then read the record again for the answer."""
    again = [k for k, v in OWN.items() if v[0].endswith("again after the commit")]
    assert len(again) == 4
    tree = ast.parse((ROOT / "api/v1/orders.py").read_text())
    for node in (n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and ("app.api.v1.orders", n.name) in again):
        calls = sorted((n.lineno, n.func.attr) for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute))
        names = [a for _, a in calls if a in ("commit", "get")]
        assert names == ["commit", "get"], (node.name, names)
        assert isinstance(node.body[-1], ast.Return) and node.body[-1].lineno > next(i for i, a in calls if a == "commit")


def test_every_router_is_the_platform_s():
    """The two that only gather others, and the health checks (reads, no unit of work)."""
    plain = {str(p.relative_to(ROOT)) for p in ROOT.rglob("*.py") if any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "APIRouter" for n in ast.walk(ast.parse(p.read_text())))}
    assert plain == {"api/v1/__init__.py", "api/v1/health.py"}


def test_the_code_check_sees_what_it_must():
    src = '''
@r.post("/a")
async def quiet(u): await svc(u).create()
@r.put("/b")
async def loud(u):
    await u.commit()
    await u.commit()
@r.post("/c")
@own_commit("a dry run keeps nothing")
async def says(u, dry):
    if not dry:
        await u.commit()
@r.get("/d")
async def read(u):
    await u.commit()
async def helper(u): await u.commit()
'''
    assert endpoints(src) == {"quiet": (True, 0, None), "loud": (True, 2, None), "says": (True, 1, "a dry run keeps nothing"), "read": (False, 1, None)}
