"""Finance follows the plant's time zone (Company Settings → Time Zone), not
the container's UTC clock (user decision 2026-11-11).

The cases are the ones UTC got wrong: a posting on the evening of the last day
of a month landed in next month's period; document numbers created after
19:00/20:00 Toronto carried tomorrow's date; the remittance "no future date"
check let tomorrow through every evening.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core import plant_time
from app.core.plant_time import fmt_datetime, plant_day_start, set_plant_tz, to_plant


@pytest.fixture(autouse=True)
def _toronto():
    set_plant_tz("America/Toronto")
    yield
    set_plant_tz("America/Toronto")


def _lines(amount="10.00"):
    return [{"line_role": "bank", "account_code": "1000", "credit": Decimal(amount), "currency": "CAD"},
            {"line_role": "accounts_payable", "account_code": "2000", "debit": Decimal(amount),
             "currency": "CAD"}]


# ── accounting period ────────────────────────────────────────────────────────

# 2026-10-31 22:30 EDT is already 2026-11-01 02:30 UTC.
OCT31_EVENING = datetime(2026, 11, 1, 2, 30, tzinfo=timezone.utc)


async def test_month_end_evening_posts_to_the_plant_month(db_session):
    from app.models.journal_voucher import JournalVoucher
    from app.models.posting import PostingEvent
    from app.services.posting import emit_event

    ev_id = await emit_event(
        db_session, source_service="finance", source_doc_type="pa", source_doc_id=uuid.uuid4(),
        source_doc_number="PA-TZ1", event_type="payment", lines=_lines(),
        occurred_at=OCT31_EVENING)
    ev = (await db_session.execute(select(PostingEvent).where(PostingEvent.id == ev_id))).scalar_one()
    assert ev.fiscal_period == "2026-10"          # UTC said 2026-11
    jv = (await db_session.execute(select(JournalVoucher).where(
        JournalVoucher.posting_event_id == ev_id))).scalar_one()
    assert jv.fiscal_period == "2026-10"
    assert jv.voucher_date == date(2026, 10, 31)  # same day as the period, not UTC's Nov 1
    assert jv.jv_number.startswith("JV-202610-")


async def test_month_end_afternoon_is_unchanged(db_session):
    """Admission half: a posting well inside the day is the same month either way."""
    from app.models.posting import PostingEvent
    from app.services.posting import emit_event

    ev_id = await emit_event(
        db_session, source_service="finance", source_doc_type="pa", source_doc_id=uuid.uuid4(),
        source_doc_number="PA-TZ2", event_type="payment", lines=_lines(),
        occurred_at=datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc))
    ev = (await db_session.execute(select(PostingEvent).where(PostingEvent.id == ev_id))).scalar_one()
    assert ev.fiscal_period == "2026-10"


async def test_opening_balance_on_the_first_stays_in_that_month(db_session):
    """gl.post_opening_balance dated the event UTC midnight; read in Toronto that is the
    previous evening — the previous month on the 1st. It is plant midnight now."""
    from app.crud import gl
    from app.models.journal_voucher import JournalVoucher
    from app.models.posting import PostingEvent

    # 1010 / 3100 are seeded chart accounts (same pair as test_gl's opening tests).
    out = await gl.post_opening_balance(db_session, as_of=date(2026, 1, 1), lines=[
        {"account_code": "1010", "debit": "100.00"}, {"account_code": "3100", "credit": "100.00"}])
    ev = (await db_session.execute(select(PostingEvent).where(
        PostingEvent.id == out["posting_event_id"]))).scalar_one()
    assert ev.fiscal_period == "2026-01"
    jv = (await db_session.execute(select(JournalVoucher).where(
        JournalVoucher.posting_event_id == ev.id))).scalar_one()
    assert jv.voucher_date == date(2026, 1, 1)


def test_period_follows_the_setting_east_of_utc():
    """The zone is a setting, not Toronto: 2026-10-31 20:00 UTC is already
    November 1st in Shanghai."""
    set_plant_tz("Asia/Shanghai")
    assert to_plant(datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)).strftime("%Y-%m") == "2026-11"


def test_plant_day_start_is_plant_midnight_across_dst():
    # 2026-11-01 is the EDT→EST change day; midnight is still EDT (UTC-4).
    assert plant_day_start(date(2026, 11, 1)).astimezone(timezone.utc) == \
        datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)
    assert plant_day_start(date(2026, 11, 2)).astimezone(timezone.utc) == \
        datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)


# ── document numbers ─────────────────────────────────────────────────────────

NOV11 = date(2026, 11, 11)


@pytest.fixture
def evening_nov11(monkeypatch):
    """21:30 Toronto on Nov 11 = 02:30 UTC Nov 12: UTC would say the 12th."""
    for mod in ("app.crud.ar", "app.crud.ap_invoice", "app.crud.payment_batch", "app.crud.vendor_credit"):
        monkeypatch.setattr(f"{mod}.plant_today", lambda: plant_time.plant_today(
            datetime(2026, 11, 12, 2, 30, tzinfo=timezone.utc)))


async def test_numbers_carry_the_plant_date_in_the_evening(db_session, evening_nov11):
    from app.crud import ap_invoice, ar, payment_batch
    from app.models.ar import ArInvoice

    assert (await ar._next_number(db_session, ArInvoice.invoice_number, "AR")) == "AR-20261111-0001"
    assert (await ap_invoice._next_number(db_session)) == "AP-20261111-0001"
    assert (await payment_batch._next_batch_number(db_session)) == "BP-20261111-0001"


async def test_batch_number_continues_a_prefix_already_used(db_session, evening_nov11):
    """The UTC era already minted numbers under today's prefix (and a cancelled
    batch may leave a gap). count()+1 would reissue BP-…-0003 — a unique
    violation; the shared allocator takes max+1."""
    from app.crud import payment_batch
    from app.models.payment_batch import PaymentBatch

    for n in ("0001", "0003"):
        db_session.add(PaymentBatch(batch_number=f"BP-20261111-{n}", batch_date=NOV11,
                                    created_by=uuid.uuid4()))
    await db_session.flush()
    assert (await payment_batch._next_batch_number(db_session)) == "BP-20261111-0004"


# ── remittance: "cannot be in the future" ────────────────────────────────────

def test_advice_date_rejects_plant_tomorrow(monkeypatch):
    from app.api.v1 import remittance

    monkeypatch.setattr(remittance, "plant_today", lambda: NOV11)
    assert remittance._advice_date(NOV11) == NOV11
    with pytest.raises(HTTPException):
        remittance._advice_date(date(2026, 11, 12))   # UTC's "today" at 21:30 Toronto


def test_advice_date_accepts_plant_today_east_of_utc():
    """A plant east of UTC is a day AHEAD of the server in the morning; the
    old UTC comparison refused its real today."""
    from app.api.v1 import remittance

    set_plant_tz("Asia/Shanghai")
    assert remittance._advice_date(plant_time.plant_today()) == plant_time.plant_today()


# ── text people read ─────────────────────────────────────────────────────────

def test_reversal_stamp_format():
    assert fmt_datetime(datetime(2026, 11, 12, 2, 30, tzinfo=timezone.utc), with_zone=True) == \
        "2026-NOV-11 21:30 EST"
