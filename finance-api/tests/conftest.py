"""Test fixtures for finance-api.

Schema is built by running the real alembic migrations against a dedicated
local Postgres database (default: finance_test on the uniops_postgres docker
container). Mirrors budget-api/tests/conftest.py.

NOTE: migration 0001 has a FK to payment_applications (an epms-api table),
so we pre-create a one-column stub before running alembic.
"""
import os
import subprocess
import sys

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TEST_DB = os.getenv("TEST_FINANCE_DB", "finance_test")
TEST_HOST = os.getenv("TEST_PG_HOST", "localhost")
TEST_USER = os.getenv("TEST_PG_USER", "epms")
TEST_PASSWORD = os.getenv("TEST_PG_PASSWORD", "epms_dev")
TEST_PORT = os.getenv("TEST_PG_PORT", "5432")

ASYNC_URL = f"postgresql+asyncpg://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"
SYNC_URL = f"postgresql+psycopg2://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/{TEST_DB}"
ADMIN_SYNC_URL = f"postgresql+psycopg2://{TEST_USER}:{TEST_PASSWORD}@{TEST_HOST}:{TEST_PORT}/postgres"


def _ensure_db():
    eng = sa.create_engine(ADMIN_SYNC_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        exists = conn.execute(
            sa.text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB}
        ).scalar()
        if not exists:
            conn.execute(sa.text(f'CREATE DATABASE "{TEST_DB}"'))
    eng.dispose()


