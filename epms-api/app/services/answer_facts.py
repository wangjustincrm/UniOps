"""What the code looks up, works out and says under a query answer.

The round of 11-10 retests showed one pattern over and over: the data or the
rule was there, and the answer still came out wrong because the narrator
(Haiku) had to (a) think of asking for it, (b) do the arithmetic, or (c)
remember the one sentence that decides the question. GR quantities were
"not in the result" because only the receipt header was queried (ISS-0031,
ISS-0068); "is the invoice over the PO" came back "difference 0.00" (ISS-0016);
a paid, matched, received PO was "not three-way matched" (ISS-0036); an empty
Not to Exceed was "cannot calculate" (ISS-0068); a vendor with no Max
Prepayment % "has no prepayment policy" (ISS-0021, ISS-0066)...

So for each of those the code does the lookup and the sum, and returns the
sentence that must be said. `supplement` runs after the planned query and
before the narrator: it adds the facts for the narrator (`from_code`) and the
sentences to `said_with_this_list`, which the chat layer writes under the
reply (assistant_chat._with_listing_notes, the ISS-0008 mechanism) — so the
statement stands even when the narrator leaves it out or says the opposite.

Every lookup goes through the caller's scope: either the controlled query
(controlled_query.execute, both gates) or an explicit visibility check.

What triggers a supplement is the query's shape (entity, rows, fields) and,
where the shape alone cannot tell "how much was received" from "what status",
a few words of the question (_ASKS). Those lists are deliberately short and
both-language; a miss costs only the extra sentence, never a wrong one.
"""
from __future__ import annotations

import logging
import re
from decimal import Decimal

from app.core import doc_numbers
from app.services import controlled_query as cq

log = logging.getLogger(__name__)

# Small answers only: these are facts about one or a few documents, not a
# second report under a list.
_MAX_DOCS = 5

_ASKS = {
    "invoice": re.compile(r"发票|invoice|匹配|match|三单|3-?way|three[- ]way|超过|差异|exceed|over the po", re.I),
    "ceiling": re.compile(r"额度|剩|上限|余额|remaining|left|ceiling|\bNTE\b|not to exceed|limit|consumed|用了多少", re.I),
    "sent": re.compile(r"发给|发送|发出|寄|sent|send|e-?mail|邮件|收到", re.I),
    "payment": re.compile(r"付|账户|账号|account|paid|pay|汇款|remittance|bank|银行", re.I),
    "remittance": re.compile(r"汇款|remittance", re.I),
    "requester": re.compile(r"申请|requester|requested|raised|谁|who", re.I),
    "prepay": re.compile(r"预付|prepay", re.I),
    "yoy": re.compile(r"同比|去年同期|上年同期|same period last year|year[- ]on[- ]year|\byoy\b|vs\.? last year|compared (?:with|to) last year", re.I),
}


def asks(kind: str, message: str) -> bool:
    return bool(_ASKS[kind].search(message or ""))


def _money(value) -> str:
    return f"{Decimal(value or 0):,.2f}"


def _qty(value) -> str:
    d = Decimal(str(value or 0)).normalize()
    return f"{d:f}" if d == d.to_integral() else f"{d:f}".rstrip("0").rstrip(".")


def _say(result: dict, key: str, english: str, chinese: str) -> None:
    listed = result.setdefault("said_with_this_list", [])
    if all(n[0] != key for n in listed):
        listed.append((key, english, chinese))


def _from_code(result: dict, key: str, facts) -> None:
    result.setdefault("from_code", {})[key] = facts


async def _numbers(db, scope, query: dict, result: dict, fields: tuple[str, ...]) -> list[str]:
    """The document numbers this answer is about: named in an eq/in filter on
    one of `fields`, else read off the rows, else — the planner did not select
    the number — the same query run again for it (same filters, same scope)."""
    found: list[str] = []
    for clause in query.get("where") or []:
        if clause.get("field") in fields and clause.get("op") in ("eq", "in"):
            values = clause.get("value")
            for v in values if isinstance(values, list) else [values]:
                if isinstance(v, str) and v and v not in found:
                    found.append(v)
    if not found:
        for row in result.get("rows") or []:
            for f in fields:
                v = row.get(f)
                if isinstance(v, str) and v and v not in found:
                    found.append(v)
    if not found and result.get("rows") and not any(f in (result.get("labels") or [])
                                                     for f in fields):
        again = {k: query[k] for k in ("entity", "where", "period") if query.get(k)}
        for row in await _rows(db, scope, {**again, "select": [fields[0]],
                                           "limit": _MAX_DOCS + 1}):
            v = row.get(fields[0])
            if isinstance(v, str) and v and v not in found:
                found.append(v)
    return found


