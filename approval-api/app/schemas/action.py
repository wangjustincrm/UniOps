import uuid
from pydantic import BaseModel


class ActionRequest(BaseModel):
    action: str   # submit | approve | return | reject | recall | cancel | process
    comment: str | None = None
    # approve / return / reject: the step the caller's decision is for. When
    # the document is no longer at it, the action is refused instead of being
    # applied to whatever step it has moved on to (a double-click). Optional —
    # callers that do not send it keep the old behaviour.
    expected_step: int | None = None


class ActionResult(BaseModel):
    doc_type: str
    doc_id: uuid.UUID
    doc_number: str
    new_status: str
    approval_step_idx: int
    workflow_complete: bool
    auto_skipped_steps: list[int] = []
