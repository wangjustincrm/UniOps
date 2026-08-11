"""Request/response schemas for Purchase Agreement (AGR)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

# 独立审批流(用户决策:不复用 PO 链)。这是种子默认值 —— 生产以 CompanyConfig
# .workflow_defs["agr"] 为准,管理员可在 Portal Admin 改。
AGR_WORKFLOW = [
    {"step": 0, "role": "dept_manager",        "label": "Department Manager"},
    {"step": 1, "role": "procurement_manager", "label": "Procurement Manager"},
    {"step": 2, "role": "finance_manager",     "label": "Finance Manager"},
]

AGREEMENT_TYPES = ("house_account", "recurring", "milestone")
RECURRING_TYPES = ("weekly", "monthly", "quarterly", "yearly")
ANCHORED_TYPES = ("quarterly", "yearly")


def validate_recurrence(
    *,
    agreement_type: str,
    recurring_type: str | None,
    expected_invoice_day: int | None,
    anchor_month: int | None,
    expected_amount_per_period: Decimal | None,
    tolerance_pct: Decimal | None,
    overdue_after_days: int | None,
    valid_from: date,
    valid_to: date,
) -> None:
    """The recurrence coherence rule. Factored out of AgreementCreate's schema
    validator so crud.agreement.update() can run the SAME check against the
    merged post-patch state (see task-3 review Finding 1: a schema-level
    model_validator on AgreementUpdate can't do this — a PATCH body is
    partial and usually doesn't carry agreement_type/valid_from/valid_to, so
    a validator that only sees `self` on the partial body has nothing to
    judge those fields against). Raises plain ValueError; AgreementCreate
    wraps this call in a model_validator so pydantic turns it into a 422
    ValidationError, while crud.agreement.update() lets the plain ValueError
    propagate to the endpoint, which maps it to a 409.
    """
    if agreement_type == "recurring":
        if recurring_type not in RECURRING_TYPES:
            raise ValueError(
                "recurring_type is required for a recurring agreement "
                f"(one of {', '.join(RECURRING_TYPES)})")
        if expected_invoice_day is None:
            raise ValueError("expected_invoice_day is required for a recurring agreement")
        if recurring_type == "weekly" and not 1 <= expected_invoice_day <= 7:
            raise ValueError(
                "For a weekly cycle expected_invoice_day is a weekday, 1..7 (1 = Monday)")
        if recurring_type in ANCHORED_TYPES and anchor_month is None:
            raise ValueError(
                f"anchor_month is required for a {recurring_type} cycle — real "
                "billing cycles often do not start in January, and the contract start "
                "date is not a reliable proxy for the billing anchor")
        # 生成不出来的排期,建档/改档时就该挡住,而不是等审批通过那一刻才炸。
        from app.services.agreement_schedule import TooManyPeriods, build_period_rows
        try:
            build_period_rows(
                recurring_type=recurring_type, valid_from=valid_from,
                valid_to=valid_to, expected_invoice_day=expected_invoice_day,
                anchor_month=anchor_month)
        except TooManyPeriods as exc:
            raise ValueError(str(exc)) from exc
    else:
        bad = [n for n, v in (
            ("recurring_type", recurring_type),
            ("expected_invoice_day", expected_invoice_day),
            ("anchor_month", anchor_month),
            ("expected_amount_per_period", expected_amount_per_period),
            ("tolerance_pct", tolerance_pct),
            ("overdue_after_days", overdue_after_days),
        ) if v is not None]
        if bad:
            raise ValueError(f"{', '.join(bad)} only apply to a recurring agreement")


class MilestoneRowIn(BaseModel):
    milestone_name: str = Field(min_length=1, max_length=255)
    # 纯文本时间(决策 8):"Within 1 week after contract signing"。阶段时间几乎
    # 总是相对合同事件的,落成日历日期只会得到一个填时就不准、之后没人维护的数字。
    expected_timing: str | None = Field(default=None, max_length=255)
    expected_amount: Decimal | None = Field(default=None, ge=0)
    amount_pct: Decimal | None = Field(default=None, ge=0, le=100)


def validate_milestones(
    *,
    agreement_type: str,
    milestones: list[MilestoneRowIn],
    not_to_exceed: Decimal | None,
) -> None:
    """Same drift-avoidance rationale as validate_recurrence — shared by
    AgreementCreate's schema validator and crud.agreement.update()."""
    if milestones and agreement_type != "milestone":
        raise ValueError("milestones can only be set on a milestone agreement")
    if any(m.amount_pct is not None for m in milestones) and not_to_exceed is None:
        raise ValueError(
            "amount_pct needs not_to_exceed as its base — set a ceiling on the "
            "agreement, or enter absolute amounts on the stages")


