"""Shared schema for the current approval step surfaced in list views."""
from datetime import datetime

from pydantic import BaseModel


class CurrentStep(BaseModel):
    """The approval step an in-review document is currently waiting on.

    Sourced from the open approve_{doctype} task, not approval_step_idx.
    """
    role: str
    label: str
    approver_name: str | None = None
    since: datetime

    model_config = {"from_attributes": True}
