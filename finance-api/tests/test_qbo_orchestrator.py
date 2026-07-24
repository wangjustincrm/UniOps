# tests/test_qbo_orchestrator.py
import uuid

import pytest
from sqlalchemy import select
from app.models.qbo import QboAccount, QboSyncRun
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
