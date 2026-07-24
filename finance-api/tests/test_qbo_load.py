import pytest
from sqlalchemy import select
from app.models.qbo import QboAccount
from scripts.qbo_import.load import load_entity

ACCOUNTS = [
    {"Id": "58", "SyncToken": "0", "Name": "Accounts Payable",
     "AcctNum": "2000", "AccountType": "Accounts Payable",
     "AccountSubType": "AccountsPayable", "Active": True,
     "CurrentBalance": 1234.5, "CurrencyRef": {"value": "CAD"},
     "MetaData": {"LastUpdatedTime": "2019-05-02T10:00:00-07:00"}},
]


@pytest.mark.asyncio
async def test_load_accounts_inserts_then_updates(db_session):
    n = await load_entity(db_session, "Account", ACCOUNTS)
    assert n["inserted"] == 1
    row = (await db_session.execute(select(QboAccount))).scalar_one()
    assert row.qbo_id == "58"
    assert row.name == "Accounts Payable"
    assert row.account_type == "Accounts Payable"

    # Re-load same id with a changed name → update, not duplicate.
    ACCOUNTS[0]["Name"] = "A/P"
    n2 = await load_entity(db_session, "Account", ACCOUNTS)
    assert n2["updated"] == 1
    rows = (await db_session.execute(select(QboAccount))).scalars().all()
    assert len(rows) == 1
    assert rows[0].name == "A/P"
