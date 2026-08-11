"""周期排期的日期算法。纯函数,不碰 DB —— 这是本期最容易算错的一块,
把它隔离出来才测得干净。

规则出处:设计文档 §4.1 / §4.2。
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import NamedTuple

MAX_PERIOD_ROWS = 500

RECURRING_TYPES = ("weekly", "monthly", "quarterly", "yearly")


class TooManyPeriods(ValueError):
    """有效期 × 周期长度会生成超过 MAX_PERIOD_ROWS 行。"""


class PeriodRow(NamedTuple):
    sequence: int
    period_label: str
    expected_date: date


def _clamp_day(year: int, month: int, day: int) -> date:
    """把 day 钳进该月的实际天数 —— 31 号遇 2 月取月末(闰年 29)。"""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = (year * 12 + (month - 1)) + delta
    return idx // 12, idx % 12 + 1


def _month_starts(valid_from: date, valid_to: date, step: int,
                  anchor_month: int | None) -> list[tuple[int, int]]:
    """产出期起始 (year, month) 序列。

    anchor_month 为 None(monthly)时锚点就是 valid_from 所在月。否则期起始月
    构成序列 {anchor_month, anchor_month+step, ...},首期取**起始月 <= valid_from
    的最后一个**,也就是覆盖 valid_from 的那一期。
    """
    if anchor_month is None:
        y, m = valid_from.year, valid_from.month
    else:
        # 从 valid_from 当年的 anchor_month 起,先退到不晚于 valid_from 的那一期
        y, m = valid_from.year, anchor_month
        while (y, m) > (valid_from.year, valid_from.month):
            y, m = _add_months(y, m, -step)
        while True:
            ny, nm = _add_months(y, m, step)
            if (ny, nm) > (valid_from.year, valid_from.month):
                break
            y, m = ny, nm

    out: list[tuple[int, int]] = []
    while (y, m) <= (valid_to.year, valid_to.month):
        out.append((y, m))
        if len(out) > MAX_PERIOD_ROWS:
            raise TooManyPeriods(
                f"This validity window would generate more than {MAX_PERIOD_ROWS} "
                "schedule rows. Shorten the validity window or use a longer cycle.")
        y, m = _add_months(y, m, step)
    return out


def _weekly_rows(valid_from: date, valid_to: date, weekday: int) -> list[PeriodRow]:
    """期 = ISO 周(周一起)。expected_invoice_day 是 ISO 星期几(1=周一)。"""
    monday = valid_from - timedelta(days=valid_from.isoweekday() - 1)
    raw: list[tuple[str, date]] = []
    while monday <= valid_to:
        iso_year, iso_week, _ = monday.isocalendar()
        raw.append((f"{iso_year}-W{iso_week:02d}", monday + timedelta(days=weekday - 1)))
        if len(raw) > MAX_PERIOD_ROWS:
            raise TooManyPeriods(
                f"This validity window would generate more than {MAX_PERIOD_ROWS} "
                "schedule rows. Shorten the validity window or use a longer cycle.")
        monday += timedelta(days=7)
    return [PeriodRow(0, label, d) for label, d in raw]


def build_period_rows(
    *,
    recurring_type: str,
    valid_from: date,
    valid_to: date,
    expected_invoice_day: int,
    anchor_month: int | None,
) -> list[PeriodRow]:
    """按协议的周期参数产出整个有效期的排期行。

    边界规则(设计文档 §4.1):
      - 首期 expected_date < valid_from → 跳过(那张票在生效前就该到了)
      - 期起始日 <= valid_to 的期都保留,即使 expected_date 晚于 valid_to
        (月结票总在期末之后才到,与 grace_days 的意图一致)
      - 超过 MAX_PERIOD_ROWS 行 → TooManyPeriods
    """
    if recurring_type not in RECURRING_TYPES:
        raise ValueError(f"Unknown recurring_type {recurring_type!r}")

    if recurring_type == "weekly":
        rows = _weekly_rows(valid_from, valid_to, expected_invoice_day)
    else:
        step = {"monthly": 1, "quarterly": 3, "yearly": 12}[recurring_type]
        anchor = None if recurring_type == "monthly" else anchor_month
        rows = [
            PeriodRow(0, f"{y:04d}-{m:02d}", _clamp_day(y, m, expected_invoice_day))
            for y, m in _month_starts(valid_from, valid_to, step, anchor)
        ]

    # 首期跳过:只丢开头连续的、到票日早于生效日的期。
    kept = [r for r in rows if r.expected_date >= valid_from]
    if len(kept) > MAX_PERIOD_ROWS:
        raise TooManyPeriods(
            f"This validity window would generate more than {MAX_PERIOD_ROWS} "
            "schedule rows. Shorten the validity window or use a longer cycle.")
    return [PeriodRow(i + 1, r.period_label, r.expected_date) for i, r in enumerate(kept)]
