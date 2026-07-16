"""Unified payment executor (Phase 0-B1.5).

THE single implementation of "money goes out": status flip + payment_records
+ posting event (+ PA-PO invoice marking), in one transaction. The three
legacy HTTP entries (epms-api PA action=process, expense-api /pa/{id}/pay and
/expenses/{id}/pay) forward here.

can_pay: JWT role in _PAY_ROLES, OR an ADDITIONAL role of finance_bp /
finance_manager held in identity's user_roles (same physical DB — phase 3
retired the old company_config.role_management assignments).
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_ZERO = Decimal("0")

from app.models.coa import AccountMapping
from app.models.fiscal_period import OPEN, FiscalPeriod
from app.models.mirrors import (
    CompanyConfig, ExpenseApprovalEvent, ExpenseClaim, ExpenseLineItem,
    ExpenseTripItem, Invoice, SodRule, Task, User,
)
from app.models.ap_invoice import ApInvoice
from app.models.bank import BankAccount
from app.models.pa import PaymentApplication
from app.models.payment import PaymentRecord
from app.services import budget_client
from app.schemas.payment_execute import PaymentExecuteRequest, PaymentExecuteResponse
from app.services.posting import emit_event

_PAY_ROLES = {"ap_clerk", "finance_manager", "finance_bp", "system_admin"}


class PaymentPermissionError(Exception):
    pass


async def _user_role_codes(db: AsyncSession, user_id: uuid.UUID, base_role: str) -> set[str]:
    """Primary role + additional roles (identity user_roles, same DB)."""
    codes = {base_role} if base_role else set()
    rows = (await db.execute(sa.text(
        "SELECT role_code FROM user_roles WHERE user_id = :u"), {"u": str(user_id)})).scalars().all()
    codes.update(rows)
    return codes


def _user_holds_assignment(rm: dict, user_id: str, role: str) -> bool:
    """Retained for app/api/v1/coa.py._can_manage (out of Task 5's scope — still
    reads company_config.role_management). can_pay itself no longer calls this;
    see _user_role_codes above."""
    if role == "finance_bp":
        return user_id in [str(x) for x in (rm.get("finance_bp_user_ids") or [])]
    if role == "finance_manager":
        ids = [rm.get("finance_manager_user_id"), rm.get("finance_manager_backup_user_id")]
        return user_id in [str(x) for x in ids if x]
    return False


async def _resolve_bank(db: AsyncSession, bank_account_id: uuid.UUID | None,
                        currency: str) -> BankAccount | None:
    """Validate the chosen funding bank: must exist and match the payment
    currency. Returns the account (for its ledger_account_code) or None."""
    if bank_account_id is None:
        return None
    acct = (await db.execute(
        select(BankAccount).where(BankAccount.id == bank_account_id)
    )).scalar_one_or_none()
    if acct is None:
        raise LookupError("Bank account not found")
    if acct.currency != currency:
        raise ValueError(f"Bank account currency {acct.currency} does not match payment currency {currency}")
    return acct


def _bank_line(amount: Decimal, currency: str, bank: BankAccount | None,
               partner_name: str | None = None) -> dict:
    """Credit-bank posting line; uses the chosen bank's GL cash account when set,
    else falls back to the 'bank' line_role mapping."""
    ln = {"line_role": "bank", "credit": amount, "currency": currency}
    if partner_name:
        ln["partner_name"] = partner_name
    if bank and bank.ledger_account_code:
        ln["account_code"] = bank.ledger_account_code
    return ln


async def _check_can_pay(db: AsyncSession, user: dict) -> None:
    if user.get("role") in _PAY_ROLES:
        return
    user_id = uuid.UUID(str(user.get("sub", "")))
    codes = await _user_role_codes(db, user_id, user.get("role", ""))
    if "finance_bp" in codes or "finance_manager" in codes:
        return
    raise PaymentPermissionError("Insufficient role to record payment")


async def _sod_enabled(db: AsyncSession, rule_code: str) -> bool:
    rule = (await db.execute(
        select(SodRule).where(SodRule.rule_code == rule_code)
    )).scalar_one_or_none()
    return bool(rule and rule.enabled)


async def _check_self_payment(db: AsyncSession, payer_id: uuid.UUID,
                              counterparty_id: uuid.UUID | None, what: str) -> None:
    """FIN-AUD-003 self_payment: the document creator / claimant cannot execute
    its own payment. Rule is config (identity-owned sod_rules) — disabling it
    is an audited act, never a code change."""
    if counterparty_id is None or payer_id != counterparty_id:
        return
    if await _sod_enabled(db, "self_payment"):
        raise PaymentPermissionError(
            f"SoD violation (self_payment): the {what} cannot execute payment "
            "of their own document"
        )


async def _complete_open_tasks(db: AsyncSession, doc_types: list[str], doc_id: uuid.UUID) -> None:
    """Close open inbox tasks (process_pa / process_expense) for the paid doc —
    the approval engine's removed process branch used to do this for PAs."""
    rows = (await db.execute(
        select(Task).where(
            Task.document_type.in_(doc_types),
            Task.document_id == doc_id,
            Task.is_completed.is_(False),
        )
    )).scalars().all()
    now = datetime.now(timezone.utc)
    for t in rows:
        t.is_completed = True
        t.completed_at = now


