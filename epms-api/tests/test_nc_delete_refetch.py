"""Deleting a mirrored PO in Data Maintenance must not lose the NC order forever.

The incremental sync filters on ``changed_at >= watermark``. Deleting the UniOps
row changes NOTHING in NC, so the order's change time stays where it was — behind
a watermark the sync has long since passed — and no run ever reads that order
again. It does not come back on the next run, or any run.

That is how PO-058-2607-02 disappeared: three mirror rows were deleted to clear
up a duplicate, and the live ERP order went with them, with no error, no task and
no way back short of an operator knowing that ``rewind_nc_purchase_watermark``
exists.

The number-reclaim half of that incident is in test_nc_rebuilt_po_number.py.
"""
import uuid
from decimal import Decimal

import pytest

from app.services.nc_purchase_sync import reader, service


@pytest.fixture
def clean_refetch_requests(test_engine):
    """The Data Maintenance tests below COMMIT, so their request rows outlive the
    test. A leftover pending request is read by every later sync test's
    ``pending_refetch_pks`` — harmless, but it makes those tests depend on
    whether this file ran first."""
    import psycopg2
    from tests.conftest import _test_pg_dsn

    def _wipe():
        con = psycopg2.connect(_test_pg_dsn()); con.autocommit = True
        con.cursor().execute(
            "delete from nc_purchase_refetch_requests where nc_source_pk like 'DEL-%' "
            "or nc_source_pk like 'PREV-%'")
        con.close()

    _wipe()
    yield
    _wipe()



# ── 2. a deleted mirror has to be re-readable ────────────────────────────────

def test_the_reader_fetches_a_requested_order_the_watermark_has_passed(monkeypatch):
    """The whole point: the order has NOT changed in NC, so every watermark
    filter excludes it. Only a standing request can put it back in the batch."""
    seen = {}

    class _Cur:
        description = [("PK_ORDER",)]

        def execute(self, sql, binds=None):
            low = sql.lower()
            if "pk_order, o.vbillcode" in low or "o.pk_order, o.vbillcode" in low:
                self.description = [("PK_ORDER",), ("VBILLCODE",)]
                self._rows = [("WANTED", "PO-GONE-01"), ("OTHER", "PO-OTHER-01")]
                return
            if "po_order_b" in low:
                seen["line_binds"] = dict(binds or {})
                self.description = [("PK_ORDER_B",), ("PK_ORDER",)]
            elif "bd_material" in low:
                self.description = [("PK_MATERIAL",), ("CODE",), ("NAME",), ("ENAME",)]
            elif "bd_" in low:
                self.description = [("A",), ("B",)]
            else:
                self.description = [("PK_ORDER",), ("CHANGED_AT",)]
            self._rows = []

        def fetchall(self):
            return self._rows

    class _Con:
        def cursor(self): return _Cur()
        def close(self): pass

    cur = _Cur()
    monkeypatch.setattr(reader, "_connect", lambda: type("C", (), {
        "cursor": lambda _s: cur, "close": lambda _s: None})())

    reader.fetch_nc("2000-01-01 00:00:00", "2026-09-08 00:00:00",
                    refetch_pks=["WANTED"])

    assert "WANTED" in (seen.get("line_binds") or {}).values(), \
        "the requested order never reached the batch"


def test_a_request_for_an_order_nc_no_longer_lists_is_not_fetched(monkeypatch):
    """A request is not a licence to read anything. NC may have deleted the order
    for real between the DM delete and the next run, and resurrecting a
    soft-deleted order would put a document back that the ERP has retired."""
    seen = {}

    class _Cur:
        description = [("PK_ORDER",)]

        def execute(self, sql, binds=None):
            low = sql.lower()
            if "o.pk_order, o.vbillcode" in low:
                self.description = [("PK_ORDER",), ("VBILLCODE",)]
                self._rows = [("STILL-LISTED", "PO-OTHER-01")]
                return
            if "po_order_b" in low:
                seen["line_binds"] = dict(binds or {})
                self.description = [("PK_ORDER_B",), ("PK_ORDER",)]
            elif "bd_material" in low:
                self.description = [("PK_MATERIAL",), ("CODE",), ("NAME",), ("ENAME",)]
            elif "bd_" in low:
                self.description = [("A",), ("B",)]
            else:
                self.description = [("PK_ORDER",), ("CHANGED_AT",)]
            self._rows = []

        def fetchall(self):
            return self._rows

    cur = _Cur()
    monkeypatch.setattr(reader, "_connect", lambda: type("C", (), {
        "cursor": lambda _s: cur, "close": lambda _s: None})())

    reader.fetch_nc("2000-01-01 00:00:00", "2026-09-08 00:00:00",
                    refetch_pks=["DELETED-IN-NC"])

    assert "DELETED-IN-NC" not in (seen.get("line_binds") or {}).values()


