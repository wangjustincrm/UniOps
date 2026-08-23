"""Invoice-attachment READ visibility — regression cover for the b345e09 fallout.

b345e09 ("enforce ownership on attachment serve/delete/list") gated every read
on `uploaded_by`, which is the OA expense-claim ownership model. But
`invoice_attachments` is a SHARED table: in production 7272 of its 7275 rows
carry `invoice_source='epms'` — procurement invoices whose files every invoice
viewer is meant to open (AP uploads them; requesters/dept admins read them).
The uploader rule silently emptied the EPMS Invoice Detail "Attachments" panel
for everyone outside AP/finance — a list filter, so the UI showed
"No attachment" with no error to trace.

These tests pin the corrected split:
  * EPMS-source reads follow the Access Control matrix key EPMS's own invoice
    endpoints gate on (`view_invoice`), resolved across the caller's FULL role
    set (primary ∪ user_roles) — the previous check read only the JWT's primary
    role, the same class of bug as the dept_admin drift.
  * OA-source reads stay owner-scoped (personal reimbursement receipts).
  * DELETE stays tight for both — being able to read a file must not imply
    being able to remove it.
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import _client, _make_token
from app.models.invoice_attachment import InvoiceAttachment


# ── Matrix fixture ────────────────────────────────────────────────────────────
# role_defs / role_permissions / role_permission_locks are identity-owned (same
# physical DB in prod, no ORM model here — app/core/authz_matrix.py reads them
# with raw SQL). conftest's test_engine shadows them for the whole session; this
# fixture only gives each test a clean matrix to seed.

@pytest.fixture
async def matrix_db(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text("DELETE FROM role_permissions"))
        await s.execute(text("DELETE FROM role_permission_locks"))
        await s.execute(text("DELETE FROM role_defs"))
        await s.execute(text("DELETE FROM user_roles"))
        await s.commit()
        yield s


async def _grant(db, role_code: str, key: str, *, locked: bool = False) -> None:
    await db.execute(text(
        "INSERT INTO role_defs (code, is_active) VALUES (:c, true) "
        "ON CONFLICT (code) DO NOTHING"), {"c": role_code})
    table = "role_permission_locks" if locked else "role_permissions"
    await db.execute(text(
        f"INSERT INTO {table} (role_code, permission_key) VALUES (:c, :k) "
        "ON CONFLICT DO NOTHING"), {"c": role_code, "k": key})
    await db.commit()


async def _assign_role(db, user_id: uuid.UUID, role_code: str) -> None:
    await db.execute(text(
        "INSERT INTO role_defs (code, is_active) VALUES (:c, true) "
        "ON CONFLICT (code) DO NOTHING"), {"c": role_code})
    await db.execute(text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, :c) "
        "ON CONFLICT DO NOTHING"), {"u": str(user_id), "c": role_code})
    await db.commit()


async def _seed_attachment(test_engine, uploaded_by: uuid.UUID, source: str) -> uuid.UUID:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    aid = uuid.uuid4()
    async with factory() as s:
        s.add(InvoiceAttachment(id=aid, invoice_id=uuid.uuid4(), invoice_source=source,
                                file_name="inv.pdf", content_type="application/pdf",
                                file_size_bytes=10, storage_key=uuid.uuid4(),
                                uploaded_by=uploaded_by))
        await s.commit()
    return aid


async def _invoice_id_of(test_engine, aid: uuid.UUID) -> uuid.UUID:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        return (await s.get(InvoiceAttachment, aid)).invoice_id


# ── EPMS source: the reported regression ──────────────────────────────────────

@pytest.mark.asyncio
async def test_epms_attachment_listed_for_view_invoice_holder(test_engine, matrix_db):
    """AP uploads the invoice PDF; a requester who holds view_invoice sees it."""
    viewer = uuid.uuid4()
    aid = await _seed_attachment(test_engine, uploaded_by=uuid.uuid4(), source="epms")
    inv_id = await _invoice_id_of(test_engine, aid)
    await _grant(matrix_db, "requester", "view_invoice")

    async with _client(_make_token("requester", str(viewer))) as c:
        r = await c.get(f"/api/v1/invoice-attachments?invoice_id={inv_id}&invoice_source=epms")
    assert r.status_code == 200
    assert [a["id"] for a in r.json()] == [str(aid)]


@pytest.mark.asyncio
async def test_epms_attachment_served_for_view_invoice_holder(test_engine, matrix_db):
    aid = await _seed_attachment(test_engine, uploaded_by=uuid.uuid4(), source="epms")
    await _grant(matrix_db, "requester", "view_invoice")

    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.get(f"/api/v1/invoice-attachments/{aid}/file")
    # Passes authz; may 410/502 because file-api is unreachable in tests — never 403.
    assert r.status_code in (200, 410, 502)


@pytest.mark.asyncio
async def test_epms_attachment_visible_via_additional_role(test_engine, matrix_db):
    """Kris's shape: primary role grants nothing, the ADDITIONAL role does.

    The old check read only the JWT's primary role, so an additional-role grant
    was invisible to it.
    """
    viewer = uuid.uuid4()
    aid = await _seed_attachment(test_engine, uploaded_by=uuid.uuid4(), source="epms")
    inv_id = await _invoice_id_of(test_engine, aid)
    await _grant(matrix_db, "dept_admin", "view_invoice")
    await _assign_role(matrix_db, viewer, "dept_admin")

    async with _client(_make_token("requester", str(viewer))) as c:
        r = await c.get(f"/api/v1/invoice-attachments?invoice_id={inv_id}&invoice_source=epms")
    assert r.status_code == 200
    assert [a["id"] for a in r.json()] == [str(aid)]


@pytest.mark.asyncio
async def test_epms_attachment_visible_via_locked_grant(test_engine, matrix_db):
    """Effective matrix = granted UNION locked (a lock is a forced grant)."""
    aid = await _seed_attachment(test_engine, uploaded_by=uuid.uuid4(), source="epms")
    inv_id = await _invoice_id_of(test_engine, aid)
    await _grant(matrix_db, "requester", "view_invoice", locked=True)

    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.get(f"/api/v1/invoice-attachments?invoice_id={inv_id}&invoice_source=epms")
    assert r.status_code == 200
    assert len(r.json()) == 1


@pytest.mark.asyncio
async def test_epms_attachment_hidden_without_view_invoice(test_engine, matrix_db):
    """The IDOR fix's intent survives: no view_invoice → no read."""
    aid = await _seed_attachment(test_engine, uploaded_by=uuid.uuid4(), source="epms")
    inv_id = await _invoice_id_of(test_engine, aid)

    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        listed = await c.get(f"/api/v1/invoice-attachments?invoice_id={inv_id}&invoice_source=epms")
        served = await c.get(f"/api/v1/invoice-attachments/{aid}/file")
    assert listed.status_code == 200 and listed.json() == []
    assert served.status_code == 403


