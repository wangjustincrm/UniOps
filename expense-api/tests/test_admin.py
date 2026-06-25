"""Tests for the OA data-maintenance admin module (expense-api)."""
import uuid
from datetime import date
from decimal import Decimal

import pytest


# ── 1. Registry unit tests ────────────────────────────────────────────────────

def test_registry_has_oa_entities():
    from app.admin.registry import REGISTRY
    assert set(REGISTRY.keys()) == {"expense_claim", "expense_invoice", "pa"}
    for spec in REGISTRY.values():
        assert spec.system == "oa"
        assert spec.schema.fields, f"{spec.schema.key} has no fields"
        assert callable(spec.cascade_preview)
        assert callable(spec.cascade_delete)


# ── 2. Endpoint smoke tests ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_oa_admin_endpoints(admin_client):
    """GET /entities lists all 3 OA entities; GET /expense_claim returns paginated list."""
    r = await admin_client.get("/api/v1/admin/entities")
    assert r.status_code == 200, r.text
    entities = r.json()
    keys_with_system = {e["key"]: e["system"] for e in entities}
    assert "expense_claim" in keys_with_system
    assert "expense_invoice" in keys_with_system
    assert "pa" in keys_with_system
    # all three belong to system "oa"
    assert keys_with_system["expense_claim"] == "oa"
    assert keys_with_system["expense_invoice"] == "oa"
    assert keys_with_system["pa"] == "oa"

    r2 = await admin_client.get("/api/v1/admin/expense_claim")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert "items" in body
    assert "total" in body


@pytest.mark.asyncio
async def test_admin_endpoints_require_system_admin(client):
    """Unauthenticated requests must be rejected."""
    r = await client.get("/api/v1/admin/entities")
    assert r.status_code in (401, 403)


# ── 3. Cascade delete test ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_expense_claim_cascade_delete(test_engine):
    """Insert a claim + a TaskMirror row, delete via service, verify audit log."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy import select

    from app.models.expense import ExpenseClaim
    from app.models.task_mirror import TaskMirror
    from app.models.admin_audit_log import AdminAuditLog
    from app.admin import service

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    claim_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    # Insert the claim and a TaskMirror row referencing it
    async with factory() as db:
        db.add(ExpenseClaim(
            id=claim_id,
            claim_number=f"EXP-T-{claim_id.hex[:6]}",
            claim_type="EXP",
            employee_id=uuid.uuid4(),
            employee_name="Test Employee",
            department_name="Finance",
            submission_date=date(2025, 6, 1),
            currency="CAD",
            total_amount=Decimal("100.00"),
            tax_amount=Decimal("13.00"),
            net_amount=Decimal("87.00"),
            status="draft",
            created_by=actor_id,
        ))
        await db.commit()

    async with factory() as db:
        db.add(TaskMirror(
            id=uuid.uuid4(),
            document_id=claim_id,
            document_type="expense_claim",
            type="approve_expense",
            assigned_role="finance_bp",
            is_completed=False,
        ))
        await db.commit()

    # Run cascade delete via service
    async with factory() as db:
        summary = await service.delete_record(
            db, "expense_claim", claim_id,
            actor_id=actor_id, actor_email="admin@test.com",
        )
        await db.commit()

    # Verify counts
    assert summary["expense_claims"] == 1
    assert summary["tasks"] >= 1

    # Verify claim is gone
    async with factory() as db:
        gone = (await db.execute(
            select(ExpenseClaim).where(ExpenseClaim.id == claim_id)
        )).scalar_one_or_none()
        assert gone is None

    # Verify audit log
    async with factory() as db:
        logs = (await db.execute(
            select(AdminAuditLog).where(
                AdminAuditLog.action == "delete",
                AdminAuditLog.system == "oa",
                AdminAuditLog.record_id == claim_id,
            )
        )).scalars().all()
        assert len(logs) == 1
        log = logs[0]
        assert log.cascade_summary["expense_claims"] == 1
        assert log.cascade_summary["tasks"] >= 1