def test_a_full_reload_ignores_requests(monkeypatch):
    """Full mode reads everything in scope, so a request has nothing to add —
    and must not smuggle an out-of-scope pk into the batch."""
    captured = {}

    class _Cur:
        description = [("PK_ORDER",)]

        def execute(self, sql, binds=None):
            low = sql.lower()
            if "po_order_b" in low:
                captured["line_binds"] = dict(binds or {})
            self.description = ([("PK_MATERIAL",), ("CODE",), ("NAME",), ("ENAME",)]
                                if "bd_material" in low else [("A",), ("B",)])
            self._rows = []

        def fetchall(self):
            return self._rows

    cur = _Cur()
    monkeypatch.setattr(reader, "_connect", lambda: type("C", (), {
        "cursor": lambda _s: cur, "close": lambda _s: None})())

    reader.fetch_nc("2000-01-01 00:00:00", None, refetch_pks=["WANTED"])

    assert "WANTED" not in (captured.get("line_binds") or {}).values()


# ── 3. settling the requests ─────────────────────────────────────────────────

def _request(cur, nc_pk, number="PO-REQ-01"):
    cur.execute("insert into nc_purchase_refetch_requests "
                "(id, nc_source_pk, po_number, reason, created_at, updated_at) "
                "values (%s,%s,%s,'test',now(),now())",
                (uuid.uuid4(), nc_pk, number))


def _state(cur, nc_pk):
    cur.execute("select fulfilled_at is not null, outcome from "
                "nc_purchase_refetch_requests where nc_source_pk=%s", (nc_pk,))
    return cur.fetchone()


def test_a_request_is_settled_once_the_order_is_mirrored_again(pg_cur):
    _request(pg_cur, "S-OK")

    service.settle_refetch_requests(pg_cur, ["S-OK"], {"S-OK"}, {"S-OK"})

    assert _state(pg_cur, "S-OK") == (True, "mirrored")


def test_a_request_for_an_order_gone_from_nc_stops_retrying(pg_cur):
    """Otherwise it re-enters every run forever, for an order that will never
    come back."""
    _request(pg_cur, "S-GONE")

    service.settle_refetch_requests(pg_cur, ["S-GONE"], set(), {"SOMETHING-ELSE"})

    assert _state(pg_cur, "S-GONE") == (True, "gone_from_nc")


def test_a_request_whose_order_was_skipped_stays_pending(pg_cur):
    """NC still lists the order but the run could not import it — a missing
    vendor, a number it could not free. The request is the only thing that will
    bring the order back once somebody fixes the cause, so it must survive."""
    _request(pg_cur, "S-SKIPPED")

    service.settle_refetch_requests(pg_cur, ["S-SKIPPED"], set(), {"S-SKIPPED"})

    assert _state(pg_cur, "S-SKIPPED") == (False, None)


def test_pending_requests_exclude_settled_ones(pg_cur):
    _request(pg_cur, "S-PENDING")
    _request(pg_cur, "S-DONE")
    service.settle_refetch_requests(pg_cur, ["S-DONE"], {"S-DONE"}, {"S-DONE"})

    pending = service.pending_refetch_pks(pg_cur)

    assert "S-PENDING" in pending and "S-DONE" not in pending


# ── 4. the Data Maintenance delete raises the request ────────────────────────

