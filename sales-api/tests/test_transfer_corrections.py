"""A transfer sent wrong, and a receipt that differs (user 2026-11-13; transfers 00104 / 00106: our 943 named
the wrong item): amend and send again before the 944; settle the difference after it — corrected to what
arrived, or accepted — with why; the inventory follows."""
import pytest

from app.platform.actors import SYSTEM_ACTOR
from app.platform.commands import ANY_VERSION
from app.platform.errors import BusinessError
from app.platform.query import QuerySpec
from app.schemas.transfers import AmendIn, ResolveIn
from app.services.stock import StockItemService, sync
from app.services.transfer_views import TransferService
from tests.domain.test_transfers_edi import real_944
from tests.test_orders import OP, env  # noqa: F401 — a fixture
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_transfers import p7, sent_transfer, status, store  # noqa: F401 — p7 is a fixture


async def _sync(t):
    async with t.factory(SYSTEM_ACTOR) as u:
        out = await sync(u)
        await u.commit()
    return out


async def _items(t):
    async with t.factory(OP) as u:
        return {r.item_code: r for r in (await StockItemService(u).list(QuerySpec(page_size=100))).items}


@pytest.fixture(autouse=True)
async def _empty_ledger(db_engine):
    import sqlalchemy as sa
    async with db_engine.begin() as conn:
        await conn.execute(sa.text("TRUNCATE sales_stock_moves RESTART IDENTITY"))


async def test_a_sent_943_is_amended_and_sent_again_and_the_real_944_then_matches(p7):  # noqa: F811
    x = await sent_transfer(p7)  # 00104 as sent: CF0092 8400 + CF0093 7200 (wrong: CF0103 was meant)
    await _sync(p7)
    async with p7.factory(OP) as u:
        out = await TransferService(u).amend(p7.tr, x.id, AmendIn(lines=[{"item_code": "CF0092", "qty_ea": 8400},
                                             {"item_code": "CF0103", "qty_ea": 7200}], reason="CF0103 was packed, not CF0093"), ANY_VERSION)
        await u.commit()
    assert out.status == "943 Generated" and [(ln.item_code, ln.qty_ea) for ln in out.lines] == [("CF0092", 8400), ("CF0103", 7200)]
    docs = await p7.q("SELECT file_status, replaced_by_id FROM sales_documents WHERE kind = '943_EDI' ORDER BY id")
    assert docs[0]["file_status"] == "Replaced" and docs[0]["replaced_by_id"] is not None and len(docs) == 2
    await p7.worker.run_once()
    assert await status(p7, x.id) == "Waiting for 997"
    await store(p7, "944_EDI", real_944(), "944-16468.edi")
    await p7.tr.process_944s()
    assert await status(p7, x.id) == "Received"  # the real 944 matches the amended transfer
    await _sync(p7)
    items = await _items(p7)
    assert items["CF0093"].to_radiant_ea == 0 and items["CF0103"].to_radiant_ea == 0 and items["CF0103"].on_hand_ea == 7200
    async with p7.factory(OP) as u:  # answered: no more amending
        with pytest.raises(BusinessError) as e:
            await TransferService(u).amend(p7.tr, x.id, AmendIn(lines=[{"item_code": "CF0092", "qty_ea": 1}], reason="again"), ANY_VERSION)
    assert e.value.code == "transfer.not_amendable"


async def test_a_difference_from_our_wrong_943_is_corrected_to_what_arrived(p7):  # noqa: F811
    x = await sent_transfer(p7)
    await store(p7, "944_EDI", real_944(), "944-16468.edi")
    await p7.tr.process_944s()
    assert await status(p7, x.id) == "Discrepancy"
    await _sync(p7)
    assert (await _items(p7))["CF0093"].to_radiant_ea == 7200  # sent, never came: before it is settled
    async with p7.factory(OP) as u:
        out = await TransferService(u).resolve(p7.tr, x.id, ResolveIn(how="corrected", reason="our 943 named CF0093; CF0103 was sent"), ANY_VERSION)
        await u.commit()
    assert out.status == "Received" and out.resolution == "corrected" and out.resolved_by_name == "Op"
    assert sorted((ln.item_code, ln.qty_ea, ln.received_ea) for ln in out.lines) == [("CF0092", 8400, 8400), ("CF0103", 7200, 7200)]
    assert all(r.outcome == "Received" and not r.discrepancies for r in out.receipts)
    assert await p7.q("SELECT count(*) n FROM tasks WHERE type = 'sales_transfer_discrepancy' AND NOT is_completed") == [{"n": 0}]
    await _sync(p7)
    items = await _items(p7)
    assert items["CF0093"].to_radiant_ea == 0 and items["CF0103"].to_radiant_ea == 0  # nothing stays in transit
    [log] = await p7.q("SELECT context FROM sales_log WHERE message LIKE '%receipt difference corrected%'")
    assert ["CF0093", 7200, 0] in log["context"]["lines_before"]


async def test_a_real_difference_is_accepted_and_written_off_in_transit(p7):  # noqa: F811
    x = await sent_transfer(p7)
    await store(p7, "944_EDI", real_944(), "944-16468.edi")
    await p7.tr.process_944s()
    async with p7.factory(OP) as u:
        with pytest.raises(ValueError):  # why is required (at least 3 characters)
            ResolveIn(how="corrected", reason="x")
        out = await TransferService(u).resolve(p7.tr, x.id, ResolveIn(how="accepted", reason="7200 CF0093 lost in transit"), ANY_VERSION)
        await u.commit()
    assert out.status == "Received" and out.resolution == "accepted"
    assert sorted((ln.item_code, ln.qty_ea) for ln in out.lines) == [("CF0092", 8400), ("CF0093", 7200)]  # as sent
    await _sync(p7)
    items = await _items(p7)
    assert items["CF0093"].to_radiant_ea == 0 and items["CF0103"].to_radiant_ea == 0 and items["CF0103"].on_hand_ea == 7200
    async with p7.factory(OP) as u:
        with pytest.raises(BusinessError) as e:
            await TransferService(u).resolve(p7.tr, x.id, ResolveIn(how="accepted", reason="again"), ANY_VERSION)
    assert e.value.code == "transfer.not_discrepant"