class ScheduleRowResponse(BaseModel):
    id: uuid.UUID
    agreement_id: uuid.UUID
    schedule_type: str
    sequence: int
    expected_amount: Decimal | None
    expected_date: date | None
    expected_timing: str | None
    status: str
    invoice_id: uuid.UUID | None
    period_label: str | None
    tolerance_pct: Decimal | None
    overdue_after_days: int | None
    milestone_name: str | None
    amount_pct: Decimal | None
    accepted_by: uuid.UUID | None
    accepted_at: datetime | None

    model_config = {"from_attributes": True}


class ScheduleListResponse(BaseModel):
    items: list[ScheduleRowResponse]


class AgreementCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    agreement_type: str = Field(pattern="^(house_account|recurring|milestone)$")
    vendor_id: uuid.UUID
    contract_no: str | None = Field(default=None, max_length=100)
    contact_email: str | None = Field(default=None, max_length=255)
    vendor_reference: str | None = Field(default=None, max_length=100)
    valid_from: date
    valid_to: date
    grace_days: int = Field(default=30, ge=0, le=365)
    not_to_exceed: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="CAD", min_length=1, max_length=10)
    tax_code: str | None = Field(default=None, max_length=20)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1)
    department_id: uuid.UUID | None = None
    budget_code: str | None = Field(default=None, max_length=100)
    owner_id: uuid.UUID | None = None
    notes: str | None = None

    cost_center_id: uuid.UUID | None = None
    recurring_type: str | None = None
    expected_invoice_day: int | None = Field(default=None, ge=1, le=31)
    anchor_month: int | None = Field(default=None, ge=1, le=12)
    expected_amount_per_period: Decimal | None = Field(default=None, ge=0)
    tolerance_pct: Decimal | None = Field(default=None, ge=0, le=100)
    overdue_after_days: int | None = Field(default=None, ge=0, le=365)
    milestones: list[MilestoneRowIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validity_window_is_ordered(self):
        if self.valid_to < self.valid_from:
            raise ValueError("valid_to must be on or after valid_from")
        return self

    @model_validator(mode="after")
    def _recurrence_is_coherent(self):
        validate_recurrence(
            agreement_type=self.agreement_type,
            recurring_type=self.recurring_type,
            expected_invoice_day=self.expected_invoice_day,
            anchor_month=self.anchor_month,
            expected_amount_per_period=self.expected_amount_per_period,
            tolerance_pct=self.tolerance_pct,
            overdue_after_days=self.overdue_after_days,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
        )
        return self

    @model_validator(mode="after")
    def _milestones_are_coherent(self):
        validate_milestones(
            agreement_type=self.agreement_type,
            milestones=self.milestones,
            not_to_exceed=self.not_to_exceed,
        )
        return self


class AgreementUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    contract_no: str | None = Field(default=None, max_length=100)
    contact_email: str | None = Field(default=None, max_length=255)
    vendor_reference: str | None = Field(default=None, max_length=100)
    valid_from: date | None = None
    valid_to: date | None = None
    grace_days: int | None = Field(default=None, ge=0, le=365)
    not_to_exceed: Decimal | None = Field(default=None, ge=0)
    tax_code: str | None = Field(default=None, max_length=20)
    tax_rate: Decimal | None = Field(default=None, ge=0, le=1)
    department_id: uuid.UUID | None = None
    budget_code: str | None = Field(default=None, max_length=100)
    owner_id: uuid.UUID | None = None
    notes: str | None = None

    cost_center_id: uuid.UUID | None = None
    recurring_type: str | None = None
    expected_invoice_day: int | None = Field(default=None, ge=1, le=31)
    anchor_month: int | None = Field(default=None, ge=1, le=12)
    expected_amount_per_period: Decimal | None = Field(default=None, ge=0)
    tolerance_pct: Decimal | None = Field(default=None, ge=0, le=100)
    overdue_after_days: int | None = Field(default=None, ge=0, le=365)
    # None = 不动阶段行(维持既有排期);[] = 清空阶段行。
    milestones: list[MilestoneRowIn] | None = None


class AgreementActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=20)   # submit|approve|return|cancel
    comment: str | None = None


class AgreementResponse(BaseModel):
    id: uuid.UUID
    number: str
    title: str
    agreement_type: str
    contract_no: str | None
    contact_email: str | None
    vendor_id: uuid.UUID
    vendor_name: str
    vendor_reference: str | None
    valid_from: date
    valid_to: date
    grace_days: int
    not_to_exceed: Decimal | None
    consumed_amount: Decimal
    currency: str
    tax_code: str | None
    tax_rate: Decimal | None
    department_id: uuid.UUID | None
    budget_code: str | None
    owner_id: uuid.UUID | None
    cost_center_id: uuid.UUID | None
    recurring_type: str | None
    expected_invoice_day: int | None
    anchor_month: int | None
    expected_amount_per_period: Decimal | None
    tolerance_pct: Decimal | None
    overdue_after_days: int | None
    status: str
    approval_step_idx: int
    notes: str | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgreementListResponse(BaseModel):
    items: list[AgreementResponse]
    total: int
