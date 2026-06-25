"""Hierarchy response — CC → L1 → Account tree (compat with old expense-api endpoint)."""
import uuid

from pydantic import BaseModel


class HierarchyAccount(BaseModel):
    id: uuid.UUID
    code: str
    name: str


class HierarchyL1(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    accounts: list[HierarchyAccount]


class HierarchyResponse(BaseModel):
    l1_groups: list[HierarchyL1]
