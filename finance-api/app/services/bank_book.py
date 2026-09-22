"""The book side of a bank reconciliation: NC's GL, scoped to one bank account.

This reproduces what finance does by hand today — NC's 科目余额表 on account
`100201 Checking`, expanded by the **bank-account auxiliary**, drilled through to
线 detail (联查明细). Account 100201 is a single postable account, so that
auxiliary is the only thing separating RBC from Bank of China from JPMorgan; see
`nc_bank_account.py` for why and migration 0036 for when it arrived.

Measured against NC for 2026-07, bank `1033760` (RBC CAD):

    opening  564,623.34     debit  1,414,741.74     credit  1,269,496.36
    closing  709,868.72     260 lines

— the same opening and closing the RBC statement prints, and 260 = the 253
payments + 7 deposits of the QuickBooks reconciliation report.

## The contra account is a classification, not decoration

NC's drill-down shows 对方科目 per line, and it says what KIND of movement this
is (user, 2026-09-22):

    应付账款 (2202)        a payment voucher generated from an AP bill — these are
                           the vendor payments that appear in the bank's payment files
    其他应付 (2241)        manual voucher (e.g. an employee reimbursement)
    银行存款 (1002*)       manual voucher — an internal bank-to-bank transfer, whose
                           OTHER side is another bank account on this very same
                           account, which is exactly why the auxiliary matters
    应付职工薪酬 (2211)    manual voucher — payroll
    短期借款 (2001)        manual voucher — a credit-card repayment
    财务费用 (6603)        bank charges and interest

The matcher uses it: AP lines are the ones to look for inside a payment advice,
transfers pair against "Funds transfer credit" statement lines, and fees match
one-to-one. It is also what the reconciliation screen shows, because it is what
finance reads when they do this by hand.
"""
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank import BankAccount
from app.models.journal_voucher import POSTED, JournalVoucher, JournalVoucherLine
from app.models.nc_bank_account import NcBankAccount

ZERO = Decimal("0")

# The cash accounts a bank reconciliation draws from. 1002 is "Cash on Bank";
# 100201 Checking and 100202 Savings are its postable children, and both carry
# the bank_account auxiliary.
CASH_ACCOUNT_PREFIX = "1002"

# contra account code -> what that movement IS. Prefix-matched longest-first, so
# 100201 resolves as a transfer via 1002 without needing its own entry.
CONTRA_KINDS: tuple[tuple[str, str], ...] = (
    ("2202", "ap"),              # AP bill -> payment voucher (the automated path)
    ("2000", "ap"),
    ("1002", "bank_transfer"),   # the other side is another bank account
    ("1001", "cash"),
    ("2211", "payroll"),
    ("2001", "credit_card"),     # repaying the card
    ("2050", "credit_card"),
    ("2241", "other_payable"),
    ("2310", "other_payable"),
    ("6603", "bank_fee"),
)

KIND_LABELS = {
    "ap": "Accounts payable",
    "bank_transfer": "Bank transfer",
    "cash": "Cash",
    "payroll": "Payroll",
    "credit_card": "Credit card",
    "other_payable": "Other payable",
    "bank_fee": "Bank charges / interest",
    "mixed": "Several accounts",
    "other": "Other",
    "unknown": "—",
}


def classify_contra(codes: list[str]) -> str:
    """Contra account codes -> one kind. Several DIFFERENT kinds on one voucher is
    `mixed`, never a silent pick of the first."""
    if not codes:
        return "unknown"
    kinds = set()
    for code in codes:
        hit = "other"
        for prefix, kind in sorted(CONTRA_KINDS, key=lambda kv: -len(kv[0])):
            if (code or "").startswith(prefix):
                hit = kind
                break
        kinds.add(hit)
    if len(kinds) == 1:
        return kinds.pop()
    return "mixed"


@dataclass
class BookLine:
    """One GL line on the bank account, in bank-statement terms."""
    jv_line_id: uuid.UUID
    nc_voucher_pk: str | None
    jv_number: str
    voucher_date: date
    line_no: int
    account_code: str | None
    summary: str | None
    currency: str
    debit: Decimal              # as posted (local/CAD)
    credit: Decimal
    contra_codes: list[str]
    contra_names: list[str]
    contra_kind: str
    partner_name: str | None
    posted: bool

    @property
    def amount(self) -> Decimal:
        """Signed the way a bank statement reads: negative = money left.

        On an asset account a DEBIT is money arriving, so this is debit − credit.
        Getting it the other way round would make every payment look like a
        deposit, which is the same mistake the statement parser guards against
        from the other side.
        """
        return self.debit - self.credit


