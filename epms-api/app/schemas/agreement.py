"""Request/response schemas for Purchase Agreement (AGR)."""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

# 独立审批流(用户决策:不复用 PO 链)。这是种子默认值 —— 生产以 CompanyConfig
# .workflow_defs["agr"] 为准,管理员可在 Portal Admin 改。
AGR_WORKFLOW = [
    {"step": 0, "role": "dept_manager",        "label": "Department Manager"},
    {"step": 1, "role": "procurement_manager", "label": "Procurement Manager"},
    {"step": 2, "role": "finance_manager",     "label": "Finance Manager"},
]

AGREEMENT_TYPES = ("house_account", "recurring", "milestone")
RECURRING_TYPES = ("weekly", "monthly", "quarterly", "yearly", "special_monthly")
ANCHORED_TYPES = ("quarterly", "yearly")


def normalize_active_months(value: list[int] | None) -> list[int] | None:
    """1..12,升序去重。存进库的是这份规范化后的值 —— 排期生成只做集合判断,
    但这一列还要被人读(协议详情、Data Maintenance、以后的 PDF),乱序或重复
    的 [11, 5, 5] 会让同一份选择在不同协议上长得不一样。"""
    if value is None:
        return None
    bad = sorted({m for m in value if not 1 <= m <= 12})
    if bad:
        raise ValueError(
            f"active_months must be month numbers 1..12 (got {bad})")
    return sorted(set(value))


def validate_validity_window(*, valid_from: date, valid_to: date) -> None:
    """Same drift-avoidance rationale as validate_recurrence — shared by
    AgreementCreate's schema validator and crud.agreement.update(), so a
    PATCH can't push valid_to before valid_from any more than create() can
    (task-3 re-review Finding 3: this was still create()-only)."""
    if valid_to < valid_from:
        raise ValueError("valid_to must be on or after valid_from")


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
    schedule_start_date: date | None = None,
    active_months: list[int] | None = None,
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
        # special_monthly 的月份清单是这个周期存在的全部理由 —— 空选择等于一份
        # 没有任何期次的排期,与 schedule_start_date 落在窗口外是同一类事故:
        # 协议看着是 recurring,却一行期次都没有,发票认领不到、PA 那道"未链接
        # 期次"闸门永远过不去,而那时协议已 active、字段不可再改。
        if recurring_type == "special_monthly":
            if not active_months:
                raise ValueError(
                    "Tick at least one month for a special monthly cycle — an "
                    "empty selection generates an agreement with no billing "
                    "periods at all")
        elif active_months is not None:
            raise ValueError(
                "active_months only applies to a special_monthly cycle — the "
                f"other cycles ({', '.join(t for t in RECURRING_TYPES if t != 'special_monthly')}) "
                "bill in every period of the cycle")
        if recurring_type in ANCHORED_TYPES and anchor_month is None:
            raise ValueError(
                f"anchor_month is required for a {recurring_type} cycle — real "
                "billing cycles often do not start in January, and the contract start "
                "date is not a reliable proxy for the billing anchor")
        # 起始期必须落在有效期内。晚于 valid_to 会生成一份空排期 —— 协议看着
        # 是 recurring,却一行期次都没有,任何发票都认领不到、PA 那道"未链接
        # 期次"闸门永远过不去,而且到那时协议已 active、字段不可再改。
        if schedule_start_date is not None:
            if schedule_start_date > valid_to:
                raise ValueError(
                    "Schedule start date must fall inside the validity window — "
                    f"it is after valid_to ({valid_to}), which would generate a "
                    "recurring agreement with no billing periods at all")
            if schedule_start_date < valid_from:
                raise ValueError(
                    f"Schedule start date cannot be before valid_from ({valid_from})")
        # 生成不出来的排期,建档/改档时就该挡住,而不是等审批通过那一刻才炸。
        from app.services.agreement_schedule import TooManyPeriods, build_period_rows
        try:
            rows = build_period_rows(
                recurring_type=recurring_type, valid_from=valid_from,
                valid_to=valid_to, expected_invoice_day=expected_invoice_day,
                anchor_month=anchor_month, schedule_start_date=schedule_start_date,
                active_months=active_months)
        except TooManyPeriods as exc:
            raise ValueError(str(exc)) from exc
        # 同一个理由的另一半:窗口本身合法,但起始期把最后一期也挤掉了
        # (比如月结、起始期设在最后一期到票日之后的那几天)。
        if not rows:
            raise ValueError(
                "These recurrence settings generate no billing periods at all — "
                "check the schedule start date against the expected invoice day "
                "and the validity window")
    else:
        bad = [n for n, v in (
            ("recurring_type", recurring_type),
            ("expected_invoice_day", expected_invoice_day),
            ("anchor_month", anchor_month),
            ("active_months", active_months),
            ("schedule_start_date", schedule_start_date),
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
    # special_monthly 专用。normalize_active_months 在 field_validator 里跑,
    # 所以落库前就已经是升序去重的 1..12。
    active_months: list[int] | None = None
    schedule_start_date: date | None = None
    expected_amount_per_period: Decimal | None = Field(default=None, ge=0)
    tolerance_pct: Decimal | None = Field(default=None, ge=0, le=100)
    overdue_after_days: int | None = Field(default=None, ge=0, le=365)
    milestones: list[MilestoneRowIn] = Field(default_factory=list)

    @field_validator("active_months")
    @classmethod
    def _normalize_active_months(cls, v):
        return normalize_active_months(v)

    @model_validator(mode="after")
    def _validity_window_is_ordered(self):
        validate_validity_window(valid_from=self.valid_from, valid_to=self.valid_to)
        return self

    @model_validator(mode="after")
    def _recurrence_is_coherent(self):
        validate_recurrence(
            agreement_type=self.agreement_type,
            recurring_type=self.recurring_type,
            expected_invoice_day=self.expected_invoice_day,
            anchor_month=self.anchor_month,
            active_months=self.active_months,
            schedule_start_date=self.schedule_start_date,
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
    active_months: list[int] | None = None
    schedule_start_date: date | None = None
    expected_amount_per_period: Decimal | None = Field(default=None, ge=0)
    tolerance_pct: Decimal | None = Field(default=None, ge=0, le=100)
    overdue_after_days: int | None = Field(default=None, ge=0, le=365)
    # None = 不动阶段行(维持既有排期);[] = 清空阶段行。
    milestones: list[MilestoneRowIn] | None = None

    @field_validator("active_months")
    @classmethod
    def _normalize_active_months(cls, v):
        return normalize_active_months(v)


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
    active_months: list[int] | None
    schedule_start_date: date | None
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
