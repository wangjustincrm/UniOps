"""Data-maintenance admin module for finance-api (delete-only payment_batch)."""
import uuid
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.admin import service
from app.admin.registry import REGISTRY
from app.core.config import settings
from app.db.base import get_db
from app.main import app
from app.models.admin_audit_log import AdminAuditLog
from app.models.payment_batch import PaymentBatch, PaymentBatchLine


def _token(role="system_admin"):
    return jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _h(role="system_admin"):
    return {"Authorization": f"Bearer {_token(role)}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _batch(status="draft") -> PaymentBatch:
    return PaymentBatch(
        batch_number=f"BATCH-{uuid.uuid4().hex[:8]}", batch_date=date.today(),
        status=status, currency="CAD", total=Decimal("100.00"),
        payment_method="bank_transfer", created_by=uuid.uuid4(),
    )


# ── 1. registry ───────────────────────────────────────────────────────────────

def test_registry_payment_batch_delete_only():
    assert "payment_batch" in REGISTRY
    spec = REGISTRY["payment_batch"]
    assert spec.schema.allow_edit is False
    assert spec.schema.editable_field_names() == set()
    assert callable(spec.cascade_preview)
    assert callable(spec.cascade_delete)


# ── 2. endpoints + gating ───────────────────────────────────────────────────────

async def test_finance_admin_endpoints(client, db_session):
    r = await client.get("/finance/v1/admin/entities", headers=_h())
    assert r.status_code == 200, r.text
    entities = r.json()
    pb = next(e for e in entities if e["key"] == "payment_batch")
    assert pb["system"] == "finance"

    r2 = await client.get("/finance/v1/admin/payment_batch", headers=_h())
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert "items" in body and "total" in body

    # non-system_admin is rejected
    r3 = await client.get("/finance/v1/admin/entities", headers=_h("ap_clerk"))
    assert r3.status_code == 403


# ── 3. cascade delete ───────────────────────────────────────────────────────────

async def test_payment_batch_cascade_delete(db_session):
    batch = _batch()
    db_session.add(batch)
    await db_session.flush()
    line = PaymentBatchLine(
        batch_id=batch.id, doc_kind="pa", doc_id=uuid.uuid4(),
        doc_number="PA-1", amount=Decimal("100.00"), status="pending",
    )
    db_session.add(line)
    await db_session.flush()
    batch_id, line_id = batch.id, line.id

    summary = await service.delete_record(
        db_session, "payment_batch", batch_id,
        actor_id=uuid.uuid4(), actor_email="admin@x.com",
    )
    await db_session.flush()

    assert summary["payment_batches"] == 1
    assert summary["payment_batch_lines"] == 1

    assert (await db_session.execute(
        select(PaymentBatch).where(PaymentBatch.id == batch_id))).scalar_one_or_none() is None
    assert (await db_session.execute(
        select(PaymentBatchLine).where(PaymentBatchLine.id == line_id))).scalar_one_or_none() is None

    audit = (await db_session.execute(
        select(AdminAuditLog).where(AdminAuditLog.record_id == batch_id))).scalar_one()
    assert audit.action == "delete"
    assert audit.system == "finance"
    assert audit.cascade_summary["payment_batches"] == 1
