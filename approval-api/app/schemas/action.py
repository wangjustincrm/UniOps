import uuid
from pydantic import BaseModel


class ActionRequest(BaseModel):
    action: str   # submit | approve | return | reject | recall | cancel | process
    comment: str | None = None


class ActionResult(BaseModel):
    doc_type: str
    doc_id: uuid.UUID
    doc_number: str
    new_status: str
    approval_step_idx: int
    workflow_complete: bool
    auto_skipped_steps: list[int] = []
