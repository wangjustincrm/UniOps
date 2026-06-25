import uuid
from decimal import Decimal
from pydantic import BaseModel


class BudgetBalanceResponse(BaseModel):
    budget_code: str
    cost_center_id: uuid.UUID
    annual_budget: Decimal
    committed: Decimal
    actual_spent: Decimal
    available: Decimal


class BudgetCommitRequest(BaseModel):
    document_type: str          # pr | po | pa
    document_id: uuid.UUID
    document_number: str
    budget_code: str
    cost_center_id: uuid.UUID
    amount: Decimal


class BudgetCommitResponse(BaseModel):
    budget_code: str
    annual_budget: Decimal
    committed_before: Decimal
    committed_after: Decimal
    available: Decimal


class BudgetReleaseRequest(BaseModel):
    document_type: str
    document_id: uuid.UUID
    budget_code: str
    cost_center_id: uuid.UUID


class BudgetActualizeRequest(BaseModel):
    document_type: str
    document_id: uuid.UUID
    budget_code: str
    cost_center_id: uuid.UUID
    amount: Decimal             # actual payment amount (committed -= amount, actual_spent += amount)
