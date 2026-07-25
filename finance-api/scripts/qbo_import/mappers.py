"""Pure functions from a raw QBO entity dict to qbo_* column dicts.

No DB or network here — trivially unit-testable against synthetic payloads.
The `*_DETAIL` key that holds a line's account/tax varies by DetailType; we scan
the known detail keys rather than hardcode one.
"""
from datetime import datetime

# DetailType -> the key holding its AccountRef/TaxCodeRef.
_LINE_DETAIL_KEYS = (
    "AccountBasedExpenseLineDetail",
    "ItemBasedExpenseLineDetail",
    "JournalEntryLineDetail",
    "DepositLineDetail",
    "SalesItemLineDetail",
)


def ref_id(obj: dict, key: str) -> str | None:
    ref = obj.get(key)
    return ref.get("value") if isinstance(ref, dict) else None


def ref_name(obj: dict, key: str) -> str | None:
    ref = obj.get(key)
    return ref.get("name") if isinstance(ref, dict) else None


def to_dt(s: str | None) -> datetime | None:
    """Parse a QBO timestamp preserving its timezone offset (never naive)."""
    if not s:
        return None
    # QBO uses ISO-8601 with an offset, e.g. 2019-05-02T10:00:00-07:00.
    return datetime.fromisoformat(s)


def last_updated(obj: dict) -> datetime | None:
    return to_dt((obj.get("MetaData") or {}).get("LastUpdatedTime"))


def txn_header(obj: dict, counterparty: str) -> dict:
    """Common transaction-header columns. `counterparty` is VendorRef/CustomerRef."""
    return {
        "qbo_id": obj["Id"],
        "sync_token": obj.get("SyncToken"),
        "doc_number": obj.get("DocNumber"),
        "txn_date": obj.get("TxnDate"),
        "due_date": obj.get("DueDate"),
        "currency": ref_id(obj, "CurrencyRef"),
        "exchange_rate": obj.get("ExchangeRate"),
        "total_amt": obj.get("TotalAmt"),
        "home_total_amt": obj.get("HomeTotalAmt"),
        "balance": obj.get("Balance"),
        "home_balance": obj.get("HomeBalance"),
        "global_tax_calc": obj.get("GlobalTaxCalculation"),
        "private_note": obj.get("PrivateNote"),
        "counterparty_id": ref_id(obj, counterparty),
        "counterparty_name": ref_name(obj, counterparty),
        "last_updated_time": last_updated(obj),
        "raw": obj,
    }


def _line_detail(line: dict) -> dict:
    for k in _LINE_DETAIL_KEYS:
        if isinstance(line.get(k), dict):
            return line[k]
    return {}


def txn_line(line: dict, parent_qbo_id: str) -> dict:
    """Common line columns. `linked_txn_*` are filled for payment-type lines."""
    detail = _line_detail(line)
    linked = (line.get("LinkedTxn") or [{}])[0] if line.get("LinkedTxn") else {}
    return {
        "parent_qbo_id": parent_qbo_id,
        "line_num": line.get("LineNum"),
        "amount": line.get("Amount"),
        "detail_type": line.get("DetailType"),
        "account_id": ref_id(detail, "AccountRef"),
        "account_name": ref_name(detail, "AccountRef"),
        "tax_code_ref": ref_id(detail, "TaxCodeRef"),
        "posting_type": detail.get("PostingType"),
        "description": line.get("Description"),
        "linked_txn_id": linked.get("TxnId"),
        "linked_txn_type": linked.get("TxnType"),
        "raw": line,
    }


def billpayment_header(obj: dict) -> dict:
    h = txn_header(obj, counterparty="VendorRef")
    h["pay_type"] = obj.get("PayType")
    return h


def payment_header(obj: dict) -> dict:
    h = txn_header(obj, counterparty="CustomerRef")
    h["deposit_to_account_id"] = ref_id(obj, "DepositToAccountRef")
    h["deposit_to_account_name"] = ref_name(obj, "DepositToAccountRef")
    return h


def account_header(obj: dict) -> dict:
    return {
        "qbo_id": obj["Id"],
        "sync_token": obj.get("SyncToken"),
        "name": obj.get("Name"),
        "acct_num": obj.get("AcctNum"),
        "account_type": obj.get("AccountType"),
        "account_sub_type": obj.get("AccountSubType"),
        "currency": ref_id(obj, "CurrencyRef"),
        "current_balance": obj.get("CurrentBalance"),
        "active": obj.get("Active"),
        "last_updated_time": last_updated(obj),
        "raw": obj,
    }


def purchase_header(obj: dict) -> dict:
    h = txn_header(obj, counterparty=None)
    h["payment_type"] = obj.get("PaymentType")
    h["is_credit"] = obj.get("Credit")
    h["account_id"] = ref_id(obj, "AccountRef")
    h["account_name"] = ref_name(obj, "AccountRef")
    ent = obj.get("EntityRef") or {}
    h["entity_id"] = ent.get("value")
    h["entity_name"] = ent.get("name")
    h["entity_type"] = ent.get("type")
    return h


def deposit_header(obj: dict) -> dict:
    h = txn_header(obj, counterparty=None)
    h["deposit_to_account_id"] = ref_id(obj, "DepositToAccountRef")
    h["deposit_to_account_name"] = ref_name(obj, "DepositToAccountRef")
    return h


def vendor_header(obj: dict) -> dict:
    email = (obj.get("PrimaryEmailAddr") or {}).get("Address")
    return {
        "qbo_id": obj["Id"],
        "sync_token": obj.get("SyncToken"),
        "display_name": obj.get("DisplayName"),
        "print_on_check_name": obj.get("PrintOnCheckName"),
        "currency": ref_id(obj, "CurrencyRef"),
        "balance": obj.get("Balance"),
        "email": email,
        "active": obj.get("Active"),
        "last_updated_time": last_updated(obj),
        "raw": obj,
    }