@dataclass
class BookPeriod:
    bank_account_code: str
    bank_account_label: str
    date_from: date
    date_to: date
    opening: Decimal
    closing: Decimal
    total_debit: Decimal
    total_credit: Decimal
    lines: list[BookLine]


async def resolve_nc_account(db: AsyncSession, account: BankAccount) -> NcBankAccount | None:
    """The NC bank account a UniOps bank_accounts row IS, or None if unmapped.

    Unmapped is a real, common state (the link is a new column in 0036) and the
    caller must say so out loud — a reconciliation with no book side is not an
    empty reconciliation, it is a misconfigured one.
    """
    if not account.nc_bank_account_code:
        return None
    return (await db.execute(
        select(NcBankAccount).where(NcBankAccount.code == account.nc_bank_account_code)
    )).scalar_one_or_none()


def _posted_filter(include_unposted: bool):
    """NC's TALLYDATE decides: an un-tallied voucher is not in NC's ledger, and
    the account-balance report excludes it. Reconciliation follows the ledger."""
    return sa_true() if include_unposted else (JournalVoucher.status == POSTED)


def sa_true():
    from sqlalchemy import true
    return true()


async def _cash_subtree(db: AsyncSession) -> list[str]:
    """1002 and its postable children, read from the COA rather than assumed."""
    from app.models.coa import ChartOfAccount
    rows = (await db.execute(
        select(ChartOfAccount.code).where(
            or_(ChartOfAccount.code == CASH_ACCOUNT_PREFIX,
                ChartOfAccount.parent_code == CASH_ACCOUNT_PREFIX))
    )).scalars().all()
    return list(rows) or [CASH_ACCOUNT_PREFIX, "100201", "100202"]


