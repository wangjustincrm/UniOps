"""AgreementCreate 的周期字段校验 + milestone 行录入。纯 schema 层,不碰 DB。"""
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.agreement import AgreementCreate, MilestoneRowIn

VENDOR = "11111111-1111-1111-1111-111111111111"


def _base(**over):
    body = dict(
        title="Bell circuit", agreement_type="recurring", vendor_id=VENDOR,
        valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
    )
    body.update(over)
    return body


def test_recurring_requires_recurring_type_and_invoice_day():
    with pytest.raises(ValidationError, match="recurring_type"):
        AgreementCreate(**_base())


def test_recurring_monthly_accepts_a_day_of_month():
    a = AgreementCreate(**_base(recurring_type="monthly", expected_invoice_day=5))
    assert a.expected_invoice_day == 5


def test_recurring_weekly_rejects_a_day_above_7():
    with pytest.raises(ValidationError, match=r"1\.\.7"):
        AgreementCreate(**_base(recurring_type="weekly", expected_invoice_day=15))


def test_quarterly_requires_anchor_month():
    with pytest.raises(ValidationError, match="anchor_month"):
        AgreementCreate(**_base(recurring_type="quarterly", expected_invoice_day=10))


def test_quarterly_with_anchor_month_is_accepted():
    a = AgreementCreate(**_base(
        recurring_type="quarterly", expected_invoice_day=10, anchor_month=2))
    assert a.anchor_month == 2


def test_non_recurring_must_not_carry_recurrence_fields():
    with pytest.raises(ValidationError, match="only apply to a recurring agreement"):
        AgreementCreate(**_base(agreement_type="house_account",
                                recurring_type="monthly", expected_invoice_day=5))


def test_a_window_over_the_row_cap_is_rejected_at_create_time():
    # 生成不出来的排期不该等到审批通过那一刻才炸 —— 建档时就挡住。
    with pytest.raises(ValidationError, match="500"):
        AgreementCreate(**_base(
            recurring_type="weekly", expected_invoice_day=1,
            valid_to=date(2040, 1, 1)))


def test_milestones_only_allowed_on_milestone_agreements():
    with pytest.raises(ValidationError, match="milestone agreement"):
        AgreementCreate(**_base(
            recurring_type="monthly", expected_invoice_day=5,
            milestones=[MilestoneRowIn(milestone_name="Deposit")]))


def test_milestone_amount_pct_requires_not_to_exceed():
    with pytest.raises(ValidationError, match="not_to_exceed"):
        AgreementCreate(**_base(
            agreement_type="milestone",
            milestones=[MilestoneRowIn(milestone_name="Deposit",
                                       amount_pct=Decimal("30"))]))


def test_milestone_with_an_absolute_amount_needs_no_ceiling():
    a = AgreementCreate(**_base(
        agreement_type="milestone",
        milestones=[MilestoneRowIn(
            milestone_name="Deposit on signing",
            expected_timing="Within 1 week after contract signing",
            expected_amount=Decimal("15000"))]))
    assert a.milestones[0].expected_timing == "Within 1 week after contract signing"


def test_special_monthly_requires_at_least_one_month():
    with pytest.raises(ValidationError, match="Tick at least one month"):
        AgreementCreate(**_base(recurring_type="special_monthly",
                                expected_invoice_day=15))


def test_special_monthly_rejects_an_empty_selection():
    with pytest.raises(ValidationError, match="Tick at least one month"):
        AgreementCreate(**_base(recurring_type="special_monthly",
                                expected_invoice_day=15, active_months=[]))


def test_special_monthly_normalizes_the_month_list():
    a = AgreementCreate(**_base(
        recurring_type="special_monthly", expected_invoice_day=15,
        active_months=[11, 5, 5, 6]))
    assert a.active_months == [5, 6, 11]


def test_special_monthly_rejects_a_month_outside_1_to_12():
    with pytest.raises(ValidationError, match=r"1\.\.12"):
        AgreementCreate(**_base(recurring_type="special_monthly",
                                expected_invoice_day=15, active_months=[0, 13]))


def test_active_months_is_rejected_on_the_other_cycles():
    with pytest.raises(ValidationError, match="only applies to a special_monthly"):
        AgreementCreate(**_base(recurring_type="monthly", expected_invoice_day=5,
                                active_months=[5, 6]))


def test_non_recurring_must_not_carry_active_months():
    with pytest.raises(ValidationError, match="only apply to a recurring agreement"):
        AgreementCreate(**_base(agreement_type="house_account",
                                active_months=[5, 6]))