async def _check_period_open(db: AsyncSession, pay_date: date) -> None:
    """FIN-GL-002 close gate (Phase 0): payments are operational postings —
    blocked in both soft- and hard-closed periods. No row means open."""
    period = pay_date.strftime("%Y-%m")
    row = (await db.execute(
        select(FiscalPeriod).where(FiscalPeriod.period == period)
    )).scalar_one_or_none()
    if row is not None and row.status != OPEN:
        raise ValueError(f"Fiscal period {period} is closed ({row.status})")


async def _stamp_account_codes(db: AsyncSession, lines: list[dict]) -> list[dict]:
    """A1: resolve line_role → COA account_code from account_mappings so every
    posting line carries a ledger account from now on (GL replay depends on it).
    Missing mapping leaves account_code NULL — visible in posting queries, not
    a payment blocker."""
    roles = {ln["line_role"] for ln in lines}
    rows = (await db.execute(
        select(AccountMapping).where(
            AccountMapping.mapping_type == "line_role",
            AccountMapping.source_code.in_(roles),
        )
    )).scalars().all()
    by_role = {r.source_code: r.account_code for r in rows}
    for ln in lines:
        ln.setdefault("account_code", by_role.get(ln["line_role"]))
    return lines


async def _stamp_fx(db: AsyncSession, lines: list[dict], as_of: date) -> list[dict]:
    """A3 (FIN-CASH-003): lock the FX rate on non-CAD lines from exchange_rates
    effective on the business date. No rate found → fx_rate stays 1 (surfaces
    on the A5 FX exception list — never blocks the posting)."""
    from app.crud.bank import rate_on
    cache: dict[str, Decimal | None] = {}
    for ln in lines:
        ccy = ln.get("currency", "CAD")
        if ccy == "CAD":
            continue
        if ccy not in cache:
            cache[ccy] = await rate_on(db, ccy, as_of)
        if cache[ccy] is not None:
            ln.setdefault("fx_rate", cache[ccy])
    return lines


async def _actor_name(db: AsyncSession, user_id: uuid.UUID) -> str:
    row = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    return (row.full_name or row.email) if row else "Unknown"


async def _claim_sales_tax_lines(db: AsyncSession, claim: ExpenseClaim) -> list[dict]:
    """A5: split the claim's sales tax into one sales_tax line per tax_code so
    ITC flows coded into the GST/HST return worksheet. Lines without a code
    aggregate into one uncoded sales_tax line (surfaces on the A5 exception
    list). MIL claims carry no line tax → single uncoded line if any tax."""
    by_code: dict[str | None, Decimal] = {}
    if claim.claim_type != "MIL":
        rows = (await db.execute(
            select(ExpenseLineItem).where(ExpenseLineItem.claim_id == claim.id)
        )).scalars().all()
        for li in rows:
            if li.tax_amount and li.tax_amount != _ZERO:
                by_code[li.tax_code] = by_code.get(li.tax_code, _ZERO) + li.tax_amount
    booked = sum(by_code.values(), _ZERO)
    # reconcile against header tax (covers MIL and any rounding/uncoded remainder)
    remainder = claim.tax_amount - booked
    if remainder != _ZERO:
        by_code[None] = by_code.get(None, _ZERO) + remainder
    return [
        {"line_role": "sales_tax", "debit": amt, "tax_code": code,
         "currency": claim.currency}
        for code, amt in by_code.items() if amt != _ZERO
    ]


