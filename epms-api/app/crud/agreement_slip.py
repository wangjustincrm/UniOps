"""CRUD for house-account pickup slips."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import PurchaseAgreement
from app.models.agreement_slip import AgreementPickupSlip
from app.schemas.agreement_slip import SlipCreate, SlipUpdate, validate_totals


async def create(
    db: AsyncSession, agr: PurchaseAgreement, body: SlipCreate, created_by: uuid.UUID,
) -> AgreementPickupSlip:
    # 只有给了 missing_slip_reason 才走 pending_ap_review —— 附件必填是前端
    # 规则(见 test_pickup_slip_api.py 顶部注释),后端无法可靠判断"用户是否
    # 打算传附件",强制要求会让"先建行再传附件"的两步上传流程无法进行。
    slip = AgreementPickupSlip(
        agreement_id=agr.id, created_by=created_by,
        status="pending_ap_review" if body.missing_slip_reason else "open",
        **body.model_dump(exclude={"missing_slip_reason"}),
        missing_slip_reason=body.missing_slip_reason)
    db.add(slip)
    await db.flush()
    return slip


async def list_for_agreement(
    db: AsyncSession, agreement_id: uuid.UUID, status: str | None = None,
) -> list[AgreementPickupSlip]:
    q = select(AgreementPickupSlip).where(AgreementPickupSlip.agreement_id == agreement_id)
    if status:
        q = q.where(AgreementPickupSlip.status == status)
    rows = (await db.execute(q.order_by(AgreementPickupSlip.created_at.desc()))).scalars().all()
    return list(rows)


# 唯二可离开的活跃态 —— reconciled 已被发票认领(编辑/作废会让发票挂着一份
# 跟匹配依据对不上的凭证)、rejected/voided 已经是终态。PATCH 复用同一个
# 集合(review finding #1):既然 void 只放行这两个状态,编辑没道理更宽松。
EDITABLE = ("open", "pending_ap_review")
VOIDABLE = EDITABLE

# 已退役的终态 —— 这两个状态的行不再参与 slip_ref 唯一性(whole-branch review
# I3):录错作废/AP 驳回后必须能用同一个参考号重录那张纸质小票。与
# models/agreement_slip.py 的部分唯一索引谓词、alembic ag05_slip_ref_uq_active
# 是同一条规则的三处表述,改一处必须改三处。
RETIRED = ("voided", "rejected")


async def update(db: AsyncSession, slip: AgreementPickupSlip, body: SlipUpdate) -> AgreementPickupSlip:
    if slip.status not in EDITABLE:
        raise ValueError(
            f"Slip is {slip.status}; only an open or pending-AP-review slip can be "
            "edited. Editing a reconciled slip would desync it from the invoice "
            "it was matched against without a trace; a rejected or voided slip is final.")
    had_reason = bool(slip.missing_slip_reason)
    patch = body.model_dump(exclude_unset=True)
    for field, value in patch.items():
        setattr(slip, field, value)
    # Route to AP review on the SAME rule create() already enforces — this is
    # not a favour to any particular caller (in particular, not "the thing
    # SlipEntryForm's post-upload-failure PATCH needs"), it is update()
    # catching up to a rule create() has had since day one: declaring "no
    # photo for this slip" must always cost the AP gate, no matter whether
    # that declaration happens at creation or is added later via edit.
    # Without this, PATCHing a reason onto an already-`open` slip produced a
    # slip that *claims* to have no evidence yet was never sent for review —
    # a hole in the evidence chain a claimed/paid invoice could ride through.
    #
    # Deliberately ONE-DIRECTIONAL. Clearing the reason (or blanking it back
    # to falsy) never flips a pending_ap_review slip back to open — that
    # would let an editor silently undo an AP gate that's an AP decision to
    # make (see ap_review() below), not something a PATCH should be able to
    # revoke by emptying a text field. And deliberately a no-op, not an
    # error, when the slip is already pending_ap_review and the PATCH
    # touches missing_slip_reason again (e.g. rewording it) — `had_reason`
    # is already True in that case, so the condition below simply doesn't
    # fire and the existing pending_ap_review status is left alone.
    if (
        "missing_slip_reason" in patch
        and not had_reason
        and slip.missing_slip_reason
        and slip.status == "open"
    ):
        slip.status = "pending_ap_review"
    # 对合并后的行校验,不是对 patch body 本身 —— 一次只改 amount/tax_amount/
    # total_amount 三者之一的 PATCH,必须按合并后的整行判断是否还自洽
    # (review finding #1):校验 body 自身在这里永远通过,因为大多数 PATCH
    # 天生只带一个金额字段。
    validate_totals(amount=slip.amount, tax_amount=slip.tax_amount, total_amount=slip.total_amount)
    await db.flush()
    return slip


async def void(db: AsyncSession, slip: AgreementPickupSlip) -> None:
    if slip.status not in VOIDABLE:
        raise ValueError(
            f"A {slip.status} slip cannot be voided; detach its invoice first")
    slip.status = "voided"
    await db.flush()


async def claim(
    db: AsyncSession, agr: PurchaseAgreement, slip_ids: list[uuid.UUID], invoice,
) -> list[AgreementPickupSlip]:
    """Claim one or more OPEN pickup slips against `invoice` (house_account match,
    Task 5). Duplicate ids are deduped (order-preserving) and treated as one —
    the caller picking the same slip twice from a UI multi-select is not a
    reason to fail the whole match.

    Two passes on purpose: validate every id BEFORE mutating any row. A
    single-pass "validate-then-mutate-as-we-go" loop would leave earlier slips
    already flipped to reconciled/invoice_id-set in the session's identity map
    when a later id fails — the caller (`_match_to_agreement`) converts our
    ValueError to a 422 and does not roll back, so those partial writes would
    ride along on the next successful flush.
    """
    seen_ids = list(dict.fromkeys(slip_ids))
    rows: list[AgreementPickupSlip] = []
    for slip_id in seen_ids:
        slip = (await db.execute(
            select(AgreementPickupSlip).where(AgreementPickupSlip.id == slip_id)
        )).scalar_one_or_none()
        if slip is None or slip.agreement_id != agr.id:
            raise ValueError(
                f"Pickup slip {slip_id} does not belong to agreement {agr.number}")
        if slip.status != "open":
            raise ValueError(
                f"Pickup slip {slip_id} is {slip.status}; only an open slip can be claimed")
        rows.append(slip)
    for slip in rows:
        slip.status = "reconciled"
        slip.invoice_id = invoice.id
    await db.flush()
    return rows


async def ap_review(
    db: AsyncSession, slip: AgreementPickupSlip, action: str, reviewer_id: uuid.UUID,
) -> AgreementPickupSlip:
    if slip.status != "pending_ap_review":
        raise ValueError(f"Slip is {slip.status}; only a slip awaiting AP review can be decided")
    if action not in ("approve", "reject"):
        raise ValueError("action must be 'approve' or 'reject'")
    slip.status = "open" if action == "approve" else "rejected"
    slip.ap_reviewed_by = reviewer_id
    slip.ap_reviewed_at = datetime.now(timezone.utc)
    await db.flush()
    return slip
