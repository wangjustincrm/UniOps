"""app/services/inventory_aging.py — the boundaries, which is all this module is.

No database and no fixtures on purpose: every trap here is arithmetic on dates,
and arithmetic tests that need a fixture do not get written.
"""
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple

from app.services.inventory_aging import (
    AGING_BUCKETS,
    bucket_bounds,
    bucket_for,
    days_until,
    summarise,
)

TODAY = date(2026, 8, 17)


class _Lot(NamedTuple):
    warehouse_id: str
    material_code: str
    supplier_batch: str | None
    expiry_date: date | None
    qty: Decimal


#: "not specified" — distinct from None, which is a real value here (a lot with
#: no supplier batch at all, of which there are 92). Using None as the sentinel
#: silently gave every unbatched lot its own generated batch, so a test asserting
#: they group together got 3 where it wanted 2.
_UNSET = object()

_seq = iter(range(1, 10_000))


def _lot(code: str, expiry: date | None, qty: str, batch=_UNSET) -> _Lot:
    """`batch` defaults to a unique value per call, so tests that do not care
    about grouping get one batch per lot; the ones that do pin it explicitly,
    including pinning it to None."""
    return _Lot("CANADA", code,
                f"B-{next(_seq)}" if batch is _UNSET else batch,
                expiry, Decimal(qty))


def _in(days: int) -> date:
    return TODAY + timedelta(days=days)


# ── the boundary that decides whether somebody may use it ────────────────


def test_a_lot_expiring_today_is_expired_not_nearly_expired():
    """Shelf life ran out at the start of the day. A screen calling it "usable
    for 0 more days" invites somebody to use it."""
    assert bucket_for(TODAY, TODAY) == "expired"
    assert bucket_for(_in(-1), TODAY) == "expired"
    assert bucket_for(_in(1), TODAY) == "under_30"


def test_every_threshold_at_exactly_the_day_it_names():
    """Half-open bands: 30 days out is 30_to_60, not under_30. No lot may fall
    in two buckets and none may fall in none."""
    assert bucket_for(_in(29), TODAY) == "under_30"
    assert bucket_for(_in(30), TODAY) == "30_to_60"
    assert bucket_for(_in(59), TODAY) == "30_to_60"
    assert bucket_for(_in(60), TODAY) == "60_to_90"
    assert bucket_for(_in(89), TODAY) == "60_to_90"
    assert bucket_for(_in(90), TODAY) == "90_to_180"
    assert bucket_for(_in(179), TODAY) == "90_to_180"
    assert bucket_for(_in(180), TODAY) == "over_180"
    assert bucket_for(_in(3650), TODAY) == "over_180"


def test_every_lot_lands_in_exactly_one_bucket():
    """Swept across a whole year rather than spot-checked: an off-by-one in the
    chain of thresholds shows up as a gap or an overlap, and both are invisible
    when you only test the named boundaries."""
    for offset in range(-30, 400):
        bucket = bucket_for(_in(offset), TODAY)
        assert bucket in AGING_BUCKETS, (offset, bucket)


def test_no_expiry_date_is_not_a_bucket():
    """2,053 of the 3,532 mirrored lots have no expiry, 1,640 of them packaging,
    which does not expire. Bucketing those as expired would bury the eleven
    raw-material lots that genuinely need attention this month."""
    assert bucket_for(None, TODAY) is None


def test_bucketing_does_not_depend_on_the_month_boundary():
    """A date-only comparison, with no month or year arithmetic to get wrong:
    31 December to 1 January is 1 day, not a year."""
    nye = date(2026, 12, 31)
    assert bucket_for(date(2027, 1, 1), nye) == "under_30"
    assert bucket_for(date(2027, 1, 30), nye) == "30_to_60"


# ── the summary ──────────────────────────────────────────────────────────


def test_summary_returns_every_bucket_even_when_empty():
    """A missing key on the wire reads as "no data", not as "nothing in this
    band". The screen renders five bands unconditionally."""
    summary = summarise([_lot("CR0025", _in(5), "10")], TODAY)
    assert set(summary.buckets) == set(AGING_BUCKETS)
    assert summary.buckets["over_180"] == summary.buckets["over_180"]
    assert summary.buckets["over_180"].lots == 0
    assert summary.buckets["over_180"].qty == Decimal("0")


