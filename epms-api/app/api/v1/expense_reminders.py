"""Manual "Send reminder" for OA expense claims.

expense_claims belongs to expense-api, but the only notification machinery in
the system — SMTP settings, delegation stand-ins, role pools, shared mailboxes,
the notification_logs cooldown — lives here. Rather than a second copy in
expense-api that drifts, OA's Approval Status card calls expense-api
(`POST /expenses/{id}/remind`), which enforces the claim's object-level read
gate and then forwards the user's bearer to this endpoint.

Like the PR/PO endpoints there is no role gate: the 24h cooldown is what
protects the approver's inbox. The claim id is only used to find its open
`approve_<code>` tasks in the shared tasks table; nothing else is read from it.
"""
import re
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.deps import CurrentUserPayload, SessionDep
from app.schemas.reminder import ReminderResponse
from app.services.manual_reminder import expense_approval_task_types, remind_document

router = APIRouter(prefix="/expense-claims", tags=["expense-claims"])

# EXP / MIL / TRV / CFM and custom-form codes. Bounded so a crafted value cannot
# widen the task query into something that is not an expense claim type.
_CLAIM_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,19}$")


class ExpenseReminderRequest(BaseModel):
    claim_type: str


@router.post("/{claim_id}/remind", response_model=ReminderResponse)
async def remind_expense_approver(
    claim_id: uuid.UUID,
    body: ExpenseReminderRequest,
    db: SessionDep,
    user: CurrentUserPayload,
):
    if not _CLAIM_TYPE.match(body.claim_type):
        raise HTTPException(status_code=422, detail="Invalid claim type")
    return await remind_document(
        db,
        document_type=body.claim_type.lower(),
        document_id=claim_id,
        actor_id=uuid.UUID(user["sub"]),
        task_types=expense_approval_task_types(body.claim_type),
    )
