"""Calendar words and dates in a question, read in code (ISS-0020, ISS-0035, ISS-0048).

The planner was told, in its prompt, that 本月 / this month is a named calendar
period and that the same question must map to the same period. On production
it still did not, often enough to be reported four ways:

  * "本月一共提交了多少张 PR？" — 6 in a new conversation, 153 when asked again:
    once `status = submitted` (PRs still waiting today), once submitted_at in
    this month;
  * "去年同期的采购额是多少？" asked alone returned this year's total — the
    calendar period with no shift_years;
  * "本周提交了哪些请购单？" listed only the PRs still in approval, leaving out
    those submitted this week and approved since;
  * "本月到期待付款的发票总额" ran as `due_date ≤ today` with no start date at
    all, 592 invoices from every earlier month;
  * "10/11" was 10 November 2026 on one page and 10 November 2025 on Portal.

So the words are read here, from the message itself, and the planned query is
corrected to them before it runs (`enforce`). Whatever the planner wrote for
the period, a question that names exactly one calendar period runs over that
period's dates. What the planner may still decide is everything the words do
not settle: a question naming two periods, or a rolling window ("最近 3 个月"),
is left as planned.
"""
from __future__ import annotations

import re
from datetime import date

from app.core.ontology import DATE, DATETIME, Entity

# (calendar name, patterns). Longer phrases first so 上个月 is not read as 月.
_CALENDAR_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("today", ("今天", "今日", r"\btoday\b")),
    ("yesterday", ("昨天", "昨日", r"\byesterday\b")),
    ("this_week", ("本周", "这周", "这一周", "这个星期", "本星期", "这星期", r"\bthis week\b")),
    ("last_week", ("上周", "上一周", "上个星期", "上星期", r"\blast week\b")),
    ("this_month", ("本月", "这个月", "这月", "当月", r"\bthis month\b")),
    ("last_month", ("上个月", "上月", "上一个月", r"\blast month\b")),
    ("this_quarter", ("本季度", "这个季度", "本季", "这季度", r"\bthis quarter\b")),
    ("last_quarter", ("上季度", "上个季度", "上一季度", r"\blast quarter\b")),
    ("this_year", ("今年", "本年度", "本年", r"\bthis year\b", r"\byear to date\b", r"\bytd\b")),
    ("last_year", ("去年", "上一年", r"\blast year\b")),
)

# "The same period last year": the period moved back one year, not last year.
_SAME_PERIOD_LAST_YEAR = ("去年同期", "上年同期", r"\bsame period last year\b",
                          r"\bsame time last year\b")
# Asked as a comparison, both periods are wanted (compare_previous_year).
_COMPARISON = ("同比", "增长", "增加了多少", "减少了多少", "相比", "对比", "比去年",
               r"\bcompared?\b", r"\bvs\.?\b", r"\byear[- ]on[- ]year\b", r"\byoy\b",
               r"\bgrowth\b", r"\bgrew\b")
# A rolling window is a different question, planned as last_n_months/days.
_ROLLING = ("最近", "过去", "近一", "近两", "近三", r"近\d", r"\blast \d+", r"\bpast \d+",
            r"\bpast (?:few|several|couple)", r"\brecent", r"\bprevious \d+")

# The event a question counts by, on the entity it is about. "提交了多少张 PR"
# is PRs submitted in the period, whatever has happened to them since — not
# PRs whose status is still `submitted` (ISS-0020 #2, #4).
_EVENT_FIELDS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "purchase_request": (
        ("submitted_at", ("提交", r"\bsubmit")),
        ("approved_at", ("批准", "审批通过", r"\bapproved\b")),
    ),
    "invoice": (
        ("due_date", ("到期", r"\bdue\b")),
    ),
}
# Statuses that only restate the event: a PR submitted this week and approved
# since is still one submitted this week. Dropped unless the question also asks
# about the state ("还在审批中的").
_EVENT_STATUS = {"submitted_at": ("submitted", "in_review")}
_STATE_WORDS = ("审批中", "待审批", "审核中", "待审", "未批", "还没批", "pending",
                "in review", "under review", "awaiting approval", "not yet approved")

