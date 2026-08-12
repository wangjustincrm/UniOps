"""CRUD for agreement receipts — typed evidence for house-account invoices."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import PurchaseAgreement
from app.models.agreement_receipt import AgreementReceipt
from app.models.agreement_receipt_attachment import AgreementReceiptAttachment
from app.models.invoice import Invoice
from app.schemas.agreement_receipt import ReceiptCreate, ReceiptUpdate, validate_totals


# The "receipt + its agreement" row, written down once. Every consumer unpacks
# this tuple POSITIONALLY, so spelling the shape out at three call sites (this
# module twice, api/v1/agreement_receipts.py once) was three chances to
# renumber a column by hand and not notice — Task 14 appended a seventh
# element and this alias is what made that a one-line change.
# Order: the receipt row, agreement number, agreement currency, linked
# invoice's human ref (NULL until reconciled), attachment count, agreement
# vendor NAME, agreement vendor ID.
ReceiptRow = tuple[AgreementReceipt, str, str, str | None, int, str, uuid.UUID]


async def create(
    db: AsyncSession, agr: PurchaseAgreement, body: ReceiptCreate, created_by: uuid.UUID,
) -> AgreementReceipt:
    # 只有给了 missing_receipt_reason 才走 pending_ap_review —— 附件必填是前端
    # 规则(见 test_agreement_receipt_api.py 顶部注释),后端无法可靠判断"用户是否
    # 打算传附件",强制要求会让"先建行再传附件"的两步上传流程无法进行。
    receipt = AgreementReceipt(
        agreement_id=agr.id, created_by=created_by,
        status="pending_ap_review" if body.missing_receipt_reason else "open",
        **body.model_dump(exclude={"missing_receipt_reason"}),
        missing_receipt_reason=body.missing_receipt_reason)
    db.add(receipt)
    await db.flush()
    return receipt


async def list_for_agreement(
    db: AsyncSession, agreement_id: uuid.UUID, status: str | None = None,
) -> list[AgreementReceipt]:
    q = select(AgreementReceipt).where(AgreementReceipt.agreement_id == agreement_id)
    if status:
        q = q.where(AgreementReceipt.status == status)
    rows = (await db.execute(q.order_by(AgreementReceipt.created_at.desc()))).scalars().all()
    return list(rows)


def _with_agreement_select():
    """The one SELECT shape behind every "receipt + its agreement" read.

    Shared by list_all() (the cross-agreement listing) and
    get_one_with_agreement() (the detail page's single-row fetch, Task 12) so
    the two can never drift into returning differently-shaped rows — the
    frontend reads the SAME ReceiptWithAgreementResponse from both, and a
    detail page that quietly lost `attachment_count` or `currency` because a
    second hand-written query forgot the subquery would mislabel amounts or
    hide the "no photo" warning.

    Columns, in order: the receipt row, agreement number, agreement currency,
    linked invoice's human ref (LEFT joined — NULL until `reconciled`),
    attachment count, agreement vendor name.

    The agreement's vendor is APPENDED (Task 13) rather than slotted in
    next to its number on purpose: every consumer unpacks this tuple
    positionally, so a new column in the middle would silently renumber
    `currency` and `attachment_count` at every call site. It is the
    AGREEMENT's vendor — the receipt's own `vendor_name` (what the slip
    printed) travels on the receipt row itself, and the whole point of
    selecting both is that the API layer can compare them.

    The agreement's vendor **ID** is appended too (Task 14), for the same
    reason and by the same rule: the receipt now carries its own `vendor_id`,
    and when both sides have one the comparison is an `==` on ids instead of a
    guess about spellings (schemas/agreement_receipt.py::
    receipt_vendor_mismatch). Appended, not slotted in beside the name, for
    the positional reason spelled out above.
    """
    attachment_count = (
        select(func.count(AgreementReceiptAttachment.id))
        .where(AgreementReceiptAttachment.receipt_id == AgreementReceipt.id)
        .correlate(AgreementReceipt)
        .scalar_subquery()
        .label("attachment_count")
    )
    return (
        select(AgreementReceipt, PurchaseAgreement.number, PurchaseAgreement.currency,
               Invoice.internal_ref, attachment_count,
               PurchaseAgreement.vendor_name.label("agreement_vendor_name"),
               PurchaseAgreement.vendor_id.label("agreement_vendor_id"))
        .join(PurchaseAgreement, AgreementReceipt.agreement_id == PurchaseAgreement.id)
        .outerjoin(Invoice, AgreementReceipt.invoice_id == Invoice.id)
    )


async def get_one_with_agreement(
    db: AsyncSession, receipt_id: uuid.UUID,
) -> ReceiptRow | None:
    """Single receipt, same row shape as list_all (Task 12).

    ReceiptDetailPage's URL is /receipts/{receipt_id} — no agreement_id in it —
    so it cannot use the agreement-scoped read (GET /agreements/{a}/receipts).
    Returning the identical tuple the listing returns is what lets the detail
    page render agreement_number / currency / invoice_ref / attachment_count
    without a second round of lookups or a second response shape.

    None (not an exception) when there is no such receipt — the caller turns
    that into a 404 whose wording names the RECEIPT, since with no agreement
    in the URL "not found" is otherwise ambiguous.
    """
    row = (await db.execute(
        _with_agreement_select().where(AgreementReceipt.id == receipt_id)
    )).first()
    if row is None:
        return None
    return row[0], row[1], row[2], row[3], row[4], row[5], row[6]


async def list_all(
    db: AsyncSession,
    *,
    agreement_id: uuid.UUID | None = None,
    receipt_type: str | None = None,
    status: str | None = None,
    search: str | None = None,
    agreement_ids_subq=None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[ReceiptRow], int]:
    """Cross-agreement listing (GET /agreement-receipts, Task 9) — the reason
    this page exists is that AP/warehouse staff shouldn't have to open an
    agreement first to record or find a receipt (see the task brief's "where
    this fits"). Joined to PurchaseAgreement so the caller gets the parent
    agreement's human `number` AND its `currency` back alongside each row —
    the frontend must never be handed a bare agreement_id UUID to render
    (brief item 5), and — fix round 1, Critical — must never assume every
    row on this CROSS-agreement listing shares one currency either. Every
    sibling view of this same data (ReceiptTable, GrListPage,
    AgreementListPage) renders amount+currency as a pair; this is the one
    place that used to hardcode CAD.

    Also LEFT-outer-joined to Invoice for `internal_ref` — a receipt's
    invoice_id (set once it's `reconciled`) is usually NULL, and even when
    set it has no FK to `invoices` (see the model's own comment on why), so
    this must be a LEFT join, not an inner one, or every unreconciled row
    would silently vanish from the listing. Fix round 1, Important 2: the
    frontend must never render a bare invoice_id UUID either.

    `attachment_count` (whole-branch review I2): this listing is the ONLY
    place an AP clerk can approve or reject a receipt sitting in
    pending_ap_review, and "is there actually a photo on it?" is half of what
    that decision rests on (the other half, missing_receipt_reason, is
    already on ReceiptResponse). Without it AP was approving blind, or the
    frontend would have had to fire one
    GET /agreements/{a}/receipts/{r}/attachments per visible row. A
    correlated scalar subquery keeps this at exactly the two queries it
    already ran (COUNT + page SELECT), and — unlike a JOIN onto the
    attachments table — cannot multiply rows when a receipt has several
    photos.
    """
    q = _with_agreement_select()
    # Row scope of the PARENT agreements (access_scope.visible_agreement_subquery).
    # None = unrestricted. A receipt is part of its agreement's record, so it is
    # visible on exactly the terms the agreement is: this page must not become
    # the way to read every house account's spending without opening one.
    if agreement_ids_subq is not None:
        q = q.where(AgreementReceipt.agreement_id.in_(agreement_ids_subq))
    if agreement_id:
        q = q.where(AgreementReceipt.agreement_id == agreement_id)
    if receipt_type:
        q = q.where(AgreementReceipt.receipt_type == receipt_type)
    if status:
        q = q.where(AgreementReceipt.status == status)
    if search:
        term = f"%{search}%"
        # Matches what the receipt table shows: the paper reference # and the
        # agreement number — the two things a person doing this lookup
        # actually has in hand (a slip in one hand, a house-account name in
        # the other), same rationale as GoodsReceipt.get_all's search.
        q = q.where(
            AgreementReceipt.receipt_ref.ilike(term)
            | PurchaseAgreement.number.ilike(term)
        )
    total: int = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    offset = (page - 1) * page_size
    # Fix round 1, Important 1: receipt_date alone is not a unique sort key —
    # a busy house account can post dozens of receipts on one calendar date,
    # and Postgres does not guarantee stable ordering among tied rows across
    # two separate queries (this page's COUNT above and this SELECT are two
    # queries). Without a full tiebreaker chain down to a unique column,
    # paging (offset/limit) over tied rows can both duplicate a row onto two
    # pages and skip another entirely — AP reading page 2 would see a slip
    # that was already on page 1, and never see one that fell in the gap.
    # created_at narrows the tie a lot (insertion order); id (the primary
    # key, always unique) guarantees the chain terminates.
    rows = (await db.execute(
        q.order_by(
            AgreementReceipt.receipt_date.desc(),
            AgreementReceipt.created_at.desc(),
            AgreementReceipt.id.desc(),
        ).offset(offset).limit(page_size)
    )).all()
    return [(row[0], row[1], row[2], row[3], row[4], row[5], row[6]) for row in rows], total


# 唯二可离开的活跃态 —— reconciled 已被发票认领(编辑/作废会让发票挂着一份
# 跟匹配依据对不上的凭证)、rejected/voided 已经是终态。PATCH 复用同一个
# 集合(review finding #1):既然 void 只放行这两个状态,编辑没道理更宽松。
EDITABLE = ("open", "pending_ap_review")
VOIDABLE = EDITABLE

# 已退役的终态 —— 这两个状态的行不再参与 receipt_ref 唯一性(whole-branch review
# I3):录错作废/AP 驳回后必须能用同一个参考号重录那份凭证(小票/送货单/服务单
# 皆可)。与 models/agreement_receipt.py 的部分唯一索引谓词是同一条规则的两处
# 表述(ag04_agreement_receipts 已把当初计划中的 ag05 narrowing 一次性折叠进去,
# 不再有第三处独立迁移),改一处必须改两处。
RETIRED = ("voided", "rejected")


async def update(db: AsyncSession, receipt: AgreementReceipt, body: ReceiptUpdate) -> AgreementReceipt:
    if receipt.status not in EDITABLE:
        raise ValueError(
            f"Receipt is {receipt.status}; only an open or pending-AP-review receipt can be "
            "edited. Editing a reconciled receipt would desync it from the invoice "
            "it was matched against without a trace; a rejected or removed receipt is final.")
    had_reason = bool(receipt.missing_receipt_reason)
    patch = body.model_dump(exclude_unset=True)
    for field, value in patch.items():
        setattr(receipt, field, value)
    # Route to AP review on the SAME rule create() already enforces. This is
    # not a special case carved out for any one caller (in particular, not
    # "the thing a failed-attachment-upload retry needs") — it is update()
    # staying aligned with a rule create() has had since day one: declaring
    # "no evidence for this receipt" must always cost the AP gate, no matter
    # whether that declaration happens at creation or is added later via
    # edit. Without this, PATCHing a reason onto an already-`open` receipt
    # produced a receipt that *claims* to have no evidence yet was never sent
    # for review — a hole in the evidence chain a claimed/paid invoice could
    # ride through.
    #
    # Deliberately ONE-DIRECTIONAL. Clearing the reason (or blanking it back
    # to falsy) never flips a pending_ap_review receipt back to open — that
    # would let an editor silently undo an AP gate that's an AP decision to
    # make (see ap_review() below), not something a PATCH should be able to
    # revoke by emptying a text field. And deliberately a no-op, not an
    # error, when the receipt is already pending_ap_review and the PATCH
    # touches missing_receipt_reason again (e.g. rewording it) — `had_reason`
    # is already True in that case, so the condition below simply doesn't
    # fire and the existing pending_ap_review status is left alone.
    if (
        "missing_receipt_reason" in patch
        and not had_reason
        and receipt.missing_receipt_reason
        and receipt.status == "open"
    ):
        receipt.status = "pending_ap_review"
    # 对合并后的行校验,不是对 patch body 本身 —— 一次只改 amount/tax_amount/
    # total_amount 三者之一的 PATCH,必须按合并后的整行判断是否还自洽
    # (review finding #1):校验 body 自身在这里永远通过,因为大多数 PATCH
    # 天生只带一个金额字段。
    validate_totals(
        amount=receipt.amount, tax_amount=receipt.tax_amount, total_amount=receipt.total_amount)
    await db.flush()
    return receipt


async def void(db: AsyncSession, receipt: AgreementReceipt) -> None:
    if receipt.status not in VOIDABLE:
        raise ValueError(
            f"A {receipt.status} receipt cannot be removed; detach its invoice first")
    receipt.status = "voided"
    await db.flush()


async def claim(
    db: AsyncSession, agr: PurchaseAgreement, receipt_ids: list[uuid.UUID], invoice,
) -> list[AgreementReceipt]:
    """Claim one or more OPEN receipts against `invoice` (house_account match,
    Task 5). Duplicate ids are deduped (order-preserving) and treated as one —
    the caller picking the same receipt twice from a UI multi-select is not a
    reason to fail the whole match.

    Two passes on purpose: validate every id BEFORE mutating any row. A
    single-pass "validate-then-mutate-as-we-go" loop would leave earlier
    receipts already flipped to reconciled/invoice_id-set in the session's
    identity map when a later id fails partway through the batch — and that
    identity map is not a safe boundary to lean on: whether a pending change
    actually reaches the database before the failure surfaces depends on the
    CALLER's session (autoflush setting, any query issued in between, when it
    eventually calls flush()/commit()) — not on anything this function
    controls. Production's session factory (app/db/session.py's
    AsyncSessionLocal) sets autoflush=False, so a pending UPDATE there would
    likely stay unflushed until this function's own trailing `db.flush()` —
    which a single-pass bug would skip entirely on the failing path anyway.
    A test session built with plain defaults (autoflush=True, e.g.
    tests/test_receipt_match.py's) can flush it earlier, via the very next
    SELECT in the same loop. Either way, this function must not depend on
    which of those two the caller happens to be running — the two-pass
    structure makes the guarantee unconditional instead of a race against
    flush timing.

    This function makes no assumption about what its caller does with the
    session afterward — commit, roll back, both, or neither. As of Task 7 it
    has a real caller: `crud/invoice.py`'s `set_receipts`, reached from
    `PUT /invoices/{id}/receipts` (api/v1/invoices.py). That endpoint's
    session is a request-scoped one from api/v1's dependency chain
    (app/db/session.py's get_session), which commits on a clean return and
    rolls back on any raised exception — but this function still makes no
    assumption about that, because `set_receipts` is not the only thing that
    can call it (test_receipt_match.py's guard tests call it directly, with
    sessions that behave differently — see their docstrings). (Reviewed
    round 3: an earlier version of this docstring named `_match_to_agreement`
    as "the caller" and claimed it "does not roll back" — both false. That
    call site was removed by Task 6, and even before it was, the real
    request-scoped session in api/v1's dependency chain rolls back on any
    raised exception, not the opposite.)

    Accepts a receipt whose status is "open", OR "reconciled" AND already
    claimed by THIS SAME invoice (receipt.invoice_id == invoice.id) — added
    Task 7. A receipt "reconciled" by a DIFFERENT invoice, or sitting in
    pending_ap_review/rejected/voided, is still refused.

    Positive contract this gives claim(): it is IDEMPOTENT for a receipt the
    calling invoice already holds. That matters beyond the obvious "re-PUT
    the same list" case. `set_receipts` (crud/invoice.py) always calls
    `_release_agreement_evidence` before this function runs, and that
    release only walks `invoice.receipt_ids` — the JSONB array on the
    invoice row, NOT a query of "every AgreementReceipt row whose
    invoice_id currently points at this invoice". Those two are supposed to
    agree, but nothing enforces it: `agreement_receipts.invoice_id` and
    `invoices.receipt_ids` have no FK to each other — nothing in the schema
    keeps them in sync — and Data Maintenance can reset `invoice.status`
    back to "unmatched" via a bare `setattr` with no release hook at all
    (see the comment on `delete()`'s own release call, and
    `_match_to_agreement`'s "if invoice.receipt_ids or invoice.schedule_id"
    guard above `_release_agreement_evidence`'s definition) — which is
    documented as a legitimate way to force a re-match. Whenever that drift
    happens, a receipt can sit at
    `reconciled` with `invoice_id` correctly pointing at this invoice while
    `invoice.receipt_ids` no longer lists it — release() cannot find it, and
    without the widened guard here, claim() could never accept it back
    either (`update()`/`void()` both refuse `reconciled` too), so the row
    would be permanently stuck. Widening this guard makes the very next PUT
    /invoices/{id}/receipts that names that id the row's ONLY way back to a
    consistent state, instead of a second dead end on top of the first.
    """
    seen_ids = list(dict.fromkeys(receipt_ids))
    rows: list[AgreementReceipt] = []
    for receipt_id in seen_ids:
        receipt = (await db.execute(
            select(AgreementReceipt).where(AgreementReceipt.id == receipt_id)
        )).scalar_one_or_none()
        if receipt is None or receipt.agreement_id != agr.id:
            raise ValueError(
                f"Receipt {receipt_id} does not belong to agreement {agr.number}")
        already_held_by_this_invoice = (
            receipt.status == "reconciled" and receipt.invoice_id == invoice.id
        )
        if receipt.status != "open" and not already_held_by_this_invoice:
            raise ValueError(
                f"Receipt {receipt_id} is {receipt.status}; only an open receipt can be claimed")
        rows.append(receipt)
    for receipt in rows:
        receipt.status = "reconciled"
        receipt.invoice_id = invoice.id
    await db.flush()
    return rows


async def ap_review(
    db: AsyncSession, receipt: AgreementReceipt, action: str, reviewer_id: uuid.UUID,
) -> AgreementReceipt:
    if receipt.status != "pending_ap_review":
        raise ValueError(
            f"Receipt is {receipt.status}; only a receipt awaiting AP review can be decided")
    if action not in ("approve", "reject"):
        raise ValueError("action must be 'approve' or 'reject'")
    receipt.status = "open" if action == "approve" else "rejected"
    receipt.ap_reviewed_by = reviewer_id
    receipt.ap_reviewed_at = datetime.now(timezone.utc)
    await db.flush()
    return receipt
