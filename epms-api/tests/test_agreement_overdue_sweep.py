"""缺票逾期扫描。"""
import asyncio
import copy
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.db.session as session_module
from app.crud import user as user_crud
from app.crud.config import get_or_create as get_config
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.config import CompanyConfig
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest
from app.tasks import agreement_overdue as agreement_overdue_module
from app.tasks.agreement_overdue import sweep_overdue_periods

pytestmark = pytest.mark.asyncio

# _seed / _factory 从 tests/test_agreement_schedule_claim.py 复制过来


async def _seed(db, **over):
    """种子必须建在同一个 async session 里 —— conftest 的 seeded_vendor 走的是
    另一条未提交的 psycopg2 连接,async engine 看不见(FK 违约)。"""
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Bell", category="supplier",
                    contact_name="AP", contact_email="ap@bell.example")
    db.add(vendor)
    user = await user_crud.create(db, RegisterRequest(
        email=f"agr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="T", role="procurement_officer"))
    await db.flush()
    kw = dict(
        number=f"AGR-202608-T{uuid.uuid4().hex[:11]}", title="Bell", agreement_type="recurring",
        vendor_id=vendor.id, vendor_name=vendor.name,
        valid_from=date(2026, 1, 1), valid_to=date(2026, 3, 31),
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1200.00"), tolerance_pct=Decimal("5.00"),
        overdue_after_days=7, status="active", created_by=user.id,
    )
    kw.update(over)
    agr = PurchaseAgreement(**kw)
    db.add(agr)
    await db.flush()
    return agr, vendor, user


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def _row(db, agr, *, days_ago: int, grace: int = 7,
               status: str = "pending", schedule_type: str = "period"):
    row = AgreementPaymentSchedule(
        agreement_id=agr.id, schedule_type=schedule_type, sequence=1,
        expected_date=date.today() - timedelta(days=days_ago) if schedule_type == "period" else None,
        expected_timing=None if schedule_type == "period" else "After signing",
        milestone_name=None if schedule_type == "period" else "Deposit",
        period_label="2026-01" if schedule_type == "period" else None,
        overdue_after_days=grace, status=status,
    )
    db.add(row)
    await db.flush()
    return row


async def test_a_row_past_expected_date_plus_grace_becomes_overdue(test_engine):
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db)
        row = await _row(db, agr, days_ago=8, grace=7)      # 到票日 + 7 < 今天
        flipped = await sweep_overdue_periods(db)
        await db.commit()
        # Scoped to THIS agreement: sweep_overdue_periods is global, so any
        # past-due row another test left pending in the shared database rides
        # along in the return value — and does, now that claim_next_period
        # picks by invoice date and leaves earlier periods unclaimed. Filtering
        # keeps the assertion exact about the row under test rather than about
        # whatever else happens to be in the database.
        assert [r.id for r in flipped if r.agreement_id == agr.id] == [row.id]
        row_id = row.id

    # _factory uses expire_on_commit=False, so `row.status == "overdue"` would
    # pass even if the UPDATE never reached the database — re-SELECT through a
    # fresh session/identity map to prove it actually persisted.
    async with _factory(test_engine)() as verify_db:
        persisted = await verify_db.get(AgreementPaymentSchedule, row_id)
        assert persisted.status == "overdue"


async def test_the_boundary_day_itself_is_not_overdue(test_engine):
    # 到票日 + grace == 今天 → **不算**逾期。宽限期的最后一天仍在宽限内。
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db)
        row = await _row(db, agr, days_ago=7, grace=7)
        assert await sweep_overdue_periods(db) == []
        assert row.status == "pending"
        await db.commit()


async def test_received_rows_are_never_swept(test_engine):
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db)
        row = await _row(db, agr, days_ago=90, status="received")
        assert await sweep_overdue_periods(db) == []
        assert row.status == "received"
        await db.commit()


async def test_waived_rows_are_never_swept(test_engine):
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db)
        row = await _row(db, agr, days_ago=90, status="waived")
        assert await sweep_overdue_periods(db) == []
        assert row.status == "waived"
        await db.commit()


async def test_milestone_rows_are_never_swept(test_engine):
    """milestone 行没有 expected_date,本就进不了扫描。但实现里的查询必须**显式**
    带 schedule_type='period' —— Phase 1C 若给阶段加了可选日期,靠
    `expected_date IS NULL` 的隐式过滤会静默失效,阶段会被当成逾期期次刷掉。"""
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db, agreement_type="milestone", recurring_type=None,
                                expected_invoice_day=None,
                                expected_amount_per_period=None, tolerance_pct=None)
        row = await _row(db, agr, days_ago=90, schedule_type="milestone")
        assert await sweep_overdue_periods(db) == []
        assert row.status == "pending"
        await db.commit()


