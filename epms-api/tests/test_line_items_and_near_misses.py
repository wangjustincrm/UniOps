"""Line items, and what to say when a name search finds nothing.

Two failures from the same conversation.

Asked for a vendor's orders "including LineItem", the assistant returned the
headers and told the requester to "request a query of purchase_order_line_item
filtered by the PO numbers". Asked for exactly that, it answered — correctly —
that no such entity exists. It had invented a table, sent someone to ask for it,
and then denied it: a round trip to nothing, which is worse than declining at
the start. There were no line-item entities at all.

Separately, a name has to be given in full to match. "Canadian Bearing" against
a vendor recorded as "Canadian Bearing Ltd" returns nothing, and nothing reads
as "we never bought from them" — the one answer that is both plausible and
false. So an empty result over a name search now comes back with what does
exist.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.core.ontology import REGISTRY
from app.services import controlled_query as cq

ADMIN_SCOPE = {
    "perms": {"view_pr": True, "view_po": True, "view_gr": True,
              "view_invoice": True, "view_pa": True},
    "pr_subq": None, "po_subq": None, "invoice_subq": None, "pa_subq": None,
}


# ── The entities exist and are shaped as claimed ─────────────────────────────

def test_line_item_entities_are_registered():
    """The entity the assistant told someone to ask for now exists — under the
    name the ontology gives it, which is what the planner is shown."""
    assert "po_line" in REGISTRY
    assert "pr_line" in REGISTRY


def test_a_line_is_scoped_through_its_document_not_on_its_own():
    """A line must not have visibility rules of its own: two answers about who
    may see an order would drift, and the looser one would be a leak."""
    from app.core.ontology import scope_po_line, scope_pr_line

    assert REGISTRY["po_line"].apply_scope is scope_po_line
    assert REGISTRY["pr_line"].apply_scope is scope_pr_line


def test_a_line_carries_the_numbers_people_ask_for():
    fields = REGISTRY["po_line"].fields
    for name in ("description", "qty", "unit", "unit_price", "line_total",
                 "received_qty"):
        assert name in fields, name


def test_an_order_links_to_its_lines_and_back():
    assert REGISTRY["purchase_order"].links["lines"].target == "po_line"
    assert REGISTRY["po_line"].links["order"].target == "purchase_order"


def test_a_line_dates_itself_by_its_order():
    """Lines have no date of their own. Without this a period filter has
    nothing local to land on and "what did we buy since 2023" cannot be asked."""
    assert REGISTRY["po_line"].date_field == "order.created_at"
    assert REGISTRY["pr_line"].date_field == "request.created_at"


# ── Against real rows ────────────────────────────────────────────────────────

VENDOR = "Rothsay Bearing Works Ltd"


@pytest.fixture
async def one_order(test_engine):
    """Two orders from two vendors, one of them with line items.

    Built through the ORM rather than raw INSERTs: these tables carry a dozen
    NOT NULL columns with model-level defaults, and spelling them out by hand
    means chasing one constraint at a time and re-chasing them whenever a column
    is added.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models.po import PoLineItem, PurchaseOrder
    from app.models.user import User
    from app.models.vendor import Vendor

    factory = async_sessionmaker(test_engine, class_=AsyncSession,
                                 expire_on_commit=False)
    async with factory() as db:
        author = User(email=f"line-test-{uuid.uuid4().hex[:8]}@example.com",
                      full_name="Line Test", hashed_password="x", role="requester")
        db.add(author)
        vendors = {}
        for vname in (VENDOR, "Quite Other Supplies Inc"):
            bp = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name=vname,
                                 category="supplier", contact_name="A",
                                 contact_email="a@example.com",
                                 payment_terms="net30", currency="CAD",
                                 is_supplier=True, is_customer=False)
            db.add(bp)
            vendors[vname] = bp
        await db.flush()

        orders = {}
        for num, vname in (("PO-TEST-0001", VENDOR),
                           ("PO-TEST-0002", "Quite Other Supplies Inc")):
            po = PurchaseOrder(
                number=num, title=f"Order {num}", type=2, status="issued",
                currency="CAD", subtotal=Decimal("100"), tax_rate=Decimal("0"),
                tax_amount=Decimal("0"), total=Decimal("100"),
                vendor_id=vendors[vname].id, vendor_name=vname,
                created_by=author.id,
                # Explicit: the ORM default is now(), and a period test against
                # "2024" would then be testing nothing.
                created_at=datetime(2024, 3, 1, tzinfo=timezone.utc),
            )
            db.add(po)
            orders[num] = po
        await db.flush()

        for i, desc in enumerate(("Radial bearing 6204", "Taper bearing 30206"), start=1):
            db.add(PoLineItem(
                po_id=orders["PO-TEST-0001"].id, description=desc,
                qty=Decimal(i), unit="EA", unit_price=Decimal("12.50"),
                line_total=Decimal(i) * Decimal("12.50")))
        await db.commit()

        try:
            yield db
        finally:
            # A fresh session to clean up. controlled_query.execute sets its
            # transaction READ ONLY — that is the guarantee the whole query
            # layer rests on — so the session the tests ran through cannot
            # issue a DELETE afterwards.
            #
            # Only the rows this fixture created, by id. An earlier version
            # said DELETE FROM purchase_orders, which in a full run tried to
            # take every other test's orders with it — it failed on a foreign
            # key from invoice_po_allocations, and the failure was the lucky
            # outcome: had those tables been empty it would have quietly
            # destroyed the rows other tests were relying on.
            ids = {"po": [str(o.id) for o in orders.values()],
                   "vendor": [str(v.id) for v in vendors.values()],
                   "user": str(author.id)}
            async with factory() as cleanup:
                await cleanup.execute(sa.text(
                    "DELETE FROM po_line_items WHERE po_id = ANY(CAST(:p AS uuid[]))"),
                    {"p": ids["po"]})
                await cleanup.execute(sa.text(
                    "DELETE FROM purchase_orders WHERE id = ANY(CAST(:p AS uuid[]))"),
                    {"p": ids["po"]})
                await cleanup.execute(sa.text(
                    "DELETE FROM business_partners WHERE id = ANY(CAST(:v AS uuid[]))"),
                    {"v": ids["vendor"]})
                await cleanup.execute(
                    sa.text("DELETE FROM users WHERE id = CAST(:i AS uuid)"),
                    {"i": ids["user"]})
                await cleanup.commit()


