"""CRUD for agreement receipts — typed evidence for house-account invoices."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
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
    identity map when a later id fails — the caller (`_match_to_agreement`)
    converts our ValueError to a 422 and does not roll back, so those partial
    writes would ride along on the next successful flush.

    Only "open" is accepted today — a receipt already claimed by another
    invoice ("reconciled") or awaiting/failed AP review cannot be claimed a
    second time. (Task 7 is expected to widen this to also accept a
    "reconciled" receipt already claimed by THIS SAME invoice, so re-matching
    an invoice to the same agreement isn't rejected as a duplicate claim —
    deliberately out of scope here.)
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
        if receipt.status != "open":
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
