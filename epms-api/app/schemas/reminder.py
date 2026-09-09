"""Response for the Approval Timeline's manual "Send reminder" action."""
from datetime import datetime

from pydantic import BaseModel


class ReminderResponse(BaseModel):
    sent: bool
    document_number: str
    # Display names (or "<Role> (shared@mailbox)") of everyone the reminder was
    # dispatched to — echoed back so the UI can name them instead of claiming a
    # vague success.
    recipients: list[str]
    next_allowed_at: datetime