async def test_sweep_uses_the_default_grace_when_the_row_has_none(test_engine):
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db)
        row = await _row(db, agr, days_ago=8)
        row.overdue_after_days = None       # 回落默认 7 天
        await db.flush()
        assert [r.id for r in await sweep_overdue_periods(db)] == [row.id]
        await db.commit()


# ── Whole-branch review Item 6: a cancelled/closed agreement's rows must ───
# never flip — the old query filtered ONLY on the schedule row, with no join
# back to the agreement, so a cancelled agreement's still-"pending" rows kept
# flipping to "overdue" and (toggle defaults ON) emailing its owner every
# sweep for the rest of its validity window.

async def test_a_cancelled_agreements_row_is_never_swept(test_engine):
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db, status="cancelled")
        row = await _row(db, agr, days_ago=90, grace=7)
        assert await sweep_overdue_periods(db) == []
        assert row.status == "pending"
        await db.commit()


async def test_a_closed_agreements_row_is_never_swept(test_engine):
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db, status="closed")
        row = await _row(db, agr, days_ago=90, grace=7)
        assert await sweep_overdue_periods(db) == []
        assert row.status == "pending"
        await db.commit()


async def test_an_expired_agreements_row_is_still_swept(test_engine):
    """"expired" is deliberately KEPT admissible — the final bill legitimately
    still arrives after valid_to, so its trailing periods must still be
    tracked. This is the boundary case that proves the fix targets
    cancelled/closed specifically, not "any non-active status"."""
    async with _factory(test_engine)() as db:
        agr, _, _ = await _seed(db, status="expired")
        row = await _row(db, agr, days_ago=90, grace=7)
        flipped = await sweep_overdue_periods(db)
        # Scoped to THIS agreement: sweep_overdue_periods is global, so any
        # past-due row another test left pending in the shared database rides
        # along in the return value — and does, now that claim_next_period
        # picks by invoice date and leaves earlier periods unclaimed. Filtering
        # keeps the assertion exact about the row under test rather than about
        # whatever else happens to be in the database.
        assert [r.id for r in flipped if r.agreement_id == agr.id] == [row.id]
        await db.commit()


# ── agreement_overdue_loop() — the driver, not just the payload ────────────────
#
# Regression: the original loop re-derived _seconds_until_next_run() AFTER
# sleeping. On a real clock, that second call sees "now" has reached (or just
# passed) the target and rolls forward a full day (~86400s) — which is an
# exact multiple of the 900s recheck cap, so `> 60` stayed true forever and
# the run that was just waited for never fired. Both tests below drive the
# loop deterministically (no real sleeping) by monkeypatching _load_schedule,
# _seconds_until_next_run and asyncio.sleep.

