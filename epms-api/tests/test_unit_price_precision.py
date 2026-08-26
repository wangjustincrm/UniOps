"""NC unit prices need five decimals, not two.

NC quotes to five decimal places and beyond, and the mirror was storing the
price in a NUMERIC(15,2) column — so the ERP's 0.944 became 0.94 and the line
stopped multiplying out. Measured against production NC over the 4,944 in-scope
order lines:

    2 decimals   319 lines wrong, 43,256.09 of error, worst single line 2,000
    3 decimals   158 lines,        2,454.03
    4 decimals    60 lines,           41.22
    5 decimals     5 lines,            1.00
    8 decimals     0 lines

The worst case is PO-057-2402-02: 500,000 x 0.944. At two decimals that is
470,000 against NC's 472,000.

Note what was NOT wrong: `line_total` holds NC's own norigtaxmny and the header
sums NC's norigmny, so the stored money was always right. It is the price
BESIDE it that was rounded, which is why the document did not add up and why
nothing can be re-derived from the two — the fix has to widen the column and
then re-read the prices from NC.
"""
import re
import uuid
import zlib
import base64
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.crud import user as user_crud
from app.db.base import Base
from app.models.po import PoLineItem, PurchaseOrder
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.services.pdf_po import generate_po_pdf

#: What the ERP quotes to. Five is the ask; the seven production lines that go
#: further (six and eight decimals) are documented in the module docstring.
_SCALE = 5

_PRICE_COLUMNS = [
    ("po_line_items", "unit_price"),
    ("gr_line_items", "unit_price"),
    ("pa_line_items", "unit_price"),
]


@pytest.mark.parametrize("table,column", _PRICE_COLUMNS)
def test_the_price_column_carries_five_decimals(table, column):
    col = Base.metadata.tables[table].c[column]
    assert col.type.scale == _SCALE, (
        f"{table}.{column} is scale {col.type.scale}: an ERP price rounds off on write")


@pytest.mark.parametrize("table", [t for t, _ in _PRICE_COLUMNS])
def test_line_total_stays_at_two_decimals(table):
    """Only the PRICE gains decimals. line_total is money — a currency amount
    with five decimals is not a more accurate figure, it is an unpayable one."""
    assert Base.metadata.tables[table].c["line_total"].type.scale == 2


async def _po_with_price(db, price: Decimal, qty: Decimal):
    author = await user_crud.create(db, RegisterRequest(
        email=f"buyer-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Buyer", role="erp_pa_officer"))
    v = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Acme", category="supplier",
               contact_name="C", contact_email="c@x.com")
    db.add(v)
    await db.flush()
    po = PurchaseOrder(
        number=f"PO-NC-{uuid.uuid4().hex[:8]}", title="NC order", type=1,
        status="issued", currency="CAD", vendor_id=v.id, vendor_name="Acme",
        subtotal=Decimal("0"), tax_rate=Decimal("0"), tax_amount=Decimal("0"),
        total=Decimal("0"), source="nc", nc_source_pk=uuid.uuid4().hex,
        created_by=author.id,
    )
    db.add(po)
    await db.flush()
    db.add(PoLineItem(
        po_id=po.id, description="Skim milk powder", material_id="MAT-1",
        qty=qty, unit="LB", unit_price=price,
        line_total=(qty * price).quantize(Decimal("0.01")),
        sort_order=0, nc_source_pk=f"OL-{uuid.uuid4().hex[:8]}",
    ))
    await db.flush()
    return po


