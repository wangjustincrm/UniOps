"""Unified payment executor (Phase 0-B1.5).

THE single implementation of "money goes out": status flip + payment_records
+ posting event (+ PA-PO invoice marking), in one transaction. The three
legacy HTTP entries (epms-api PA action=process, expense-api /pa/{id}/pay and
/expenses/{id}/pay) forward here.

can_pay: primary (JWT) role in _PAY_ROLES, OR an ADDITIONAL role (identity's
user_roles) in _PAY_ROLES_ASSIGNED = _PAY_ROLES minus system_admin — this
codebase treats system_admin as primary-role-only (see budget_scope.py's
FULL_ACCESS_PRIMARY vs FULL_ACCESS_ASSIGNED).
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
    ExpenseApprovalEvent, ExpenseClaim, ExpenseLineItem,
    ExpenseTripItem, Invoice, SodRule, Task, User,
)
from app.models.ap_invoice import ApInvoice
from app.models.bank import BankAccount
from app.models.pa import PaymentApplication
from app.models.payment import PaymentRecord
from app.crud import vendor_credit as vendor_credit_crud
from app.services import budget_client
from app.schemas.payment_execute import PaymentExecuteRequest, PaymentExecuteResponse
from app.services.posting import emit_event

# Segregation of duties (2026-08-13): payment EXECUTION is split out of AP
# Clerk. ap_clerk is deliberately REMOVED, not left in alongside
# payment_officer — that removal is the entire point of the change; AP Clerk
# still reads finance data (see _FINANCE_ROLES in app/core/deps.py), it just
# can no longer move money. finance_manager / finance_bp / system_admin stay
# as the availability fallback so payment doesn't deadlock while the
# payment_officer holder is away.
_PAY_ROLES = {"payment_officer", "finance_manager", "finance_bp", "system_admin"}

# Roles that confer payment authority when held as an ADDITIONAL (assigned)
# role. system_admin is deliberately EXCLUDED here (fix round 1, 2026-08-13):
# this codebase treats system_admin as a PRIMARY-role grant only — see
# budget_scope.py's FULL_ACCESS_PRIMARY (has system_admin) vs
# FULL_ACCESS_ASSIGNED (does not), and the same split in admin.py's
# require_system_admin and the shared uniops_authz package. Deriving from
# _PAY_ROLES (rather than a second hand-maintained literal) keeps this from
# drifting the next time _PAY_ROLES changes.
_PAY_ROLES_ASSIGNED = _PAY_ROLES - {"system_admin"}


class PaymentPermissionError(Exception):
    pass


async def _user_role_codes(db: AsyncSession, user_id: uuid.UUID, base_role: str) -> set[str]:
    """Primary role + additional roles (identity user_roles, same DB)."""
    codes = {base_role} if base_role else set()
    rows = (await db.execute(sa.text(
        "SELECT role_code FROM user_roles WHERE user_id = :u"), {"u": str(user_id)})).scalars().all()
    codes.update(rows)
    return codes


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
    # Generalized against _PAY_ROLES_ASSIGNED (2026-08-13, narrowed in fix
    # round 1) rather than a hardcoded finance_bp/finance_manager check —
    # payment_officer must also qualify when held as an ADDITIONAL role
    # (identity user_roles), which is how it is expected to be assigned in
    # production. system_admin is excluded from this branch on purpose: see
    # _PAY_ROLES_ASSIGNED's comment above.
    if codes & _PAY_ROLES_ASSIGNED:
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


async def _check_invoice_link(db: AsyncSession, pa: PaymentApplication) -> None:
    """Refuse to pay a PO-based PA that links no invoice while its PO still
    carries a payable one nobody has claimed.

    Everything downstream of payment reads pa.invoice_ids: the two loops in
    execute() that close the invoice and its ap_invoices row, the remittance
    advice's vendor invoice numbers (crud/remittance.py), and the create-PA
    screen's "already claimed" lock. An empty array makes all of them no-ops
    at once, so the cash leaves while the invoice stays 'matched', AP keeps
    reporting it as an open payable, the advice is blocked 'missing_invoice_no'
    with no screen able to lift it (epms-api edits draft/returned PAs only),
    and nothing stops a second PA from paying that same invoice again. Payment
    is the point of no return for all four, which is why the check sits here
    rather than at approval.

    Deliberately narrow — an empty invoice_ids is legitimate in several flows
    and every one of them must keep working:
      * prepayment PAs, paid before any invoice exists;
      * Direct (OA) and agreement PAs, which carry no po_id — agreement
        invoices have their own gate in epms-api's _validate_agreement_pa_invoices;
      * GR-only payment on a PO nobody has invoiced yet;
      * a PA whose invoices a sibling PA already claims (prepayment first,
        settlement after).
    It therefore fires only when the PO holds a payable invoice that no live PA
    has spoken for — the case where the link was simply forgotten.
    """
    if pa.invoice_ids or pa.po_id is None or pa.pa_type == "prepayment":
        return

    candidates = (await db.execute(
        select(Invoice.id, Invoice.internal_ref, Invoice.vendor_invoice_number)
        .where(Invoice.po_id == pa.po_id,
               Invoice.status.in_(("matched", "approved", "partially_paid")))
    )).all()
    if not candidates:
        return

    # Whatever any other live PA on this PO already points at is somebody
    # else's payable, not a forgotten link on this one.
    claimed: set[str] = set()
    for (ids,) in (await db.execute(
        select(PaymentApplication.invoice_ids).where(
            PaymentApplication.po_id == pa.po_id,
            PaymentApplication.status.not_in(("cancelled", "rejected")),
            PaymentApplication.id != pa.id,
        )
    )).all():
        claimed.update(str(i) for i in (ids or []))

    unclaimed = [c for c in candidates if str(c.id) not in claimed]
    if not unclaimed:
        return

    refs = ", ".join(
        f"{c.internal_ref} (#{c.vendor_invoice_number})" if c.vendor_invoice_number
        else c.internal_ref
        for c in unclaimed
    )
    raise ValueError(
        f"PA {pa.pa_number} links no invoice, but {pa.po_number} still carries "
        f"unclaimed invoice(s): {refs}. Link the invoice before paying — "
        "otherwise it stays open in AP, no remittance advice can be sent, and "
        "nothing prevents a second PA from paying it again."
    )

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


async def apply_pa_invoice_writeback(db: AsyncSession, pa: PaymentApplication) -> dict:
    """Bring a paid PA's invoices and AP subledger rows in step with it.

    Extracted from execute() so it can be REPLAYED. The two loops used to live
    inline, which meant they ran exactly once — at payment — over whatever
    pa.invoice_ids held at that instant. A PA paid with an empty array therefore
    left its invoice at 'matched' and its ap_invoices row 'posted' at
    paid_amount 0 forever, with no code path anywhere able to catch up once the
    link was repaired (2026-08-31: PA-20260729-0001, fixed by hand in SQL).
    Data Maintenance now calls this after editing invoice_ids on a paid PA.

    Idempotent by construction: every write is guarded on a status the flow has
    not passed yet, so replaying it changes nothing. It only ever moves a
    document FORWARD — it will not reopen an invoice dropped from the array,
    because "this invoice is no longer covered" is a reversal, not a catch-up,
    and reversing a settled payable silently is not something a repair screen
    should do on its own.

    A prepayment leaves both at 'partially_paid' (still an open item); every
    other PA type closes them. Invoice rows are touched only for a PO/agreement
    PA: a Direct (OA) PA's invoice_ids hold expense_invoices ids, which live in
    a different table and would never match here — but their ap_invoices rows
    do exist and are keyed by the same id, so the AP half runs for both.
    """
    closed = settled = 0
    inv_status = "partially_paid" if pa.pa_type == "prepayment" else "paid"

    for inv_id_str in pa.invoice_ids or []:
        try:
            inv_id = uuid.UUID(str(inv_id_str))
        except (ValueError, TypeError):
            continue

        if not pa.is_direct:
            inv = (await db.execute(
                select(Invoice).where(Invoice.id == inv_id)
            )).scalar_one_or_none()
            if inv and inv.status in ("matched", "approved", "partially_paid"):
                inv.status = inv_status
                closed += 1

        ap = (await db.execute(
            select(ApInvoice).where(ApInvoice.source_invoice_id == inv_id)
        )).scalar_one_or_none()
        if ap and ap.status in ("posted", "partially_paid"):
            ap.status = inv_status
            if inv_status == "paid":
                ap.paid_amount = ap.total_amount
            settled += 1

    return {"invoices_closed": closed, "ap_invoices_settled": settled}


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
        await _check_invoice_link(db, pa)
        # actual kind derives from the document, not the client
        doc_kind = "pa_dir" if pa.is_direct else "pa"
        pa.status = "processed"
        # Stamp the real payment date (honours a back-dated req.payment_date).
        # Dashboards read paid_at, never the onupdate-bumped updated_at.
        pa.paid_at = datetime.combine(pay_date, datetime.min.time(), tzinfo=timezone.utc)
        await _complete_open_tasks(db, ["pa", "pa_dir"], pa.id)

        await apply_pa_invoice_writeback(db, pa)

        bank = await _resolve_bank(db, req.bank_account_id, pa.currency)
        base = req.amount_paid if req.amount_paid is not None else pa.payment_amount

        # Vendor credits reduce the cash that leaves the bank. Selection takes row
        # locks, so it must happen inside this transaction, immediately before the
        # PaymentRecord — a credit consumed without its payment (or the reverse)
        # is money that exists in one place and not the other.
        picks = await vendor_credit_crud.select_credits_for_payment(
            db, vendor_id=pa.vendor_id, currency=pa.currency,
            base=base, credit_ids=req.credit_ids,
        )
        # Decimal("0.00"), not Decimal("0"): an empty `picks` would otherwise
        # store scale-0 zero and serialize as "0" where every other credit
        # figure in the system (app/crud/vendor_credit.py, /suggest) says
        # "0.00". Same value on the way to Numeric(15,2), consistent on the way
        # back out.
        credit_applied = sum((take for _, take in picks), Decimal("0.00"))
        net = base - credit_applied

        record = PaymentRecord(
            doc_kind=doc_kind, doc_id=pa.id, doc_number=pa.pa_number,
            pa_id=pa.id, pa_number=pa.pa_number,
            vendor_id=pa.vendor_id, vendor_name=pa.vendor_name,
            payment_date=pay_date, payment_method=req.payment_method,
            reference=req.reference, amount=net, credit_applied=credit_applied,
            currency=pa.currency,
            recorded_by=recorded_by, notes=req.notes, batch_id=batch_id,
            bank_account_id=req.bank_account_id,
        )
        db.add(record)
        await db.flush()

        if picks:
            await vendor_credit_crud.apply_credits(
                db, picks, payment_record_id=record.id, batch_id=batch_id,
                doc_kind=doc_kind, doc_id=pa.id, doc_number=pa.pa_number,
                applied_by=recorded_by,
            )

        posting_lines = [
            {"line_role": "accounts_payable", "debit": base,
             "partner_id": pa.vendor_id, "partner_name": pa.vendor_name,
             "currency": pa.currency},
            _bank_line(net, pa.currency, bank),
        ]
        if credit_applied > Decimal("0"):
            # Bank is credited only with the cash that left; the netted portion
            # parks in a clearing account so the GL bank balance still ties to
            # the bank statement.
            #
            # "vendor_credit_clearing" requires an account_mappings row
            # (mapping_type='line_role') to appear on the balance sheet / income
            # statement: app/crud/gl.py's builders do `if not acct: continue`
            # for an unmapped code, so an unmapped clearing line is silently
            # dropped from both reports while AP and bank still move — the
            # balance sheet then reports "balanced": false by exactly this
            # amount on every credited payment. Only the trial balance shows it
            # (as "(unmapped)"). Seeded by alembic/versions/
            # 0032_vendor_credit_clearing_mapping.py to account 1123
            # "Advance to suppliers" (existing IFRS asset account, no new NC
            # account): an unapplied vendor credit is money the supplier owes
            # us, so it belongs there until the credit note itself is booked.
            # That migration guards on 1123 existing (COA is NC-synced and a
            # given environment may not have it yet) — if it does not, the
            # role stays unmapped and this line's account_code stays NULL,
            # which is exactly the tolerated-but-visible-in-trial-balance
            # state described above, not a payment blocker.
            posting_lines.append({
                "line_role": "vendor_credit_clearing", "credit": credit_applied,
                "partner_id": pa.vendor_id, "partner_name": pa.vendor_name,
                "currency": pa.currency,
            })

        event_id = await emit_event(
            db,
            source_service="finance",
            source_doc_type=doc_kind,
            source_doc_id=pa.id,
            source_doc_number=pa.pa_number,
            event_type="payment",
            lines=await _stamp_fx(db, await _stamp_account_codes(db, posting_lines), pay_date),
        )
        return PaymentExecuteResponse(
            doc_kind=doc_kind, doc_id=pa.id, doc_number=pa.pa_number,
            new_status="processed", payment_record_id=record.id,
            posting_event_id=event_id,
        )

    # expense_claim
    #
    # Vendor credits are a vendor-AP instrument: they net against what the
    # company owes a SUPPLIER, never against an employee reimbursement. Reject
    # the parameter loudly rather than letting eligibility fall out of "this
    # branch happens not to read req.credit_ids" — a caller that passed credits
    # here believed they would be applied, and silently paying the claim in
    # full would leave the credit unconsumed with nobody told.
    if req.credit_ids:
        raise ValueError("Vendor credits do not apply to expense claims")

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
