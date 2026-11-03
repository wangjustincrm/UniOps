"""Shadow-run comparison DTOs (design §5.4)."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints


class ShadowMatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    partner_name: str | None = None
    id: int
    kind: str
    status: str
    reference_document_id: int | None
    order_id: int | None
    reference_number: str | None
    partner_code: str | None
    differences: int
    expected_reason: str | None
    note: str | None
    compared_at: datetime
    version: int
    updated_by_name: str | None
    document_id: int | None = None   # a 997 / 945 compared (P5)


class ShadowRow(BaseModel):
    zoho: str | None
    sales: str | None
    same: bool


class ShadowCompare(ShadowMatchRead):
    """Both files side by side; `ignored` lists the masked fields."""
    rows: list[ShadowRow]
    reference_name: str | None
    ignored: list[str]
    document_name: str | None = None
    would: dict[str, Any] | None = None   # 997 / 945: what Sales would have recorded


class ExpectedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]