def _migrate():
    """Reset public schema, create mirror tables, run alembic upgrade head.

    Mirror tables (payment_applications / invoices / company_config /
    expense_claims) are owned by other services in the real DB; tests build
    them from this service's mirror models so the payment executor has real
    columns to work with. 0001's FK target (payment_applications) is among them.
    """
    _ensure_db()
    eng = sa.create_engine(SYNC_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
    eng.dispose()

    from app.db.base import Base
    from app.models.admin_audit_log import AdminAuditLog  # shared table, no finance migration owns it
    from app.models.mirrors import (  # noqa: F401
        BudgetAccount, BusinessPartner, CompanyConfig, CostCenter, Department, ErpSupplier, ExpenseApprovalEvent,
        ExpenseClaim, ExpenseInvoice, ExpenseLineItem, ExpenseTripItem, Invoice, InvoicePoAllocation,
        InvoiceTaxLine, PurchaseRequest, SodRule, Task, User,
    )
    from app.models.pa import PaymentApplication
    eng = sa.create_engine(SYNC_URL)
    Base.metadata.create_all(eng, tables=[
        PaymentApplication.__table__, Invoice.__table__, InvoiceTaxLine.__table__,
        CompanyConfig.__table__, ExpenseClaim.__table__, Task.__table__,
        SodRule.__table__, User.__table__, ExpenseApprovalEvent.__table__,
        ExpenseLineItem.__table__, ExpenseTripItem.__table__,
        AdminAuditLog.__table__, CostCenter.__table__,
        Department.__table__, BudgetAccount.__table__, ErpSupplier.__table__,
        ExpenseInvoice.__table__, InvoicePoAllocation.__table__, PurchaseRequest.__table__,
        BusinessPartner.__table__,
    ])
    # company_config is epms-owned; its physical table carries the shared
    # smtp_*/po_smtp_* columns (epms-api migrations e5f6a7b8c9d0 /
    # u1p2q3r4s5t6) and logo_data_url (epms-api migration
    # d4e5f6a7b8c9_sprint4_company_config, Text, nullable) that finance-api's
    # mirror deliberately does not map — remittance_config.load() reads them
    # via raw SQL, so the test schema needs them shadowed here too (same
    # pattern as user_roles below).
    with eng.connect() as conn:
        for stmt in (
            "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS smtp_host varchar(255)",
            "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS smtp_port integer",
            "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS smtp_user varchar(255)",
            "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS smtp_password varchar(255)",
            "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS smtp_use_tls boolean",
            "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS smtp_from varchar(255)",
            "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS po_smtp_host varchar(255)",
            "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS po_smtp_port integer",
            "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS po_smtp_user varchar(255)",
            "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS po_smtp_password varchar(255)",
            "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS po_smtp_use_tls boolean",
            "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS po_smtp_from varchar(255)",
            "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS logo_data_url text",
        ):
            conn.execute(sa.text(stmt))
        conn.commit()

    # `user_roles` is identity-owned (no ORM model here — payment_execute's
    # _user_role_codes reads it directly, same physical DB in prod, phase-3
    # Task 5). Shadow it so tests can grant additional roles.
    #
    # role_defs / permission_defs / role_permissions / role_permission_locks
    # are also identity-owned (no ORM model here) — same physical DB in prod.
    # The shared uniops_authz package (require_permission / effective_
    # permissions / user_role_codes), wired into coa.py / periods.py /
    # journal_voucher.py in phase-2 Task 5, reads these directly via raw SQL,
    # so the test DB needs them too. Seeded here with only the phase-2
    # finance keys these three gates actually depend on — the admission sets
    # match identity-api/scripts/seed_phase2_keys.py's PHASE2_DEFAULTS
    # exactly (that script is the source of truth; keep in sync if it
    # changes). role_defs only needs entries for roles a test grants via a
    # user_roles ASSIGNMENT (base/primary roles are admitted straight from
    # the JWT regardless of role_defs) — finance_bp is the only one any
    # current test assigns that way, plus the JWT-role set tests use.
    with eng.connect() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS user_roles CASCADE"))
        conn.execute(sa.text(
            "CREATE TABLE user_roles (user_id uuid NOT NULL, role_code varchar(50) NOT NULL,"
            " PRIMARY KEY (user_id, role_code))"))
        # approval-api owns this in the real shared DB (no ORM model here);
        # budget_scope.py reads it to resolve which departments a user directs.
        conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS approval_dept_routing ("
            " dept_id uuid PRIMARY KEY,"
            " director_user_id uuid,"
            " gm_or_opm varchar(3) NOT NULL DEFAULT 'gm'"
            ")"
        ))
        for stmt in (
            "DROP TABLE IF EXISTS role_permission_locks CASCADE",
            "DROP TABLE IF EXISTS role_permissions CASCADE",
            "DROP TABLE IF EXISTS permission_defs CASCADE",
            "DROP TABLE IF EXISTS role_defs CASCADE",
        ):
            conn.execute(sa.text(stmt))
        conn.execute(sa.text(
            "CREATE TABLE role_defs (code varchar(50) PRIMARY KEY, label varchar(100) NOT NULL,"
            " sort integer NOT NULL DEFAULT 0, is_active boolean NOT NULL DEFAULT true)"))
        conn.execute(sa.text(
            "CREATE TABLE permission_defs (key varchar(64) PRIMARY KEY, module varchar(20) NOT NULL,"
            " label varchar(120) NOT NULL, sort integer NOT NULL DEFAULT 0)"))
        conn.execute(sa.text(
            "CREATE TABLE role_permissions (role_code varchar(50) NOT NULL,"
            " permission_key varchar(64) NOT NULL, updated_by uuid,"
            " updated_at timestamptz NOT NULL DEFAULT now(),"
            " PRIMARY KEY (role_code, permission_key))"))
        conn.execute(sa.text(
            "CREATE TABLE role_permission_locks (role_code varchar(50) NOT NULL,"
            " permission_key varchar(64) NOT NULL, PRIMARY KEY (role_code, permission_key))"))

        for i, code in enumerate((
            "system_admin", "finance_manager", "finance_bp", "ap_clerk", "requester",
            # budget_scope.py resolves the caller's role union through
            # uniops_authz, which drops an ADDITIONAL role without an active
            # role_defs row — these are the ones its tests assign that way.
            "dept_manager", "director", "opm",
        )):
            conn.execute(sa.text(
                "INSERT INTO role_defs (code, label, sort, is_active) VALUES (:c, :c, :s, true)"),
                {"c": code, "s": i})
        for i, (key, module) in enumerate((
            ("finance.coa.manage", "finance"),
            ("finance.period.close", "finance"),
            ("finance.jv.post", "finance"),
            # Budget Dashboard data scope — identity migration
            # 0010_budget_view_scope_perms owns these two keys and the
            # production defaults mirrored in _phase2_defaults below.
            ("finance.budget.view_all", "finance"),
            ("finance.budget.view_dept", "finance"),
        )):
            conn.execute(sa.text(
                "INSERT INTO permission_defs (key, module, label, sort) VALUES (:k, :m, :k, :s)"),
                {"k": key, "m": module, "s": i})
        _phase2_defaults = {
            "finance.coa.manage": ("system_admin", "finance_manager"),
            "finance.period.close": ("system_admin", "finance_manager"),
            "finance.jv.post": ("system_admin", "finance_manager", "finance_bp"),
            "finance.budget.view_all": (
                "system_admin", "finance_manager", "finance_bp", "ap_clerk",
            ),
            "finance.budget.view_dept": ("requester", "dept_manager", "director", "opm"),
        }
        for key, roles in _phase2_defaults.items():
            for role in roles:
                conn.execute(sa.text(
                    "INSERT INTO role_permissions (role_code, permission_key) VALUES (:r, :k)"),
                    {"r": role, "k": key})
        conn.commit()
    eng.dispose()
    # PaymentApplication mirror needs pa_type for A4 partial-payment logic

    env = dict(os.environ)
    env["DATABASE_URL"] = ASYNC_URL
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=root, env=env, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def db_session():
    _migrate()
    engine = create_async_engine(ASYNC_URL, echo=False)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def seed_posted_jv_two_cc(db_session):
    """POSTED JV lines in TWO cost centers, same predreal account/income_expense
    item, FY2026, with distinct amounts — for Task 5's `cc_ids` CRUD filter
    tests, and Task 6's endpoint department-scoping tests. Mirrors the
    emit_event + backfill_posted_jvs seeding style used by
    test_account_balance.py's `_posted_dim_event` / `_posted_partner_event`
    helpers (account_code "5101" falls in the MOH predreal subtree, same as
    those tests).

    cc_a/cc_b each carry a `department_id` (dept_a/dept_b), and a `users` row
    (`mgr_uid`) is seeded in dept_a — so budget_scope.py's resolver maps a
    dept-manager caller to cc_a only. finance-api's `User` mirror model only
    carries the columns journal_voucher.py's actor-name lookup needs
    (email/full_name) — it doesn't model `department_id`, even though the real
    shared `users` table (owned by identity/epms) has it and the resolver reads
    it via raw SQL. Extend the test-schema table here rather than touching the
    production ORM model."""
    import uuid
    from datetime import datetime, timezone
    from decimal import Decimal

    import sqlalchemy as sa

    from app.crud import journal_voucher as jv_crud
    from app.models.mirrors import BudgetAccount, CostCenter
    from app.services.posting import emit_event

    dept_a = uuid.uuid4()
    dept_b = uuid.uuid4()
    cc_a = uuid.uuid4()
    cc_b = uuid.uuid4()
    db_session.add_all([
        CostCenter(id=cc_a, code="SCOPE-CC-A", name="Scope CC A", is_active=True,
                   department_id=dept_a),
        CostCenter(id=cc_b, code="SCOPE-CC-B", name="Scope CC B", is_active=True,
                   department_id=dept_b),
    ])
    item = uuid.uuid4()
    db_session.add(BudgetAccount(id=item, code="CRM003", name="IT General Fee", is_active=True))
    await db_session.flush()

    await db_session.execute(sa.text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS department_id uuid"))
    mgr_uid = uuid.uuid4()
    await db_session.execute(sa.text(
        "INSERT INTO users (id, email, full_name, department_id) "
        "VALUES (CAST(:id AS uuid), 'dept.manager@test.local', 'Dept Manager', CAST(:dept AS uuid))"),
        {"id": str(mgr_uid), "dept": str(dept_a)})

    async def _line(cc_id, amount, period="2026-06"):
        occurred = datetime(2026, int(period[5:7]), 15, tzinfo=timezone.utc)
        await emit_event(
            db_session, source_service="finance", source_doc_type="ap_invoice",
            source_doc_id=uuid.uuid4(), source_doc_number="AP-SCOPE", event_type="accrual",
            occurred_at=occurred, prepared_by=uuid.uuid4(),
            lines=[
                {"line_role": "purchase_expense", "account_code": "5101",
                 "debit": Decimal(amount), "currency": "CAD", "cost_center_id": cc_id,
                 "aux": {"income_expense_item": {"value_id": item, "value_text": "X"}}},
                {"line_role": "accounts_payable", "account_code": "2000",
                 "credit": Decimal(amount), "currency": "CAD"},
            ])

    await _line(cc_a, "100.00")
    await _line(cc_b, "40.00")
    await jv_crud.backfill_posted_jvs(db_session)   # -> status=posted

    return {"cc_a": cc_a, "cc_b": cc_b, "income_expense_item_id": item,
            "dept_a": dept_a, "dept_b": dept_b, "mgr_uid": mgr_uid}
