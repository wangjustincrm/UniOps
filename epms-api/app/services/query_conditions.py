"""The conditions an earlier answer used, read back from its query in code (ISS-0035).

Asked "这个结果用的到期日范围和发票状态条件是什么？" after a total, the
Assistant planned the previous question again and repeated its answer. The
conditions are all in the query that ran, which the panel sends back with the
earlier turn; they are listed here from it, field by field, with the dates the
period stood for. Nothing is re-planned and nothing is left to the model.
"""
from __future__ import annotations

import re
from datetime import date

from app.core.ontology import REGISTRY
from app.services import controlled_query as cq

# What is asked about: the conditions / criteria / range / filter …
_ABOUT = ("条件", "口径", "筛选", "过滤", "范围", "标准", r"\bconditions?\b", r"\bcriteria\b",
          r"\bfilters?\b", r"\bdate range\b")
# … of the answer already given.
_THAT_ANSWER = ("这个结果", "这个数", "这个回答", "这个答案", "上面", "刚才", "上一个", "上个回答",
                "你用", "用的", "用了", "是怎么算", "怎么算出", r"\byou used\b", r"\bdid you use\b",
                r"\bthis (?:result|answer|number|total)\b", r"\bthat (?:result|answer|number|total)\b")
# A follow-up that CHANGES the conditions is a new query, planned as one.
_CHANGE = ("改成", "改为", "去掉", "换成", "只要", "加上", "不要", "排除", r"\bdrop\b",
           r"\bremove\b", r"\bchange\b", r"\bonly\b", r"\bwithout\b", r"\bexclude")
_DOC_NUMBER = re.compile(r"\b(?:PR|PO|GR|PA|INV|AGR)-?\d", re.I)

_OPS = {
    "eq": ("is", "="), "ne": ("is not", "≠"), "gt": ("is after / above", ">"),
    "gte": ("is on or after / at least", "≥"), "lt": ("is before / below", "<"),
    "lte": ("is on or before / at most", "≤"), "in": ("is one of", "属于"),
    "not_in": ("is none of", "不属于"), "like": ("contains", "包含"),
    "not_like": ("does not contain", "不包含"),
}


def asks_for_conditions(message: str) -> bool:
    text = " ".join((message or "").lower().split())
    if _DOC_NUMBER.search(text) or any(re.search(p, text) for p in _CHANGE):
        return False
    return (any(re.search(p, text) for p in _ABOUT)
            and any(re.search(p, text) for p in _THAT_ANSWER))


def _field(entity, name: str):
    parts = name.split(".")
    current = entity
    for step in parts[:-1]:
        link = current.links.get(step)
        if link is None:
            return None
        current = REGISTRY[link.target]
    return current.fields.get(parts[-1])


def _value(f, value) -> str:
    labels = dict(f.value_labels) if f is not None else {}
    values = value if isinstance(value, list) else [value]
    shown = [f"{labels[str(v)]} ({v})" if str(v) in labels else str(v) for v in values]
    return ", ".join(shown) if shown else "—"


def _ddmmyyyy(day: date | str | None) -> str:
    if not day:
        return "…"
    # Name kept for its callers; on-screen format (UI-DATE-002): 2026-NOV-11.
    from app.core.display_dates import text_date
    return text_date(str(day)[:10]) or "…"


def describe(query: dict, today: date | None = None) -> tuple[str, str] | None:
    """(English, Chinese) list of what the query filtered on, or None."""
    entity = REGISTRY.get(str(query.get("entity") or ""))
    if entity is None:
        return None
    name = entity.label.split("—")[0].strip()
    en = [f"The previous answer was read from {name}, with these conditions:"]
    zh = [f"上一个回答查询的是 {name}，条件如下："]

    for c in query.get("where") or []:
        if not isinstance(c, dict) or not c.get("field"):
            continue
        f = _field(entity, str(c["field"]))
        label = f.label if f is not None else c["field"]
        op_en, op_zh = _OPS.get(str(c.get("op")), (str(c.get("op")), str(c.get("op"))))
        value = _value(f, c.get("value"))
        if c.get("value") is None and c.get("op") in ("eq", "ne"):
            value = "empty"
        en.append(f"- {label} {op_en} {value}")
        zh.append(f"- {label} {op_zh} {value}")

    period = query.get("period") or {}
    if period:
        field = period.get("field") or entity.date_field
        f = _field(entity, field)
        label = f.label if f is not None else field
        if period.get("calendar"):
            frm, to = cq.calendar_range(period["calendar"], int(period.get("shift_years") or 0),
                                        today=today,
                                        whole=bool(f is not None and f.scheduled))
            span = f"{_ddmmyyyy(frm.isoformat())} – {_ddmmyyyy(to.isoformat())}"
        elif period.get("from") or period.get("to"):
            span = f"{_ddmmyyyy(period.get('from'))} – {_ddmmyyyy(period.get('to'))}"
        elif period.get("last_n_months"):
            span = f"last {period['last_n_months']} month(s)"
        elif period.get("last_n_days"):
            span = f"last {period['last_n_days']} day(s)"
        else:
            span = None
        if span:
            en.append(f"- {label}: {span}")
            zh.append(f"- {label}：{span}")

    if len(en) == 1:
        en.append("- no conditions: every record this person may see")
        zh.append("- 没有筛选条件：你有权查看的全部记录")

    metrics = [m for m in query.get("metrics") or [] if m in entity.metrics]
    excludes = entity.amount_excludes
    if excludes and any(entity.metrics[m].fn == "sum" for m in metrics) and not any(
            isinstance(c, dict) and c.get("field") == excludes[0]
            for c in query.get("where") or []):
        en.append(f"- {excludes[2]}")
        zh.append(f"- 金额合计不含状态为 {', '.join(excludes[1])} 的记录（问题没有指定状态时的默认口径）")
    if query.get("group_by"):
        en.append(f"Grouped by: {', '.join(query['group_by'])}.")
        zh.append(f"分组：{', '.join(query['group_by'])}。")
    return "\n".join(en), "\n".join(zh)
