"""Documents shows the orders each file belongs to (user 2026-11-09): the first refs and the count,
search and filter by an order's ref, and, with none, why. A Shopify page links every order on it that
Sales has, also the ones that did not change."""
import hashlib
from datetime import timedelta

from app.models.documents import Document
from app.platform.actors import SYSTEM_ACTOR
from app.platform.query import QuerySpec
from app.services.operations import DocumentService
from tests.test_orders import OP, T0, env, po, run_850, shopify_order  # noqa: F401 — env is a fixture


async def listed(env, **spec):
    async with env.factory(OP) as u:
        return (await DocumentService(u).list(QuerySpec(**spec))).items


async def test_an_850_with_two_pos_shows_both_orders_and_is_found_by_either(env):
    await run_850(env, {"PurchaseOrder": [
        po("LBL", "PO-A", [{"Line": 1, "BuyerPart": "X", "Quantity": 1, "UOM": "CA"}]),
        po("LBL", "PO-B", [{"Line": 1, "BuyerPart": "X", "Quantity": 1, "UOM": "CA"}])]})
    [d] = await listed(env)
    assert d.orders_total == 2 and sorted(r.label for r in d.orders) == ["LBL-PO-A", "LBL-PO-B"]
    assert d.no_order_reason is None
    assert [x.id for x in await listed(env, search="PO-B")] == [d.id]
    assert await listed(env, search="PO-Z") == []
    from app.platform.query import Filter
    assert [x.id for x in await listed(env, filters=[Filter(field="order_refs", op="contains", value="PO-A")])] == [d.id]
    async with env.factory(OP) as u:  # the record sheet reads the same
        one = await DocumentService(u).get(d.id)
    assert one.orders_total == 2


async def test_a_document_with_no_order_says_why(env):
    async with env.factory(SYSTEM_ACTOR) as u:
        for kind, status, data in (("997_EDI", "Processed", b"997"), ("945_EDI", "Duplicate", b"945")):
            u.session.add(Document(direction="IN", origin="PARTNER", system="RADIANT", kind=kind, remote_name=kind,
                                   size=len(data), sha256=hashlib.sha256(data).hexdigest(), content=data,
                                   processing_status=status))
        await u.commit()
    reasons = {d.kind: d.no_order_reason for d in await listed(env)}
    assert reasons == {"997_EDI": "997 for a file Sales did not send (Zoho's)", "945_EDI": "Duplicate of a file already read"}
    assert all(d.orders == [] and d.orders_total == 0 for d in await listed(env))


async def test_a_shopify_page_links_the_orders_on_it_that_did_not_change(env):
    env.shop.orders = [shopify_order(1, 9301)]
    await env.pull()
    env.shop.orders = [shopify_order(1, 9301), shopify_order(2, 9302, updated=T0 + timedelta(minutes=5))]
    await env.pull()
    pages = sorted(await listed(env), key=lambda d: d.id)
    assert len(pages) == 2
    assert [sorted(r.label for r in p.orders) for p in pages] == [["SHOP-9301"], ["SHOP-9301", "SHOP-9302"]]