async def _rows(db, scope, query: dict) -> list[dict]:
    try:
        out = await cq.execute(db, query, scope)
    except cq.QueryRejected:  # pragma: no cover — the queries below are fixed
        log.exception("answer_facts | fixed query rejected | %s", query)
        return []
    return [] if out.get("denied") else out.get("rows") or []


def _detail(result: dict) -> bool:
    return not result.get("currency_split") and "totals" not in result and \
        "matched_rows" not in result


# ── ISS-0031 / ISS-0068: what each receipt received, per line ────────────────

async def _receipt_lines(db, scope, message, query, result) -> None:
    rows = result.get("rows") or []
    if not (_detail(result) and 0 < len(rows) <= _MAX_DOCS):
        return
    numbers = await _numbers(db, scope, query, result, ("number",))
    if not numbers or len(numbers) > _MAX_DOCS:
        return
    lines = await _rows(db, scope, {
        "entity": "gr_line",
        "select": ["receipt.number", "receipt.currency", "receipt.po_number", "description",
                   "qty_counted", "unit", "condition", "line_total"],
        "where": [{"field": "receipt.number", "op": "in", "value": numbers}],
        "order_by": {"field": "sort_order"}, "limit": 100})
    if not lines:
        return
    _from_code(result, "receipt_lines", lines)
    en, zh = [], []
    for number in numbers:
        mine = [r for r in lines if r["receipt.number"] == number]
        if not mine:
            continue
        cur = mine[0].get("receipt.currency") or ""
        value = sum((Decimal(r["line_total"] or 0) for r in mine), Decimal(0))
        po = mine[0].get("receipt.po_number")
        parts_en = [f"{r['description']} — {_qty(r['qty_counted'])} {r['unit'] or ''}".rstrip()
                    + ("" if r["condition"] in (None, "good") else f" ({r['condition']})")
                    for r in mine]
        en.append(f"{number}{' (' + po + ')' if po else ''} received {len(mine)} "
                  f"line{'s' if len(mine) != 1 else ''}, value {cur} {_money(value)}: "
                  + "; ".join(parts_en) + ".")
        zh.append(f"{number}{'（' + po + '）' if po else ''}收货 {len(mine)} 行，金额 {cur} "
                  f"{_money(value)}：" + "；".join(parts_en) + "。")
    if en:
        _say(result, "receipt_lines", " ".join(en), "".join(zh))


# ── ISS-0016 / ISS-0036: the PO against its invoices, and three-way match ────

_PO_FIELDS = {"purchase_order": ("number",), "invoice": ("po_number", "order.number"),
              "invoice_allocation": ("order.number",), "po_line": ("order.number",)}


async def _po_match(db, scope, message, query, result) -> None:
    from app.services import po_match_view

    entity = result.get("entity")
    fields = _PO_FIELDS.get(entity)
    if not fields:
        return
    selected = set(query.get("select") or []) | {
        c.get("field") for c in query.get("where") or []}
    if entity in ("purchase_order", "po_line") and not (
            asks("invoice", message) or selected & {"three_way_matched", "invoiced",
                                                    "order.three_way_matched"}):
        return
    if entity == "invoice" and not any(
            c.get("field") in fields for c in query.get("where") or []) and not asks(
                "invoice", message):
        # An invoice question not framed by its PO ("has this invoice been
        # paid?") is not a PO comparison.
        return
    numbers = await _numbers(db, scope, query, result, fields)
    if not numbers or len(numbers) > 2:
        return
    views = [v for v in [await po_match_view.compare(db, scope, n) for n in numbers] if v]
    if not views:
        return
    _from_code(result, "po_vs_invoices", views)
    said = [po_match_view.sentences(v) for v in views]
    _say(result, "po_vs_invoices", " ".join(s[0] for s in said), "".join(s[1] for s in said))


