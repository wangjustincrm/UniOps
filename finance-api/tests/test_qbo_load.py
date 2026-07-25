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


from app.models.qbo import QboBill, QboBillLine

BILLS = [
    {"Id": "1645", "SyncToken": "0", "DocNumber": "INV-001",
     "TxnDate": "2019-05-01", "DueDate": "2019-06-01",
     "CurrencyRef": {"value": "USD"}, "ExchangeRate": 1.35,
     "TotalAmt": 100.0, "HomeTotalAmt": 135.0, "Balance": 100.0, "HomeBalance": 135.0,
     "GlobalTaxCalculation": "TaxExcluded",
     "VendorRef": {"value": "64", "name": "Acme Inc"},
     "MetaData": {"LastUpdatedTime": "2019-05-02T10:00:00-07:00"},
     "Line": [
        {"Id": "1", "LineNum": 1, "Amount": 100.0, "Description": "widgets",
         "DetailType": "AccountBasedExpenseLineDetail",
         "AccountBasedExpenseLineDetail": {
            "AccountRef": {"value": "141", "name": "COGS"}, "TaxCodeRef": {"value": "2"}}},
     ]},
]


@pytest.mark.asyncio
async def test_load_bill_header_and_lines_multicurrency(db_session):
    await load_entity(db_session, "Bill", BILLS)
    b = (await db_session.execute(select(QboBill))).scalar_one()
    assert b.qbo_id == "1645"
    assert b.currency == "USD"
    assert float(b.exchange_rate) == 1.35
    assert float(b.total_amt) == 100.0
    assert float(b.home_total_amt) == 135.0
    assert b.counterparty_id == "64"

    lines = (await db_session.execute(select(QboBillLine))).scalars().all()
    assert len(lines) == 1
    assert lines[0].account_id == "141"
    assert lines[0].tax_code_ref == "2"


@pytest.mark.asyncio
async def test_reload_bill_replaces_lines_not_duplicates(db_session):
    await load_entity(db_session, "Bill", BILLS)
    await load_entity(db_session, "Bill", BILLS)  # second load
    lines = (await db_session.execute(select(QboBillLine))).scalars().all()
    assert len(lines) == 1  # delete-then-insert, no dup


from app.models.qbo import QboBillPayment, QboBillPaymentLine

BILLPAYMENTS = [
    {"Id": "22", "SyncToken": "0", "DocNumber": "CHK-1",
     "TxnDate": "2019-05-26", "PayType": "Check",
     "CurrencyRef": {"value": "CAD"}, "ExchangeRate": 1,
     "TotalAmt": 100.0, "VendorRef": {"value": "64", "name": "Acme Inc"},
     "MetaData": {"LastUpdatedTime": "2019-05-27T10:00:00-07:00"},
     "Line": [
        {"Amount": 100.0, "LinkedTxn": [{"TxnId": "1645", "TxnType": "Bill"}]},
     ]},
]


@pytest.mark.asyncio
async def test_billpayment_line_captures_reconciliation(db_session):
    await load_entity(db_session, "BillPayment", BILLPAYMENTS)
    p = (await db_session.execute(select(QboBillPayment))).scalar_one()
    assert p.counterparty_id == "64"
    assert p.pay_type == "Check"

    line = (await db_session.execute(select(QboBillPaymentLine))).scalar_one()
    # The payment->bill reconciliation link:
    assert line.linked_txn_id == "1645"
    assert line.linked_txn_type == "Bill"
    assert float(line.amount) == 100.0


from app.models.qbo import QboVendorCredit, QboVendorCreditLine

