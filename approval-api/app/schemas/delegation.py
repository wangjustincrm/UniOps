import uuid
from datetime import date, datetime

from pydantic import BaseModel


class DelegationCreate(BaseModel):
    delegator_user_id: uuid.UUID
    delegate_user_id: uuid.UUID
    start_date: date   # inclusive
    end_date: date     # inclusive
    note: str | None = None


class DelegationUpdate(BaseModel):
    """Partial update — only start_date/end_date/note are editable.

    delegator_user_id/delegate_user_id are immutable after creation; revoke +
    create a new one to reassign a delegation to different people.
    """
    start_date: date | None = None
    end_date: date | None = None
    note: str | None = None


class DelegationOut(BaseModel):
    id: uuid.UUID
    delegator_user_id: uuid.UUID
    delegate_user_id: uuid.UUID
    delegator_name: str | None = None
    delegate_name: str | None = None
    start_date: date
    end_date: date
    note: str | None = None
    revoked_at: datetime | None = None
    revoked_by: uuid.UUID | None = None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime
