"""Single-row settings schema."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BudgetSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    max_factors_per_account: int
    updated_at: datetime


class BudgetSettingsUpdate(BaseModel):
    max_factors_per_account: int | None = Field(default=None, ge=1, le=50)