@pytest.mark.asyncio
async def test_line_items_come_back_with_their_order(one_order):
    """The question that started this: a vendor's orders WITH the line items."""
    result = await cq.execute(one_order, {
        "entity": "po_line",
        "select": ["order.number", "order.vendor_name", "description", "qty",
                   "unit_price", "line_total"],
        "where": [{"field": "order.vendor_name", "op": "like", "value": "Rothsay Bearing"}],
    }, ADMIN_SCOPE)

    assert result["row_count"] == 2
    assert {r["description"] for r in result["rows"]} == {
        "Radial bearing 6204", "Taper bearing 30206"}
    assert all(r["order.number"] == "PO-TEST-0001" for r in result["rows"])


@pytest.mark.asyncio
async def test_a_period_on_lines_uses_the_orders_date(one_order):
    """Admission and denial together: in range returns the lines, out of range
    returns none. A period silently ignored would pass the first alone."""
    def query(frm, to):
        return {"entity": "po_line", "select": ["description"],
                "period": {"field": "order.created_at", "from": frm, "to": to}}

    assert (await cq.execute(one_order, query("2024-01-01", "2024-12-31"),
                             ADMIN_SCOPE))["row_count"] == 2
    assert (await cq.execute(one_order, query("2019-01-01", "2019-12-31"),
                             ADMIN_SCOPE))["row_count"] == 0


# ── Near misses ──────────────────────────────────────────────────────────────

async def _names_for(db, term: str) -> list[str]:
    result = await cq.execute(db, {
        "entity": "purchase_order", "select": ["number", "vendor_name"],
        "where": [{"field": "vendor_name", "op": "like", "value": term}],
    }, ADMIN_SCOPE)
    assert result["row_count"] == 0, f"{term!r} was supposed to match nothing"
    return (result.get("near_misses") or {}).get("vendor_name", [])


@pytest.mark.asyncio
async def test_a_wrong_last_word_is_offered_back(one_order):
    """A shortened name needs no help — `like` already matches a prefix, which
    is worth stating: "Canadian Bearing" against "Canadian Bearing Ltd" was
    never the failing case. What does fail is a word that is simply wrong."""
    assert VENDOR in await _names_for(one_order, "Rothsay Bearing Company")


@pytest.mark.asyncio
async def test_a_plural_slip_is_offered_back(one_order):
    """"Bearings" against "Bearing" — one trailing letter, and the whole-word
    pass cannot see it."""
    assert VENDOR in await _names_for(one_order, "Rothsay Bearings")


@pytest.mark.asyncio
async def test_a_typo_in_one_word_still_finds_it_by_another(one_order):
    """The typo is as long as the good word, so it is tried first and fails.
    The suggestion has to come from the other word."""
    assert VENDOR in await _names_for(one_order, "Rothsey Bearing")


@pytest.mark.asyncio
async def test_a_generic_word_alone_suggests_nothing(one_order):
    """"Ltd" matches every incorporated vendor. Offering them all is noise
    dressed as help, and points confidently at the wrong company."""
    assert await _names_for(one_order, "Limited") == []


@pytest.mark.asyncio
async def test_a_truncated_fragment_is_checked_for_genericness_too(one_order):
    """"Canadan" shortens to "Canada", which matches half the vendor list.
    Arriving by truncation does not make a generic word useful."""
    assert await _names_for(one_order, "Canadan Widgets") == []


@pytest.mark.asyncio
async def test_a_name_that_really_is_absent_suggests_nothing(one_order):
    """The admission beside every denial above: suggestions must not appear
    for a vendor we genuinely never dealt with, or they mean nothing."""
    assert await _names_for(one_order, "Zzzyx Kettleworks") == []


@pytest.mark.asyncio
async def test_a_query_that_matched_offers_no_suggestions(one_order):
    result = await cq.execute(one_order, {
        "entity": "purchase_order", "select": ["number", "vendor_name"],
        "where": [{"field": "vendor_name", "op": "like", "value": "Rothsay"}],
    }, ADMIN_SCOPE)
    assert result["row_count"] == 1
    assert "near_misses" not in result


@pytest.mark.asyncio
async def test_suggestions_respect_the_row_scope(one_order):
    """The leak this must not be. With a scope admitting only the OTHER order,
    a search for the invisible vendor must suggest nothing — the name itself is
    a fact about a row this caller cannot see.
    """
    visible_other = sa.select(sa.column("id")).select_from(
        sa.table("purchase_orders")).where(
        sa.column("number") == "PO-TEST-0002")
    restricted = {**ADMIN_SCOPE, "po_subq": visible_other}

    result = await cq.execute(one_order, {
        "entity": "purchase_order", "select": ["number", "vendor_name"],
        "where": [{"field": "vendor_name", "op": "like", "value": "Rothsay Bearings"}],
    }, restricted)

    assert result["row_count"] == 0
    names = (result.get("near_misses") or {}).get("vendor_name", [])
    assert VENDOR not in names, "suggested a vendor from an order outside the scope"
