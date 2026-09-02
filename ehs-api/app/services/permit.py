"""Permit rules: what a safe atmosphere is, and when a permit stops being one.

Both of the things in this file are decisions the system must make rather than
accept. They are pure functions for the same reason the statutory clocks are:
these are the calculations that must not be wrong, and a pure function is one a
test can put a standard's own numbers into.

The gas limits are the standard's, not the plant's, and are deliberately not in
`ehs_config`. An employer may add precautions; an employer may not decide that
18.9% oxygen is fine today. Anything the plant genuinely wants to tune goes on
the permit type instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

# ── Atmospheric limits, with the authority for each ─────────────────────────
# CSA Z1006 / Ontario Reg 632 (confined spaces). An atmosphere outside any of
# these is not enterable without respiratory protection, and this module does
# not model "enter anyway with an SCBA" — that is a different plan, not a
# looser threshold.

OXYGEN_MIN_PCT = Decimal("19.5")
OXYGEN_MAX_PCT = Decimal("23.0")
OXYGEN_AUTHORITY = "O. Reg. 632/05 — oxygen 19.5%–23.0%"

LEL_MAX_PCT = Decimal("10.0")
LEL_AUTHORITY = "O. Reg. 632/05 — combustible gas below 10% LEL"

# Occupational exposure limits, Reg 833.
H2S_MAX_PPM = Decimal("10.0")
CO_MAX_PPM = Decimal("25.0")
TOXIC_AUTHORITY = "O. Reg. 833 — occupational exposure limits"

# A gas test goes stale. Reg 632 requires testing "as often as necessary"; the
# plant's working rule is that a reading older than this does not authorise
# entry, and the permit says so rather than leaving it to judgement.
GAS_READING_VALID_MINUTES = 120


@dataclass(frozen=True)
class GasVerdict:
    acceptable: bool
    failed: list[str]


def assess_gas(
    *, oxygen_pct: Decimal | None, lel_pct: Decimal | None,
    h2s_ppm: Decimal | None, co_ppm: Decimal | None,
    require_oxygen: bool = True,
) -> GasVerdict:
    """Whether this atmosphere may be entered, and what failed if not.

    A missing oxygen reading fails rather than passes. "We did not measure it"
    and "it was fine" must not produce the same verdict on a confined space —
    that substitution is the recurring feature of confined-space fatalities.
    The other gases are optional because not every space warrants them, and a
    hot-work permit outdoors legitimately has none.
    """
    failed: list[str] = []

    if oxygen_pct is None:
        if require_oxygen:
            failed.append("oxygen was not measured")
    elif oxygen_pct < OXYGEN_MIN_PCT:
        failed.append(f"oxygen {oxygen_pct}% is below {OXYGEN_MIN_PCT}%")
    elif oxygen_pct > OXYGEN_MAX_PCT:
        failed.append(f"oxygen {oxygen_pct}% is above {OXYGEN_MAX_PCT}% — enriched")

    if lel_pct is not None and lel_pct >= LEL_MAX_PCT:
        failed.append(f"combustible gas {lel_pct}% LEL is at or above {LEL_MAX_PCT}%")
    if h2s_ppm is not None and h2s_ppm > H2S_MAX_PPM:
        failed.append(f"hydrogen sulphide {h2s_ppm} ppm is above {H2S_MAX_PPM} ppm")
    if co_ppm is not None and co_ppm > CO_MAX_PPM:
        failed.append(f"carbon monoxide {co_ppm} ppm is above {CO_MAX_PPM} ppm")

    return GasVerdict(acceptable=not failed, failed=failed)


def reading_is_fresh(taken_at: datetime, now: datetime | None = None) -> bool:
    """Whether a gas reading is recent enough to authorise entry."""
    now = now or datetime.now(timezone.utc)
    return (now - taken_at) <= timedelta(minutes=GAS_READING_VALID_MINUTES)


def effective_status(
    stored_status: str, valid_to: datetime | None, now: datetime | None = None
) -> str:
    """What the permit actually is, as opposed to what the row last said.

    Computed on every read rather than trusted from the column. A permit that
    ran out at 18:00 is expired at 18:01 whether or not any sweep has run, and
    "the board still says ACTIVE" is exactly how a crew re-enters a space on
    yesterday's authorisation. The sweep reconciles the stored value; it is not
    what anybody is shown.
    """
    if stored_status not in ("issued", "suspended"):
        return stored_status
    if valid_to is None:
        return stored_status
    return "expired" if (now or datetime.now(timezone.utc)) > valid_to else stored_status


def missing_precautions(required: list, confirmed: dict) -> list[str]:
    """Which of the type's precautions have not been ticked.

    Returns labels rather than keys: this is shown to whoever is trying to
    issue, and a list of keys tells them nothing about what to go and do.
    """
    out: list[str] = []
    for item in required or []:
        key = item.get("key") if isinstance(item, dict) else str(item)
        if not key:
            continue
        if not confirmed.get(key):
            label = item.get("label") if isinstance(item, dict) else str(item)
            out.append(label or key)
    return out
