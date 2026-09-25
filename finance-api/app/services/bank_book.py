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

## A foreign-currency account reconciles in ITS currency, not in CAD

NC books every line twice: 原币 (orig_debit/orig_credit, in the line's currency)
and 本币 (local_debit/local_credit, CAD). A bank statement only ever knows the
first. RBC USD 4010351, July 2026, measured against production:

                     orig (USD)     local (CAD)     statement (USD)
    opening           13,135.23      18,665.16       13,135.23
    movement            -636.12        -897.82        -636.12
    FX revaluation         0.00        -232.34            —
    closing           12,499.11      17,535.00       12,499.11

Reading local turned a USD account that reconciles to the cent into a
"difference" of −5,035.89 that no amount of matching could clear: every line
was off by the day's rate, and the month-end 汇兑损益结转 (orig 0.00, CAD only)
sat in the ledger with nothing on the statement to match it to.

So on a non-CAD account the book side is the ORIGINAL amount of the lines booked
in that account's currency. Lines with no amount in it — the FX revaluation, and
the CAD-denominated "interest + Carry forward" pairs NC posts on the USD accounts
— are not movements of that bank's money. They are kept out of the matchable
list and handed back separately (`BookPeriod.base_only`), with their CAD total,
so they are visible rather than silently dropped.

A CAD account is untouched: it keeps reading local, exactly as before (on RBC
1033760 orig == local on all 4,830 lines anyway, and the CNY/EUR lines that sit
on a few CAD accounts are only meaningful in CAD).
"""
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank import BankAccount
from app.models.journal_voucher import POSTED, JournalVoucher, JournalVoucherLine
from app.models.nc_bank_account import NcBankAccount

ZERO = Decimal("0")

# NC's 本币 for this book. local_debit/local_credit are always in it; a bank
# account in any other currency reconciles on orig_debit/orig_credit instead.
BASE_CURRENCY = "CAD"

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
    debit: Decimal              # in the BANK ACCOUNT's currency — see module doc
    credit: Decimal
    contra_codes: list[str]
    contra_names: list[str]
    contra_kind: str
    partner_name: str | None
    posted: bool
    # The same line in CAD, as NC's 本币. Equal to debit/credit on a CAD account.
    local_debit: Decimal = ZERO
    local_credit: Decimal = ZERO

    @property
    def local_amount(self) -> Decimal:
        return self.local_debit - self.local_credit

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
    # The account's currency, and the lines on it that have no amount in that
    # currency (FX revaluation, CAD-only adjustments). Always empty on a CAD
    # account. Not part of opening/closing — they are not the bank's money.
    currency: str = BASE_CURRENCY
    base_only: list[BookLine] | None = None

    @property
    def base_only_local_total(self) -> Decimal:
        return sum((ln.local_amount for ln in (self.base_only or [])), ZERO)


def account_label(account: BankAccount) -> str:
    """The name that goes on screen and on the auditor's report: the Name column
    of Bank Settings, exactly as finance maintains it ("RBC Operating").

    Never NC's name. NC names some of these accounts in Chinese
    ("RBC加拿大元活期户", "JPMORGAN NEWYORK美元活期户") and this is an English
    document. NC's code is not in the label either — it is already carried
    separately as bank_account_code, for anyone who needs to trace the ledger
    side back.
    """
    return account.name


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


# NC VOUCHERKIND 2 — the opening voucher it writes into period 00 to carry a
# year's closing balance into the next one. It is a restatement, not a movement,
# so summing it ALONGSIDE the transactions it summarises counts the same money
# twice. On RBC 1033760 that inflated the July 2026 opening by 820,087.14 =
# 245,567.79 (the 2025 opening voucher, equal to all of 2024) + 574,519.35 (the
# 2026 one, equal to 2024 + 2025) — and the period itself reconciled to the cent,
# so Sign off refused over a difference that was entirely in the carry-in.
#
# Only kind 2. Kinds 1/3/4 (year-end adjustment, cost and R&D carry-forward) are
# real postings that move cash; excluding them would break the number the other
# way. Measured: with kind 2 out and the rest in, the opening is 564,623.34 —
# the balance the RBC statement prints.
#
# ★ EXCEPT the go-live one. The earliest opening voucher on an account, when no
# movement precedes it, is not a restatement of anything — it IS the balance the
# account arrived in NC with. BOC 1060 went live in 2020: its 2020-00 opening
# voucher nets 5,670,888.07, and dropping it put the July 2026 ledger opening at
# −4,307,455.32 against a statement opening of 1,363,432.75 — a gap of exactly
# 5,670,888.07, with the period's own movement agreeing to the cent. RBC never
# hit this because it has no go-live voucher: its first movement (2024-05) is
# older than its first opening voucher (2025-00). See `_go_live_year`.
_OPENING_VOUCHER_KIND = 2


def _excludes_opening_vouchers():
    """NULL kind = a go-forward UniOps JV, which has no NC voucher kind and is a
    real movement — it must stay in."""
    return func.coalesce(JournalVoucher.nc_voucher_kind, 0) != _OPENING_VOUCHER_KIND


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


def account_currency(account: BankAccount, nc: NcBankAccount | None) -> str:
    """NC's own currency for the bank account wins; Bank Settings is the fallback."""
    return ((nc.currency if nc is not None else None) or account.currency
            or BASE_CURRENCY).upper()


def _in_currency(ccy: str):
    """(debit, credit) SQL expressions in the bank account's currency.

    CAD: local, as it always was. Anything else: orig on the lines booked in that
    currency, zero on the rest — a CAD-only line has no USD amount to add.
    """
    if ccy == BASE_CURRENCY:
        return JournalVoucherLine.local_debit, JournalVoucherLine.local_credit
    same = func.upper(JournalVoucherLine.currency) == ccy
    return (case((same, JournalVoucherLine.orig_debit), else_=0),
            case((same, JournalVoucherLine.orig_credit), else_=0))


def _line_in_currency(ln: JournalVoucherLine, ccy: str) -> tuple[Decimal, Decimal]:
    """Python twin of `_in_currency`, for one loaded line."""
    if ccy == BASE_CURRENCY:
        return ln.local_debit or ZERO, ln.local_credit or ZERO
    if (ln.currency or "").upper() == ccy:
        return ln.orig_debit or ZERO, ln.orig_credit or ZERO
    return ZERO, ZERO


def _voucher_year():
    """Fiscal year of a voucher. Opening vouchers are dated 0001-01-01 in NC, so
    the year has to come from fiscal_period ("2020-00"), never from the date."""
    from sqlalchemy import Integer, cast
    return cast(func.substr(JournalVoucher.fiscal_period, 1, 4), Integer)


async def _go_live_year(db: AsyncSession, nc_account_id: uuid.UUID, codes: list[str],
                        include_unposted: bool) -> int | None:
    """The fiscal year whose opening voucher carries the account's INITIAL balance,
    or None when every opening voucher is a restatement.

    That is the earliest year with an opening voucher on this account, provided no
    real movement is in an earlier year. Later years' opening vouchers restate
    movements already in the ledger and stay excluded.
    """
    scope = (_posted_filter(include_unposted),
             JournalVoucherLine.account_code.in_(codes),
             JournalVoucherLine.bank_account_id == nc_account_id)
    is_opening = func.coalesce(JournalVoucher.nc_voucher_kind, 0) == _OPENING_VOUCHER_KIND
    first_opening, first_move = (await db.execute(
        select(func.min(_voucher_year()).filter(is_opening),
               func.min(_voucher_year()).filter(~is_opening))
        .select_from(JournalVoucherLine)
        .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
        .where(*scope)
    )).one()
    if first_opening is None:
        return None
    if first_move is not None and first_move < first_opening:
        return None
    return first_opening


async def opening_balance(db: AsyncSession, nc_account_id: uuid.UUID, before: date,
                          include_unposted: bool = False,
                          currency: str = BASE_CURRENCY) -> Decimal:
    """Balance carried into `before` — every posted line on this bank account
    strictly earlier. This is NC's 期初余额 for the account-balance expansion, and
    on the July RBC account it is 564,623.34, the statement's opening balance.

    In the account's own currency: on RBC USD 4010351 it is 13,135.23 USD, the
    statement's opening — not the 18,665.16 CAD that local sums to.
    """
    codes = await _cash_subtree(db)
    dr, cr = _in_currency(currency.upper())
    go_live = await _go_live_year(db, nc_account_id, codes, include_unposted)
    kept = _excludes_opening_vouchers()
    if go_live is not None:
        kept = or_(kept, _voucher_year() == go_live)
    row = (await db.execute(
        select(func.coalesce(func.sum(dr), 0),
               func.coalesce(func.sum(cr), 0))
        .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
        .where(_posted_filter(include_unposted),
               kept,
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
               # Same exclusion as the opening: an opening voucher dated inside the
               # window would restate the carry-in as if it were a movement, and the
               # closing would drift by that amount instead. None fall in July 2026
               # on RBC, but nothing stops NC dating one there.
               _excludes_opening_vouchers(),
               JournalVoucherLine.account_code.in_(codes),
               JournalVoucherLine.bank_account_id == nc.id,
               JournalVoucher.voucher_date >= date_from,
               JournalVoucher.voucher_date <= date_to)
        .order_by(JournalVoucher.voucher_date, JournalVoucher.jv_number,
                  JournalVoucherLine.line_no)
    )).all()

    ccy = account_currency(account, nc)
    contra = await _contra_map(db, [ln for ln, _jv in rows])
    lines = []
    base_only = []
    for ln, jv in rows:
        pairs = contra.get(ln.id, [])
        debit, credit = _line_in_currency(ln, ccy)
        local_dr, local_cr = ln.local_debit or ZERO, ln.local_credit or ZERO
        # Nothing in the account's currency but something in CAD: an FX
        # revaluation or a CAD-only adjustment. Never on a CAD account, where
        # debit/credit ARE local.
        target = (base_only if debit == ZERO and credit == ZERO
                  and (local_dr != ZERO or local_cr != ZERO) else lines)
        target.append(BookLine(
            jv_line_id=ln.id, nc_voucher_pk=jv.nc_source_pk, jv_number=jv.jv_number,
            voucher_date=jv.voucher_date, line_no=ln.line_no,
            account_code=ln.account_code, summary=ln.summary, currency=ln.currency,
            debit=debit, credit=credit,
            contra_codes=[c for c, _n in pairs],
            contra_names=[n for _c, n in pairs if n],
            contra_kind=classify_contra([c for c, _n in pairs]),
            partner_name=ln.partner_name,
            posted=(jv.status == POSTED),
            local_debit=local_dr, local_credit=local_cr,
        ))

    opening = await opening_balance(db, nc.id, date_from, include_unposted, ccy)
    total_debit = sum((ln.debit for ln in lines), ZERO)
    total_credit = sum((ln.credit for ln in lines), ZERO)
    return BookPeriod(
        bank_account_code=nc.code, bank_account_label=account_label(account),
        date_from=date_from, date_to=date_to,
        opening=opening, closing=opening + total_debit - total_credit,
        total_debit=total_debit, total_credit=total_credit, lines=lines,
        currency=ccy, base_only=base_only,
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
        select(JournalVoucherLine.id, JournalVoucherLine.jv_id, NcBankAccount.code,
               NcBankAccount.name, JournalVoucherLine.local_debit,
               JournalVoucherLine.local_credit)
        .join(NcBankAccount, NcBankAccount.id == JournalVoucherLine.bank_account_id)
        .where(JournalVoucherLine.jv_id.in_(jv_ids),
               JournalVoucherLine.account_code.in_(codes))
    )).all()
    by_jv: dict = {}
    for lid, jv_id, code, name, dr, cr in others:
        by_jv.setdefault(jv_id, []).append((lid, code, name, (dr or ZERO) - (cr or ZERO)))

    out: dict = {}
    for line_id, jv_id in jv_of:
        mine = ours[line_id]
        for lid, code, name, local in by_jv.get(jv_id, []):
            # The leg that is not ours, on the opposite side. Compared by line id
            # and by the SIGN of the CAD amount: across currencies (USD account →
            # CAD account) the two legs' own amounts never match, and mine.amount
            # is in this account's currency while `local` is CAD.
            if lid != line_id and code and (local > ZERO) != (mine.local_amount > ZERO):
                out[line_id] = {"code": code, "name": name}
                break
    return out