# 10/11, 10/11/2026, 10-11-2026 (day first, as EPMS shows dates); 11月10日,
# 2026年11月10日. ISO dates (2026-11-10) are unambiguous and left alone.
_SLASHED = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{4}))?(?![\d/])")
_CHINESE_DAY = re.compile(r"(?:(\d{4})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")
_ISO_DAY = re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b")
# 2026-NOV-11, the way every screen, PDF and answer shows dates (UI-DATE-002):
# people copy it into their questions. Read in code like the forms above, so
# the day they wrote is the day queried.
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_YMMMD_DAY = re.compile(r"\b(\d{4})-(" + "|".join(_MONTHS) + r")-(\d{1,2})\b", re.I)
# Any date or month written out, besides the calendar word: then the dates in
# the query may be the person's own, and the query is left as planned.
_WRITTEN_DATE = re.compile(
    r"\d\s*月|\d{1,2}/\d{1,2}|\d{4}-\d{1,2}|\d{4}-[a-z]{3}-\d{1,2}|\d{4}\s*年|\bq[1-4]\b|\b(?:january|february|"
    r"march|april|june|july|august|september|october|november|december|jan|feb|apr|jun|"
    r"jul|aug|sept?|oct|nov|dec)\b", re.I)


def _found(text: str, patterns) -> bool:
    return any(re.search(p, text) for p in patterns)


def _fold(message: str) -> str:
    return " ".join((message or "").lower().split())


def same_period_last_year(message: str) -> bool:
    return _found(_fold(message), _SAME_PERIOD_LAST_YEAR)


def is_comparison(message: str) -> bool:
    return _found(_fold(message), _COMPARISON)


def is_rolling(message: str) -> bool:
    return _found(_fold(message), _ROLLING)


def calendar_words(message: str) -> list[str]:
    """Every calendar period the message names, in the order of the table."""
    text = _fold(message)
    for p in _SAME_PERIOD_LAST_YEAR:
        text = re.sub(p, " ", text)
    found = []
    for name, patterns in _CALENDAR_WORDS:
        hits = [p for p in patterns if re.search(p, text)]
        if hits:
            found.append(name)
            for p in hits:  # 上个月 must not also count as 本月 / 月
                text = re.sub(p, " ", text)
    return found


def explicit_days(message: str, today: date) -> list[tuple[str, date]]:
    """Days written in the message, read day first. A day without a year is
    in the current (plant) year — the same on every page (ISS-0048)."""
    out: list[tuple[str, date]] = []
    text = message or ""
    for m in _CHINESE_DAY.finditer(text):
        year = int(m.group(1)) if m.group(1) else today.year
        try:
            out.append((m.group(0), date(year, int(m.group(2)), int(m.group(3)))))
        except ValueError:
            continue
    for m in _YMMMD_DAY.finditer(text):
        try:
            out.append((m.group(0), date(int(m.group(1)), _MONTHS.index(m.group(2).lower()) + 1,
                                         int(m.group(3)))))
        except ValueError:
            continue
    for m in _SLASHED.finditer(_ISO_DAY.sub(" ", text)):
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            out.append((m.group(0), date(year, int(m.group(2)), int(m.group(1)))))
        except ValueError:
            continue
    return out


def event_field(entity: Entity, message: str) -> str | None:
    text = _fold(message)
    for field, patterns in _EVENT_FIELDS.get(entity.name, ()):
        if field in entity.fields and _found(text, patterns):
            return field
    return None


def _date_kind(entity: Entity, name: str) -> bool:
    f = entity.fields.get(name) if "." not in name else None
    return f is not None and f.kind in (DATE, DATETIME)


def _looks_like_a_date(value) -> bool:
    return isinstance(value, str) and bool(re.match(r"^\d{4}-\d{2}-\d{2}", value))


