import uuid
from datetime import datetime
from pydantic import BaseModel


class UserDirectoryResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    department_id: uuid.UUID | None
    teams_account: str | None
    notification_channel: str
    is_active: bool
    created_at: datetime
    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    items: list[UserDirectoryResponse]
    total: int