VENDORCREDITS = [
    {"Id": "900", "SyncToken": "0", "DocNumber": "VC-1", "TxnDate": "2019-07-01",
     "CurrencyRef": {"value": "CAD"}, "ExchangeRate": 1, "TotalAmt": 50.0,
     "VendorRef": {"value": "64", "name": "Acme Inc"},
     "MetaData": {"LastUpdatedTime": "2019-07-02T10:00:00-07:00"},
     "Line": [
        {"Id": "1", "LineNum": 1, "Amount": 50.0,
         "DetailType": "AccountBasedExpenseLineDetail",
         "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "141", "name": "COGS"}}},
     ]},
]


@pytest.mark.asyncio
async def test_load_vendor_credit(db_session):
    await load_entity(db_session, "VendorCredit", VENDORCREDITS)
    vc = (await db_session.execute(select(QboVendorCredit))).scalar_one()
    assert vc.qbo_id == "900"
    assert vc.counterparty_id == "64"
    line = (await db_session.execute(select(QboVendorCreditLine))).scalar_one()
    assert line.account_id == "141"


from app.models.qbo import QboInvoice, QboInvoiceLine

INVOICES = [
    {"Id": "9", "SyncToken": "0", "DocNumber": "1001", "TxnDate": "2019-05-26",
     "DueDate": "2019-06-25", "CurrencyRef": {"value": "CAD"}, "ExchangeRate": 1,
     "TotalAmt": 108.0, "HomeTotalAmt": 108.0, "Balance": 0,
     "CustomerRef": {"value": "1", "name": "Bird Sanctuary"},
     "MetaData": {"LastUpdatedTime": "2019-05-27T10:00:00-07:00"},
     "Line": [
        {"Id": "1", "LineNum": 1, "Amount": 100.0, "Description": "Service",
         "DetailType": "SalesItemLineDetail",
         "SalesItemLineDetail": {"ItemAccountRef": {"value": "45", "name": "Sales"},
                                 "TaxCodeRef": {"value": "TAX"}}},
     ]},
]


@pytest.mark.asyncio
async def test_load_invoice_customer_and_lines(db_session):
    await load_entity(db_session, "Invoice", INVOICES)
    inv = (await db_session.execute(select(QboInvoice))).scalar_one()
    assert inv.qbo_id == "9"
    assert inv.counterparty_id == "1"
    assert inv.counterparty_name == "Bird Sanctuary"
    lines = (await db_session.execute(select(QboInvoiceLine))).scalars().all()
    assert len(lines) == 1
    assert lines[0].detail_type == "SalesItemLineDetail"


from app.models.qbo import QboPayment, QboPaymentLine

PAYMENTS = [
    {"Id": "31", "SyncToken": "0", "TxnDate": "2019-05-27",
     "CurrencyRef": {"value": "CAD"}, "ExchangeRate": 1, "TotalAmt": 108.0,
     "CustomerRef": {"value": "1", "name": "Bird Sanctuary"},
     "DepositToAccountRef": {"value": "35", "name": "Chequing"},
     "MetaData": {"LastUpdatedTime": "2019-05-28T10:00:00-07:00"},
     "Line": [{"Amount": 108.0, "LinkedTxn": [{"TxnId": "9", "TxnType": "Invoice"}]}]},
]


@pytest.mark.asyncio
async def test_load_payment_reconciliation_and_deposit(db_session):
    await load_entity(db_session, "Payment", PAYMENTS)
    p = (await db_session.execute(select(QboPayment))).scalar_one()
    assert p.counterparty_id == "1"
    assert p.deposit_to_account_id == "35"
    line = (await db_session.execute(select(QboPaymentLine))).scalar_one()
    assert line.linked_txn_id == "9"
    assert line.linked_txn_type == "Invoice"


from app.models.qbo import QboCreditMemo, QboCreditMemoLine

CREDITMEMOS = [
    {"Id": "77", "SyncToken": "0", "DocNumber": "CM-1", "TxnDate": "2019-06-01",
     "CurrencyRef": {"value": "CAD"}, "ExchangeRate": 1, "TotalAmt": 20.0,
     "CustomerRef": {"value": "1", "name": "Bird Sanctuary"},
     "MetaData": {"LastUpdatedTime": "2019-06-02T10:00:00-07:00"},
     "Line": [{"Id": "1", "LineNum": 1, "Amount": 20.0,
               "DetailType": "SalesItemLineDetail",
               "SalesItemLineDetail": {"ItemRef": {"value": "6"}}}]},
]


@pytest.mark.asyncio
async def test_load_credit_memo(db_session):
    await load_entity(db_session, "CreditMemo", CREDITMEMOS)
    cm = (await db_session.execute(select(QboCreditMemo))).scalar_one()
    assert cm.qbo_id == "77"
    assert cm.counterparty_id == "1"
    assert (await db_session.execute(select(QboCreditMemoLine))).scalar_one().amount == 20


from app.models.qbo import QboPurchase, QboPurchaseLine

PURCHASES = [
    {"Id": "500", "SyncToken": "0", "TxnDate": "2019-05-01",
     "CurrencyRef": {"value": "CAD"}, "ExchangeRate": 1, "TotalAmt": 75.0,
     "PaymentType": "Check", "Credit": False,
     "AccountRef": {"value": "224", "name": "Bank"},
     "EntityRef": {"value": "64", "name": "Acme Inc", "type": "Vendor"},
     "MetaData": {"LastUpdatedTime": "2019-05-02T10:00:00-07:00"},
     "Line": [{"Id": "1", "LineNum": 1, "Amount": 75.0,
               "DetailType": "AccountBasedExpenseLineDetail",
               "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "141"}}}]},
]


@pytest.mark.asyncio
async def test_load_purchase_entity_and_payment(db_session):
    await load_entity(db_session, "Purchase", PURCHASES)
    p = (await db_session.execute(select(QboPurchase))).scalar_one()
    assert p.payment_type == "Check"
    assert p.account_id == "224"
    assert p.entity_id == "64"
    assert p.entity_type == "Vendor"
    assert (await db_session.execute(select(QboPurchaseLine))).scalar_one().account_id == "141"


from app.models.qbo import QboDeposit, QboDepositLine

DEPOSITS = [
    {"Id": "600", "SyncToken": "0", "TxnDate": "2019-05-30",
     "CurrencyRef": {"value": "CAD"}, "ExchangeRate": 1, "TotalAmt": 108.0,
     "HomeTotalAmt": 108.0, "DepositToAccountRef": {"value": "35", "name": "Chequing"},
     "MetaData": {"LastUpdatedTime": "2019-05-31T10:00:00-07:00"},
     "Line": [{"Amount": 108.0, "LinkedTxn": [{"TxnId": "31", "TxnType": "Payment"}]}]},
]


@pytest.mark.asyncio
async def test_load_deposit(db_session):
    await load_entity(db_session, "Deposit", DEPOSITS)
    d = (await db_session.execute(select(QboDeposit))).scalar_one()
    assert d.deposit_to_account_id == "35"
    line = (await db_session.execute(select(QboDepositLine))).scalar_one()
    assert line.linked_txn_id == "31"
    assert line.linked_txn_type == "Payment"


from app.models.qbo import QboTransfer

TRANSFERS = [
    {"Id": "1", "SyncToken": "0", "TxnDate": "2017-01-09",
     "CurrencyRef": {"value": "USD"}, "ExchangeRate": 1.3, "Amount": 1200000.0,
     "FromAccountRef": {"value": "67", "name": "CAD Chequing"},
     "ToAccountRef": {"value": "66", "name": "USD Chequing"},
     "MetaData": {"LastUpdatedTime": "2017-01-09T00:00:00-08:00"}},
]


@pytest.mark.asyncio
async def test_load_transfer_no_lines(db_session):
    await load_entity(db_session, "Transfer", TRANSFERS)
    t = (await db_session.execute(select(QboTransfer))).scalar_one()
    assert t.from_account_id == "67"
    assert t.to_account_id == "66"
    assert float(t.total_amt) == 1200000.0
    assert t.currency == "USD"


from app.models.qbo import QboJournalEntry, QboJournalEntryLine

JOURNALS = [
    {"Id": "6", "SyncToken": "0", "DocNumber": "JE-1", "TxnDate": "2019-05-13",
     "CurrencyRef": {"value": "CAD"}, "ExchangeRate": 1, "TotalAmt": 100.0,
     "MetaData": {"LastUpdatedTime": "2019-05-14T10:00:00-07:00"},
     "Line": [
        {"Id": "0", "Amount": 100.0, "DetailType": "JournalEntryLineDetail",
         "JournalEntryLineDetail": {"PostingType": "Debit", "AccountRef": {"value": "38", "name": "Truck"}}},
        {"Id": "1", "Amount": 100.0, "DetailType": "JournalEntryLineDetail",
         "JournalEntryLineDetail": {"PostingType": "Credit", "AccountRef": {"value": "34", "name": "Equity"}}},
     ]},
]


@pytest.mark.asyncio
async def test_load_journal_entry_debit_credit(db_session):
    await load_entity(db_session, "JournalEntry", JOURNALS)
    je = (await db_session.execute(select(QboJournalEntry))).scalar_one()
    assert je.qbo_id == "6"
    lines = (await db_session.execute(
        select(QboJournalEntryLine).order_by(QboJournalEntryLine.id))).scalars().all()
    assert [l.posting_type for l in lines] == ["Debit", "Credit"]
    assert [l.account_id for l in lines] == ["38", "34"]


from app.models.qbo import QboRaw

TAXCODES = [
    {"Id": "2", "Name": "GST", "MetaData": {"LastUpdatedTime": "2019-01-01T00:00:00-08:00"}},
    {"Id": "3", "Name": "HST", "MetaData": {"LastUpdatedTime": "2019-01-02T00:00:00-08:00"}},
]


@pytest.mark.asyncio
async def test_load_raw_entity(db_session):
    n = await load_entity(db_session, "TaxCode", TAXCODES)
    assert n["inserted"] == 2
    rows = (await db_session.execute(
        select(QboRaw).where(QboRaw.entity_type == "TaxCode").order_by(QboRaw.qbo_id))).scalars().all()
    assert [r.qbo_id for r in rows] == ["2", "3"]
    assert rows[0].payload["Name"] == "GST"

    # Re-load with a change → update, not duplicate.
    TAXCODES[0]["Name"] = "GST-13"
    n2 = await load_entity(db_session, "TaxCode", TAXCODES)
    assert n2["updated"] == 2
    row = (await db_session.execute(
        select(QboRaw).where(QboRaw.entity_type == "TaxCode", QboRaw.qbo_id == "2"))).scalar_one()
    assert row.payload["Name"] == "GST-13"
