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
