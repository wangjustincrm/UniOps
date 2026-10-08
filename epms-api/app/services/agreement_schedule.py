"""周期排期的日期算法。纯函数,不碰 DB —— 这是本期最容易算错的一块,
把它隔离出来才测得干净。

规则出处:设计文档 §4.1 / §4.2。
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import NamedTuple

MAX_PERIOD_ROWS = 500

RECURRING_TYPES = ("weekly", "monthly", "quarterly", "yearly", "special_monthly")

# quarterly/yearly 的 anchor_month 承载的是真实账期锚点(比如季度账单常年跑
# 2/5/8/11 而不是日历季度),不能也不该从 valid_from 反推 —— 那是 monthly 的规则。
_TYPES_REQUIRING_ANCHOR = ("quarterly", "yearly")

# special_monthly = "只在选中的那几个月出账的月结"。季节性服务(除雪、草坪、
# 空调保养)一年只跑其中几个月,却按月开票 —— 用 monthly 排会给停工的月份也
# 生成期次,那些期永远等不到发票,逾期扫描每天催一遍;用 yearly 又丢掉了月度
# 的颗粒度。这里保留 monthly 的全部规则(网格按月推、标签 YYYY-MM、到票日钳
# 月末),只额外按 active_months 过滤月份。
_TYPES_REQUIRING_ACTIVE_MONTHS = ("special_monthly",)


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
    schedule_start_date: date | None = None,
    active_months: list[int] | None = None,
) -> list[PeriodRow]:
    """按协议的周期参数产出整个有效期的排期行。

    边界规则(设计文档 §4.1):
      - 首期 expected_date < valid_from → 跳过(那张票在生效前就该到了)
      - schedule_start_date(ag08,可选)→ 到票日早于它的期一律不生成。协议
        中途上线时,合同前段的票是在系统外付掉的,给它们建行只会换来一串
        永远认领不掉的 pending 和每天一封催票信。**网格仍按 valid_from 推**,
        所以标签与季度/年度锚点保持合同口径,这里只决定从哪一行开始留;
        留下来的行重新从 1 编号(sequence 是排期内的序号,不是合同期数)。
        早于 valid_from 的值不会把范围反向扩大 —— 取两者中较晚的那个。
      - 期起始日 <= valid_to 的期都保留,即使 expected_date 晚于 valid_to
        (月结票总在期末之后才到,与 grace_days 的意图一致)
      - 超过 MAX_PERIOD_ROWS 行 → TooManyPeriods
      - quarterly/yearly 必须显式提供 anchor_month —— 不能从 valid_from 推导,
        因为真实账期锚点(如季度账单常年跑 2/5/8/11)与合同生效月无关;
        缺失时静默套用 valid_from 会产出一份看似合理、实则错误的排期表。
      - special_monthly 必须显式提供非空 active_months(1..12)。网格与 monthly
        完全一致,只保留月份落在 active_months 里的期 —— 三年期只勾 5..11 月
        就是每年 7 期、共 21 期。空集合不是"全选",而是一份一行都没有的排期,
        所以在这里就当错误挡下,而不是静默产出空表。
        注意行数上限 MAX_PERIOD_ROWS 卡的是**过滤前**的月度网格(它同时是循环
        的硬边界):41 年以上的有效期即使只勾一个月也会被拦下。现实里的协议
        没有这么长,而放开这个边界换来的是一个可以被有效期撑爆的循环。
    """
    if recurring_type not in RECURRING_TYPES:
        raise ValueError(f"Unknown recurring_type {recurring_type!r}")

    if recurring_type in _TYPES_REQUIRING_ACTIVE_MONTHS and not active_months:
        raise ValueError(
            f"active_months is required for recurring_type={recurring_type!r}: "
            "a special monthly cycle bills only in the months that were ticked, "
            "and an empty selection would generate no billing periods at all."
        )

    if recurring_type in _TYPES_REQUIRING_ANCHOR and anchor_month is None:
        raise ValueError(
            f"anchor_month is required for recurring_type={recurring_type!r}: "
            "the billing anchor (e.g. quarterly cycles that run Feb/May/Aug/Nov) "
            "cannot be safely inferred from valid_from."
        )

    if recurring_type == "weekly":
        rows = _weekly_rows(valid_from, valid_to, expected_invoice_day)
    else:
        step = {"monthly": 1, "quarterly": 3, "yearly": 12,
                "special_monthly": 1}[recurring_type]
        anchor = None if recurring_type in ("monthly", "special_monthly") else anchor_month
        months = _month_starts(valid_from, valid_to, step, anchor)
        if recurring_type == "special_monthly":
            wanted = set(active_months or ())
            months = [(y, m) for y, m in months if m in wanted]
        rows = [
            PeriodRow(0, f"{y:04d}-{m:02d}", _clamp_day(y, m, expected_invoice_day))
            for y, m in months
        ]

    # 首期跳过:只丢开头连续的、到票日早于起始日的期。
    start = max(valid_from, schedule_start_date) if schedule_start_date else valid_from
    kept = [r for r in rows if r.expected_date >= start]
    if len(kept) > MAX_PERIOD_ROWS:
        raise TooManyPeriods(
            f"This validity window would generate more than {MAX_PERIOD_ROWS} "
            "schedule rows. Shorten the validity window or use a longer cycle.")
    return [PeriodRow(i + 1, r.period_label, r.expected_date) for i, r in enumerate(kept)]
