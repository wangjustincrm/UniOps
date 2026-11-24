"""No lost update (user 2026-11-24): amend, returned and invoice prices name the version the person saw, and a change
they make moves the order's version — they change its lines, not the order, so without it two people who saw the same
order both passed."""
import asyncio

import pytest
import sqlalchemy as sa

from app.models.orders import Order
from app.platform.commands import Stale
from app.platform.errors import InvalidInput
from app.schemas.orders import AmendOrderIn, InvoicePricesIn, ReturnedIn
from app.services.order_views import OrderService
from tests.test_invoicing import OP, confirm, p6, shipped_850  # noqa: F401 — p6 is a fixture
from tests.test_order_amend import _order
from tests.test_order_payment import OPERATOR, _orders, api  # noqa: F401 — api is a fixture
from tests.test_orders import env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture


async def amend(t, svc, o, line, n, version):
    return await svc.amend(o.id, AmendOrderIn(reason="wrong quantity", lines=[{"id": line, "item_code": "CF0092", "ordered_qty": str(n)}]), t.edi.out, version)


async def returned(t, svc, o, line, n, version):  # noqa: ARG001
    return await svc.set_returned(o.id, ReturnedIn(reason="refused", lines=[{"id": line, "returned_ea": n}]), version)


async def prices(t, svc, o, line, n, version):  # noqa: ARG001
    return await svc.set_invoice_prices(o.id, InvoicePricesIn(reason="promo", lines=[{"id": line, "invoice_unit_price": str(n)}]), version)


CASES = [pytest.param(amend, _order, (7, 8), id="amend"), pytest.param(returned, shipped_850, (6, 12), id="returned"),
         pytest.param(prices, shipped_850, (41, 42), id="invoice prices")]


async def _start(t, make, number):
    o = await make(t, number)
    [r] = await t.q("SELECT o.version, l.id FROM sales_orders o JOIN sales_order_lines l ON l.order_id = o.id WHERE o.id = :o", o=o.id)
    return o, r["id"], r["version"]


async def _version(t, o):
    return (await t.q("SELECT version FROM sales_orders WHERE id = :o", o=o.id))[0]["version"]


@pytest.mark.parametrize(("act", "make", "n"), CASES)
async def test_the_second_of_two_people_who_saw_the_same_order_is_refused(p6, act, make, n):  # noqa: F811
    o, line, seen = await _start(p6, make, "PO-LU1")
    async with p6.factory(OP) as a, p6.factory(OP) as b:
        await act(p6, OrderService(a), o, line, n[0], seen)
        second = asyncio.create_task(act(p6, OrderService(b), o, line, n[1], seen))  # waits for the first's row lock
        await asyncio.sleep(0.3)
        assert not second.done()
        await a.commit()
        with pytest.raises(Stale) as e:
            await second
        assert e.value.status_code == 409 and e.value.code == "entity.stale"
        await b.rollback()
    assert await _version(p6, o) == seen + 1
    async with p6.factory(OP) as u:  # who reloaded is let through
        await act(p6, OrderService(u), o, line, n[1], seen + 1)
        await u.commit()
    assert await _version(p6, o) == seen + 2


@pytest.mark.parametrize(("act", "make", "n"), CASES)
async def test_a_change_leaves_who_and_when_and_no_empty_audit_row_and_no_change_moves_nothing(p6, act, make, n):  # noqa: F811
    o, line, seen = await _start(p6, make, "PO-LU2")
    count = "SELECT count(*) n FROM sales_audit WHERE entity = 'sales_orders' AND entity_id = :o"
    before = (await p6.q(count, o=str(o.id)))[0]["n"]
    async with p6.factory(OP) as u:
        await act(p6, OrderService(u), o, line, n[0], seen)
        await u.commit()
    [r] = await p6.q("SELECT version, updated_by_name, updated_at IS NOT NULL AS at FROM sales_orders WHERE id = :o", o=o.id)
    assert (r["version"], r["updated_by_name"], r["at"]) == (seen + 1, OP.name, True)
    assert (await p6.q(count, o=str(o.id)))[0]["n"] == before  # the lines have their own rows; the order's columns did not change
    assert (await p6.q("SELECT count(*) n FROM sales_audit WHERE entity = 'sales_order_lines' AND entity_id = :l AND action = 'update'", l=str(line)))[0]["n"] >= 1
    async with p6.factory(OP) as u:  # the same again: nothing to change
        await act(p6, OrderService(u), o, line, n[0], seen + 1)
        await u.commit()
    assert await _version(p6, o) == seen + 1