def test_summary_counts_lots_and_quantities_per_bucket():
    summary = summarise([
        _lot("CR0025", _in(-5), "100"),
        _lot("CR0025", _in(-1), "50"),
        _lot("CR0031", _in(10), "7"),
        _lot("CR0031", _in(200), "1000"),
    ], TODAY)
    assert summary.buckets["60_to_90"].lots == 0
    assert summary.buckets["expired"].lots == 2
    assert summary.buckets["expired"].qty == Decimal("150")
    assert summary.buckets["under_30"].lots == 1
    assert summary.buckets["over_180"].qty == Decimal("1000")


def test_summary_counts_lots_without_an_expiry_separately():
    """Reported, not dropped: "excluded, and here is how many" must not look the
    same as "there are none"."""
    summary = summarise([
        _lot("CR0025", _in(-5), "100"),
        _lot("CP0133", None, "5000"),
        _lot("CP0134", None, "1"),
    ], TODAY)
    assert summary.buckets["expired"].qty == Decimal("100")
    assert summary.no_expiry_lots == 2
    assert summary.no_expiry_qty == Decimal("5001")


def test_summary_accounts_for_every_lot_it_was_given():
    """Nothing may be silently swallowed: bucketed plus excluded equals input."""
    lots = [
        _lot("A", _in(-1), "1"), _lot("B", _in(15), "1"), _lot("C", _in(45), "1"),
        _lot("D", _in(75), "1"), _lot("E", _in(120), "1"),
        _lot("F", _in(400), "1"), _lot("G", None, "1"),
    ]
    assert summarise(lots, TODAY).total_lots == len(lots)


def test_summary_of_nothing_is_five_empty_buckets_not_an_empty_dict():
    summary = summarise([], TODAY)
    assert set(summary.buckets) == set(AGING_BUCKETS)
    assert summary.total_lots == 0
    assert summary.no_expiry_qty == Decimal("0")


# ── days remaining ───────────────────────────────────────────────────────


def test_days_until_goes_negative_once_it_is_past():
    """"16 days" and "2026-09-02" answer different questions, and the first is
    the one that decides whether somebody picks up the phone. Past date reads as
    a negative rather than clamping to 0, which would make a lot three months
    gone look like one that just turned."""
    assert days_until(_in(16), TODAY) == 16
    assert days_until(TODAY, TODAY) == 0
    assert days_until(_in(-90), TODAY) == -90
    assert days_until(None, TODAY) is None


# ── bucket_bounds: the same thresholds, seen from the other side ─────────


def test_bounds_and_bucket_for_agree_on_every_day():
    """★ The property that matters: for any expiry date, the band `bucket_for`
    assigns it to must be the band whose bounds contain it.

    Filtering rows to a band and counting rows in a band are two code paths
    over one definition, and when they drift the symptom is silent — a card
    says "92 expired", the table below it shows 91, and nothing anywhere
    reports an error. The frontend originally rebuilt these windows from the
    day counts and was off by one in three of the five bands.
    """
    for offset in range(-60, 500):
        day = _in(offset)
        band = bucket_for(day, TODAY)
        assert band is not None
        lower, upper = bucket_bounds(band, TODAY)
        assert lower is None or day >= lower, (offset, band, lower)
        assert upper is None or day < upper, (offset, band, upper)


def test_bounds_tile_the_line_without_gaps_or_overlaps():
    """Each band starts exactly where the previous one ends. A gap loses lots
    silently; an overlap counts them twice."""
    ordered = [bucket_bounds(name, TODAY) for name in AGING_BUCKETS]
    assert ordered[0][0] is None, "the expired band is unbounded below"
    assert ordered[-1][1] is None, "the over-180 band is unbounded above"
    for (_, upper), (lower, _) in zip(ordered, ordered[1:]):
        assert upper == lower, f"{upper} does not meet {lower}"


def test_expired_bounds_include_a_lot_expiring_today():
    """The boundary the frontend got wrong first: `expired` is expiry <= today,
    so its exclusive upper bound is TOMORROW."""
    _lower, upper = bucket_bounds("expired", TODAY)
    assert upper == _in(1)


def test_unknown_bucket_is_rejected_rather_than_silently_unbounded():
    """Returning (None, None) for a typo would quietly match every lot."""
    import pytest

    with pytest.raises(ValueError):
        bucket_bounds("under_45", TODAY)


# ── batches, which is what the screen actually lists ─────────────────────