# ── ISS-0068: an agreement with no Not to Exceed has no ceiling ──────────────

async def _agreement_ceiling(db, scope, message, query, result) -> None:
    if result.get("entity") != "purchase_agreement" or not _detail(result):
        return
    selected = set(query.get("select") or [])
    if not (asks("ceiling", message) or selected & {"not_to_exceed", "remaining_amount"}):
        return
    numbers = await _numbers(db, scope, query, result, ("number",))
    if not numbers or len(numbers) > _MAX_DOCS:
        return
    rows = await _rows(db, scope, {
        "entity": "purchase_agreement",
        "select": ["number", "not_to_exceed", "consumed_amount", "remaining_amount", "currency"],
        "where": [{"field": "number", "op": "in", "value": numbers}]})
    en, zh = [], []
    for r in rows:
        cur = r.get("currency") or ""
        used = _money(r.get("consumed_amount"))
        if r.get("not_to_exceed") is None:
            en.append(f"{r['number']} has no Not to Exceed ceiling, so there is no limit on what "
                      f"is left (consumed so far {cur} {used}, before tax).")
            zh.append(f"{r['number']} 没有设置 Not to Exceed 上限（No ceiling），所以剩余额度不受限制"
                      f"（目前已使用 {cur} {used}，税前）。")
        else:
            en.append(f"{r['number']}: Not to Exceed {cur} {_money(r['not_to_exceed'])}, consumed "
                      f"{cur} {used}, remaining {cur} {_money(r.get('remaining_amount'))} (before tax).")
            zh.append(f"{r['number']}：Not to Exceed 上限 {cur} {_money(r['not_to_exceed'])}，已使用 "
                      f"{cur} {used}，剩余 {cur} {_money(r.get('remaining_amount'))}（税前）。")
    if en:
        _from_code(result, "agreement_ceiling", rows)
        _say(result, "agreement_ceiling", " ".join(en), "".join(zh))


# ── ISS-0021 #5: the year-on-year change, said by code ───────────────────────

def _year_on_year(db, scope, message, query, result) -> None:
    comparison = result.get("comparison")
    if not comparison:
        return
    entity = cq.REGISTRY.get(result.get("entity") or "")
    split = result.get("currency_split")
    now = (result.get("resolved_period") or {})
    before = ((result.get("previous_year") or {}).get("resolved_period") or {})

    def span(p):
        f, t = p.get("from"), p.get("to")
        fmt = (lambda d: "/".join(reversed(str(d).split("-"))))
        return f"{fmt(f)} – {fmt(t)}" if f and t else ""
    en, zh = [], []
    for c in comparison:
        label = entity.metrics[c["metric"]].label if entity and c["metric"] in entity.metrics else c["metric"]
        cur = (c.get(split) or "") if split else ""
        change = Decimal(c["change"])
        sign = "+" if change > 0 else ""
        pct = f" ({sign}{c['change_pct']}%)" if c.get("change_pct") is not None else \
            " (no figure last year, so no percent)"
        pct_zh = f"（{sign}{c['change_pct']}%）" if c.get("change_pct") is not None else "（去年同期为 0，无法算百分比）"
        is_count = entity and c["metric"] in entity.metrics and entity.metrics[c["metric"]].fn == "count"
        fmt = _qty if is_count else _money
        en.append(f"{label}: {cur} {fmt(c['current'])} this period ({span(now)}) against "
                  f"{cur} {fmt(c['previous_year'])} a year earlier ({span(before)}), a change of "
                  f"{sign}{cur} {fmt(change)}{pct}.".replace("  ", " "))
        zh.append(f"{label}：本期（{span(now)}）{cur} {fmt(c['current'])}，去年同期（{span(before)}）"
                  f"{cur} {fmt(c['previous_year'])}，变化 {sign}{cur} {fmt(change)}{pct_zh}。".replace("  ", " "))
    _say(result, "year_on_year", " ".join(en), "".join(zh))


