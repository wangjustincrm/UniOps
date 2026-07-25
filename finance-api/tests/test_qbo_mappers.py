from scripts.qbo_import.mappers import ref_id, ref_name, txn_header, txn_line, to_dt

# Synthetic payload — real STRUCTURE, fake values (never commit real QBO data).
SAMPLE_BILL = {
    "Id": "1645",
    "SyncToken": "3",
    "DocNumber": "INV-001",
    "TxnDate": "2019-05-01",
    "DueDate": "2019-06-01",
    "CurrencyRef": {"value": "CAD", "name": "Canadian Dollar"},
    "ExchangeRate": 1,
    "TotalAmt": 38040.0,
    "Balance": 0,
    "HomeBalance": 0,
    "GlobalTaxCalculation": "TaxExcluded",
    "PrivateNote": "note",
    "VendorRef": {"value": "64", "name": "Acme Inc"},
    "MetaData": {"LastUpdatedTime": "2019-05-02T10:00:00-07:00"},
    "Line": [
        {
            "Id": "1", "LineNum": 1, "Amount": 100.0,
            "Description": "widgets",
            "DetailType": "AccountBasedExpenseLineDetail",
            "AccountBasedExpenseLineDetail": {
                "AccountRef": {"value": "141", "name": "Construction"},
                "TaxCodeRef": {"value": "2"},
            },
        }
    ],
}


def test_ref_helpers():
    assert ref_id(SAMPLE_BILL, "VendorRef") == "64"
    assert ref_name(SAMPLE_BILL, "VendorRef") == "Acme Inc"
    assert ref_id(SAMPLE_BILL, "MissingRef") is None


def test_to_dt_is_tz_aware():
    dt = to_dt("2019-05-02T10:00:00-07:00")
    assert dt.utcoffset() is not None  # tz preserved, not naive


def test_txn_header_maps_amounts_and_currency():
    h = txn_header(SAMPLE_BILL, counterparty="VendorRef")
    assert h["qbo_id"] == "1645"
    assert h["sync_token"] == "3"
    assert h["doc_number"] == "INV-001"
    assert h["currency"] == "CAD"
    assert h["exchange_rate"] == 1
    assert h["total_amt"] == 38040.0
    assert h["counterparty_id"] == "64"
    assert h["counterparty_name"] == "Acme Inc"
    assert h["global_tax_calc"] == "TaxExcluded"
    assert h["last_updated_time"].utcoffset() is not None
    assert h["raw"] == SAMPLE_BILL


def test_txn_line_maps_account_and_tax():
    line = txn_line(SAMPLE_BILL["Line"][0], parent_qbo_id="1645")
    assert line["parent_qbo_id"] == "1645"
    assert line["line_num"] == 1
    assert line["amount"] == 100.0
    assert line["detail_type"] == "AccountBasedExpenseLineDetail"
    assert line["account_id"] == "141"
    assert line["account_name"] == "Construction"
    assert line["tax_code_ref"] == "2"


def test_txn_line_captures_posting_type_for_journal_lines():
    je_line = {
        "Id": "0", "Amount": 100.0, "DetailType": "JournalEntryLineDetail",
        "JournalEntryLineDetail": {
            "PostingType": "Credit",
            "AccountRef": {"value": "285", "name": "A/P"},
        },
    }
    line = txn_line(je_line, parent_qbo_id="9")
    assert line["posting_type"] == "Credit"
    assert line["account_id"] == "285"


def test_txn_line_posting_type_none_for_non_journal():
    normal = {
        "Id": "1", "Amount": 10.0, "DetailType": "AccountBasedExpenseLineDetail",
        "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "1"}},
    }
    assert txn_line(normal, parent_qbo_id="1")["posting_type"] is None
