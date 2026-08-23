"""Shelf-life bucketing for WMS inventory lots.

Thresholds come from the plant: **180 / 60 / 30 days**. "Already expired" is a
FOURTH bucket rather than part of "under 30", because 90 raw-material lots
holding 32 tonnes are past date right now and a countdown that stops at zero
never shows them — the single most useful thing this screen has to say on the
day it ships.

**Buckets are half-open**: `[today, +30)` is `under_30`, `[+30, +60)` is
`30_to_60`, `[+60, +90)` is `60_to_90`, `[+90, +180)` is `90_to_180`, and `+180`
and beyond is `over_180`. So no lot can land in two buckets and none can land in
none. A lot expiring **today** is expired: its shelf life ran out at the start
of the day, and a screen that calls it "usable for 0 more days" invites somebody
to use it.

**A lot with no expiry date gets no bucket at all.** 2,053 of the mirror's 3,532
lots have none — 1,640 of them packaging, which does not expire. Calling those
"expired" would bury the eleven raw-material lots that genuinely need attention
this month under two thousand cans and cartons. They are counted separately and
reported, never silently dropped: "excluded, and here is how many" and "there
are none" must not look the same.

Pure: no database, no clock. `today` is passed in so that every lot in one
request is bucketed against the same date — bucketing per row means two lots can
land on opposite sides of midnight within a single response.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Protocol

#: Bucket keys, in the order a planner reads them: most urgent first. Callers
#: return every one of these even when empty — a missing key on the wire reads
#: as "no data" rather than "nothing in this band".
AGING_BUCKETS: tuple[str, ...] = (
    "expired", "under_30", "30_to_60", "60_to_90", "90_to_180", "over_180",
)

#: Upper bound (exclusive) of each non-expired bucket, in days from today.
#: ★ The single definition. `bucket_for` classifies from it and
#: `bucket_bounds` derives the filter windows from it, so a band added here
#: propagates to the counting, the filtering and the API's accepted values
#: without any of them being able to disagree — which is the whole reason the
#: two were unified after the frontend rebuilt these windows itself and was
#: off by one at three of five boundaries.
_THRESHOLDS: tuple[tuple[str, int], ...] = (
    ("under_30", 30),
    ("30_to_60", 60),
    # 60-90 split out on request, 2026-08-18. It also makes the bands line up
    # with the summary's 90-day warning: under_30 + 30_to_60 + 60_to_90 is
    # exactly the window that tile counts.
    ("60_to_90", 90),
    ("90_to_180", 180),
)

#: Human labels, so the API and the screen cannot drift into describing the
#: same bucket differently.
BUCKET_LABELS: dict[str, str] = {
    "expired": "Expired",
    "under_30": "Under 30 days",
    "30_to_60": "30 to 60 days",
    "60_to_90": "60 to 90 days",
    "90_to_180": "90 to 180 days",
    "over_180": "Over 180 days",
}


class HasExpiry(Protocol):
    """What `summarise` needs off a lot — satisfied by `WmsInventoryLot` as it
    is, so the endpoint hands its ORM rows straight in."""
    warehouse_id: str
    material_code: str
    supplier_batch: str | None
    expiry_date: date | None
    qty: Decimal


def bucket_for(expiry: date | None, today: date) -> str | None:
    """Which shelf-life band a lot falls in, or None if it has no expiry date.

    None is not a bucket and must not be treated as one: see this module's
    docstring on the 2,053 lots that do not expire.
    """
    if expiry is None:
        return None
    if expiry <= today:
        return "expired"
    for name, days in _THRESHOLDS:
        if expiry < today + timedelta(days=days):
            return name
    return "over_180"


@dataclass(frozen=True)
class BucketTotal:
    lots: int = 0
    qty: Decimal = Decimal("0")
    #: Distinct supplier batches. The screen lists BATCHES, not WMS lots, so
    #: this is the number that has to match the row count under the card —
    #: 3,532 lots are only 877 batches, and one supplier batch can hold 192 of
    #: them (CP0080). A card counting lots over a table listing batches is a
    #: discrepancy nobody can explain and nothing reports.
    batches: int = 0


@dataclass
class AgingSummary:
    """Per-bucket totals, plus what was left out and why it was left out."""
    buckets: dict[str, BucketTotal] = field(default_factory=dict)
    #: Lots with no expiry date — reported alongside, never bucketed.
    no_expiry_lots: int = 0
    no_expiry_qty: Decimal = Decimal("0")
    no_expiry_batches: int = 0

    @property
    def total_lots(self) -> int:
        return sum(b.lots for b in self.buckets.values()) + self.no_expiry_lots


def summarise(lots: Iterable[HasExpiry], today: date) -> AgingSummary:
    """Count lots and quantities per bucket.

    Every bucket in `AGING_BUCKETS` is present in the result even at zero, so a
    caller can render five bands without inventing the empty ones and a reader
    can tell "nothing expires in this window" from "this window was not
    computed".
    """
    counts: dict[str, list] = {name: [0, Decimal("0")] for name in AGING_BUCKETS}
    batches: dict[str, set] = {name: set() for name in AGING_BUCKETS}
    no_expiry_batches: set = set()
    summary = AgingSummary()

    for lot in lots:
        # Batch identity, matching the grouping the list endpoint uses. A NULL
        # supplier batch is its own group per material rather than being lumped
        # with every other unbatched lot in the plant.
        key = (lot.warehouse_id, lot.material_code, lot.supplier_batch)
        bucket = bucket_for(lot.expiry_date, today)
        if bucket is None:
            summary.no_expiry_lots += 1
            summary.no_expiry_qty += lot.qty
            no_expiry_batches.add(key)
            continue
        counts[bucket][0] += 1
        counts[bucket][1] += lot.qty
        # Bucketed FIRST, then grouped: a supplier batch whose lots carry
        # different expiry dates (42 of the 877 do) genuinely straddles two
        # bands, and belongs in both — counted only for the lots that are
        # actually in each.
        batches[bucket].add(key)

    summary.buckets = {
        name: BucketTotal(lots=lots_, qty=qty, batches=len(batches[name]))
        for name, (lots_, qty) in counts.items()
    }
    summary.no_expiry_batches = len(no_expiry_batches)
    return summary


def bucket_bounds(bucket: str, today: date) -> tuple[date | None, date | None]:
    """The half-open date range of a bucket, as `(from_inclusive, to_exclusive)`.

    Exists so a CALLER can filter rows to a band without restating the
    thresholds. The frontend used to rebuild these windows from the day counts
    and was off by one in three of the five bands — `expired` dropped anything
    expiring today, `30_to_60` dropped anything expiring on exactly day 30, and
    those lots then appeared in the count on the card and in no table anywhere.
    Deriving them here, from `_THRESHOLDS`, makes that class of bug impossible
    rather than merely fixed.

    `None` means unbounded on that side.
    """
    if bucket == "expired":
        # `bucket_for` calls expiry <= today expired, so the exclusive upper
        # bound is TOMORROW, not today.
        return None, today + timedelta(days=1)
    # The first non-expired band starts the day after today (today itself is
    # expired); every later band starts exactly where the previous one ended.
    start = 1
    for name, days in _THRESHOLDS:
        if name == bucket:
            return today + timedelta(days=start), today + timedelta(days=days)
        start = days
    if bucket == "over_180":
        return today + timedelta(days=start), None
    raise ValueError(f"unknown aging bucket: {bucket!r}")


def days_until(expiry: date | None, today: date) -> int | None:
    """Days of shelf life left; negative once it is past. None without a date.

    Returned alongside the date because "2026-09-02" and "16 days" answer
    different questions, and the second is the one that decides whether somebody
    picks up the phone.
    """
    return None if expiry is None else (expiry - today).days