def with_year_on_year(message: str, query: dict) -> dict:
    """A "same period last year" question planned as a plain total gets the
    comparison anyway: the planner left the flag off and the reply was "cannot
    calculate the growth" (ISS-0021 #5). Only a total with a period qualifies."""
    period = query.get("period") or {}
    if (not asks("yoy", message) or period.get("compare_previous_year")
            or not query.get("metrics") or not (period.get("calendar") or (
                period.get("from") and period.get("to")))):
        return query
    # The currency is split off anyway; grouping by anything else is not a
    # total any more, and the comparison refuses it.
    entity = cq.REGISTRY.get(query.get("entity") or "")
    currencies = {m.currency for m in (entity.metrics.values() if entity else []) if m.currency}
    if [g for g in query.get("group_by") or [] if g not in currencies]:
        return query
    return {**{k: v for k, v in query.items() if k != "group_by"},
            "period": {**period, "compare_previous_year": True}}


# ── ISS-0013 ③: "past its expected delivery" counts only orders that can receive

_DELIVERY_FIELD = {"purchase_order": ("expected_delivery", "status"),
                   "po_line": ("order.expected_delivery", "order.status")}


def receivable_statuses() -> list[str]:
    """PO statuses a goods receipt may be recorded against (GR-P-001), from
    the receipt endpoint itself."""
    from app.api.v1.gr import PHYSICAL_GR_PO_STATUSES, SERVICE_GR_PO_STATUSES
    return sorted(set(PHYSICAL_GR_PO_STATUSES) | set(SERVICE_GR_PO_STATUSES))


def with_receivable_orders_only(query: dict) -> tuple[dict, bool]:
    """"Which POs are past their expected delivery and not fully received?"
    listed a Draft PO first (PO-442-2607-01, ISS-0013): a PO that has not been
    placed is not late. When a query filters on the expected delivery date
    being before a date and names no status to keep, the orders that cannot
    receive goods are left out, and the answer says so."""
    fields = _DELIVERY_FIELD.get(query.get("entity") or "")
    if not fields:
        return query, False
    where = query.get("where") or []
    late = any(c.get("field") == fields[0] and c.get("op") in ("lt", "lte") for c in where)
    named = any(c.get("field") == fields[1] and c.get("op") in ("eq", "in") for c in where)
    if not late or named:
        return query, False
    return {**query, "where": [*where, {
        "field": fields[1], "op": "in", "value": receivable_statuses(),
        "because": "only orders that can still receive goods (GR-P-001)"}]}, True


def say_receivable_only(result: dict) -> None:
    names = {"approved": "Approved (service orders)", "issued": "Issued",
             "partially_received": "Partial Receipt"}
    zh = {"approved": "已批准（服务类）", "issued": "已下单（Issued）",
          "partially_received": "部分收货"}
    statuses = receivable_statuses()
    _say(result, "when_a_receipt_can_be_created",
         "Only orders that can still receive goods are counted ("
         + ", ".join(names.get(s, s) for s in statuses)
         + "); a draft or unapproved PO has not been placed, so it is not late.",
         "只统计还可以收货的订单（" + "、".join(zh.get(s, s) for s in statuses)
         + "）；草稿或尚未审批的 PO 还没有下单，不算逾期未收货。")


# ── ISS-0024: one digit wrong — offer the real number ────────────────────────

def _did_you_mean(db, scope, message, query, result) -> None:
    if result.get("rows"):
        return
    near = result.get("near_misses") or {}
    entity = cq.REGISTRY.get(result.get("entity") or "")
    for field, values in near.items():
        f = entity.fields.get(field) if entity else None
        if f is None or not f.doc_number or not values:
            continue
        typed = next((c.get("value") for c in query.get("where") or []
                      if c.get("field") == field), "")
        shown = ", ".join(values)
        _say(result, "did_you_mean",
             f"Nothing was found under {typed}. Did you mean {shown}?",
             f"没有找到 {typed}。您是不是指 {shown}？")
        return


# ── ISS-0044: a part with no PO lines still exists ───────────────────────────

