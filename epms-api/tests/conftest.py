"""Shared pytest fixtures for the EPMS API test suite."""
import asyncio
import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — registers all models with Base.metadata
import app.db.session as session_module
from app.core.config import settings
from app.core.security import create_access_token
from app.crud import user as user_crud
from app.db.base import Base
from app.main import create_app
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest


# ── Default permission matrix (mirrors identity's seed_authz.py) ──────────────
# Source of truth: identity-api/scripts/seed_authz.py (DEFAULTS / LOCKED /
# ROLE_LABELS / MODULE_BY_KEY / compute_effective). identity-api is a separate
# service running in its own container — only ./epms-api is bind-mounted into
# uniops_epms_api (see docker-compose.dev.yml), so identity's script isn't on
# this process's sys.path and can't be imported directly (it also imports its
# own `app.db.base`, which would collide with epms's own `app` package).
# These constants are therefore a hand-copy of the phase-1 defaults and MUST
# be kept in sync with identity-api/scripts/seed_authz.py if that file's
# DEFAULTS/LOCKED ever change. Only the 17 phase-1 keys are reproduced here
# (the ones access_scope's view_pr/view_po/view_gr/view_invoice/view_pa
# checks — and everything else in MODULE_BY_KEY — actually gate); add
# phase-2 keys here only if a test comes to depend on one of their defaults.
_MODULE_BY_KEY = {
    "view_pr": "epms", "view_po": "epms", "view_gr": "epms",
    "view_invoice": "epms", "view_pa": "epms", "create_pr": "epms",
    "create_gr": "epms", "invoice_upload": "epms", "vendor_master": "epms",
    "parts_catalog": "epms", "admin_panel": "epms", "data_maintenance": "epms",
    "pa_override_receipt": "epms",
    "view_budget_dashboard": "finance", "view_budget_plans": "finance",
    "view_finance": "finance",
    "view_booking": "booking", "manage_meeting_rooms": "booking",
}
_PERMISSION_KEYS = list(_MODULE_BY_KEY)

_ROLE_LABELS = {  # built-in 17
    "requester": "Requester", "dept_admin": "Department Admin",
    "dept_manager": "Department Manager", "supervisor": "Supervisor",
    "director": "Director", "gm": "General Manager", "opm": "Operations Manager",
    "procurement_officer": "Procurement Officer",
    "procurement_manager": "Procurement Manager",
    "warehouse_staff": "Warehouse Staff", "ap_clerk": "AP Clerk",
    "finance_bp": "Finance BP", "finance_manager": "Finance Manager",
    "vendor_manager": "Vendor Manager", "cfo": "CFO", "auditor": "Auditor",
    "system_admin": "System Admin",
}

_LOCKED = {
    "requester": {"view_pr"},
    "procurement_officer": {"view_pr", "view_po", "view_gr"},
    "procurement_manager": {"view_pr", "view_po", "view_gr"},
    "warehouse_staff": {"view_gr"},
    "ap_clerk": {"view_invoice", "view_pa"},
    "finance_bp": {"view_pa"},
    "finance_manager": {"view_pa"},
    "system_admin": {"admin_panel"},
}

_VIEW_ALL = {k: True for k in ("view_pr", "view_po", "view_gr", "view_invoice", "view_pa")}
_FINANCE_ALL = {"view_budget_dashboard": True, "view_budget_plans": True, "view_finance": True}
_BOOKING = {"view_booking": True}
_BUDGET_VIEW = {"view_budget_dashboard": True, "view_budget_plans": True}


def _p(**kw):
    base = {k: False for k in _PERMISSION_KEYS}
    base.update(kw)
    return base