async def _book_claim_budget(db: AsyncSession, claim: ExpenseClaim,
                             bearer_token: str | None) -> None:
    """Report actual spend to budget-api — moved verbatim from expense-api's
    _book_budget (A0). Aggregates by (budget_account, cost_center); MIL trips
    carry no cost_center (physical table has no such column) and are skipped
    unless a future migration adds one. Idempotent + fail-open as before."""
    if claim.paid_at is None:
        return
    fiscal_year, month = claim.paid_at.year, claim.paid_at.month

    buckets: dict[tuple[uuid.UUID, uuid.UUID | None], object] = {}
    if claim.claim_type == "MIL":
        rows = (await db.execute(
            select(ExpenseTripItem).where(ExpenseTripItem.claim_id == claim.id)
        )).scalars().all()
        for ti in rows:
            if ti.budget_account_id is None:
                continue
            key = (ti.budget_account_id, None)
            buckets[key] = (buckets.get(key) or 0) + ti.amount
    else:  # EXP / TRV / CFM*
        rows = (await db.execute(
            select(ExpenseLineItem).where(ExpenseLineItem.claim_id == claim.id)
        )).scalars().all()
        for li in rows:
            key = (li.budget_account_id, li.cost_center_id)
            buckets[key] = (buckets.get(key) or 0) + li.net_amount

    lines = [
        {"account_id": account_id, "cost_center_id": cost_center_id,
         "fiscal_year": fiscal_year, "month": month, "amount": amount}
        for (account_id, cost_center_id), amount in buckets.items()
        if cost_center_id is not None and amount > 0
    ]
    if not lines:
        return
    await budget_client.book_expense(
        bearer_token=bearer_token,
        source_doc_id=claim.id,
        source_doc_type="expense_claim",   # historical idempotency key — do not change
        lines=lines,
        notes=f"Claim {claim.claim_number} ({claim.claim_type})",
    )