@pytest.mark.parametrize(("act", "make", "n"), CASES)
async def test_without_the_version_or_with_an_old_one_nothing_changes(p6, act, make, n):  # noqa: F811
    o, line, seen = await _start(p6, make, "PO-LU3")
    for version, error in ((None, InvalidInput), (seen - 1, Stale)):
        async with p6.factory(OP) as u:
            with pytest.raises(error):
                await act(p6, OrderService(u), o, line, n[0], version)
            await u.rollback()
    assert await _version(p6, o) == seen


async def test_the_scheduler_s_writers_go_on_after_each(p6):  # noqa: F811
    o, line, v = await _start(p6, shipped_850, "PO-LU4")
    async with p6.factory(OP) as u:
        await prices(p6, OrderService(u), o, line, 41, v)
        await u.commit()
    async with p6.factory(OP) as u:
        await returned(p6, OrderService(u), o, line, 6, v + 1)
        await u.commit()
    await p6.inv.advance()
    assert (await confirm(p6, o.id)).lines  # the delivery and the draft
    o2 = await _order(p6, "PO-LU5", "940 Rejected")  # amended, its new 940 is made in the same unit, and the pass goes on
    [r] = await p6.q("SELECT o.version, l.id FROM sales_orders o JOIN sales_order_lines l ON l.order_id = o.id WHERE o.id = :o", o=o2.id)
    async with p6.factory(OP) as u:
        out = await amend(p6, OrderService(u), o2, r["id"], 7, r["version"])
        await u.commit()
    assert out["sent"] and await _version(p6, o2) > r["version"]
    await p6.inv.advance()


async def test_a_unit_that_read_the_order_unlocked_before_the_bump_fails_and_works_when_run_again(p6):  # noqa: F811
    """A Shopify finance read loads the order without a lock: had a person saved in between, its unit fails (the pass
    isolates it and takes it again) — as it always did when another writer of the order's own columns came first."""
    from sqlalchemy.orm.exc import StaleDataError
    o, line, v = await _start(p6, shipped_850, "PO-LU6")
    async with p6.factory(OP) as late, p6.factory(OP) as person:
        held = await late.session.get(Order, o.id)
        await returned(p6, OrderService(person), o, line, 6, v)
        await person.commit()
        held.finance_updated_at = sa.func.now()
        with pytest.raises(StaleDataError):
            await late.session.flush()
        await late.rollback()
    async with p6.factory(OP) as u:
        (await u.session.get(Order, o.id)).finance_updated_at = sa.func.now()
        await u.commit()


@pytest.mark.parametrize(("method", "path", "body"), [
    ("post", "amend", {"reason": "wrong", "lines": [{"id": 1, "item_code": "CF0092", "ordered_qty": "1"}]}),
    ("put", "returned", {"reason": "refused", "lines": [{"id": 1, "returned_ea": 1}]}),
    ("put", "invoice-prices", {"reason": "promo", "lines": [{"id": 1, "invoice_unit_price": "1"}]})])
async def test_over_http_a_missing_version_is_422_and_an_old_one_409(api, method, path, body):  # noqa: F811
    o = (await _orders(api, 9801))["9801"]
    url = f"/api/v1/orders/{o['id']}/{path}"
    r = await getattr(api, method)(url, json=body, headers=OPERATOR)
    assert r.status_code == 422 and r.json()["error"]["code"] == "entity.version_required", r.text
    r = await getattr(api, method)(f"{url}?version=0", json=body, headers=OPERATOR)
    assert r.status_code == 409 and r.json()["error"]["code"] == "entity.stale", r.text