async def _part_without_orders(db, scope, message, query, result) -> dict | None:
    """po_line filtered by a material code found nothing: say whether the part
    is in the catalog, in code. "Please check the part code" for a part that is
    there read as "it does not exist" (ISS-0044)."""
    if result.get("entity") != "po_line" or result.get("rows") or not _detail(result):
        return None
    codes = [c.get("value") for c in query.get("where") or []
             if c.get("field") in ("material_id", "supplier_item_id")
             and c.get("op") == "eq" and isinstance(c.get("value"), str)]
    if len(codes) != 1:
        return None
    parts = await _rows(db, scope, {
        "entity": "part", "select": ["code", "name", "supplier", "unit_price", "unit"],
        "where": [{"field": "code", "op": "eq", "value": codes[0].strip()}]})
    if not parts:
        return None
    p = parts[0]
    return {"_fixed_text": True, "kind": "answer",
            "answer": (f"{p['code']} ({p['name']}) is in the Parts Catalog — supplier "
                       f"{p['supplier']}, catalog price CAD {_money(p['unit_price'])} per "
                       f"{p['unit']} — but no purchase order line carries this code, so there is "
                       f"no purchase price history for it in EPMS."),
            "answer_zh": (f"{p['code']}（{p['name']}）在零件目录（Parts Catalog）中——供应商 "
                          f"{p['supplier']}，目录单价 CAD {_money(p['unit_price'])}/{p['unit']}——"
                          f"但没有任何采购订单行使用这个代码，所以 EPMS 里没有它的采购价格记录。")}


# ── ISS-0021 #6 / ISS-0066: the prepayment cap that applies ──────────────────

async def _prepayment_cap(db, scope, message, query, result) -> None:
    from app.core.prepayment_cap import effective_cap

    if result.get("entity") != "vendor" or not _detail(result):
        return
    if not (asks("prepay", message) or "max_prepayment_pct" in (query.get("select") or [])):
        return
    names = await _numbers(db, scope, query, result, ("name",))
    if not names or len(names) > _MAX_DOCS:
        return
    rows = await _rows(db, scope, {"entity": "vendor", "select": ["name", "max_prepayment_pct"],
                                   "where": [{"field": "name", "op": "in", "value": names}]})
    if not rows:
        return
    from app.api.v1.pa import _get_prepayment_config
    system = (await _get_prepayment_config(db)).max_prepayment_pct
    en, zh = [], []
    for r in rows:
        own = r.get("max_prepayment_pct")
        cap, source = effective_cap(own, system)
        if source == "system":
            en.append(f"{r['name']} has no Max Prepayment % of its own, so the system cap in "
                      f"Admin Panel → Prepayment Config applies: {_qty(cap)}%"
                      + (" — it can be prepaid up to that." if cap > 0 else " — it cannot be prepaid."))
            zh.append(f"{r['name']} 没有设置自己的 Max Prepayment %，因此适用系统上限（Admin Panel → "
                      f"Prepayment Config）：{_qty(cap)}%"
                      + ("，可以预付，最多到这个比例。" if cap > 0 else "，不能预付。"))
        else:
            capped = Decimal(str(own)) > Decimal(str(cap))
            en.append(f"{r['name']}: Max Prepayment % {_qty(own)}"
                      + (f", limited to the system cap of {_qty(cap)}%" if capped else "")
                      + (" — it can be prepaid up to that." if cap > 0 else " — it cannot be prepaid."))
            zh.append(f"{r['name']}：Max Prepayment % 为 {_qty(own)}"
                      + (f"，但不能超过系统上限 {_qty(cap)}%" if capped else "")
                      + ("，可以预付，最多到这个比例。" if cap > 0 else "，不能预付。"))
    _from_code(result, "prepayment_cap", {"system_cap_pct": str(system), "vendors": rows})
    _say(result, "prepayment_cap", " ".join(en), "".join(zh))


# ── ISS-0059: invoices of paid PAs ───────────────────────────────────────────

