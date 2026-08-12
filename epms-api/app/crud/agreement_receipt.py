"""CRUD for agreement receipts — typed evidence for house-account invoices."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import PurchaseAgreement
from app.models.agreement_receipt import AgreementReceipt
from app.schemas.agreement_receipt import ReceiptCreate, ReceiptUpdate, validate_totals


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


async def list_all(
    db: AsyncSession,
    *,
    agreement_id: uuid.UUID | None = None,
    receipt_type: str | None = None,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[tuple[AgreementReceipt, str]], int]:
    """Cross-agreement listing (GET /agreement-receipts, Task 9) — the reason
    this page exists is that AP/warehouse staff shouldn't have to open an
    agreement first to record or find a receipt (see the task brief's "where
    this fits"). Joined to PurchaseAgreement so the caller gets the parent
    agreement's human `number` back alongside each row — the frontend must
    never be handed a bare agreement_id UUID to render (brief item 5).
    """
    q = select(AgreementReceipt, PurchaseAgreement.number).join(
        PurchaseAgreement, AgreementReceipt.agreement_id == PurchaseAgreement.id)
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
    rows = (await db.execute(
        q.order_by(AgreementReceipt.receipt_date.desc()).offset(offset).limit(page_size)
    )).all()
    return [(row[0], row[1]) for row in rows], total


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
            "it was matched against without a trace; a rejected or voided receipt is final.")
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
            f"A {receipt.status} receipt cannot be voided; detach its invoice first")
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
