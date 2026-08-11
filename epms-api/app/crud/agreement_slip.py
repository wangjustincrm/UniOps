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


async def update(db: AsyncSession, slip: AgreementPickupSlip, body: SlipUpdate) -> AgreementPickupSlip:
    if slip.status not in EDITABLE:
        raise ValueError(
            f"Slip is {slip.status}; only an open or pending-AP-review slip can be "
            "edited. Editing a reconciled slip would desync it from the invoice "
            "it was matched against without a trace; a rejected or voided slip is final.")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(slip, field, value)
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