_DEFAULTS = {
    "requester":           _p(create_pr=True, create_gr=True, **_VIEW_ALL, **_BOOKING),
    "dept_admin":          _p(create_pr=True, create_gr=True, **_VIEW_ALL, **_BOOKING),
    "dept_manager":        _p(create_pr=True, create_gr=True, **_VIEW_ALL, **_BUDGET_VIEW, **_BOOKING),
    "supervisor":          _p(view_pr=True, **_BOOKING),
    "director":            _p(view_pr=True, view_pa=True, **_BOOKING),
    "gm":                  _p(create_pr=True, create_gr=True, **_VIEW_ALL, **_BOOKING),
    "opm":                 _p(create_pr=True, create_gr=True, **_VIEW_ALL, **_BOOKING),
    "procurement_officer": _p(create_gr=True, vendor_master=True, parts_catalog=True, pa_override_receipt=True, **_VIEW_ALL, **_BOOKING),
    "procurement_manager": _p(create_gr=True, vendor_master=True, parts_catalog=True, pa_override_receipt=True, **_VIEW_ALL, **_BOOKING),
    "warehouse_staff":     _p(create_gr=True, view_gr=True, **_BOOKING),
    "ap_clerk":            _p(create_gr=True, invoice_upload=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "finance_bp":          _p(create_gr=True, pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "finance_manager":     _p(create_pr=True, create_gr=True, admin_panel=True, pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "vendor_manager":      _p(vendor_master=True, admin_panel=True, **_BOOKING),
    "cfo":                 _p(pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "auditor":             _p(**_VIEW_ALL, **_BOOKING),
    "system_admin":        {k: True for k in _PERMISSION_KEYS},
}


def _effective_default_matrix() -> dict[str, dict[str, bool]]:
    """role -> {key: bool}: mirrors identity's compute_effective() with an
    empty `stored` override and no custom roles — i.e. exactly what a
    freshly-provisioned prod DB looks like right after
    `python -m scripts.seed_authz` runs with no admin edits yet."""
    result = {}
    for role, defaults in _DEFAULTS.items():
        merged = dict(defaults)
        for k in _LOCKED.get(role, set()):
            merged[k] = True
        result[role] = merged
    return result


async def _seed_default_matrix(conn) -> None:
    """Insert the DEFAULT permission matrix into role_defs/permission_defs/
    role_permissions/role_permission_locks — every statement is
    `ON CONFLICT DO NOTHING` (mirrors identity's real seed_authz.py), so this
    is safe to call unconditionally before every single test regardless of
    what state the tables are already in. `conn` may be an engine Connection
    (session-fixture setup) or an AsyncSession (per-test restore — see
    `_restore_default_matrix` below); both expose the same
    `.execute(text(...), params)` interface.

    This must NOT be gated on "table already non-empty ⇒ skip": that was
    tried and is wrong — test_authz_proxy.py's `authz_factory` fixture
    blanket-DELETEs these 4 tables for its own clean-slate tests, and the
    LAST such test in that module (test_me_permissions_ignores_forward_
    mock_entirely) leaves role_defs with exactly one custom row afterward
    (not zero), which would fool an emptiness check into thinking the
    default matrix is already there and skip reseeding — silently starving
    every later test in the session of the baseline again. Unconditional +
    ON CONFLICT DO NOTHING sidesteps that: it always ensures every default
    row exists, cheaply no-ops the ones that already do, and never touches
    rows outside the default set (e.g. a test's own extra custom role/grant
    survives untouched alongside it).
    """
    for i, (code, label) in enumerate(_ROLE_LABELS.items()):
        await conn.execute(text(
            "INSERT INTO role_defs (code, label, sort, is_active) VALUES (:c, :l, :s, true) "
            "ON CONFLICT (code) DO NOTHING"),
            {"c": code, "l": label, "s": i})
    for i, (key, module) in enumerate(_MODULE_BY_KEY.items()):
        await conn.execute(text(
            "INSERT INTO permission_defs (key, module, label, sort) VALUES (:k, :m, :l, :s) "
            "ON CONFLICT (key) DO NOTHING"),
            {"k": key, "m": module, "l": key.replace("_", " ").title(), "s": i})
    for role, perms in _effective_default_matrix().items():
        for key, granted in perms.items():
            if granted:
                await conn.execute(text(
                    "INSERT INTO role_permissions (role_code, permission_key) VALUES (:r, :k) "
                    "ON CONFLICT DO NOTHING"),
                    {"r": role, "k": key})
    for role, keys in _LOCKED.items():
        for key in keys:
            await conn.execute(text(
                "INSERT INTO role_permission_locks (role_code, permission_key) VALUES (:r, :k) "
                "ON CONFLICT DO NOTHING"),
                {"r": role, "k": key})


# ── Fake mdm-api client ──────────────────────────────────────────────────────────
# Vendor master is owned by mdm-api (B3 / P1): EPMS forwards supplier writes to
# mdm /partners. In tests we stand in for mdm by writing the shared
# business_partners table directly (what mdm would do), so vendor creation works
# without running mdm-api. Set FakeMdmClient.suppliers for ERP-mirror reads.
class FakeMdmClient:
    suppliers: dict[str, dict] = {}

    def __init__(self, bearer_token: str | None = None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_supplier(self, code: str):
        return FakeMdmClient.suppliers.get(code)

    async def get_person(self, code: str):
        return None

    async def find_partner_by_code(self, code: str):
        async with session_module.AsyncSessionLocal() as db:
            v = (await db.execute(select(Vendor).where(Vendor.code == code))).scalar_one_or_none()
            return {"id": str(v.id), "code": v.code} if v else None

    async def create_partner(self, payload: dict) -> dict:
        from app.services.mdm_client import MdmError
        cols = {c.name for c in Vendor.__table__.columns}
        async with session_module.AsyncSessionLocal() as db:
            if (await db.execute(select(Vendor).where(Vendor.code == payload["code"]))).scalar_one_or_none():
                raise MdmError(409, f"Partner code '{payload['code']}' already exists")
            v = Vendor(**{k: val for k, val in payload.items() if k in cols})
            db.add(v)
            await db.commit()
            await db.refresh(v)
            return {"id": str(v.id), "code": v.code, "name": v.name}

    async def update_partner(self, partner_id: str, payload: dict) -> dict:
        from app.services.mdm_client import MdmError
        cols = {c.name for c in Vendor.__table__.columns}
        async with session_module.AsyncSessionLocal() as db:
            v = await db.get(Vendor, uuid.UUID(partner_id))
            if v is None:
                raise MdmError(404, "Partner not found")
            for k, val in payload.items():
                if k in cols:
                    setattr(v, k, val)
            await db.commit()
            return {"id": partner_id}


@pytest.fixture(autouse=True)
def _patch_mdm_client(monkeypatch):
    """Route EPMS vendor-write forwarding to the in-test fake (no real mdm-api)."""
    import app.api.v1.vendors as vendors_api
    monkeypatch.setattr(vendors_api, "MdmClient", FakeMdmClient)
    FakeMdmClient.suppliers = {}
    yield

# ── Test DB ────────────────────────────────────────────────────────────────────
# Requires a pre-created database. Run once:
#   python -m scripts.create_test_db
_base_url, _ = str(settings.DATABASE_URL).rsplit("/", 1)
# TEST_EPMS_DB lets a second session run this suite against its own database.
# The suite drops and recreates every table, so two sessions sharing `epms_test`
# corrupt each other's runs (the 2026-08-11 three-way-allocation release saw a
# shared DB produce three different fake failure counts). Same knob identity-api's
# conftest has had as TEST_IDENTITY_DB.
_TEST_DB_URL = f"{_base_url}/{os.getenv('TEST_EPMS_DB', 'epms_test')}"


# ── Force all async tests to use the session event loop ───────────────────────
# pytest-asyncio 0.24 defaults tests to per-function loops while session-scoped
# fixtures use a single session loop. asyncpg connections are loop-bound, so
# tests must share the same session loop to avoid "Future attached to different
# loop" errors.

def pytest_collection_modifyitems(items):
    session_mark = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if isinstance(item, pytest.Function) and asyncio.iscoroutinefunction(item.function):
            item.add_marker(session_mark, append=False)


@pytest.fixture(scope="session")
async def test_engine():
    """Create tables once per session; drop them on teardown."""
    engine = create_async_engine(_TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        # `user_roles` is identity-owned (no ORM model here — access_scope's
        # _effective_role_codes reads it directly, same physical DB in prod,
        # phase-3 Task 5). Shadow it so tests can grant additional roles.
        await conn.execute(text("DROP TABLE IF EXISTS user_roles CASCADE"))
        await conn.execute(text(
            "CREATE TABLE user_roles (user_id uuid NOT NULL, role_code varchar(50) NOT NULL,"
            " PRIMARY KEY (user_id, role_code))"))
        # approval-api's routing table (same physical DB in prod, no ORM model
        # here). access_scope reads it with raw SQL whenever it resolves a scoped
        # approver's departments, which every dashboard that counts pending
        # approvals goes through — without the shadow those tests die on
        # UndefinedTable instead of asserting anything.
        await conn.execute(text("DROP TABLE IF EXISTS approval_dept_routing CASCADE"))
        await conn.execute(text(
            "CREATE TABLE approval_dept_routing ("
            " dept_id uuid PRIMARY KEY,"
            " gm_or_opm varchar(3) NOT NULL DEFAULT 'gm',"
            " director_user_id uuid,"
            " supervisor_enabled boolean NOT NULL DEFAULT false,"
            " updated_by uuid,"
            " updated_at timestamptz NOT NULL DEFAULT now())"))
        # approval-api's delegation table (same physical DB in prod, no ORM
        # model here — epms only reads it via app.core.delegation, never
        # writes). Shadow it, minus the constraints approval-api enforces on
        # write (exclusion constraint on overlapping windows, etc.) — this
        # side only needs to select rows a test seeds directly.
        await conn.execute(text("DROP TABLE IF EXISTS approval_delegations CASCADE"))
        await conn.execute(text(
            "CREATE TABLE approval_delegations ("
            "  id uuid PRIMARY KEY,"
            "  delegator_user_id uuid NOT NULL,"
            "  delegate_user_id uuid NOT NULL,"
            "  start_date date NOT NULL,"
            "  end_date date NOT NULL,"
            "  note text NULL,"
            "  revoked_at timestamptz NULL,"
            "  revoked_by uuid NULL,"
            "  created_by uuid NOT NULL,"
            "  created_at timestamptz NOT NULL DEFAULT now(),"
            "  updated_at timestamptz NOT NULL DEFAULT now())"))
        # role_defs / permission_defs / role_permissions / role_permission_locks
        # are also identity-owned (no ORM model here) — same physical DB in
        # prod. The shared uniops_authz package (require_permission,
        # effective_permissions, user_role_codes) and config.py's
        # _effective_role_matrix read these directly via raw SQL, so the test
        # DB needs them too. Seeded below with the same DEFAULT matrix
        # identity's scripts/seed_authz.py seeds into a freshly-provisioned
        # prod (see _DEFAULTS/_LOCKED above) — this is what access_scope's
        # _effective_permissions actually reads for the view_pr/view_po/
        # view_gr/view_invoice/view_pa checks that gate document visibility,
        # so pre-existing scoping tests (written before Task 3 moved this off
        # company_config's JSONB-with-DEFAULT-fallback) keep working
        # unmodified. Tests may still layer additional grants/locks on top of
        # this baseline (e.g. test_authz_proxy.py's authz_factory fixture
        # wipes these 4 tables first to test from a clean slate).
        for stmt in (
            "DROP TABLE IF EXISTS role_permission_locks CASCADE",
            "DROP TABLE IF EXISTS role_permissions CASCADE",
            "DROP TABLE IF EXISTS permission_defs CASCADE",
            "DROP TABLE IF EXISTS role_defs CASCADE",
        ):
            await conn.execute(text(stmt))
        await conn.execute(text(
            "CREATE TABLE role_defs (code varchar(50) PRIMARY KEY, label varchar(100) NOT NULL,"
            " sort integer NOT NULL DEFAULT 0, is_active boolean NOT NULL DEFAULT true)"))
        await conn.execute(text(
            "CREATE TABLE permission_defs (key varchar(64) PRIMARY KEY, module varchar(20) NOT NULL,"
            " label varchar(120) NOT NULL, sort integer NOT NULL DEFAULT 0)"))
        await conn.execute(text(
            "CREATE TABLE role_permissions (role_code varchar(50) NOT NULL,"
            " permission_key varchar(64) NOT NULL, updated_by uuid,"
            " updated_at timestamptz NOT NULL DEFAULT now(),"
            " PRIMARY KEY (role_code, permission_key))"))
        await conn.execute(text(
            "CREATE TABLE role_permission_locks (role_code varchar(50) NOT NULL,"
            " permission_key varchar(64) NOT NULL, PRIMARY KEY (role_code, permission_key))"))

        await _seed_default_matrix(conn)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _restore_default_matrix(test_engine):
    """Function-scoped + autouse: re-seeds the DEFAULT permission matrix at
    the start of every test if a previous test wiped it — a no-op otherwise.

    Why this is needed: role_defs/permission_defs/role_permissions/
    role_permission_locks are session-scoped tables (created once, not
    wrapped in a per-test transaction rollback), but test_authz_proxy.py's
    `authz_factory` fixture blanket-DELETEs all 4 of them to get a clean
    slate for its own tests — with no matching restore afterward. Left
    alone, that would leave the tables permanently empty for the rest of the
    session (order-dependent breakage: test_pr_scoping.py, which runs later
    alphabetically, would 404/empty-list for every user because
    access_scope._effective_permissions reads an empty matrix). pytest runs
    autouse fixtures before explicitly-requested ones in the same scope, so
    this always seeds BEFORE `authz_factory`'s DELETE runs within a
    test_authz_proxy.py test — giving that module the empty tables it wants
    for its own test body — and BEFORE every other test, restoring the
    baseline any prior test may have wiped.
    """
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await _seed_default_matrix(db)
        await db.commit()
    yield


@pytest.fixture(autouse=True)
async def _drain_background_tasks():
    """Cancel any fire-and-forget task the test left in flight, and WAIT for the
    cancellation to unwind it.

    Endpoints spawn background coroutines (notification emails, PDF generation)
    that each open their own `async with AsyncSessionLocal() as db`. A test that
    triggers one and then returns leaves it suspended there forever: the session
    is never closed, so its connection goes back to the pool still inside a
    transaction. That is not merely untidy — a leaked `idle in transaction`
    backend holds row/table locks, and the next module's `drop_all` teardown
    blocks on them. It wedged a full-suite run for four minutes on a
    `DROP TABLE invoices` before the blocker was killed by hand.

    tests/test_gr.py's `_quiet_notification_email` fixture already documents
    this exact deadlock ("a reliable deadlock recipe on the shared test DB") and
    works around it for that one module by stubbing SMTP. This is the general
    fix; that local stub stays valid (it also keeps the tests fast).

    Cancel rather than await-to-completion: the emails cannot succeed in tests
    anyway (no SMTP), and awaiting them would let previously-dead notifications
    start writing notification_logs rows, changing assertions in tests that
    count them. Cancelling only reclaims the connection. `CancelledError` is a
    BaseException, so the `except Exception` guards inside those coroutines do
    not swallow it and the `async with` unwinds properly.
    """
    yield
    from app.core.background import drain
    await drain(timeout=0)


@pytest.fixture(scope="session", autouse=True)
async def _patch_session_factory(test_engine):
    """
    Redirect AsyncSessionLocal to the test engine for the whole session.
    This makes every HTTP request go to epms_test instead of epms.
    """
    original = session_module.AsyncSessionLocal
    session_module.AsyncSessionLocal = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    yield
    session_module.AsyncSessionLocal = original


# ── HTTP clients ───────────────────────────────────────────────────────────────

@pytest.fixture
async def client():
    """Unauthenticated HTTPX client pointed at the test DB."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _authenticated_client(test_engine, role: str):
    """Create a user and return a pre-authenticated HTTPX client."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        user = await user_crud.create(
            db,
            RegisterRequest(
                email=f"{role}-{uuid.uuid4().hex[:8]}@example.com",
                password="TestPass1!",
                full_name=f"Test {role}",
                role=role,
            ),
        )
        await db.commit()
    token = create_access_token(str(user.id), user.role)
    app = create_app()
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture
async def admin_client(test_engine):
    async with await _authenticated_client(test_engine, "system_admin") as c:
        yield c


@pytest.fixture
async def finance_client(test_engine):
    async with await _authenticated_client(test_engine, "finance_manager") as c:
        yield c


@pytest.fixture
async def requester_client(test_engine):
    async with await _authenticated_client(test_engine, "requester") as c:
        yield c


# ── psycopg2 fixtures for the NC purchase writer/service (Task 4) ─────────────
# writer.py / service.py talk to Postgres over psycopg2 (bulk upsert + run
# registry), NOT the async ORM. These fixtures connect to the SAME epms_test DB
# the async suite uses, but only AFTER `test_engine` has created the schema —
# every psycopg2 fixture depends on `test_engine` so the tables exist first.
import psycopg2 as _psycopg2  # noqa: E402
from psycopg2.extras import register_uuid as _register_uuid  # noqa: E402
from sqlalchemy.engine.url import make_url as _make_url  # noqa: E402

_register_uuid()


def _test_pg_dsn() -> str:
    """psycopg2 keyword DSN for epms_test, derived from the same URL conftest
    uses for the async engine (strips the +asyncpg driver)."""
    u = _make_url(_TEST_DB_URL)
    return (f"host={u.host} port={u.port or 5432} dbname={u.database} "
            f"user={u.username} password={u.password}")


@pytest.fixture
def test_pg_dsn(test_engine):
    """The epms_test psycopg2 DSN string (schema guaranteed by test_engine)."""
    return _test_pg_dsn()


@pytest.fixture
def pg_conn(test_engine):
    """A psycopg2 connection on epms_test. Rolled back on teardown so writer
    tests that never commit leave the DB clean (per-test isolation)."""
    conn = _psycopg2.connect(_test_pg_dsn())
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def pg_cur(pg_conn):
    cur = pg_conn.cursor()
    yield cur
    cur.close()


@pytest.fixture
def seeded_vendor(pg_cur):
    """A business_partners supplier row with erp_id='0000415' (uncommitted in
    pg_conn's txn). Returns (id, name)."""
    vid = uuid.uuid4()
    pg_cur.execute(
        "insert into business_partners "
        "(id, code, erp_id, name, category, contact_name, contact_email, "
        " payment_terms, currency, is_active, is_supplier, is_customer) "
        "values (%s,%s,%s,%s,%s,%s,%s,'net30','CAD',true,true,false)",
        (vid, "NCV-0000415", "0000415", "NC Vendor 415", "supplier",
         "NC Contact", "nc-vendor@example.com"))
    return vid, "NC Vendor 415"


@pytest.fixture
def system_user_id(pg_cur):
    """The nc-sync system user id (created via writer.ensure_system_user_sync,
    uncommitted in pg_conn's txn)."""
    from app.services.nc_purchase_sync import writer
    return writer.ensure_system_user_sync(pg_cur)


@pytest.fixture
def clean_nc_sync_runs(test_engine):
    """Truncate nc_purchase_sync_runs before and after — service tests use their
    own autocommit connections, so their run rows persist outside pg_conn's txn."""
    def _truncate():
        c = _psycopg2.connect(_test_pg_dsn()); c.autocommit = True
        c.cursor().execute("truncate nc_purchase_sync_runs")
        c.close()
    _truncate()
    yield
    _truncate()