async def _paid_pa_invoices(db, scope, message, query, result) -> None:
    if result.get("entity") != "invoice":
        return
    used = {c.get("field") for c in query.get("where") or []} | set(query.get("select") or [])
    if "pa_payment_shown" not in used:
        return
    where = [c for c in query.get("where") or [] if c.get("field") == "pa_payment_shown"]
    if not where:
        where = [{"field": "pa_payment_shown", "op": "eq", "value": False}]
    # Counted twice rather than grouped: pa_type is a correlated subquery,
    # which Postgres will not group by.
    async def count(extra):
        rows = await _rows(db, scope, {"entity": "invoice", "metrics": ["count"],
                                       "where": [*where, *extra]})
        return int((rows[0] if rows else {}).get("count") or 0)
    total = await count([])
    prepay = await count([{"field": "pa_type", "op": "eq", "value": "prepayment"}])
    regular = total - prepay
    by_kind = {"prepayment": prepay, "other": regular}
    _from_code(result, "invoices_by_pa_kind", by_kind)
    _say(result, "paid_pa_invoice_status",
         f"Paying a PA marks its invoices Paid — or Partially Paid when the PA is a prepayment "
         f"(PAY-005). Of the invoices matched here, {regular} are on regular PAs (they should "
         f"be Paid) and {prepay} on prepayment PAs (they should be Partially Paid).",
         f"PA 付款后，其发票应变为 Paid（已付）；预付款 PA 的发票应为 Partially Paid（部分付款）"
         f"（PAY-005）。这里查到的发票中，{regular} 张在普通 PA 上（应为 Paid），{prepay} 张在预付款 "
         f"PA 上（应为 Partially Paid）。")


# ── ISS-0013 ①: Issued is not "the vendor has it" ────────────────────────────

async def _how_it_was_placed(db, scope, message, query, result) -> None:
    if result.get("entity") != "purchase_order" or not _detail(result):
        return
    if not asks("sent", message):
        return
    numbers = await _numbers(db, scope, query, result, ("number",))
    if not numbers or len(numbers) > _MAX_DOCS:
        return
    rows = await _rows(db, scope, {
        "entity": "purchase_order",
        "select": ["number", "status", "place_order_method", "place_order_email_to", "placed_at"],
        "where": [{"field": "number", "op": "in", "value": numbers}]})
    en, zh = [], []
    for r in rows:
        when = (r.get("placed_at") or "")[:10]
        when = "/".join(reversed(when.split("-"))) if when else ""
        method = r.get("place_order_method")
        if method == "email":
            to = r.get("place_order_email_to") or "the vendor"
            en.append(f"{r['number']}: EPMS records that the order email was sent to {to}"
                      f"{' on ' + when if when else ''}; it does not record that the vendor "
                      f"received or read it.")
            zh.append(f"{r['number']}：EPMS 记录的是订单邮件已于{when + ' ' if when else ''}发送到 "
                      f"{to}，并不记录供应商是否收到或已读。")
        elif method == "online":
            en.append(f"{r['number']}: placed online{' on ' + when if when else ''} and confirmed "
                      f"in EPMS; no email was sent from EPMS.")
            zh.append(f"{r['number']}：{when + ' ' if when else ''}在线下单并在 EPMS 确认，"
                      f"EPMS 没有发送邮件。")
        elif method == "nc" or r.get("status") in ("issued", "partially_received"):
            en.append(f"{r['number']}: status {r.get('status')} means the order was placed"
                      f"{' (in NC)' if method == 'nc' else ''}; EPMS has no record of it being "
                      f"sent to or received by the vendor.")
            zh.append(f"{r['number']}：状态为已下单（Issued）只表示订单已下达"
                      f"{'（在 NC 下单）' if method == 'nc' else ''}，EPMS 没有发送给供应商或供应商已收到的记录。")
        else:
            en.append(f"{r['number']} has not been placed with the vendor yet.")
            zh.append(f"{r['number']} 还没有向供应商下单。")
    if en:
        _say(result, "issued_is_not_received", " ".join(en), "".join(zh))


# ── ISS-0043 / ISS-0066: when a PA was paid, from which account, remittance ──

