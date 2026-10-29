"""What HSE sets once for everybody's dashboard: colours and chart styles.

Round 9: "when the days since last medical incident is greater than 15 change
the colour … to green … red when the count is less than 5, and yellow in
between … allow for changing those numbers in the future", and "a way to change
the graph styles … from a bar graph to a circle graph". Round 10 answered who
sets them: "HSE will set the numbers once for everybody under Settings", and
the chart style too.

★ Which way is better belongs to the metric, never to the two numbers. Reading
it off "is good larger than bad" would turn a typo into a dashboard that shows
a bad year in green. A pair that contradicts its metric's direction is refused
with the reason, instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, status

Tone = Literal["good", "warn", "bad"]


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    higher_is_better: bool


METRICS: tuple[Metric, ...] = (
    Metric("days_since", "Days since the last medical incident", True),
    Metric("injuries", "Medical incidents this year", False),
    Metric("first_aid", "First aid injuries this year", False),
    Metric("medical_aid", "Medical aid injuries this year", False),
    Metric("lost_time", "Lost time injuries this year", False),
    Metric("trir", "Recordable incident rate", False),
    Metric("resolution_days", "Average days to resolve, this month", False),
)
_BY_KEY = {m.key: m for m in METRICS}

# Which drawings each chart can take. The first is what it draws when HSE has
# not chosen.
CHARTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "injured_locations": ("Where injuries are happening", ("bar", "circle")),
    "injured_departments": ("Injured by department", ("bar", "circle")),
    "body_parts": ("Part of the body affected", ("body", "bar", "circle")),
}


def tone(key: str, value: float | None, thresholds: dict) -> Tone | None:
    """The colour a figure earns, or None when it has no numbers or no value.

    Strict at both ends, as Kirk wrote it: "greater than 15" is green, "less
    than 5" is red, so 15 and 5 themselves are yellow.
    """
    metric = _BY_KEY.get(key)
    pair = thresholds.get(key) if isinstance(thresholds, dict) else None
    if metric is None or value is None or not isinstance(pair, dict):
        return None
    good, bad = pair.get("good"), pair.get("bad")
    if good is None or bad is None:
        return None
    if metric.higher_is_better:
        return "good" if value > good else "bad" if value < bad else "warn"
    return "good" if value < good else "bad" if value > bad else "warn"


def clean_thresholds(raw: dict) -> dict:
    """Validated thresholds, or 422 naming every problem at once.

    A metric sent with both numbers empty is cleared — that is how HSE turns
    the colour off for it.
    """
    out: dict[str, dict] = {}
    problems: list[str] = []
    for key, pair in raw.items():
        metric = _BY_KEY.get(key)
        if metric is None:
            problems.append(f"There is no dashboard figure called {key!r}.")
            continue
        good = pair.get("good") if isinstance(pair, dict) else None
        bad = pair.get("bad") if isinstance(pair, dict) else None
        if good is None and bad is None:
            continue
        if good is None or bad is None:
            problems.append(f"{metric.label}: give both numbers, or clear both.")
            continue
        if not all(isinstance(n, (int, float)) and not isinstance(n, bool) and n >= 0
                   for n in (good, bad)):
            problems.append(f"{metric.label}: the numbers must be zero or more.")
            continue
        if metric.higher_is_better and good < bad:
            problems.append(f"{metric.label}: more is better here, so the green number "
                            f"({good}) cannot be below the red one ({bad}).")
            continue
        if not metric.higher_is_better and good > bad:
            problems.append(f"{metric.label}: fewer is better here, so the green number "
                            f"({good}) cannot be above the red one ({bad}).")
            continue
        out[key] = {"good": good, "bad": bad}
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"problems": problems})
    return out


def clean_chart_styles(raw: dict) -> dict:
    out: dict[str, str] = {}
    problems: list[str] = []
    for chart, style in raw.items():
        known = CHARTS.get(chart)
        if known is None:
            problems.append(f"There is no dashboard chart called {chart!r}.")
        elif style not in known[1]:
            problems.append(f"{known[0]} can be drawn as {', '.join(known[1])} — not {style!r}.")
        else:
            out[chart] = style
    if problems:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, {"problems": problems})
    return out


def chart_styles(stored: dict) -> dict[str, str]:
    """Every chart's style: HSE's choice where it is still a valid one, the
    chart's default otherwise."""
    stored = stored if isinstance(stored, dict) else {}
    return {chart: (stored.get(chart) if stored.get(chart) in styles else styles[0])
            for chart, (_label, styles) in CHARTS.items()}
