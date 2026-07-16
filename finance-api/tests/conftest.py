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
        BudgetAccount, CompanyConfig, CostCenter, Department, ErpSupplier, ExpenseApprovalEvent, ExpenseClaim,
        ExpenseInvoice, ExpenseLineItem, ExpenseTripItem, Invoice, InvoicePoAllocation, InvoiceTaxLine, PurchaseRequest, SodRule, Task, User,
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
    ])
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
        )):
            conn.execute(sa.text(
                "INSERT INTO role_defs (code, label, sort, is_active) VALUES (:c, :c, :s, true)"),
                {"c": code, "s": i})
        for i, (key, module) in enumerate((
            ("finance.coa.manage", "finance"),
            ("finance.period.close", "finance"),
            ("finance.jv.post", "finance"),
        )):
            conn.execute(sa.text(
                "INSERT INTO permission_defs (key, module, label, sort) VALUES (:k, :m, :k, :s)"),
                {"k": key, "m": module, "s": i})
        _phase2_defaults = {
            "finance.coa.manage": ("system_admin", "finance_manager"),
            "finance.period.close": ("system_admin", "finance_manager"),
            "finance.jv.post": ("system_admin", "finance_manager", "finance_bp"),
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
