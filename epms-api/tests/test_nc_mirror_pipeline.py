"""Task 8 capstone: a PO+GR mirrored from NC by ``writer.upsert`` (NOT the EPMS
GR-create workflow) must satisfy the REAL 3-way receipt gate once a UniOps-uploaded
invoice is matched against the mirrored PO.

This is the end-to-end proof of the whole "NC procurement → UniOps payment"
approach. It drives the genuine pipeline — nothing hand-sets ``invoice.status``:

  writer.upsert (mirror PO+po_line+GR)               ← the backend under test
    → POST /api/v1/invoices        (real upload endpoint)
    → POST /api/v1/invoices/{id}/match   (real match: allocation on the mirrored
                                          po_line + the mirrored GR id in gr_ids)
    → crud.po.po_has_three_way_matched_invoice   (the exact gate PA/create_pa use)

Txn visibility: the async HTTP layer (asyncpg) and the psycopg2 writer run on
SEPARATE connections against the same ``epms_test`` DB, so the writer's rows must
be COMMITTED before the HTTP upload/match can see them. conftest's
``pg_conn``/``seeded_vendor``/``system_user_id`` fixtures deliberately roll back
(uncommitted, per-test isolation) and are therefore invisible across connections —
so this test uses its own AUTOCOMMIT psycopg2 connection (mirroring
test_nc_purchase_writer's ``committed_nc_env``) to seed the vendor, ensure the
sync system user, and run ``writer.upsert`` — all committed — then wipes its rows
on teardown.
"""
import uuid
from decimal import Decimal

import psycopg2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

INV_URL = "/api/v1/invoices"

# Kept distinct from test_nc_purchase_writer's committed_nc_env (erp_id 0000415)
# so the two committed environments can never clobber each other in a shared run.
_ERP_ID = "0000499"
_VENDOR_CODE = "NCV-PIPELINE-499"
_VENDOR_NAME = "NC Pipeline Vendor 499"


def _mini_payload(vendor):
    """1 order (O1) + 1 line (OL1, line_total 100.00) + 1 GR (A1:O1) + 1 gr line.
    A local copy of test_nc_purchase_writer._mini_payload so this test is
    self-contained (no cross-test-module import)."""
    vid, vname = vendor
    return {
        "orders": [{
            "nc_source_pk": "O1", "number": "PO-NC-O1", "title": "PO-NC-O1",
            "type": 1, "status": "issued", "source": "nc", "currency": "CAD",
            "total": Decimal("100.00"), "subtotal": Decimal("0"),
            "tax_rate": Decimal("0"), "tax_amount": Decimal("0"),
            "vendor_id": vid, "vendor_name": vname, "pr_id": None,
            "place_order_method": "nc", "place_order_reference": "PO-NC-O1",
            "notes": "nc order",
        }],
        "order_lines": [{
            "nc_source_pk": "OL1", "po_nc_pk": "O1", "material_id": "MAT-1",
            "description": "Widget", "qty": Decimal("10"), "unit": "EA",
            "unit_price": Decimal("10.00"), "line_total": Decimal("100.00"),
            "received_qty": Decimal("10"), "sort_order": 1,
        }],
        "grs": [{
            "nc_source_pk": "A1:O1", "po_nc_pk": "O1", "number": "GR-NC-A1",
            "title": "GR-NC-A1", "gr_type": "physical", "procurement_type": 1,
            "status": "confirmed", "source": "nc",
        }],
        "gr_lines": [{
            "nc_source_pk": "AL1", "gr_nc_pk": "A1:O1", "po_line_nc_pk": "OL1",
            "material_id": "MAT-1", "description": "Widget",
            "qty_ordered": Decimal("0"), "qty_received": Decimal("10"),
            "unit_price": Decimal("10.00"), "line_total": Decimal("100.00"),
            "sort_order": 1,
        }],
        "skipped_no_vendor": [],
    }