def enforce(query: dict, message: str, entity: Entity, today: date,
            carried: str | None = None) -> tuple[dict, list[str]]:
    """The planned query with its period set from the words of the message.

    `carried` is the calendar period of the previous answer's query, for a
    follow-up that names none of its own ("去年同期的呢？" after "本季度的采购额").

    Returns (query, what was changed). A message that names no calendar period
    and no single day, names two periods, or asks for a rolling window comes
    back unchanged.
    """
    if not entity.date_field and not any(_date_kind(entity, n) for n in entity.fields):
        return query, []
    words = calendar_words(message)
    spl = same_period_last_year(message)
    if is_rolling(message) or len(words) > 1:
        return query, []
    if (words or spl) and _WRITTEN_DATE.search(message or ""):
        return query, []
    period = dict(query.get("period") or {})
    where = list(query.get("where") or [])
    changed: list[str] = []

    if not words and not spl:
        days = {d for _, d in explicit_days(message, today)}
        if len(days) == 1 and period.get("from") and period.get("to") \
                and period["from"] == period["to"] and not period.get("calendar"):
            (day,) = days
            if period["from"] != day.isoformat():
                period["from"] = period["to"] = day.isoformat()
                changed.append(f"day read as {day.isoformat()}")
                return {**query, "period": period}, changed
        return query, []

    # Which date the period is on: the event the question names, else a date
    # the planner filtered on itself, else what it chose, else the default.
    event = event_field(entity, message)
    date_clauses = [c for c in where
                    if isinstance(c, dict) and _date_kind(entity, str(c.get("field") or ""))
                    and c.get("op") in ("lt", "lte", "gt", "gte", "eq")
                    and _looks_like_a_date(c.get("value"))]
    field = (event or (date_clauses[0]["field"] if date_clauses else None)
             or period.get("field"))
    if field:
        dropped = [c for c in date_clauses if c["field"] == field]
        if dropped:
            where = [c for c in where if c not in dropped]
            changed.append(f"dates on {field} worked out by the planner replaced")
    if event in _EVENT_STATUS and not _found(_fold(message), _STATE_WORDS):
        restating = set(_EVENT_STATUS[event])
        kept = []
        for c in where:
            value = c.get("value") if isinstance(c, dict) else None
            values = set(value) if isinstance(value, list) else {value}
            if (isinstance(c, dict) and c.get("field") == "status"
                    and c.get("op") in ("eq", "in") and values and values <= restating):
                changed.append(f"status {sorted(values)} dropped: the question counts by {event}")
                continue
            kept.append(c)
        where = kept

    compare = bool(period.get("compare_previous_year"))
    if spl and is_comparison(message):
        compare = True
    if words:
        name = words[0]
    else:
        # 去年同期 alone: of the period the conversation was about, else of
        # this year to date — never this year itself (ISS-0020 #3).
        name = carried or period.get("calendar") or "this_year"
    shift = -1 if (spl and not compare) else 0

    wanted = {"calendar": name, **({"field": field} if field else {}),
              **({"shift_years": shift} if shift else {}),
              **({"compare_previous_year": True} if compare else {})}
    if period != wanted:
        changed.append(f"period {period or None} -> {wanted}")
    out = {**query, "period": wanted}
    if where != list(query.get("where") or []):
        out["where"] = where
    return (out, changed) if changed else (query, [])


# ── Saying which dates were used (ISS-0020 #1) ───────────────────────────────
#
# The narrator was told to state `resolved_period` — and was never given it:
# narrate()'s payload is a whitelist and the key was not on it, so no answer
# on production said which dates it used. The sentence is now written here and
# added under the reply by the chat layer, like when_listed (ISS-0008).

def _ddmmyyyy(iso: str | None) -> str:
    if not iso:
        return "…"
    # Name kept for its callers; on-screen format (UI-DATE-002): 2026-NOV-11.
    from app.core.display_dates import text_date
    return text_date(str(iso)[:10]) or "…"


def _field_label(entity: Entity | None, name: str | None) -> str | None:
    if entity is None or not name or name == entity.date_field:
        return None
    parts = name.split(".")
    current = entity
    from app.core.ontology import REGISTRY
    for step in parts[:-1]:
        link = current.links.get(step)
        if link is None:
            return None
        current = REGISTRY[link.target]
    f = current.fields.get(parts[-1])
    return f.label if f is not None else None


def stated(resolved: dict | None, entity: Entity | None = None,
           previous: dict | None = None) -> tuple[str, str] | None:
    """(English, Chinese) sentence naming the dates a query covered, or None."""
    if not resolved or not (resolved.get("from") or resolved.get("to")):
        return None
    span = f"{_ddmmyyyy(resolved.get('from'))} – {_ddmmyyyy(resolved.get('to'))}"
    label = _field_label(entity, resolved.get("field"))
    en = f"Period used: {span}" + (f" (by {label})" if label else "") + "."
    zh = f"查询期间：{span}" + (f"（按 {label}）" if label else "") + "。"
    if previous and (previous.get("from") or previous.get("to")):
        before = f"{_ddmmyyyy(previous.get('from'))} – {_ddmmyyyy(previous.get('to'))}"
        en += f" Same period last year: {before}."
        zh += f"去年同期：{before}。"
    return en, zh
