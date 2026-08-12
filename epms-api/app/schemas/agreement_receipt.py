"""Request/response schemas for agreement receipts (AGR § 凭证).

A house_account agreement is not necessarily a counter-pickup account — it may
be a monthly delivery or an outsourced service instead. `receipt_type`
(counter_slip | delivery | service) is the discriminator; all three share the
same set of columns, and the difference is purely presentational (UI copy).
The legality check for `receipt_type` lives here, not on the ORM model —
deliberately no CHECK constraint on the table, matching this table's existing
`status` column convention (see app/models/agreement_receipt.py).
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, field_validator, model_validator

_RECEIPT_TYPES = ("counter_slip", "delivery", "service")


def _validate_receipt_type(v: str | None) -> str | None:
    if v is not None and v not in _RECEIPT_TYPES:
        raise ValueError(
            "receipt_type must be one of: counter_slip, delivery, service")
    return v


def validate_totals(*, amount: Decimal, tax_amount: Decimal, total_amount: Decimal) -> None:
    """Shared by ReceiptCreate's schema validator and crud.agreement_receipt.update()
    (post-merge, on the MERGED row) — same drift-avoidance rationale as
    agreement.py's validate_recurrence/validate_validity_window: two copies
    of this check could silently diverge. Three amounts are OCR-prefilled
    and all independently editable; a person changing one and forgetting
    another is the normal case, not the exception, and later reconciliation
    arithmetic reads total_amount, so an inconsistency here is not cosmetic.
    """
    if total_amount != amount + tax_amount:
        raise ValueError("total_amount must equal amount + tax_amount")


def _vendor_words(name: str) -> list[str]:
    """casefold + split on every non-alphanumeric character. No filtering."""
    word = ""
    words: list[str] = []
    for ch in name.casefold():
        if ch.isalnum():
            word += ch
        elif word:
            words.append(word)
            word = ""
    if word:
        words.append(word)
    return words


def normalize_vendor_name(name: str) -> str:
    """Fold a vendor name down to what two spellings of the SAME merchant share.

    casefold, split on every non-alphanumeric character, DROP any word that is
    all digits, and join what's left:

        "Princess Auto #12"  -> "princessauto"
        "PRINCESS AUTO LTD." -> "princessautoltd"
        "7-Eleven"           -> "eleven"

    The all-digit words are store, till and lane numbers — the single most
    common way a till header differs from the vendor's registered name, and
    the one difference that carries no information about WHICH merchant this
    is. Dropping them (rather than listing "#", "store", "no." and every other
    prefix a till might print) needs no dictionary: a bare number is never the
    identifying part of a merchant's name.

    Legal suffixes ("Ltd", "Inc") are deliberately NOT dropped here — that
    would need exactly the dictionary this function avoids. is_vendor_mismatch
    absorbs them with containment instead: "princessauto" is a prefix of
    "princessautoltd".

    Nothing else — no word-order sorting, no synonym table: those would need
    a dictionary this system does not have and would make the result
    impossible to explain to the person looking at the warning.
    """
    return "".join(w for w in _vendor_words(name) if not w.isdigit())


def is_vendor_mismatch(receipt_vendor: str | None, agreement_vendor: str | None) -> bool:
    """Does the merchant printed on the receipt disagree with the agreement's vendor?

    CONTAINMENT, NOT EQUALITY — and this is the part a later reader will want
    to "simplify" into `==`. Don't. The two strings come from different worlds:
    the agreement's vendor is the master-data legal name ("Princess Auto Ltd"),
    while the receipt's is whatever the till printed on the paper
    ("PRINCESS AUTO #12", store number and all). Those are the SAME merchant,
    and equality calls every single one of them a mismatch. A warning that
    fires on the normal case gets ignored within a week, and then the one row
    that really is a slip from another shop rides through unnoticed — the
    exact failure this field exists to catch. So: normalise both sides
    (which already discards store numbers) and accept EITHER string
    containing the other, which additionally tolerates a legal suffix present
    on one side only, while still refusing two genuinely different names
    ("Canadian Tire" vs "Princess Auto").

    Containment is checked in BOTH directions because neither side is
    reliably the longer one: the till adds a store number, the master data
    adds "Ltd". "Princess Auto #12" vs "Princess Auto Ltd" — the case that
    motivated this whole rule — only passes because BOTH the digit-dropping
    in normalize_vendor_name AND two-way containment are in play; equality on
    the normalised strings still calls it a mismatch.

    A blank receipt vendor is NEVER a mismatch: OCR returns null whenever the
    slip header is illegible or cropped, and manual entry may legitimately
    leave it empty. Not filled in is not filled in wrong. A blank AGREEMENT
    vendor is likewise never a mismatch — there is nothing to disagree with.

    This is deliberately a REMINDER, not a rule: nothing here blocks a write.
    A group with several trading names (different stores, same house account)
    is a normal, legal receipt that this function will flag, which is why the
    frontend renders a badge and not an error.
    """
    if not receipt_vendor or not receipt_vendor.strip():
        return False
    if not agreement_vendor or not agreement_vendor.strip():
        return False
    a = normalize_vendor_name(receipt_vendor)
    b = normalize_vendor_name(agreement_vendor)
    # Review round 1, Minor 1: for a merchant whose name is ALL digits ("7-11",
    # "1-800-GOT-JUNK" keyed as digits), dropping digit words erases the entire
    # name — and an empty side then short-circuits to "no mismatch" below,
    # making a 7-11 slip on a Princess Auto house account the one thing this
    # function is for and cannot see. When digit-dropping erased a side, the
    # digits ARE the name, so compare with them kept. Only then; the store
    # number case above must keep its tolerance.
    if not a or not b:
        a = "".join(_vendor_words(receipt_vendor))
        b = "".join(_vendor_words(agreement_vendor))
    # A side that normalises to nothing even with digits kept (punctuation-only,
    # e.g. an OCR reading of "***") carries no evidence of disagreement — treat
    # it like a blank rather than letting `"" in b` decide by accident.
    if not a or not b:
        return False
    return a not in b and b not in a


def receipt_vendor_mismatch(
    *,
    receipt_vendor_id: uuid.UUID | None,
    receipt_vendor_name: str | None,
    agreement_vendor_id: uuid.UUID | None,
    agreement_vendor_name: str | None,
) -> bool:
    """Is this receipt's merchant a different party from the agreement's vendor?

    THREE TIERS, and the order is the whole point of Task 14:

    1. Both sides carry a master-data `vendor_id` -> compare the IDS. Equal is
       a match, different is a mismatch, and **the names are not consulted at
       all**. Two ids are the same party or they are not; there is no spelling
       involved and therefore nothing to be tolerant or intolerant about.
    2. The receipt has no `vendor_id` (nobody could match it to master data —
       the routine case for a one-off counter merchant) -> fall back to
       `is_vendor_mismatch`, the fuzzy text comparison that was the only tool
       available before this task.
    3. Neither an id nor a name on the receipt -> not a mismatch. Unchanged
       rule: not filled in is not filled in wrong.

    Why ids WIN rather than merely being consulted first: every awkward case
    this branch fought through — "Princess Auto #12" vs "Princess Auto Ltd"
    (false positive), "7-11" normalising to nothing (false negative),
    "7-11" vs "7-Eleven" (still a false positive) — is a limit of comparing
    SPELLINGS, not a limit of the question being asked. Once both sides name
    the same row of master data, a text rule can only add errors: it can call
    two spellings of one vendor a mismatch, and it can call two genuinely
    different vendors that happen to share a trading name a match. So when the
    ids are present they are the answer, and — deliberately — a receipt bound
    to vendor A on an agreement with vendor B IS flagged even if the two rows
    are spelled identically, because two rows in the vendor master ARE two
    different parties no matter what they are called.

    Note that this function does not touch `is_vendor_mismatch` itself: that
    function had a review round of its own (including the all-digit fallback
    that made a 7-11 slip visible) and its behaviour is unchanged. What
    changed is WHEN it is consulted — only when there is no id to compare.

    Like `is_vendor_mismatch`, this stays a REMINDER: nothing here blocks a
    write, and the frontend renders a badge, never an error.
    """
    if receipt_vendor_id is not None and agreement_vendor_id is not None:
        return receipt_vendor_id != agreement_vendor_id
    return is_vendor_mismatch(receipt_vendor_name, agreement_vendor_name)


class ReceiptCreate(BaseModel):
    # counter_slip | delivery | service。默认 counter_slip —— 绝大多数 house
    # account 仍是柜台领用,让最常见的情形免于每次都选。
    receipt_type: str = "counter_slip"
    receipt_date: date
    receipt_ref: str | None = None
    # 绑到供应商主数据的那一条(Task 14)。**可空是核心裁定**:柜台小票常来自
    # 一次性商家,匹配不到时只存文本、照样提交。给了它,API 层会把
    # vendor_name 覆写成主数据的规范名(见 api/v1/agreement_receipts.py
    # ::_resolve_vendor_name)—— vendor_name 是快照,不是第二处真相。
    vendor_id: uuid.UUID | None = None
    # 小票抬头上印的商家名(OCR 预填、可改)。允许为空:抽不到是正常情况,
    # 不该卡住录入。它与协议的供应商不一致时只是**提醒**,不拦写入 ——
    # 见 is_vendor_mismatch 的 docstring。
    vendor_name: str | None = None
    amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    received_by: uuid.UUID
    missing_receipt_reason: str | None = None
    notes: str | None = None

    @field_validator("receipt_type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        return _validate_receipt_type(v)

    @model_validator(mode="after")
    def _totals_are_consistent(self):
        validate_totals(
            amount=self.amount, tax_amount=self.tax_amount, total_amount=self.total_amount)
        return self


class ReceiptUpdate(BaseModel):
    receipt_type: str | None = None
    receipt_date: date | None = None
    receipt_ref: str | None = None
    # 显式传 null = 解绑主数据、退回自由文本(小票其实是别家开的);字段整个
    # 不传 = 不动。传了非空 id 时 API 层同样会把 vendor_name 覆写成规范名。
    vendor_id: uuid.UUID | None = None
    vendor_name: str | None = None
    amount: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    received_by: uuid.UUID | None = None
    missing_receipt_reason: str | None = None
    notes: str | None = None

    @field_validator("receipt_type")
    @classmethod
    def _known_type(cls, v: str | None) -> str | None:
        # None (field omitted or explicitly nulled) is let through on purpose —
        # a PATCH that doesn't touch receipt_type must not be forced to repeat
        # a valid value just to pass this check.
        return _validate_receipt_type(v)

    # Deliberately NO totals validator here (unlike ReceiptCreate): a PATCH body
    # is partial and usually only touches one of the three amount fields, so
    # a validator that only sees `self` has nothing coherent to check against
    # (same reasoning as AgreementUpdate vs validate_recurrence in
    # schemas/agreement.py). The check instead runs in
    # crud.agreement_receipt.update() against the MERGED post-patch row, via the
    # shared validate_totals() above.


class ReceiptResponse(BaseModel):
    id: uuid.UUID
    agreement_id: uuid.UUID
    receipt_type: str
    receipt_date: date
    receipt_ref: str | None
    vendor_id: uuid.UUID | None
    vendor_name: str | None
    amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    received_by: uuid.UUID
    missing_receipt_reason: str | None
    ap_reviewed_by: uuid.UUID | None
    ap_reviewed_at: datetime | None
    status: str
    invoice_id: uuid.UUID | None
    notes: str | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReceiptListResponse(BaseModel):
    items: list[ReceiptResponse]
    total: int


class ReceiptWithAgreementResponse(ReceiptResponse):
    """Same shape as ReceiptResponse plus the parent agreement's human number
    (and currency), plus the linked invoice's human reference if any.

    Only used by the cross-agreement listing (GET /agreement-receipts) —
    the per-agreement listing (GET /agreements/{id}/receipts) already has the
    agreement in the URL, so plain ReceiptResponse is enough there. This
    listing has no such context, and the frontend must never render a bare
    agreement_id or invoice_id UUID (task-9 brief, item 5) — the number/ref
    is what a human recognises.

    `currency` (fix round 1, Critical): this endpoint spans MULTIPLE
    agreements, which can each be denominated in a different currency
    (AgreementCreatePage's currency dropdown is user-facing, not decorative —
    CAD/USD/EUR/RMB are all live options). amount/tax_amount/total_amount on
    ReceiptResponse are bare numbers with no currency of their own; rendering
    them without per-row currency would silently mislabel every non-CAD row.

    `invoice_ref` (fix round 1, Important 2): None until the receipt is
    `reconciled` — a receipt's `invoice_id` has no FK to `invoices` (see
    models/agreement_receipt.py's comment on that column), so this is always
    optional even for a reconciled row in principle.

    `attachment_count` (whole-branch review I2): how many photos/proof files
    are on this receipt. This listing is the only surface that can approve or
    reject a pending_ap_review receipt, and "does it have a photo at all" is
    the fact that decision turns on — see crud.agreement_receipt.list_all for
    why it is counted in the same query rather than fetched per row. NOT on
    the per-agreement ReceiptResponse: that list is rendered by ReceiptTable,
    which already fetches each row's attachment list to render download links.
    """
    agreement_number: str
    currency: str
    invoice_ref: str | None = None
    attachment_count: int = 0
    # The AGREEMENT's vendor (master data), as opposed to the receipt's own
    # `vendor_name` (what the paper says). Both are needed on the same row:
    # the warning is only actionable if it can name the two merchants it is
    # comparing ("the slip says X, this account is with Y") — a bare "vendor
    # mismatch" badge tells the reader nothing they can act on.
    agreement_vendor_name: str = ""
    # Derived server-side (see is_vendor_mismatch above) so the list page, the
    # detail page, and anything added later all get ONE verdict instead of
    # each re-implementing the comparison — the rule is deliberately fuzzy,
    # and a fuzzy rule copied three times is three different rules.
    vendor_mismatch: bool = False
    # Is this receipt's vendor a row in the vendor master, or just a string
    # somebody (or OCR) typed? Both are legitimate — a one-off counter
    # merchant has no master-data record and does not need one — but they are
    # not the same fact, and the reader has to be able to tell them apart:
    # a bound receipt's verdict above came from comparing IDS, an unbound
    # one's came from comparing spellings. Sent as its own flag rather than
    # left to the frontend to infer from `vendor_id != null`, so the two
    # pages that render it cannot disagree about what "matched" means.
    # NOT an error state: never rendered in a danger colour.
    vendor_matched: bool = False


class ReceiptListAllResponse(BaseModel):
    items: list[ReceiptWithAgreementResponse]
    total: int


class ReceiptApReview(BaseModel):
    action: str   # approve | reject