def test_batches_are_counted_not_just_lots():
    """The list shows one row per SUPPLIER BATCH, not per WMS lot: 3,532 lots
    in the mirror are only 877 batches, and one batch can hold 192 of them
    (CP0080, "Old Wooden Racking Pallet"). A card counting lots above a table
    listing batches is a discrepancy nobody can explain."""
    lots = [
        _lot("CR0025", _in(-5), "10", batch="SB-1"),
        _lot("CR0025", _in(-4), "20", batch="SB-1"),
        _lot("CR0025", _in(-3), "30", batch="SB-1"),
        _lot("CR0025", _in(-2), "40", batch="SB-2"),
    ]
    summary = summarise(lots, TODAY)
    assert summary.buckets["expired"].lots == 4
    assert summary.buckets["expired"].batches == 2
    assert summary.buckets["expired"].qty == Decimal("100")


def test_the_same_batch_of_two_materials_counts_twice():
    """Supplier batch numbers are not unique across materials — they are the
    supplier's, and two products can share one. The identity is the material
    AND the batch."""
    lots = [
        _lot("CR0025", _in(-1), "1", batch="SAME"),
        _lot("CR0031", _in(-1), "1", batch="SAME"),
    ]
    assert summarise(lots, TODAY).buckets["expired"].batches == 2


def test_a_batch_straddling_two_bands_is_counted_in_both():
    """42 of the 877 batches carry more than one expiry date. Such a batch
    genuinely has stock in two bands, and each band counts only the lots that
    are actually in it — folding it into one band would misstate both."""
    lots = [
        _lot("CR0025", _in(-1), "10", batch="SB-1"),    # expired
        _lot("CR0025", _in(200), "90", batch="SB-1"),   # over_180
    ]
    summary = summarise(lots, TODAY)
    assert summary.buckets["expired"].batches == 1
    assert summary.buckets["expired"].qty == Decimal("10")
    assert summary.buckets["over_180"].batches == 1
    assert summary.buckets["over_180"].qty == Decimal("90")


def test_lots_with_no_supplier_batch_group_per_material_not_all_together():
    """92 lots carry no supplier batch. Lumping them into one row across the
    whole plant would report a single meaningless "(none)" batch; per material
    is the smallest honest grouping."""
    lots = [
        _lot("CR0025", _in(-1), "1", batch=None),
        _lot("CR0025", _in(-1), "1", batch=None),
        _lot("CR0031", _in(-1), "1", batch=None),
    ]
    summary = summarise(lots, TODAY)
    assert summary.buckets["expired"].lots == 3
    assert summary.buckets["expired"].batches == 2


def test_no_expiry_batches_are_counted_too():
    lots = [
        _lot("CP0133", None, "100", batch="PALLET"),
        _lot("CP0133", None, "100", batch="PALLET"),
        _lot("CP0133", None, "100", batch="OTHER"),
    ]
    summary = summarise(lots, TODAY)
    assert summary.no_expiry_lots == 3
    assert summary.no_expiry_batches == 2


def test_the_three_soonest_bands_are_exactly_the_ninety_day_warning():
    """★ The bands and the summary's 90-day tile must describe the same window.

    `under_30 + 30_to_60 + 60_to_90` is `(today, today+90)`, which is precisely
    what the summary counts as "expiring soon". Splitting 60-90 out of the old
    60-180 band was what made them line up, and this pins it: the two are
    derived from the same thresholds, so a future change to one has to move the
    other or fail here.
    """
    soon = ("under_30", "30_to_60", "60_to_90")
    lower, _ = bucket_bounds(soon[0], TODAY)
    _, upper = bucket_bounds(soon[-1], TODAY)
    assert lower == _in(1), "the window opens the day after today, since today is expired"
    assert upper == _in(90), "and closes at exactly the 90-day horizon"

    # And the three tile without a gap between them.
    for earlier, later in zip(soon, soon[1:]):
        assert bucket_bounds(earlier, TODAY)[1] == bucket_bounds(later, TODAY)[0]


def test_the_new_band_splits_the_old_one_without_losing_anything():
    """60_to_90 and 90_to_180 together cover exactly what 60_to_180 covered, so
    nothing moved out of the shelf-life picture when the band was split."""
    lower, _ = bucket_bounds("60_to_90", TODAY)
    _, upper = bucket_bounds("90_to_180", TODAY)
    assert lower == _in(60)
    assert upper == _in(180)