# ── OA source: privacy model unchanged ────────────────────────────────────────

@pytest.mark.asyncio
async def test_oa_attachment_hidden_from_unrelated_view_invoice_holder(test_engine, matrix_db):
    """view_invoice must NOT unlock someone else's expense-claim receipt."""
    aid = await _seed_attachment(test_engine, uploaded_by=uuid.uuid4(), source="oa")
    inv_id = await _invoice_id_of(test_engine, aid)
    await _grant(matrix_db, "requester", "view_invoice")

    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        listed = await c.get(f"/api/v1/invoice-attachments?invoice_id={inv_id}&invoice_source=oa")
        served = await c.get(f"/api/v1/invoice-attachments/{aid}/file")
    assert listed.status_code == 200 and listed.json() == []
    assert served.status_code == 403


@pytest.mark.asyncio
async def test_oa_attachment_visible_to_payer_via_additional_role(test_engine, matrix_db):
    """ap_clerk held as an ADDITIONAL role now counts, as it always should have."""
    viewer = uuid.uuid4()
    aid = await _seed_attachment(test_engine, uploaded_by=uuid.uuid4(), source="oa")
    inv_id = await _invoice_id_of(test_engine, aid)
    await _assign_role(matrix_db, viewer, "ap_clerk")

    async with _client(_make_token("requester", str(viewer))) as c:
        r = await c.get(f"/api/v1/invoice-attachments?invoice_id={inv_id}&invoice_source=oa")
    assert r.status_code == 200
    assert len(r.json()) == 1


# ── Deletion stays tight ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_forbidden_for_read_only_viewer(test_engine, matrix_db):
    """Reading an EPMS invoice's file must not confer the right to delete it."""
    aid = await _seed_attachment(test_engine, uploaded_by=uuid.uuid4(), source="epms")
    await _grant(matrix_db, "requester", "view_invoice")

    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.delete(f"/api/v1/invoice-attachments/{aid}")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_allowed_for_payer_via_additional_role(test_engine, matrix_db):
    deleter = uuid.uuid4()
    aid = await _seed_attachment(test_engine, uploaded_by=uuid.uuid4(), source="epms")
    await _assign_role(matrix_db, deleter, "finance_manager")

    async with _client(_make_token("requester", str(deleter))) as c:
        r = await c.delete(f"/api/v1/invoice-attachments/{aid}")
    # Passes authz; the file-api call may fail in tests — never 403.
    assert r.status_code != 403