async def _pa_payment(db, scope, message, query, result) -> None:
    if result.get("entity") != "payment_application" or not _detail(result):
        return
    if not asks("payment", message):
        return
    numbers = await _numbers(db, scope, query, result, ("pa_number",))
    if not numbers or len(numbers) > 3:
        return
    facts = [await payment_facts(db, scope, n) for n in numbers]
    facts = [f for f in facts if f]
    if not facts:
        return
    _from_code(result, "payment", facts)
    said = [payment_sentences(f, remittance=asks("remittance", message)) for f in facts]
    _say(result, "pa_payment", " ".join(s[0] for s in said), "".join(s[1] for s in said))


async def payment_facts(db, scope, pa_number: str) -> dict | None:
    """A PA's payment as Finance recorded it: date, account, net paid, and the
    remittance advice sent for it. Account and remittance need View Finance
    (the Finance → Payments pages); without it they are reported as such."""
    from sqlalchemy import select

    from app.core.access_scope import is_pa_visible
    from app.models.pa import PaymentApplication

    pa = (await db.execute(select(PaymentApplication).where(
        PaymentApplication.pa_number == pa_number))).scalar_one_or_none()
    if pa is None or not await is_pa_visible(db, pa, scope):
        return None
    out = {"pa_number": pa.pa_number, "status": pa.status,
           "paid_on": pa.paid_at.date().isoformat() if pa.paid_at else None}
    if pa.status not in ("processed", "paid"):
        return out
    if not (scope.get("perms") or {}).get("view_finance", False):
        out["finance"] = "needs_view_finance"
        return out
    payments = await _rows(db, scope, {
        "entity": "payment_record",
        "select": ["payment_date", "bank_account.name", "bank_account.account_masked",
                   "payment_method", "amount", "credit_applied", "currency"],
        "where": [{"field": "pa_number", "op": "eq", "value": pa.pa_number}]})
    out["payments"] = payments
    out["remittance"] = await _remittance(db, pa.pa_number)
    return out


async def _remittance(db, pa_number: str) -> list[dict] | None:
    """The remittance advice log (finance-api payment_remittance_notifications)
    for this PA's payment records. None when the log cannot be read."""
    import sqlalchemy as sa

    try:
        async with db.begin_nested():
            rows = (await db.execute(sa.text(
                "SELECT n.status, n.email, n.party_name, n.sent_at, n.error"
                "  FROM payment_remittance_notifications n"
                "  JOIN payment_records p ON n.payment_record_ids @> to_jsonb(ARRAY[p.id::text])"
                " WHERE p.pa_number = :pa AND n.recipient_kind = 'vendor'"
                " ORDER BY n.sent_at NULLS LAST"), {"pa": pa_number})).mappings().all()
    except Exception:  # noqa: BLE001 — a missing log is "cannot see it", not an error
        log.info("answer_facts | remittance log not readable", exc_info=True)
        return None
    return [{"status": r["status"], "email": r["email"], "party_name": r["party_name"],
             "sent_at": cq._serialise(r["sent_at"]), "error": r["error"]} for r in rows]


def _dmy(iso: str | None) -> str:
    if not iso:
        return ""
    day, _, clock = str(iso).partition("T")
    out = "/".join(reversed(day.split("-")))
    return f"{out} {clock[:5]}" if clock else out


