"""Pydantic schemas for BookingConfig admin endpoints."""
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ConfigOut(BaseModel):
    smtp_settings: dict
    rules: dict
    organizer_mode: str
    model_config = ConfigDict(from_attributes=True)


class ConfigUpdate(BaseModel):
    smtp_settings: dict | None = None
    rules: dict | None = None
    organizer_mode: Literal["system", "initiator"] | None = None
