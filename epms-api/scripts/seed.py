"""
Seed script — populates the database with initial master data and a system admin user.

Usage:
    python -m scripts.seed
    python -m scripts.seed --env production   # reads .env.production
"""
import asyncio
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — register all models
from app.core.config import settings
from app.core.security import hash_password
# NOTE: Budget seeding moved to budget-api. epms seed no longer creates L1/Account rows.
from app.models.cost_center import CostCenter
from app.models.department import Department
from app.models.user import User

# ── Seed data ──────────────────────────────────────────────────────────────────

ADMIN = {
    "email": "admin@epms.local",
    "password": "Admin@epms2026",
    "full_name": "System Administrator",
    "role": "system_admin",
}

DEPARTMENTS = [
    {"code": "MKT", "name": "Marketing"},
    {"code": "IT",  "name": "Information Technology"},
    {"code": "FIN", "name": "Finance"},
    {"code": "HR",  "name": "Human Resources"},
    {"code": "OPS", "name": "Operations"},
    {"code": "PRC", "name": "Procurement"},
]

COST_CENTERS = [
    {"code": "CC-MKT-01", "name": "Brand & Campaigns",   "dept_code": "MKT"},
    {"code": "CC-MKT-02", "name": "CRM & Digital",       "dept_code": "MKT"},
    {"code": "CC-IT-01",  "name": "Infrastructure",      "dept_code": "IT"},
    {"code": "CC-IT-02",  "name": "Software Development","dept_code": "IT"},
    {"code": "CC-FIN-01", "name": "Financial Control",   "dept_code": "FIN"},
    {"code": "CC-HR-01",  "name": "Talent Acquisition",  "dept_code": "HR"},
    {"code": "CC-OPS-01", "name": "Logistics",           "dept_code": "OPS"},
    {"code": "CC-PRC-01", "name": "Sourcing",            "dept_code": "PRC"},
]

BUDGET_L1 = [
    {"code": "L1-MKT-01", "name": "Advertising",       "cc_code": "CC-MKT-01"},
    {"code": "L1-MKT-02", "name": "Events & Promo",    "cc_code": "CC-MKT-02"},
    {"code": "L1-IT-01",  "name": "Hardware",          "cc_code": "CC-IT-01"},
    {"code": "L1-IT-02",  "name": "Software Licenses", "cc_code": "CC-IT-02"},
    {"code": "L1-FIN-01", "name": "Audit & Consulting","cc_code": "CC-FIN-01"},
    {"code": "L1-OPS-01", "name": "Freight & Shipping","cc_code": "CC-OPS-01"},
]

BUDGET_ACCOUNTS = [
    # CC-MKT-01 / Advertising
    {"code": "BA-MKT-001", "name": "Digital Ads",      "l1_code": "L1-MKT-01", "annual": 300_000, "committed": 50_000, "spent": 30_000},
    {"code": "BA-MKT-002", "name": "Print & Outdoor",  "l1_code": "L1-MKT-01", "annual": 100_000, "committed": 10_000, "spent":  5_000},
    # CC-MKT-02 / Events
    {"code": "BA-MKT-003", "name": "Trade Shows",      "l1_code": "L1-MKT-02", "annual": 200_000, "committed": 80_000, "spent": 60_000},
    # CC-IT-01 / Hardware
    {"code": "BA-IT-001",  "name": "Servers",          "l1_code": "L1-IT-01",  "annual": 500_000, "committed": 120_000,"spent": 80_000},
    {"code": "BA-IT-002",  "name": "Laptops",          "l1_code": "L1-IT-01",  "annual": 200_000, "committed":  40_000,"spent": 35_000},
    # CC-IT-02 / Software
    {"code": "BA-IT-003",  "name": "SaaS Subscriptions","l1_code":"L1-IT-02",  "annual": 150_000, "committed":  60_000,"spent": 55_000},
    # CC-FIN-01 / Audit
    {"code": "BA-FIN-001", "name": "External Audit",   "l1_code": "L1-FIN-01", "annual": 250_000, "committed":  80_000,"spent": 75_000},
    # CC-OPS-01 / Freight
    {"code": "BA-OPS-001", "name": "Air Freight",      "l1_code": "L1-OPS-01", "annual": 400_000, "committed": 100_000,"spent": 90_000},
    {"code": "BA-OPS-002", "name": "Sea Freight",      "l1_code": "L1-OPS-01", "annual": 600_000, "committed": 150_000,"spent":120_000},
]


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _upsert(db: AsyncSession, model_cls, lookup_field: str, lookup_val, **kwargs):
    """Get or create a model instance."""
    from sqlalchemy import select
    result = await db.execute(
        select(model_cls).where(getattr(model_cls, lookup_field) == lookup_val)
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        obj = model_cls(**{lookup_field: lookup_val}, **kwargs)
        db.add(obj)
        await db.flush()
        print(f"  [+] {model_cls.__name__}: {lookup_val}")
    else:
        print(f"  [=] {model_cls.__name__}: {lookup_val} (already exists)")
    return obj


# ── Main ───────────────────────────────────────────────────────────────────────

async def seed():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db:
        print("\n── Admin user ───────────────────────────────────────")
        await _upsert(
            db, User, "email", ADMIN["email"],
            hashed_password=hash_password(ADMIN["password"]),
            full_name=ADMIN["full_name"],
            role=ADMIN["role"],
        )

        print("\n── Departments ──────────────────────────────────────")
        dept_map: dict[str, Department] = {}
        for d in DEPARTMENTS:
            dept = await _upsert(db, Department, "code", d["code"], name=d["name"])
            dept_map[d["code"]] = dept

        print("\n── Cost Centers ─────────────────────────────────────")
        cc_map: dict[str, CostCenter] = {}
        for c in COST_CENTERS:
            dept = dept_map[c["dept_code"]]
            cc = await _upsert(db, CostCenter, "code", c["code"], name=c["name"], department_id=dept.id)
            cc_map[c["code"]] = cc

        # Budget L1 / Accounts are seeded by budget-api (see budget-api/alembic
        # migrations + budget-api/scripts/seed.py if you add one). epms-api no
        # longer owns budget tables.
        await db.commit()

    await engine.dispose()
    print("\n✓ Seed complete.\n")


if __name__ == "__main__":
    asyncio.run(seed())