@pytest.mark.asyncio
async def test_deleting_a_mirrored_po_queues_it_for_re_import(test_engine, clean_refetch_requests):
    """The delete itself is what has to leave the trail. An admin cleaning up
    duplicates cannot be expected to know that the sync will never look at the
    order again."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.admin import service as admin_service
    from app.models.nc_purchase_sync import NcPurchaseRefetchRequest
    from app.models.po import PurchaseOrder
    from app.models.user import User
    from app.models.vendor import Vendor

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    vendor_id, user_id, po_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        db.add(Vendor(id=vendor_id, code=f"V-{vendor_id.hex[:8]}", name="NC Vendor",
                      category="supplier", contact_name="A", contact_email="a@example.com"))
        db.add(User(id=user_id, email=f"u-{user_id.hex[:8]}@example.com",
                    hashed_password="x", full_name="U", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseOrder(id=po_id, number="PO-DEL-01", title="t", type=1,
                             status="nc_pending", currency="CAD",
                             subtotal=Decimal("1"), tax_rate=Decimal("0"),
                             tax_amount=Decimal("0"), total=Decimal("1"),
                             vendor_id=vendor_id, vendor_name="NC Vendor",
                             created_by=user_id, source="nc",
                             nc_source_pk="DEL-PK-1"))
        await db.commit()

    async with factory() as db:
        summary = await admin_service.delete_record(
            db, "po", po_id, actor_id=user_id, actor_email="admin@example.com")
        await db.commit()

    assert summary.get("nc_orders_queued_for_resync") == 1
    async with factory() as db:
        req = (await db.execute(select(NcPurchaseRefetchRequest).where(
            NcPurchaseRefetchRequest.nc_source_pk == "DEL-PK-1"))).scalar_one()
        assert req.fulfilled_at is None
        assert req.po_number == "PO-DEL-01"


@pytest.mark.asyncio
async def test_deleting_a_native_po_queues_nothing(test_engine, clean_refetch_requests):
    """A UniOps-native or PMS-imported PO has no NC order behind it. Queueing one
    would put a pk in front of the sync that NC has never heard of."""
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.admin import service as admin_service
    from app.models.nc_purchase_sync import NcPurchaseRefetchRequest
    from app.models.po import PurchaseOrder
    from app.models.user import User
    from app.models.vendor import Vendor

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    vendor_id, user_id, po_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        db.add(Vendor(id=vendor_id, code=f"V-{vendor_id.hex[:8]}", name="Native Vendor",
                      category="supplier", contact_name="A", contact_email="b@example.com"))
        db.add(User(id=user_id, email=f"u-{user_id.hex[:8]}@example.com",
                    hashed_password="x", full_name="U", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseOrder(id=po_id, number="PO-NATIVE-01", title="t", type=1,
                             status="draft", currency="CAD", subtotal=Decimal("1"),
                             tax_rate=Decimal("0"), tax_amount=Decimal("0"),
                             total=Decimal("1"), vendor_id=vendor_id,
                             vendor_name="Native Vendor", created_by=user_id))
        await db.commit()

    async with factory() as db:
        before = (await db.execute(select(func.count()).select_from(
            NcPurchaseRefetchRequest))).scalar_one()
        summary = await admin_service.delete_record(
            db, "po", po_id, actor_id=user_id, actor_email="admin@example.com")
        await db.commit()

    assert "nc_orders_queued_for_resync" not in summary
    async with factory() as db:
        after = (await db.execute(select(func.count()).select_from(
            NcPurchaseRefetchRequest))).scalar_one()
    assert after == before


@pytest.mark.asyncio
async def test_the_delete_preview_names_the_re_import(test_engine, clean_refetch_requests):
    """The confirm dialog is built from the preview, so the admin has to see it
    BEFORE pressing delete — not discover it in the audit log afterwards."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.admin import service as admin_service
    from app.models.po import PurchaseOrder
    from app.models.user import User
    from app.models.vendor import Vendor

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    vendor_id, user_id, po_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        db.add(Vendor(id=vendor_id, code=f"V-{vendor_id.hex[:8]}", name="Prev Vendor",
                      category="supplier", contact_name="A", contact_email="c@example.com"))
        db.add(User(id=user_id, email=f"u-{user_id.hex[:8]}@example.com",
                    hashed_password="x", full_name="U", role="requester"))
        await db.commit()
    async with factory() as db:
        db.add(PurchaseOrder(id=po_id, number="PO-PREV-01", title="t", type=1,
                             status="nc_pending", currency="CAD", subtotal=Decimal("1"),
                             tax_rate=Decimal("0"), tax_amount=Decimal("0"),
                             total=Decimal("1"), vendor_id=vendor_id,
                             vendor_name="Prev Vendor", created_by=user_id,
                             source="nc", nc_source_pk="PREV-PK-1"))
        await db.commit()

    async with factory() as db:
        preview = await admin_service.delete_preview(db, "po", po_id)

    assert preview.get("nc_orders_queued_for_resync") == 1


@pytest.mark.asyncio
async def test_re_deleting_an_order_reopens_its_request_instead_of_duplicating(
    test_engine, clean_refetch_requests,
):
    """An order can be deleted, brought back by the next sync, and deleted again.
    ``nc_source_pk`` is UNIQUE, so the second delete has to REOPEN the settled row
    — queueing a second one would raise and take the whole delete down with it.
    """
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.admin.nc_refetch import request_nc_refetch
    from app.models.nc_purchase_sync import NcPurchaseRefetchRequest

    class _Po:
        """Only the three attributes request_nc_refetch reads. The real PO row is
        deleted in the same transaction as the request, so building one here
        would test the fixture rather than the function."""
        source, nc_source_pk = "nc", "DEL-REOPEN"
        number = "PO-REOPEN-01"

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await request_nc_refetch(db, _Po(), reason="first delete")
        await db.commit()
        await db.execute(NcPurchaseRefetchRequest.__table__.update().where(
            NcPurchaseRefetchRequest.nc_source_pk == "DEL-REOPEN"
        ).values(fulfilled_at=func.now(), outcome="mirrored"))
        await db.commit()

    async with factory() as db:
        summary = await request_nc_refetch(db, _Po(), reason="second delete")
        await db.commit()

    assert summary == {"nc_orders_queued_for_resync": 1}
    async with factory() as db:
        rows = (await db.execute(select(NcPurchaseRefetchRequest).where(
            NcPurchaseRefetchRequest.nc_source_pk == "DEL-REOPEN"))).scalars().all()
    assert len(rows) == 1, "a duplicate request was queued"
    assert rows[0].fulfilled_at is None, "the request did not reopen"
    assert rows[0].reason == "second delete"
