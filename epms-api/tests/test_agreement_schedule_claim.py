"""排期行认领 —— 按发票日期就近、容差、milestone 人工指定。"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import agreement_schedule as sched_crud
from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.invoice import Invoice
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio


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


async def _invoice(db, agr, vendor, user, total="1200.00", invoice_date=date(2026, 2, 3),
                  tax="0"):
    """`total` is the PRE-TAX amount and `tax` is added on top — the amount
    check compares the pre-tax figure against the period's expected amount, so
    a test that wants to exercise it has to be explicit about which is which."""
    inv = Invoice(
        internal_ref=f"INV-{uuid.uuid4().hex[:8]}",
        vendor_invoice_number=f"B{uuid.uuid4().hex[:6]}",
        vendor_id=vendor.id, vendor_name=vendor.name,
        amount=Decimal(total), tax_amount=Decimal(tax),
        total_amount=Decimal(total) + Decimal(tax),
        currency="CAD", invoice_date=invoice_date, due_date=date(2026, 3, 3),
        status="unmatched", line_items=[], uploaded_by=user.id,
    )
    db.add(inv)
    await db.flush()
    return inv


def _factory(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def test_claim_takes_the_period_nearest_the_invoice_date(test_engine):
    """The schedule runs 2026-01 / 02 / 03 with expected dates on the 5th. An
    invoice dated 2026-02-03 belongs to February — two days from that row and
    twenty-nine from January's.

    This replaces FIFO-by-sequence. FIFO was fine on a schedule that starts
    when the system does, and wrong the moment an agreement is onboarded
    mid-life: the schedule is generated from the CONTRACT's first period, so
    every invoice was offered a historical period no invoice will ever fill,
    failed tolerance against it, and fell into match_review."""
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        assert row is not None and row.sequence == 2 and row.period_label == "2026-02"
        await db.commit()


async def test_an_invoice_arriving_just_after_the_expected_day_still_lands_on_its_own_period(test_engine):
    """The case the old FIFO comment was written to protect: the bill for a
    period often arrives a day or two LATE. Comparing against expected_date
    (the period's own expected invoicing day) rather than the label's month
    keeps that invoice on its own row — 2026-02-07 is two days from the 02-05
    row and twenty-six from the 03-05 one."""
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, invoice_date=date(2026, 2, 7))
        row = await sched_crud.claim_next_period(db, agr, inv)
        assert row is not None and row.period_label == "2026-02"
        await db.commit()


async def test_a_mid_life_agreement_claims_the_current_period_not_the_first(test_engine):
    """The reported bug, in miniature: a 2025 contract entered into the system
    in 2026. FIFO proposed 2025-01 for an invoice dated 2026-08."""
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(
            db, valid_from=date(2025, 1, 1), valid_to=date(2026, 12, 31))
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, invoice_date=date(2026, 8, 4))
        row = await sched_crud.claim_next_period(db, agr, inv)
        assert row is not None and row.period_label == "2026-08"
        await db.commit()


async def _claim(db, agr, inv):
    """claim_next_period + 调用方那一半(invoice.schedule_id),与 crud.invoice
    的匹配路径一致 —— 一期多票的成员关系以 invoice.schedule_id 为准,只调前一半
    的话下一张票看不见这张。"""
    row = await sched_crud.claim_next_period(db, agr, inv)
    if row is not None:
        inv.agreement_id = agr.id
        inv.schedule_id = row.id
        await db.flush()
    return row


async def test_a_second_invoice_in_the_same_period_joins_it_instead_of_jumping_ahead(test_engine):
    """The reported bug (2026-10-03, AGR-202610-0001 GS1 Canada): a special
    monthly agreement billing in Jan/Apr/May/Oct receives two annual fees in
    October, both dated 2026-10-01. The second one used to skip the already-
    received 2026-10 row and land on 2027-01 — marking a period three months in
    the future as received."""
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(
            db, recurring_type="special_monthly", active_months=[1, 4, 5, 10],
            expected_invoice_day=7, valid_from=date(2026, 10, 1),
            valid_to=date(2027, 12, 31),
            expected_amount_per_period=None, tolerance_pct=None)
        await sched_crud.ensure_period_rows(db, agr)
        easl = await _invoice(db, agr, vendor, user, total="1575.00",
                              invoice_date=date(2026, 10, 1))
        pcert = await _invoice(db, agr, vendor, user, total="1050.00",
                               invoice_date=date(2026, 10, 1))
        r1 = await _claim(db, agr, easl)
        r2 = await _claim(db, agr, pcert)
        assert r1.period_label == r2.period_label == "2026-10"
        assert r1.id == r2.id
        assert r1.invoice_id == easl.id, "row.invoice_id stays on the first invoice"
        members = await sched_crud.period_invoices(db, r1.id)
        assert {i.id for i in members} == {easl.id, pcert.id}
        jan = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr.id,
            AgreementPaymentSchedule.period_label == "2027-01"))).scalar_one()
        assert jan.status == "pending" and jan.invoice_id is None
        await db.commit()


async def test_a_second_invoice_that_overshoots_the_period_total_goes_to_review(test_engine):
    """A fixed monthly fee has one invoice per period; with stacking allowed the
    tolerance has to be checked against the period's RUNNING total, or a
    duplicate bill would sail into an already-received month."""
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)  # expected 1200 ± 5%
        await sched_crud.ensure_period_rows(db, agr)
        first = await _invoice(db, agr, vendor, user)
        assert await _claim(db, agr, first) is not None
        dup = await _invoice(db, agr, vendor, user)
        assert await _claim(db, agr, dup) is None
        await db.commit()


async def test_a_split_bill_completes_the_period_on_its_running_total(test_engine):
    """The check is on the period total, in both directions: once the first
    half is in (placed by hand — on its own it is under tolerance), the
    second half that brings the period to its expected amount auto-claims."""
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)  # expected 1200 ± 5%
        await sched_crud.ensure_period_rows(db, agr)
        a = await _invoice(db, agr, vendor, user, total="700.00")
        assert await _claim(db, agr, a) is None
        feb = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr.id,
            AgreementPaymentSchedule.period_label == "2026-02"))).scalar_one()
        await sched_crud.claim_specific_period(db, agr, a, feb.id)
        a.schedule_id = feb.id
        await db.flush()
        b = await _invoice(db, agr, vendor, user, total="500.00")
        row = await _claim(db, agr, b)
        assert row is not None and row.id == feb.id
        await db.commit()


async def test_waived_rows_are_never_claimed(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        feb = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr.id,
            AgreementPaymentSchedule.period_label == "2026-02"))).scalar_one()
        feb.status = "waived"
        await db.flush()
        inv = await _invoice(db, agr, vendor, user)  # dated 2026-02-03
        row = await _claim(db, agr, inv)
        assert row is not None and row.period_label != "2026-02"
        await db.commit()


async def test_joining_a_confirmed_period_voids_the_confirmation(test_engine):
    """Confirmation is the recurring route's only human checkpoint before
    payment. The confirmer saw the invoices that were in the period at the
    time — a new one arriving later must not ride on that."""
    from datetime import datetime, timezone
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db, tolerance_pct=None)
        await sched_crud.ensure_period_rows(db, agr)
        first = await _invoice(db, agr, vendor, user)
        row = await _claim(db, agr, first)
        row.accepted_at = datetime.now(timezone.utc)
        row.accepted_by = user.id
        await db.flush()
        second = await _invoice(db, agr, vendor, user)
        again = await _claim(db, agr, second)
        assert again.id == row.id
        assert again.accepted_at is None and again.accepted_by is None
        await db.commit()


async def test_overdue_rows_are_still_claimable(test_engine):
    # 逾期只是"还没来票"的标记,票来了照样该认领 —— 否则缺票告警反而堵死了收票。
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        first = (await db.execute(
            select(AgreementPaymentSchedule)
            .where(AgreementPaymentSchedule.agreement_id == agr.id)
            .order_by(AgreementPaymentSchedule.sequence).limit(1))).scalar_one()
        first.status = "overdue"
        await db.flush()
        # Dated into January so the nearest row IS the overdue one — the point
        # of this test is that `overdue` does not exclude a row from claiming,
        # not which row is nearest.
        inv = await _invoice(db, agr, vendor, user, invoice_date=date(2026, 1, 6))
        row = await sched_crud.claim_next_period(db, agr, inv)
        assert row.sequence == 1 and row.status == "received"
        await db.commit()


async def test_amount_inside_tolerance_claims_the_row(test_engine):
    # expected 1200, tolerance 5% → 允许区间 [1140, 1260]
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, total="1150.00")
        assert await sched_crud.claim_next_period(db, agr, inv) is not None
        await db.commit()


async def test_amount_at_lower_tolerance_boundary_claims_the_row(test_engine):
    # expected 1200, tolerance 5% → lower bound 1140.00 is INSIDE (inclusive).
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, total="1140.00")
        assert await sched_crud.claim_next_period(db, agr, inv) is not None
        await db.commit()


async def test_amount_at_upper_tolerance_boundary_claims_the_row(test_engine):
    # expected 1200, tolerance 5% → upper bound 1260.00 is INSIDE (inclusive).
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, total="1260.00")
        assert await sched_crud.claim_next_period(db, agr, inv) is not None
        await db.commit()


async def test_amount_outside_tolerance_claims_nothing(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, total="1400.00")
        assert await sched_crud.claim_next_period(db, agr, inv) is None
        rows = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr.id))).scalars().all()
        assert all(r.status == "pending" for r in rows)
        await db.commit()


async def test_null_expected_amount_skips_the_amount_check(test_engine):
    # 决策 3:每期金额选填 → 不填就不做金额校验,任何金额都认领。
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db, expected_amount_per_period=None,
                                        tolerance_pct=None)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, total="99999.00")
        assert await sched_crud.claim_next_period(db, agr, inv) is not None
        await db.commit()


async def test_no_candidate_rows_returns_none(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        for r in (await db.execute(select(AgreementPaymentSchedule).where(
                AgreementPaymentSchedule.agreement_id == agr.id))).scalars().all():
            r.status = "waived"
        await db.flush()
        inv = await _invoice(db, agr, vendor, user)
        assert await sched_crud.claim_next_period(db, agr, inv) is None
        await db.commit()


async def test_claimed_row_records_the_invoice_and_flips_to_received(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user)
        row = await sched_crud.claim_next_period(db, agr, inv)
        assert row.status == "received" and row.invoice_id == inv.id
        await db.commit()


async def test_milestone_claim_rejects_a_row_from_another_agreement(test_engine):
    async with _factory(test_engine)() as db:
        agr_a, vendor, user = await _seed(db, agreement_type="milestone",
                                          recurring_type=None, expected_invoice_day=None,
                                          expected_amount_per_period=None, tolerance_pct=None)
        agr_b, _, _ = await _seed(db, agreement_type="milestone", recurring_type=None,
                                  expected_invoice_day=None,
                                  expected_amount_per_period=None, tolerance_pct=None)
        foreign = AgreementPaymentSchedule(
            agreement_id=agr_b.id, schedule_type="milestone", sequence=1,
            milestone_name="Deposit", status="pending")
        db.add(foreign)
        await db.flush()
        inv = await _invoice(db, agr_a, vendor, user)
        with pytest.raises(ValueError, match="does not belong"):
            await sched_crud.claim_milestone(db, agr_a, inv, foreign.id)
        await db.commit()


async def test_already_claimed_milestone_cannot_be_claimed_again(test_engine):
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db, agreement_type="milestone", recurring_type=None,
                                        expected_invoice_day=None,
                                        expected_amount_per_period=None, tolerance_pct=None)
        row = AgreementPaymentSchedule(
            agreement_id=agr.id, schedule_type="milestone", sequence=1,
            milestone_name="Deposit on signing", status="pending")
        db.add(row)
        await db.flush()
        inv1 = await _invoice(db, agr, vendor, user)
        await sched_crud.claim_milestone(db, agr, inv1, row.id)
        inv2 = await _invoice(db, agr, vendor, user)
        with pytest.raises(ValueError, match="already has an invoice"):
            await sched_crud.claim_milestone(db, agr, inv2, row.id)
        await db.commit()


# ── Assigning a billing period AFTER the match ───────────────────────────────
# The escape hatch existed only at match time. An invoice the automatic claim
# could not place lands in match_review with no period, is approved there
# without gaining one, and then can never be paid — /match refuses to run twice
# and the agreement's tolerance is no longer editable once it is active. This
# is the way out.

async def test_assign_billing_period_links_a_matched_invoice(test_engine):
    from app.crud import invoice as invoice_crud
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        # Out of tolerance on purpose (expected 1200 ± 5%): exactly the state
        # the user reported — matched, with no period, and unpayable.
        inv = await _invoice(db, agr, vendor, user, total="2237.40")
        assert await sched_crud.claim_next_period(db, agr, inv) is None
        inv.agreement_id = agr.id
        inv.status = "matched"
        await db.flush()

        target = (await db.execute(
            select(AgreementPaymentSchedule)
            .where(AgreementPaymentSchedule.agreement_id == agr.id)
            .order_by(AgreementPaymentSchedule.sequence))).scalars().all()[1]

        await invoice_crud.assign_billing_period(db, inv, target.id)
        assert inv.schedule_id == target.id
        refreshed = (await db.execute(
            select(AgreementPaymentSchedule).where(
                AgreementPaymentSchedule.id == target.id))).scalar_one()
        assert refreshed.status == "received" and refreshed.invoice_id == inv.id
        await db.commit()


async def test_assign_billing_period_moves_an_invoice_out_of_a_wrong_period(test_engine):
    """The way back for invoices auto-claimed into a future period before one
    period could hold several invoices (AGR-202610-0001: INV-2026-0598 sat on
    2027-01). The vacated period drops back to pending and its open
    confirmation task closes; the target gains the invoice."""
    from app.crud import invoice as invoice_crud
    from app.models.task import Task
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db, tolerance_pct=None)
        await sched_crud.ensure_period_rows(db, agr)
        rows = (await db.execute(
            select(AgreementPaymentSchedule)
            .where(AgreementPaymentSchedule.agreement_id == agr.id)
            .order_by(AgreementPaymentSchedule.sequence))).scalars().all()
        jan, feb, mar = rows
        keep = await _invoice(db, agr, vendor, user, invoice_date=date(2026, 2, 3))
        await _claim(db, agr, keep)
        stray = await _invoice(db, agr, vendor, user, invoice_date=date(2026, 2, 3))
        stray.agreement_id = agr.id
        await sched_crud.claim_specific_period(db, agr, stray, mar.id)
        stray.schedule_id = mar.id
        stray.status = "matched"
        await sched_crud.create_confirm_task(db, agr, mar)
        await db.flush()

        await invoice_crud.assign_billing_period(db, stray, feb.id)
        assert stray.schedule_id == feb.id
        await db.refresh(mar)
        assert mar.status == "pending" and mar.invoice_id is None
        open_mar = (await db.execute(select(Task).where(
            Task.document_id == agr.id, Task.type == "confirm_period",
            Task.document_number == f"{agr.number} · 2026-03",
            Task.is_completed.is_(False)))).scalars().all()
        assert open_mar == []
        assert {i.id for i in await sched_crud.period_invoices(db, feb.id)} == {keep.id, stray.id}
        await db.commit()


async def test_assign_billing_period_refuses_to_move_an_invoice_already_on_a_pa(test_engine):
    """Once a payment has been raised the period it pays for is fixed."""
    from app.crud import invoice as invoice_crud
    from app.models.pa import PaymentApplication
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user)
        row = await _claim(db, agr, inv)
        db.add(PaymentApplication(
            id=uuid.uuid4(), pa_number=f"PA-T-{uuid.uuid4().hex[:6]}", title="t",
            vendor_id=vendor.id, vendor_name=vendor.name, invoice_ids=[str(inv.id)],
            gr_ids=[], subtotal=Decimal("1200"), payment_amount=Decimal("1200"),
            created_by=user.id, agreement_id=agr.id))
        await db.flush()
        other = (await db.execute(
            select(AgreementPaymentSchedule)
            .where(AgreementPaymentSchedule.agreement_id == agr.id,
                   AgreementPaymentSchedule.id != row.id)
            .order_by(AgreementPaymentSchedule.sequence))).scalars().first()
        with pytest.raises(ValueError, match="payment application"):
            await invoice_crud.assign_billing_period(db, inv, other.id)
        assert inv.schedule_id == row.id, "a refused move leaves the link untouched"
        await db.rollback()


async def test_assign_billing_period_can_join_a_period_that_already_has_an_invoice(test_engine):
    from app.crud import invoice as invoice_crud
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)
        await sched_crud.ensure_period_rows(db, agr)
        first = await _invoice(db, agr, vendor, user)
        taken = await _claim(db, agr, first)
        assert taken is not None

        second = await _invoice(db, agr, vendor, user, total="2237.40")
        second.agreement_id = agr.id
        second.status = "matched"
        await db.flush()
        await invoice_crud.assign_billing_period(db, second, taken.id)
        assert second.schedule_id == taken.id
        assert taken.invoice_id == first.id
        await db.commit()


async def test_releasing_one_of_two_invoices_keeps_the_period_received(test_engine):
    from datetime import datetime, timezone
    from app.crud.invoice import _release_agreement_evidence
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db, tolerance_pct=None)
        await sched_crud.ensure_period_rows(db, agr)
        a = await _invoice(db, agr, vendor, user)
        b = await _invoice(db, agr, vendor, user)
        row = await _claim(db, agr, a)
        await _claim(db, agr, b)
        row.accepted_at = datetime.now(timezone.utc)
        row.accepted_by = user.id
        await db.flush()

        await _release_agreement_evidence(db, a)
        await db.flush()
        assert row.status == "received"
        assert row.invoice_id == b.id, "the row's pointer moves to the invoice still in it"
        assert row.accepted_at is not None, "the remaining invoice was confirmed — keep it"

        await _release_agreement_evidence(db, b)
        await db.flush()
        assert row.status == "pending" and row.invoice_id is None and row.accepted_at is None
        await db.commit()


async def test_a_second_invoice_does_not_open_a_second_confirm_task(test_engine):
    from app.models.task import Task
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db, tolerance_pct=None, owner_id=None)
        await sched_crud.ensure_period_rows(db, agr)
        for _ in range(2):
            inv = await _invoice(db, agr, vendor, user)
            row = await _claim(db, agr, inv)
            await sched_crud.create_confirm_task(db, agr, row)
        tasks = (await db.execute(select(Task).where(
            Task.document_id == agr.id, Task.type == "confirm_period",
            Task.is_completed.is_(False)))).scalars().all()
        assert len(tasks) == 1
        await db.commit()


# ── The amount check's two rules (user's ruling, 2026-08-13) ────────────────

async def test_tax_does_not_push_a_matching_invoice_out_of_tolerance(test_engine):
    """The reported bug. expected_amount_per_period is the CONTRACT price, and
    tax is added on top by law — comparing a tax-inclusive total against a net
    figure means a $1,200 monthly contract billed with 13% HST is 13% "over
    tolerance" every single month, and every invoice on such an agreement falls
    into match_review."""
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db)  # expected 1200, tolerance 5%
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, total="1200.00", tax="156.00")
        assert inv.total_amount == Decimal("1356.00")
        row = await sched_crud.claim_next_period(db, agr, inv)
        assert row is not None, "claimed on the pre-tax amount, not the tax-inclusive total"
        await db.commit()


async def test_a_blank_tolerance_accepts_any_amount(test_engine):
    """Blank means "don't check", not "check exactly". The previous reading —
    `tolerance_pct or 0` — turned an unfilled field into the strictest setting
    the system has, which is the opposite of what leaving it empty suggests."""
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db, tolerance_pct=None)
        await sched_crud.ensure_period_rows(db, agr)
        inv = await _invoice(db, agr, vendor, user, total="9999.99")
        assert await sched_crud.claim_next_period(db, agr, inv) is not None
        await db.commit()


async def test_an_explicit_zero_tolerance_still_means_exact(test_engine):
    """…and 0 keeps its meaning, so the strict setting is still reachable —
    deliberately, by typing it."""
    async with _factory(test_engine)() as db:
        agr, vendor, user = await _seed(db, tolerance_pct=Decimal("0.00"))
        await sched_crud.ensure_period_rows(db, agr)
        off_by_a_cent = await _invoice(db, agr, vendor, user, total="1200.01")
        assert await sched_crud.claim_next_period(db, agr, off_by_a_cent) is None
        exact = await _invoice(db, agr, vendor, user, total="1200.00")
        assert await sched_crud.claim_next_period(db, agr, exact) is not None
        await db.commit()