async def opening_balance(db: AsyncSession, nc_account_id: uuid.UUID, before: date,
                          include_unposted: bool = False) -> Decimal:
    """Balance carried into `before` — every posted line on this bank account
    strictly earlier. This is NC's 期初余额 for the account-balance expansion, and
    on the July RBC account it is 564,623.34, the statement's opening balance.
    """
    codes = await _cash_subtree(db)
    row = (await db.execute(
        select(func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
               func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
        .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
        .where(_posted_filter(include_unposted),
               JournalVoucherLine.account_code.in_(codes),
               JournalVoucherLine.bank_account_id == nc_account_id,
               JournalVoucher.voucher_date < before)
    )).one()
    return Decimal(row[0]) - Decimal(row[1])


async def _contra_map(db: AsyncSession, our_lines: list) -> dict:
    """jv_line_id -> [(account_code, account_name)] — NC's 对方科目, per line.

    The contra is the OPPOSITE SIDE of the same voucher, not "the non-cash
    accounts". That distinction is the whole internal-transfer case: a
    bank-to-bank transfer posts BOTH its legs to 100201, so excluding cash
    accounts leaves it with no contra at all and it classifies as "unknown" —
    when the truth is that its contra is the other bank account, which is exactly
    what makes it a transfer.

    Opposite side also gives the right answer everywhere else: a vendor payment
    is a credit on the bank whose contra is the debit to payables; a bank charge
    is a credit whose contra is the debit to financial expenses.
    """
    if not our_lines:
        return {}
    from app.models.coa import ChartOfAccount
    jv_ids = list({ln.jv_id for ln in our_lines})
    rows = (await db.execute(
        select(JournalVoucherLine.id, JournalVoucherLine.jv_id,
               JournalVoucherLine.account_code, JournalVoucherLine.local_debit,
               JournalVoucherLine.local_credit, ChartOfAccount.name)
        .outerjoin(ChartOfAccount, ChartOfAccount.code == JournalVoucherLine.account_code)
        .where(JournalVoucherLine.jv_id.in_(jv_ids))
    )).all()
    by_jv: dict = {}
    for lid, jv_id, code, dr, cr, name in rows:
        by_jv.setdefault(jv_id, []).append((lid, code, dr or ZERO, cr or ZERO, name))

    out: dict = {}
    for ln in our_lines:
        we_debit = (ln.local_debit or ZERO) > ZERO
        seen: dict = {}
        for lid, code, dr, cr, name in by_jv.get(ln.jv_id, []):
            if lid == ln.id:
                continue
            if ((dr > ZERO) if not we_debit else (cr > ZERO)):
                seen[code] = name
        out[ln.id] = sorted(seen.items(), key=lambda cn: cn[0] or "")
    return out


async def book_period(db: AsyncSession, account: BankAccount,
                      date_from: date, date_to: date,
                      include_unposted: bool = False) -> BookPeriod:
    """The ledger side of one account for one period, in statement terms.

    Raises LookupError when the account is not mapped to an NC bank account —
    silently returning an empty period would read as "nothing to reconcile".
    """
    nc = await resolve_nc_account(db, account)
    if nc is None:
        raise LookupError(
            f"Bank account {account.name!r} is not linked to an NC bank account. "
            f"Set its NC bank account code (NC 科目余额表 shows it on 100201, e.g. "
            f"'1033760') before reconciling — without it there is no book side.")

    codes = await _cash_subtree(db)
    rows = (await db.execute(
        select(JournalVoucherLine, JournalVoucher)
        .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
        .where(_posted_filter(include_unposted),
               JournalVoucherLine.account_code.in_(codes),
               JournalVoucherLine.bank_account_id == nc.id,
               JournalVoucher.voucher_date >= date_from,
               JournalVoucher.voucher_date <= date_to)
        .order_by(JournalVoucher.voucher_date, JournalVoucher.jv_number,
                  JournalVoucherLine.line_no)
    )).all()

    contra = await _contra_map(db, [ln for ln, _jv in rows])
    lines = []
    for ln, jv in rows:
        pairs = contra.get(ln.id, [])
        lines.append(BookLine(
            jv_line_id=ln.id, nc_voucher_pk=jv.nc_source_pk, jv_number=jv.jv_number,
            voucher_date=jv.voucher_date, line_no=ln.line_no,
            account_code=ln.account_code, summary=ln.summary, currency=ln.currency,
            debit=ln.local_debit or ZERO, credit=ln.local_credit or ZERO,
            contra_codes=[c for c, _n in pairs],
            contra_names=[n for _c, n in pairs if n],
            contra_kind=classify_contra([c for c, _n in pairs]),
            partner_name=ln.partner_name,
            posted=(jv.status == POSTED),
        ))

    opening = await opening_balance(db, nc.id, date_from, include_unposted)
    total_debit = sum((ln.debit for ln in lines), ZERO)
    total_credit = sum((ln.credit for ln in lines), ZERO)
    return BookPeriod(
        bank_account_code=nc.code, bank_account_label=nc.label,
        date_from=date_from, date_to=date_to,
        opening=opening, closing=opening + total_debit - total_credit,
        total_debit=total_debit, total_credit=total_credit, lines=lines,
    )


async def transfer_counterparts(db: AsyncSession, lines: list[BookLine]) -> dict:
    """For every `bank_transfer` line, the OTHER bank account on the same voucher.

    An internal transfer posts both legs to account 100201 — the credit on the
    sending bank, the debit on the receiving one — so without the auxiliary the
    two net to zero and the transfer vanishes from both books. With it, each leg
    can name its counterpart, which is what turns a statement's
    "Funds transfer credit TT" into a one-to-one match instead of a mystery.
    """
    ids = [ln.jv_line_id for ln in lines if ln.contra_kind == "bank_transfer"]
    if not ids:
        return {}
    ours = {ln.jv_line_id: ln for ln in lines}
    jv_of = (await db.execute(
        select(JournalVoucherLine.id, JournalVoucherLine.jv_id)
        .where(JournalVoucherLine.id.in_(ids))
    )).all()
    jv_ids = [jv for _lid, jv in jv_of]
    codes = await _cash_subtree(db)
    others = (await db.execute(
        select(JournalVoucherLine.jv_id, NcBankAccount.code, NcBankAccount.name,
               JournalVoucherLine.local_debit, JournalVoucherLine.local_credit)
        .join(NcBankAccount, NcBankAccount.id == JournalVoucherLine.bank_account_id)
        .where(JournalVoucherLine.jv_id.in_(jv_ids),
               JournalVoucherLine.account_code.in_(codes))
    )).all()
    by_jv: dict = {}
    for jv_id, code, name, dr, cr in others:
        by_jv.setdefault(jv_id, []).append((code, name, dr, cr))

    out: dict = {}
    for line_id, jv_id in jv_of:
        mine = ours[line_id]
        for code, name, dr, cr in by_jv.get(jv_id, []):
            # the leg that is not ours, and on the opposite side
            if code != mine and code and (dr or ZERO) - (cr or ZERO) != mine.amount:
                out[line_id] = {"code": code, "name": name}
                break
    return out
