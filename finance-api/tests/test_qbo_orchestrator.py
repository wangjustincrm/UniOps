# tests/test_qbo_orchestrator.py
import uuid

import pytest
from sqlalchemy import select
from app.models.qbo import QboAccount, QboRaw, QboSyncRun
from scripts.qbo_import.orchestrator import _max_watermark, run_sync


class FakeClient:
    def __init__(self, rows_by_entity):
        self.rows_by_entity = rows_by_entity

    def query_all(self, entity, where=""):
        return iter(self.rows_by_entity.get(entity, []))


@pytest.mark.asyncio
async def test_full_sync_loads_and_records_watermark(db_session):
    client = FakeClient({"Account": [
        {"Id": "58", "Name": "A/P", "AccountType": "Accounts Payable",
         "MetaData": {"LastUpdatedTime": "2019-05-02T10:00:00-07:00"}},
    ]})
    run = await run_sync(db_session, client, mode="full", entities=["Account"])
    assert run.status == "success"
    assert run.counters["Account"]["inserted"] == 1
    assert run.watermarks["Account"] == "2019-05-02T10:00:00-07:00"
    assert (await db_session.execute(select(QboAccount))).scalar_one().qbo_id == "58"


@pytest.mark.asyncio
async def test_full_sync_soft_deletes_local_extras(db_session):
    # First full pull loads two accounts.
    client1 = FakeClient({"Account": [
        {"Id": "1", "Name": "a", "MetaData": {"LastUpdatedTime": "2019-05-01T00:00:00-07:00"}},
        {"Id": "2", "Name": "b", "MetaData": {"LastUpdatedTime": "2019-05-01T00:00:00-07:00"}},
    ]})
    await run_sync(db_session, client1, mode="full", entities=["Account"])

    # Second full pull no longer returns id=2 -> it must be soft-deleted, not removed.
    client2 = FakeClient({"Account": [
        {"Id": "1", "Name": "a", "MetaData": {"LastUpdatedTime": "2019-05-03T00:00:00-07:00"}},
    ]})
    await run_sync(db_session, client2, mode="full", entities=["Account"])

    rows = {r.qbo_id: r for r in (await db_session.execute(select(QboAccount))).scalars().all()}
    assert rows["2"].deleted_at is not None
    assert rows["1"].deleted_at is None


@pytest.mark.asyncio
async def test_run_sync_records_started_by(db_session):
    client = FakeClient({"Account": [
        {"Id": "58", "Name": "A/P", "MetaData": {"LastUpdatedTime": "2019-05-02T10:00:00-07:00"}},
    ]})
    starter = uuid.uuid4()
    run = await run_sync(db_session, client, mode="full", entities=["Account"], started_by=starter)
    assert run.started_by == starter

    persisted = (await db_session.execute(select(QboSyncRun).where(QboSyncRun.id == run.id))).scalar_one()
    assert persisted.started_by == starter


@pytest.mark.asyncio
async def test_full_sync_soft_deletes_raw_and_isolates_entity_type(db_session):
    # Seed: Term id "1" and TaxCode id "1" (same qbo_id, different entity_type).
    c1 = FakeClient({
        "Term":    [{"Id": "1", "MetaData": {"LastUpdatedTime": "2019-01-01T00:00:00-08:00"}}],
        "TaxCode": [{"Id": "1", "MetaData": {"LastUpdatedTime": "2019-01-01T00:00:00-08:00"}}],
    })
    await run_sync(db_session, c1, mode="full", entities=["Term", "TaxCode"])
    # Second full pull: TaxCode id "1" is gone; Term id "1" still present.
    c2 = FakeClient({
        "Term":    [{"Id": "1", "MetaData": {"LastUpdatedTime": "2019-02-01T00:00:00-08:00"}}],
        "TaxCode": [],
    })
    await run_sync(db_session, c2, mode="full", entities=["Term", "TaxCode"])
    rows = {(r.entity_type, r.qbo_id): r for r in
            (await db_session.execute(select(QboRaw))).scalars().all()}
    assert rows[("TaxCode", "1")].deleted_at is not None   # removed → soft-deleted
    assert rows[("Term", "1")].deleted_at is None          # untouched by TaxCode sync


def test_max_watermark_compares_parsed_instant_not_lexical_string():
    # A is lexically GREATER ("22" > "20") but chronologically EARLIER:
    #   22:00-01:00 == 23:00 UTC May 2
    # B is lexically SMALLER but chronologically LATER:
    #   20:00-07:00 == 03:00 UTC May 3
    a = "2019-05-02T22:00:00-01:00"
    b = "2019-05-02T20:00:00-07:00"
    assert a > b  # sanity check the lexical trap actually exists in this pair

    objects = [
        {"MetaData": {"LastUpdatedTime": a}},
        {"MetaData": {"LastUpdatedTime": b}},
    ]
    assert _max_watermark(objects) == b


def test_default_entities_covers_the_whole_registry():
    """A default full sync must sync every registered entity — the hardcoded
    AP-core subset silently dropped all Phase-2 entities (Invoice/Payment/etc.)."""
    from scripts.qbo_import.orchestrator import DEFAULT_ENTITIES
    from scripts.qbo_import.registry import REGISTRY

    registered = {e.name for e in REGISTRY}
    assert set(DEFAULT_ENTITIES) == registered
    # Explicitly guard the entities that were being dropped.
    for name in ("Invoice", "Payment", "CreditMemo", "Purchase", "Deposit",
                 "Transfer", "JournalEntry", "TaxCode"):
        assert name in DEFAULT_ENTITIES