@pytest.fixture
def mirrored_nc(test_pg_dsn):
    """Committed NC-mirror environment. Over an AUTOCOMMIT psycopg2 connection:
    seed a supplier + the nc-sync system user, then mirror one order+arrival via
    ``writer.upsert`` — so the PO/po_line/GR are visible to the async HTTP layer.
    Yields the resolved ids (as strings) and wipes its rows before + after."""
    from app.services.nc_purchase_sync import writer

    con = psycopg2.connect(test_pg_dsn)
    con.autocommit = True
    cur = con.cursor()

    def _wipe():
        # invoices.po_id is ON DELETE RESTRICT → delete invoices first.
        cur.execute("delete from invoices where po_id in "
                    "(select id from purchase_orders where nc_source_pk='O1' and source='nc')")
        cur.execute("delete from goods_receipts where nc_source_pk='A1:O1' and source='nc'")
        cur.execute("delete from purchase_orders where nc_source_pk='O1' and source='nc'")
        cur.execute("delete from business_partners where erp_id=%s", (_ERP_ID,))

    _wipe()
    vid = uuid.uuid4()
    cur.execute(
        "insert into business_partners "
        "(id, code, erp_id, name, category, contact_name, contact_email, "
        " payment_terms, currency, is_active, is_supplier, is_customer) "
        "values (%s,%s,%s,%s,%s,%s,%s,'net30','CAD',true,true,false)",
        (vid, _VENDOR_CODE, _ERP_ID, _VENDOR_NAME, "supplier",
         "NC Contact", "nc-pipeline@example.com"))
    uid = writer.ensure_system_user_sync(cur)
    writer.upsert(cur, _mini_payload((vid, _VENDOR_NAME)), uid)

    cur.execute("select id from purchase_orders where nc_source_pk='O1' and source='nc'")
    po_id = cur.fetchone()[0]
    cur.execute("select id, line_total from po_line_items where nc_source_pk='OL1'")
    po_line_id, po_line_total = cur.fetchone()
    cur.execute("select id from goods_receipts where nc_source_pk='A1:O1' and source='nc'")
    gr_id = cur.fetchone()[0]

    yield {
        "vendor_id": str(vid),
        "po_id": str(po_id),
        "po_line_id": str(po_line_id),
        "po_line_total": po_line_total,
        "gr_id": str(gr_id),
    }

    _wipe()
    con.close()


@pytest.mark.asyncio
async def test_mirrored_po_and_gr_pass_three_way_gate(admin_client, test_engine, mirrored_nc):
    """The whole point: a GR created by writer.upsert (mirrored from NC, never the
    EPMS GR workflow) with its po_line_id + line_total set satisfies the real
    3-way receipt gate when a UniOps invoice is matched against the mirrored PO."""
    po_id = mirrored_nc["po_id"]
    po_line_id = mirrored_nc["po_line_id"]
    gr_id = mirrored_nc["gr_id"]
    vendor_id = mirrored_nc["vendor_id"]
    # The mirrored po_line reference the match measures variance against.
    assert mirrored_nc["po_line_total"] == Decimal("100.00")

    # 1. Upload a UniOps invoice against the mirrored PO via the REAL endpoint.
    up = await admin_client.post(INV_URL, json={
        "vendor_id": vendor_id,
        "vendor_invoice_number": "NC-PIPELINE-INV-1",
        "amount": "100.00", "tax_amount": "0.00", "currency": "CAD",
        "invoice_date": "2026-08-01", "due_date": "2026-08-31",
        "line_items": [{"description": "Widget", "quantity": "10",
                        "unit_price": "10.00", "line_total": "100.00"}],
    })
    assert up.status_code == 201, up.text
    inv = up.json()
    inv_line = inv["line_items"][0]["id"]

    # 2. Match via the REAL endpoint: one allocation on the mirrored po_line
    #    (pre-tax 100.00 == po_line line_total → zero variance → matched), and the
    #    mirrored GR id in gr_ids so _apply_gr_selection sets invoice.gr_id.
    r = await admin_client.post(f"{INV_URL}/{inv['id']}/match", json={
        "allocations": [{
            "invoice_line_id": inv_line, "po_id": po_id, "po_line_id": po_line_id,
            "allocated_amount": "100.00", "allocated_tax": "0.00",
        }],
        "gr_ids": [gr_id],
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "matched"          # driven by the real match, not hand-set
    assert data["gr_id"] == gr_id               # mirrored GR linked via gr_ids
    assert data["po_id"] == po_id
    assert float(data["variance"]) == 0.0

    # 3. The exact gate crud/po.py exposes (PA receipt gate / create_pa trigger)
    #    now passes for the mirrored PO — a mirrored GR is a real GR to the gate.
    from app.crud.po import po_has_three_way_matched_invoice
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        assert await po_has_three_way_matched_invoice(db, uuid.UUID(po_id)) is True