@pytest.mark.asyncio
async def test_a_five_decimal_price_survives_the_round_trip(test_engine):
    """The regression this whole change is about: the column, not the code, was
    doing the rounding, so the loss happened silently on write."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        po = await _po_with_price(db, Decimal("0.21290"), Decimal("500000"))
        po_id = po.id
        await db.commit()

    async with factory() as db:
        line = (await db.execute(
            select(PoLineItem).where(PoLineItem.po_id == po_id))).scalar_one()
        assert line.unit_price == Decimal("0.21290")


@pytest.mark.asyncio
async def test_the_worst_production_line_now_multiplies_out(test_engine):
    """PO-057-2402-02, the biggest single divergence found in NC."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    qty, price = Decimal("500000"), Decimal("0.944")
    async with factory() as db:
        po = await _po_with_price(db, price, qty)
        po_id = po.id
        await db.commit()

    async with factory() as db:
        line = (await db.execute(
            select(PoLineItem).where(PoLineItem.po_id == po_id))).scalar_one()
        assert line.qty * line.unit_price == Decimal("472000.000")
        assert line.line_total == Decimal("472000.00")


# ── how the price prints ─────────────────────────────────────────────────────

_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)


def _text_of(pdf_bytes: bytes) -> str:
    out = bytearray()
    for match in _STREAM_RE.finditer(pdf_bytes):
        raw = match.group(1).strip(b"\r\n")
        try:
            data = raw.rstrip()[:-2] if raw.rstrip().endswith(b"~>") else raw
            out += zlib.decompress(base64.a85decode(data))
        except Exception:
            continue
    return out.decode("latin-1")


def _pdf_po(price: Decimal, qty: Decimal, line_total: Decimal) -> str:
    po = PurchaseOrder(
        id=uuid.uuid4(), number="PO-NC-0001", title="NC order", type=1,
        status="issued", currency="CAD", vendor_id=uuid.uuid4(), vendor_name="Acme",
        subtotal=line_total, tax_rate=Decimal("0"), tax_amount=Decimal("0"),
        total=line_total, created_by=uuid.uuid4(), source="nc",
    )
    po.line_items = [PoLineItem(
        id=uuid.uuid4(), po_id=po.id, description="Skim milk powder",
        qty=qty, unit="LB", unit_price=price, line_total=line_total,
        received_qty=Decimal("0"), sort_order=0,
    )]
    return _text_of(generate_po_pdf(po))


def test_the_pdf_prints_the_price_the_erp_quoted():
    """Widening the column alone changes nothing a supplier can see — the cell
    was formatted to two decimals. This is the half that makes the signed
    document add up."""
    text = _pdf_po(Decimal("0.94400"), Decimal("500000"), Decimal("472000.00"))

    assert "0.944" in text
    assert "472,000.00" in text


def test_an_ordinary_price_still_prints_as_currency():
    """Two decimals is the floor, not the ceiling: 10 must not become '10' on a
    document people read as money."""
    text = _pdf_po(Decimal("10.00"), Decimal("10"), Decimal("100.00"))

    assert "10.00" in text
    assert "10.000" not in text, "trailing zeros past two decimals are noise"


def test_trailing_zeros_beyond_two_places_are_trimmed():
    text = _pdf_po(Decimal("0.21290"), Decimal("100"), Decimal("21.29"))

    assert "0.2129" in text
    assert "0.21290" not in text


# ── the migration ────────────────────────────────────────────────────────────

def test_the_migration_chains_onto_the_real_head():
    """Two hazards this repo has hit before: a down_revision that misses the
    actual chain tail (leaving two heads, so one branch silently never runs),
    and a revision id over 32 characters — alembic_version is VARCHAR(32), and
    that one fails HALFWAY THROUGH migrate-prod.sh."""
    import os
    import re as _re

    versions = os.path.join(os.path.dirname(__file__), "..", "alembic", "versions")
    revisions, parents = {}, set()
    for name in os.listdir(versions):
        if not name.endswith(".py"):
            continue
        src = open(os.path.join(versions, name), encoding="utf-8").read()
        found = _re.search(r"^revision\s*(?::[^=]*)?=\s*['\"]([^'\"]+)", src, _re.M)
        if found:
            revisions[found.group(1)] = name
        parents.update(_re.findall(
            r"^down_revision\s*(?::[^=]*)?=\s*\(?\s*['\"]([^'\"]+)", src, _re.M))
        # A merge revision names several parents in a tuple.
        for tup in _re.findall(r"^down_revision\s*(?::[^=]*)?=\s*\(([^)]*)\)", src, _re.M):
            parents.update(_re.findall(r"['\"]([^'\"]+)['\"]", tup))

    heads = [r for r in revisions if r not in parents]
    # 断言"恰好一个 head",而不是写死某个 revision 名 —— 后者每加一条迁移就要
    # 改一次,而它并不比数量断言多守住什么(本测试自述的两个隐患是"双 head"和
    # "id 超 32 字符")。ai01_sync_intervals 挂到 ak01 之后曾让这条断言假红。
    assert len(heads) == 1, f"expected one head, got {heads}"
    assert all(len(r) <= 32 for r in revisions), \
        [r for r in revisions if len(r) > 32]


