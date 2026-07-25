"""Declarative entity registry — the single source both extract and load read.

Each Entity says: the QBO entity name, its header model, optional line model, and
how to build header/line column dicts. Adding an entity is one registry line plus
its model — no new load code.
"""
from dataclasses import dataclass
from typing import Callable

from app.models import qbo as m
from scripts.qbo_import import mappers


@dataclass(frozen=True)
class Entity:
    name: str                       # QBO entity name, e.g. "Bill"
    model: type | None = None                     # header SQLAlchemy model
    header: Callable[[dict], dict] | None = None  # raw -> header column dict
    line_model: type | None = None  # line SQLAlchemy model, if any
    line: Callable[[dict, str], dict] | None = None  # (raw_line, parent_qbo_id) -> line dict
    raw_only: bool = False           # True: no typed columns, mirror into qbo_raw


REGISTRY: list[Entity] = []

REGISTRY.append(Entity(name="Account", model=m.QboAccount, header=mappers.account_header))
REGISTRY.append(Entity(name="Vendor", model=m.QboVendor, header=mappers.vendor_header))
REGISTRY.append(Entity(
    name="Bill", model=m.QboBill,
    header=lambda o: mappers.txn_header(o, counterparty="VendorRef"),
    line_model=m.QboBillLine, line=mappers.txn_line,
))
REGISTRY.append(Entity(
    name="BillPayment", model=m.QboBillPayment,
    header=mappers.billpayment_header,
    line_model=m.QboBillPaymentLine, line=mappers.txn_line,
))
REGISTRY.append(Entity(
    name="VendorCredit", model=m.QboVendorCredit,
    header=lambda o: mappers.txn_header(o, counterparty="VendorRef"),
    line_model=m.QboVendorCreditLine, line=mappers.txn_line,
))
REGISTRY.append(Entity(
    name="Invoice", model=m.QboInvoice,
    header=lambda o: mappers.txn_header(o, counterparty="CustomerRef"),
    line_model=m.QboInvoiceLine, line=mappers.txn_line,
))
REGISTRY.append(Entity(
    name="Payment", model=m.QboPayment, header=mappers.payment_header,
    line_model=m.QboPaymentLine, line=mappers.txn_line,
))
REGISTRY.append(Entity(
    name="CreditMemo", model=m.QboCreditMemo,
    header=lambda o: mappers.txn_header(o, counterparty="CustomerRef"),
    line_model=m.QboCreditMemoLine, line=mappers.txn_line,
))
REGISTRY.append(Entity(
    name="Purchase", model=m.QboPurchase, header=mappers.purchase_header,
    line_model=m.QboPurchaseLine, line=mappers.txn_line,
))
REGISTRY.append(Entity(
    name="Deposit", model=m.QboDeposit, header=mappers.deposit_header,
    line_model=m.QboDepositLine, line=mappers.txn_line,
))
REGISTRY.append(Entity(name="Transfer", model=m.QboTransfer, header=mappers.transfer_header))
REGISTRY.append(Entity(
    name="JournalEntry", model=m.QboJournalEntry,
    header=lambda o: mappers.txn_header(o, counterparty=None),
    line_model=m.QboJournalEntryLine, line=mappers.txn_line,
))


for _name in ("Term", "TaxCode", "TaxRate", "TaxAgency", "PaymentMethod", "Class",
              "Item", "CustomerType", "CompanyCurrency", "Employee", "Department"):
    REGISTRY.append(Entity(name=_name, raw_only=True))


def by_name(name: str) -> Entity:
    for e in REGISTRY:
        if e.name == name:
            return e
    raise KeyError(f"unknown QBO entity: {name}")
