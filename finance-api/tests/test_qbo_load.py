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


from app.models.qbo import QboVendor

VENDORS = [
    {"Id": "64", "SyncToken": "1", "DisplayName": "Acme Inc",
     "PrintOnCheckName": "Acme Inc", "Active": True,
     "Balance": 500.0, "CurrencyRef": {"value": "CAD"},
     "T4AEligible": False, "T5018Eligible": True,
     "PrimaryEmailAddr": {"Address": "ap@acme.test"},
     "MetaData": {"LastUpdatedTime": "2019-05-02T10:00:00-07:00"}},
]


@pytest.mark.asyncio
async def test_load_vendor_keeps_canadian_flags_in_raw(db_session):
    await load_entity(db_session, "Vendor", VENDORS)
    v = (await db_session.execute(select(QboVendor))).scalar_one()
    assert v.qbo_id == "64"
    assert v.display_name == "Acme Inc"
    assert v.currency == "CAD"
    assert v.email == "ap@acme.test"
    # Canadian slip flags are not columns — they live in raw.
    assert v.raw["T5018Eligible"] is True