def payment_sentences(f: dict, remittance: bool = True) -> tuple[str, str]:
    n = f["pa_number"]
    if f["status"] not in ("processed", "paid"):
        return (f"{n} has not been paid yet (status {f['status']}).",
                f"{n} 尚未付款（状态 {f['status']}）。")
    en = [f"{n} was paid on {_dmy(f['paid_on'])}."]
    zh = [f"{n} 已于 {_dmy(f['paid_on'])} 付款。"]
    if f.get("finance") == "needs_view_finance":
        en.append("The funding account and the remittance advice are on Finance's payment "
                  "record, which needs View Finance access — ask Finance.")
        zh.append("付款账户和汇款通知记录在 Finance 的付款记录里，需要 View Finance 权限才能查看，请联系财务。")
        return " ".join(en), "".join(zh)
    for p in f.get("payments") or []:
        account = " ".join(x for x in (p.get("bank_account.name"), p.get("bank_account.account_masked")) if x)
        credit = Decimal(p.get("credit_applied") or 0)
        en.append(f"Paid from {account or 'an account not recorded'} ({p.get('payment_method') or ''}), "
                  f"{p.get('currency') or ''} {_money(p.get('amount'))}"
                  + (f" after {p.get('currency') or ''} {_money(credit)} of vendor credits" if credit else "") + ".")
        zh.append(f"付款账户：{account or '未记录'}（{p.get('payment_method') or ''}），实付 "
                  f"{p.get('currency') or ''} {_money(p.get('amount'))}"
                  + (f"（已扣除供应商贷项 {p.get('currency') or ''} {_money(credit)}）" if credit else "") + "。")
    if remittance:
        sent = f.get("remittance")
        if sent is None:
            en.append("Whether the remittance advice was sent cannot be read here.")
            zh.append("这里读不到汇款通知是否已发送。")
        elif not sent:
            en.append("No remittance advice has been sent for this payment (Finance sends it by hand).")
            zh.append("这笔付款还没有发送汇款通知（汇款通知由财务手动发送）。")
        else:
            for s in sent:
                if s["status"] == "sent":
                    en.append(f"Remittance advice sent to {s['email']} on {_dmy(s['sent_at'])}.")
                    zh.append(f"汇款通知已于 {_dmy(s['sent_at'])} 发送到 {s['email']}。")
                else:
                    en.append(f"Remittance advice to {s['email']} failed: {s.get('error') or s['status']}.")
                    zh.append(f"发送到 {s['email']} 的汇款通知失败：{s.get('error') or s['status']}。")
    return " ".join(en), "".join(zh)


# ── ISS-0015 ①: who requested the purchase behind an invoice ─────────────────

async def _invoice_requester(db, scope, message, query, result) -> None:
    if result.get("entity") != "invoice" or not _detail(result):
        return
    if not asks("requester", message):
        return
    numbers = await _numbers(db, scope, query, result, ("internal_ref",))
    if not numbers or len(numbers) > 3:
        return
    rows = await _rows(db, scope, {
        "entity": "invoice",
        "select": ["internal_ref", "po_number", "order.pr_number",
                   "order.originating_pr.requester_full_name"],
        "where": [{"field": "internal_ref", "op": "in", "value": numbers}]})
    en, zh = [], []
    for r in rows:
        who, pr = r.get("order.originating_pr.requester_full_name"), r.get("order.pr_number")
        if who and pr:
            en.append(f"{r['internal_ref']} bills {r['po_number']}, raised from {pr}; the "
                      f"requester on {pr} is {who}.")
            zh.append(f"{r['internal_ref']} 对应 {r['po_number']}，来自请购单 {pr}；{pr} 的申请人"
                      f"（Requester）是 {who}。")
        elif r.get("po_number") and not pr:
            en.append(f"{r['internal_ref']} bills {r['po_number']}, which has no EPMS purchase "
                      f"request behind it, so there is no requester to name.")
            zh.append(f"{r['internal_ref']} 对应 {r['po_number']}，该 PO 没有 EPMS 请购单，所以没有申请人。")
    if en:
        _say(result, "invoice_requester", " ".join(en), "".join(zh))


_SUPPLEMENTS = (_receipt_lines, _po_match, _agreement_ceiling, _prepayment_cap,
                _paid_pa_invoices, _how_it_was_placed, _pa_payment, _invoice_requester)
_PLAIN = (_year_on_year, _did_you_mean)


async def supplement(db, scope: dict, message: str, query: dict, result: dict) -> dict | None:
    """Add what the code knows to `result`. Returns a whole reply instead when
    the code can write it alone (a part with no PO lines)."""
    if result.get("denied"):
        return None
    fixed = await _part_without_orders(db, scope, message, query, result)
    if fixed:
        return fixed
    for add in _PLAIN:
        add(db, scope, message, query, result)
    for add in _SUPPLEMENTS:
        try:
            await add(db, scope, message, query, result)
        except Exception:  # noqa: BLE001 — a supplement must never cost the answer
            log.exception("answer_facts | %s failed", add.__name__)
    return None


__all__ = ["supplement", "with_year_on_year", "payment_facts", "payment_sentences",
           "asks", "doc_numbers"]