async def test_loop_fires_the_run_when_scheduled_time_arrives(monkeypatch):
    calls: list[bool] = []

    async def _fake_load_schedule():
        return (8, 0)

    def _fake_seconds_until_next_run(hour, minute):
        return 10  # well under the recheck cap -> this iteration sleeps then fires

    async def _fake_sleep(seconds):
        return None

    async def _fake_run():
        calls.append(True)
        raise asyncio.CancelledError  # deterministically end the `while True`

    monkeypatch.setattr(agreement_overdue_module, "_load_schedule", _fake_load_schedule)
    monkeypatch.setattr(agreement_overdue_module, "_seconds_until_next_run", _fake_seconds_until_next_run)
    monkeypatch.setattr(agreement_overdue_module.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(agreement_overdue_module, "run_agreement_overdue", _fake_run)

    with pytest.raises(asyncio.CancelledError):
        await agreement_overdue_module.agreement_overdue_loop()

    assert calls == [True]


async def test_loop_does_not_re_derive_wait_after_waking_from_sleep(monkeypatch):
    calls: list[bool] = []
    seen: list[bool] = []

    async def _fake_load_schedule():
        return (8, 0)

    def _fake_seconds_until_next_run(hour, minute):
        seen.append(True)
        # First call: a tiny remaining wait -> loop sleeps then should fire
        # without asking again. A second call simulates the bug's view of the
        # clock right after waking: the target has rolled over to tomorrow.
        return 10 if len(seen) == 1 else 86400

    async def _fake_sleep(seconds):
        return None

    async def _fake_run():
        calls.append(True)
        raise asyncio.CancelledError

    monkeypatch.setattr(agreement_overdue_module, "_load_schedule", _fake_load_schedule)
    monkeypatch.setattr(agreement_overdue_module, "_seconds_until_next_run", _fake_seconds_until_next_run)
    monkeypatch.setattr(agreement_overdue_module.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(agreement_overdue_module, "run_agreement_overdue", _fake_run)

    with pytest.raises(asyncio.CancelledError):
        await agreement_overdue_module.agreement_overdue_loop()

    assert calls == [True]
    assert len(seen) == 1  # never recomputed the wait after sleeping


# ── run_agreement_overdue() — toggle gates the email, never the sweep ──────────
#
# Uses session_module.AsyncSessionLocal() directly (not the _factory helper):
# run_agreement_overdue() opens its OWN session via that module attribute, so
# seed data must be committed through the same route to be visible to it —
# same idiom as tests/test_daily_followup.py.

async def _merge_notif_settings(db, **kv) -> None:
    cfg = await get_config(db)
    ns = {**(cfg.notification_settings or {})}
    for key, value in kv.items():
        if value is None:
            ns.pop(key, None)
        else:
            ns[key] = value
    await db.execute(sa_update(CompanyConfig).values(notification_settings=ns))
    await db.commit()


@pytest.fixture(autouse=True)
async def _restore_notification_settings():
    async with session_module.AsyncSessionLocal() as db:
        cfg = await get_config(db)
        snapshot = copy.deepcopy(cfg.notification_settings)
        await db.commit()
    yield
    async with session_module.AsyncSessionLocal() as db:
        await db.execute(
            sa_update(CompanyConfig).values(notification_settings=copy.deepcopy(snapshot))
        )
        await db.commit()


async def test_toggle_off_flips_status_but_sends_no_email(monkeypatch):
    sent_emails: list[dict] = []
    sent_alerts: list[dict] = []

    async def _fake_send_email(to, subject, html, **kwargs):
        sent_emails.append({"to": to, "subject": subject, "html": html})

    async def _fake_send_admin_alert(subject, body, db=None):
        sent_alerts.append({"subject": subject, "body": body})

    monkeypatch.setattr(agreement_overdue_module, "send_email", _fake_send_email)
    monkeypatch.setattr(agreement_overdue_module, "send_admin_alert", _fake_send_admin_alert)

    async with session_module.AsyncSessionLocal() as db:
        await _merge_notif_settings(db, agreement_overdue_enabled=False)
        agr, _, user = await _seed(db)
        agr.owner_id = user.id
        row = await _row(db, agr, days_ago=8, grace=7)
        await db.commit()
        row_id = row.id

    await agreement_overdue_module.run_agreement_overdue()

    assert sent_emails == []
    assert sent_alerts == []

    async with session_module.AsyncSessionLocal() as verify_db:
        persisted = await verify_db.get(AgreementPaymentSchedule, row_id)
        assert persisted.status == "overdue"


async def test_toggle_on_emails_the_agreements_owner(monkeypatch):
    sent_emails: list[dict] = []

    async def _fake_send_email(to, subject, html, **kwargs):
        sent_emails.append({"to": to, "subject": subject, "html": html})

    monkeypatch.setattr(agreement_overdue_module, "send_email", _fake_send_email)

    async with session_module.AsyncSessionLocal() as db:
        await _merge_notif_settings(db, agreement_overdue_enabled=True)
        agr, _, user = await _seed(db)
        agr.owner_id = user.id
        row = await _row(db, agr, days_ago=8, grace=7)
        await db.commit()
        owner_email = user.email
        agr_number = agr.number
        period_label = row.period_label
        row_id = row.id

    await agreement_overdue_module.run_agreement_overdue()

    mine = [e for e in sent_emails if e["to"] == owner_email]
    assert len(mine) == 1
    assert agr_number in mine[0]["html"]
    assert period_label in mine[0]["html"]

    async with session_module.AsyncSessionLocal() as verify_db:
        persisted = await verify_db.get(AgreementPaymentSchedule, row_id)
        assert persisted.status == "overdue"


async def test_toggle_on_falls_back_to_admin_alert_when_agreement_has_no_owner(monkeypatch):
    sent_alerts: list[dict] = []

    async def _fake_send_admin_alert(subject, body, db=None):
        sent_alerts.append({"subject": subject, "body": body})

    monkeypatch.setattr(agreement_overdue_module, "send_admin_alert", _fake_send_admin_alert)

    async with session_module.AsyncSessionLocal() as db:
        await _merge_notif_settings(db, agreement_overdue_enabled=True)
        agr, _, _ = await _seed(db)   # owner_id left unset
        row = await _row(db, agr, days_ago=8, grace=7)
        await db.commit()
        agr_number = agr.number
        period_label = row.period_label

    await agreement_overdue_module.run_agreement_overdue()

    mine = [a for a in sent_alerts if agr_number in a["body"]]
    assert len(mine) == 1
    assert period_label in mine[0]["body"]