# ── the backfill ─────────────────────────────────────────────────────────────

def _insert_nc_line(cur, seeded_vendor, system_user_id, *, nc_pk, price):
    """One mirrored NC PO with one mirrored line, straight through psycopg2."""
    vid, vname = seeded_vendor
    po_id = uuid.uuid4()
    cur.execute(
        "insert into purchase_orders (id,number,title,type,status,currency,subtotal,"
        "tax_rate,tax_amount,total,vendor_id,vendor_name,is_prepaid,approval_step_idx,"
        "pr_id,created_by,place_order_method,place_order_reference,source,nc_source_pk,"
        "created_at,updated_at) values (%s,%s,%s,1,'issued','CAD',0,0,0,0,%s,%s,false,0,"
        "NULL,%s,'nc',%s,'nc',%s,now(),now())",
        (po_id, f"PO-{nc_pk}", f"PO-{nc_pk}", vid, vname, system_user_id,
         f"PO-{nc_pk}", f"O-{nc_pk}"))
    cur.execute(
        "insert into po_line_items (id,po_id,description,qty,unit,unit_price,"
        "line_total,received_qty,sort_order,nc_source_pk) "
        "values (%s,%s,'Skim milk powder',500000,'LB',%s,472000.00,0,0,%s)",
        (uuid.uuid4(), po_id, price, nc_pk))
    return po_id


def test_the_backfill_plans_only_the_lines_that_actually_differ(
        pg_cur, seeded_vendor, system_user_id):
    from scripts.backfill_nc_unit_price import _plan

    _insert_nc_line(pg_cur, seeded_vendor, system_user_id,
                    nc_pk="OL-rounded", price=Decimal("0.94"))
    _insert_nc_line(pg_cur, seeded_vendor, system_user_id,
                    nc_pk="OL-already-right", price=Decimal("0.94400"))

    changes, _ = _plan(pg_cur, "po_line_items", {
        "OL-rounded": Decimal("0.94400"),
        "OL-already-right": Decimal("0.94400"),
    })

    assert changes == [("OL-rounded", Decimal("0.94400"))]


def test_the_backfill_is_idempotent(pg_cur, seeded_vendor, system_user_id):
    """A second run must report nothing, not churn every row it already fixed."""
    from scripts.backfill_nc_unit_price import _plan

    _insert_nc_line(pg_cur, seeded_vendor, system_user_id,
                    nc_pk="OL-done", price=Decimal("0.94400"))

    changes, _ = _plan(pg_cur, "po_line_items", {"OL-done": Decimal("0.944")})

    assert changes == []


def test_the_backfill_reports_lines_nc_no_longer_lists_instead_of_touching_them(
        pg_cur, seeded_vendor, system_user_id):
    from scripts.backfill_nc_unit_price import _plan

    _insert_nc_line(pg_cur, seeded_vendor, system_user_id,
                    nc_pk="OL-orphan", price=Decimal("0.94"))

    changes, missing = _plan(pg_cur, "po_line_items", {})

    assert changes == []
    assert missing >= 1