async def execute(db: AsyncSession, req: PaymentExecuteRequest, user: dict,
                  bearer_token: str | None = None,
                  batch_id: uuid.UUID | None = None) -> PaymentExecuteResponse:
    """Raises LookupError (404), ValueError (409), PaymentPermissionError (403).
    batch_id (A4): tags the payment_record when run as part of a payment batch."""
    await _check_can_pay(db, user)
    recorded_by = uuid.UUID(user["sub"])
    pay_date = req.payment_date or date.today()
    await _check_period_open(db, pay_date)

    if req.doc_kind in ("pa", "pa_dir"):
        pa = (await db.execute(
            select(PaymentApplication).where(PaymentApplication.id == req.doc_id)
        )).scalar_one_or_none()
        if pa is None:
            raise LookupError("Payment application not found")
        await _check_self_payment(db, recorded_by, pa.created_by, "PA creator")
        if pa.status != "approved":
            raise ValueError(f"Cannot pay PA in status '{pa.status}'")
        # actual kind derives from the document, not the client
        doc_kind = "pa_dir" if pa.po_id is None else "pa"
        pa.status = "processed"
        await _complete_open_tasks(db, ["pa", "pa_dir"], pa.id)

        if doc_kind == "pa":
            # A4b partial payment: a prepayment PA leaves the invoice
            # partially_paid (still an open item); a regular/balance/settlement
            # PA closes it. Reuses the existing PA prepayment model — no generic
            # installment engine.
            new_inv_status = "partially_paid" if pa.pa_type == "prepayment" else "paid"
            for inv_id_str in pa.invoice_ids:
                try:
                    inv_id = uuid.UUID(str(inv_id_str))
                except ValueError:
                    continue
                inv = (await db.execute(
                    select(Invoice).where(Invoice.id == inv_id)
                )).scalar_one_or_none()
                if inv and inv.status in ("matched", "approved", "partially_paid"):
                    inv.status = new_inv_status

        # Writeback to finance-owned ap_invoices (EPMS + OA sources): flip
        # status + paid_amount so AP open-items/aging reflect payment (Plan 4).
        ap_paid_status = "partially_paid" if pa.pa_type == "prepayment" else "paid"
        for inv_id_str in pa.invoice_ids:
            try:
                _aid = uuid.UUID(str(inv_id_str))
            except ValueError:
                continue
            ap = (await db.execute(
                select(ApInvoice).where(ApInvoice.source_invoice_id == _aid)
            )).scalar_one_or_none()
            if ap and ap.status in ("posted", "partially_paid"):
                ap.status = ap_paid_status
                if ap_paid_status == "paid":
                    ap.paid_amount = ap.total_amount

        bank = await _resolve_bank(db, req.bank_account_id, pa.currency)
        amount = req.amount_paid if req.amount_paid is not None else pa.payment_amount
        record = PaymentRecord(
            doc_kind=doc_kind, doc_id=pa.id, doc_number=pa.pa_number,
            pa_id=pa.id, pa_number=pa.pa_number,
            vendor_id=pa.vendor_id, vendor_name=pa.vendor_name,
            payment_date=pay_date, payment_method=req.payment_method,
            reference=req.reference, amount=amount, currency=pa.currency,
            recorded_by=recorded_by, notes=req.notes, batch_id=batch_id,
            bank_account_id=req.bank_account_id,
        )
        db.add(record)
        await db.flush()

        event_id = await emit_event(
            db,
            source_service="finance",
            source_doc_type=doc_kind,
            source_doc_id=pa.id,
            source_doc_number=pa.pa_number,
            event_type="payment",
            lines=await _stamp_fx(db, await _stamp_account_codes(db, [
                {"line_role": "accounts_payable", "debit": pa.payment_amount,
                 "partner_id": pa.vendor_id, "partner_name": pa.vendor_name,
                 "currency": pa.currency},
                _bank_line(pa.payment_amount, pa.currency, bank),
            ]), pay_date),
        )
        return PaymentExecuteResponse(
            doc_kind=doc_kind, doc_id=pa.id, doc_number=pa.pa_number,
            new_status="processed", payment_record_id=record.id,
            posting_event_id=event_id,
        )

    # expense_claim
    claim = (await db.execute(
        select(ExpenseClaim).where(ExpenseClaim.id == req.doc_id)
    )).scalar_one_or_none()
    if claim is None:
        raise LookupError("Expense claim not found")
    await _check_self_payment(db, recorded_by, claim.employee_id, "claimant")
    if claim.status != "approved":
        raise ValueError(f"Cannot pay claim in status '{claim.status}'")
    claim.status = "paid"
    claim.paid_at = datetime.now(timezone.utc)
    await _complete_open_tasks(db, [claim.claim_type.lower()], claim.id)

    # A0: OA-domain follow-ups, moved from expense-api's after_payment —
    # audit row (same transaction) + idempotent budget booking (fail-open HTTP)
    db.add(ExpenseApprovalEvent(
        claim_id=claim.id,
        actor_id=recorded_by,
        actor_name=await _actor_name(db, recorded_by),
        action="pay",
        comment=req.notes,
        from_status="approved",
        to_status="paid",
    ))
    await _book_claim_budget(db, claim, bearer_token)

    bank = await _resolve_bank(db, req.bank_account_id, claim.currency)
    amount = req.amount_paid if req.amount_paid is not None else claim.total_amount
    record = PaymentRecord(
        doc_kind="expense_claim", doc_id=claim.id, doc_number=claim.claim_number,
        payment_date=pay_date, payment_method=req.payment_method,
        reference=req.reference, amount=amount, currency=claim.currency,
        recorded_by=recorded_by, notes=req.notes, batch_id=batch_id,
        bank_account_id=req.bank_account_id,
    )
    db.add(record)
    await db.flush()

    tax_lines = await _claim_sales_tax_lines(db, claim)
    event_id = await emit_event(
        db,
        source_service="finance",
        source_doc_type=claim.claim_type.lower(),
        source_doc_id=claim.id,
        source_doc_number=claim.claim_number,
        event_type="expense_paid",
        lines=await _stamp_fx(db, await _stamp_account_codes(db, [
            {"line_role": "employee_expense", "debit": claim.net_amount,
             "partner_name": claim.employee_name, "currency": claim.currency},
            *tax_lines,
            _bank_line(claim.total_amount, claim.currency, bank, partner_name=claim.employee_name),
        ]), pay_date),
    )
    return PaymentExecuteResponse(
        doc_kind="expense_claim", doc_id=claim.id, doc_number=claim.claim_number,
        new_status="paid", payment_record_id=record.id,
        posting_event_id=event_id,
    )
