"""Pydantic schemas for directory endpoint (Task 11)."""
import uuid

from pydantic import BaseModel, ConfigDict


class DirectoryUserOut(BaseModel):
    """Directory search result — user id, full_name, and email for attendee picker.

    from_attributes=True allows ORM object conversion (User mirror).
    """
    id: uuid.UUID
    full_name: str
    email: str

    model_config = ConfigDict(from_attributes=True)
