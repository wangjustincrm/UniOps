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
sentences to `said_with_this_list`, which the chat layer writes with the
reply (assistant_chat._with_listing_notes, the ISS-0008 mechanism) — so the
statement stands even when the narrator leaves it out.

Standing next to the narrator's sentence was not enough when that sentence
said the opposite: "3-way match 结果：未匹配" above "三单匹配：已通过" (ISS-0036),
"差异为 0.00" above "少 22.60" (ISS-0016), "结果中未包含收货数量" above the
quantities (ISS-0031). So a fact also names, from its own value, the sentences
that contradict it (`_overrule`), and `settle` takes those out of the reply
and puts the fact first, as the conclusion. With the narrator saying the
opposite on every point, the reply carries only what the code worked out.

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
from app.core.display_dates import text_date, text_datetime
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


# ── What the code worked out overrules what the narrator wrote ───────────────

def _overrule(result: dict, key: str, patterns, *, first: bool = True,
              opening: bool = False) -> None:
    """The fact `key` is the conclusion: said before the reply (`first`), and
    the narrator's sentences matching `patterns` — the ones that contradict
    THIS value of the fact — taken out. `opening`: the question is a yes/no
    the fact answers, so the narrator's own opening yes/no goes too."""
    o = result.setdefault("overruled_by_code", {}).setdefault(
        key, {"first": first, "opening": opening, "drop": []})
    o["drop"].extend(p for p in patterns if p not in o["drop"])


def settle(text: str, result: dict) -> tuple[str, set]:
    """The narrator's reply with what the code contradicts taken out; and the
    keys of the facts to put before it."""
    from app.services import rule_verdicts

    over = result.get("overruled_by_code") or {}
    if any(o["opening"] for o in over.values()):
        text = rule_verdicts.without_opening_verdict((text or "").strip())
    text = rule_verdicts.without_sentences(text, [p for o in over.values() for p in o["drop"]])
    text = _with_catalog_currency(text, result)
    return text, {k for k, o in over.items() if o["first"]}


# ISS-0057: the vendor summary is figures. "总体合作稳定，完成率很高" and "是一个
# 活跃的供应商……付款情况良好" are opinions the narrator was told not to add and
# added; the clauses that carry one are taken out by code.
_OPINION = re.compile(
    r"合作(关系)?\S{0,2}(稳定|良好|顺利|密切|愉快|健康|紧密)|(稳定|良好|长期|密切|健康)的合作"
    r"|完成率\S{0,2}(很|较|非常|相当)?高|活跃的?(供应商|合作伙伴)|(较为|比较|非常|很)?活跃"
    r"|(付款|交付|交货|履约|表现|合作|信用)(情况|记录|状况)?\S{0,2}(良好|很好|不错|优秀|稳定|正常|健康)"
    r"|值得信赖|可靠的?(供应商|合作伙伴)|重要的?(供应商|合作伙伴)"
    r"|\b(stable|strong|good|healthy|solid|reliable|long-standing|active|important|trusted)\s+"
    r"(relationship|partner|partnership|vendor|supplier|business|history|record)"
    r"|\bpays?\s+(well|on time|promptly|reliably)|\bpayments?\s+(are|is|have been)\s+(good|healthy|on time)"
    r"|\bin good standing\b|\bperforming well\b|\bhigh completion rate\b|\b(very|quite) active\b",
    re.I)
_FILLER = re.compile(r"^\W*(总体|整体|总的|综合)(来看|来说|而言|上)?\W*$|^\W*(总之|另外|此外)\W*$"
                     r"|^\W*(overall|in summary|in general|all in all|generally)\W*$", re.I)


def without_opinions(text: str) -> str:
    out = []
    for line in (text or "").split("\n"):
        # At a comma that ends a clause — not the one in 325,033.75.
        clauses = [c for c in re.split(r"(?<=[，。！？!?；;])|(?<=[,.])(?=\s)", line) if c]
        kept = [c for c in clauses if not _OPINION.search(c)]
        if len(kept) == len(clauses):
            out.append(line)
            continue
        kept = [c for c in kept if not _FILLER.match(c)]
        rest = "".join(kept).rstrip(" ，,；;")
        if kept and kept[0] is not clauses[0]:
            rest = rest.lstrip()
        if rest.strip(" \t-*•—:："):
            out.append(rest if rest[-1] in "。！？!?.:：|" else rest + ("。" if not rest.isascii() else "."))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


# ISS-0044: the Parts Catalog keeps its prices in CAD (the page heads the
# column "Unit Price (CAD)"; Part has no currency of its own). A catalog price
# the narrator wrote as a bare number gets the currency.
_CURRENCY_BEFORE = re.compile(r"(?:CAD|CA\$|C\$|USD|US\$|EUR|CNY|RMB|€|¥)\s*\$?\s*$", re.I)
_CURRENCY_AFTER = re.compile(r"^\s*(?:CAD|USD|EUR|CNY|RMB|加元|加币|美元)", re.I)
CATALOG_CURRENCY = "CAD"


def _with_catalog_currency(text: str, result: dict) -> str:
    prices = result.get("catalog_prices") or []
    if not text or not prices:
        return text
    for price in sorted(set(prices), key=len, reverse=True):
        d = Decimal(price)
        spelled = {f"{d:,.2f}", f"{d:.2f}"}
        for s in sorted(spelled, key=len, reverse=True):
            rx = re.compile(r"(?<![\d.,])\$?" + re.escape(s) + r"(?![\d])")
            pos, out = 0, []
            for m in rx.finditer(text):
                before, after = text[:m.start()], text[m.end():]
                out.append(text[pos:m.start()])
                if (_CURRENCY_BEFORE.search(before) or _CURRENCY_AFTER.match(after)
                        or (m.group(0).startswith("$") and re.search(r"(?:CA|C|US|A)$", before))):
                    out.append(m.group(0))
                else:
                    out.append(f"{CATALOG_CURRENCY} {s}")
                pos = m.end()
            out.append(text[pos:])
            text = "".join(out)
    return text


def _catalog_prices(db, scope, message, query, result) -> None:
    if result.get("entity") != "part":
        return
    prices = [str(r["unit_price"]) for r in result.get("rows") or []
              if isinstance(r, dict) and r.get("unit_price") not in (None, "")]
    if prices:
        result["catalog_prices"] = prices


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
    if result.get("entity") not in ("goods_receipt", "gr_line"):
        return      # only receipts have receipt numbers
    if not (_detail(result) and 0 < len(rows) <= _MAX_DOCS):
        return
    # A GR page's own lines (page_document points the planner at gr_line) are
    # named by their receipt's number.
    field = "receipt.number" if result.get("entity") == "gr_line" else "number"
    numbers = await _numbers(db, scope, query, result, (field,))
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
        _overrule(result, "receipt_lines", _NO_QUANTITIES)


# "结果中未包含每次收货的数量明细。如需了解具体收货数量，请查看收货单的详情" (ISS-0031),
# "系统中没有返回收货数量的信息" (ISS-0068) — said over the lines code read.
_NO_QUANTITIES = (
    r"(没有|未|不|并未|无)(包含|显示|返回|提供|记录|列出|给出|含)\S{0,15}(数量|明细|收了多少)",
    r"(数量|明细)\S{0,10}(不可用|不详|未知|没有(包含|显示|返回|提供|记录)?|未(包含|显示|返回|提供|记录))",
    r"数量字段", r"(如需|如要|如果需要|若需|想)(了解|知道|查看)?\S{0,10}(数量|明细)",
    r"请(到|在)?\S{0,4}(查看|打开)\S{0,25}(收货单|GR|详情)",
    r"\b(does not|doesn't|do not|don't|did not|didn't|not)\s+(include|show|contain|return|provide|list"
    r"|have|give)\w*\b[^.]{0,40}\bquantit",
    r"\bno\s+(received\s+|receipt\s+)?quantit",
    r"\bquantit(y|ies)\b[^.]{0,30}\b(not (included|shown|available|returned|provided|recorded)"
    r"|unavailable|missing)\b")


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
    drop: list[str] = []
    if all(v["three_way_matched"] for v in views):
        drop += _NOT_MATCHED
    elif not any(v["three_way_matched"] for v in views):
        drop += _MATCHED
    if all(Decimal(v["difference_pre_tax"]) != 0 or v["lines_not_invoiced"] for v in views):
        drop += _NO_DIFFERENCE
    if all(v["invoices"] for v in views):
        drop += _NO_INVOICE_DATA
    _overrule(result, "po_vs_invoices", drop, opening=True)


# Said over a PO that code found three-way matched (ISS-0036): "3-way match 结果：
# 未匹配 (Not Matched)", "三方匹配…未成功…结果为否定", "未完成匹配（three_way_matched:
# false）", "因为 PO 部分收货所以不通过".
_NOT_MATCHED = [
    r"(未|没有|不|尚未|无法|没能)(能)?(成功)?(完成)?(三单|三方|3-?way)?\s*匹配",
    r"匹配[^。.!?\n]{0,8}(失败|未通过|不通过|没有通过|未成功|没有成功|未完成|为否|否定|不成功)",
    r"(三单|三方|3-?way)[^。.!?\n]{0,12}(失败|未通过|不通过|没有通过|未成功|未完成|为否|否定)",
    r"\bnot\s+(been\s+|yet\s+)?(three[- ]way\s+|3-?way\s+|fully\s+)?matched\b", r"\bunmatched\b",
    r"three_way_matched\W{0,4}(false|否)",
    r"\bmatch\w*\b[^.]{0,30}\b(failed|fails|negative|false|not passed|did not pass)\b",
    r"结果\S{0,4}(为否|否定)",
    # ... and the reason given for the failure that is not one.
    r"^\W*(原因|理由|因为|由于|这是因为)[^。.!?]{0,40}(部分收货|差\S{0,3}\s*\d|差异|数量)",
    r"^\W*(because|this is because|the reason)\b[^.]{0,60}\b(partially received|difference|gap|quantit)"]
# ... and over one that is not.
_MATCHED = [
    r"(三单|三方|3-?way)\s*匹配\S{0,6}(已|成功|通过|完成)(?!.*(未|没|不))",
    r"(已|成功)(经)?(完成|通过)?\S{0,2}(三单|三方|3-?way)\s*匹配",
    r"\b(is|are|was|been|has been)\s+(fully\s+)?(three[- ]way|3-?way)\s+matched\b"]
# Said over a PO with an uninvoiced line or a difference (ISS-0016): "差异为
# 0.00…与采购订单完全匹配".
_NO_DIFFERENCE = [
    r"差异\S{0,4}?\s*(CAD|CA\$|\$)?\s*(?<![\d.,])0(\.00?)?(?![\d])",
    r"完全(匹配|一致|相同|吻合)|没有(任何)?差异|无差异|零差异|差异为零|一模一样",
    r"\bno (difference|variance|discrepancy)\b|\bexact(ly)?\s+match|\bfully matched\b"
    r"|\bmatch(es)? exactly\b|\b(difference|variance)\s+(of|is)\s+(CAD\s*)?0(\.00?)?(?![\d])"]
# Said over a PO whose invoices code found (ISS-0016 before: "没有发票数据").
_NO_INVOICE_DATA = [
    r"(没有|未|不)(包含|显示|返回|提供|查到|找到)?\S{0,8}发票(的)?(数据|信息|记录)",
    r"(没有|未)(找到|查到)\S{0,6}发票(?!号)", r"\bno invoice (data|information|records?)\b",
    r"(请|需要)(您)?提供\S{0,10}发票"]


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
        fmt = text_date
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
    from app.services.rule_facts import _CAN_PREPAY, _CANNOT_PREPAY
    caps = [effective_cap(r.get("max_prepayment_pct"), system)[0] for r in rows]
    # "最大预付款百分比为空，这表示没有建立预付款政策" over a 100% system cap (ISS-0066).
    drop = _CANNOT_PREPAY if all(c > 0 for c in caps) else (
        _CAN_PREPAY if all(c == 0 for c in caps) else ())
    _overrule(result, "prepayment_cap", drop, opening=True)


# ── ISS-0059: invoices of paid PAs ───────────────────────────────────────────

async def _paid_pa_invoices(db, scope, message, query, result) -> None:
    if result.get("entity") != "invoice":
        return
    used = {c.get("field") for c in query.get("where") or []} | set(query.get("select") or [])
    if "pa_payment_shown" not in used:
        return
    # The invoices this query matched — its own filters (a vendor, a period
    # ...), not every invoice in the system.
    where = list(query.get("where") or [])
    if not any(c.get("field") == "pa_payment_shown" for c in where):
        where = [*where, {"field": "pa_payment_shown", "op": "eq", "value": False}]
    # Counted twice rather than grouped: pa_type is a correlated subquery,
    # which Postgres will not group by.
    async def count(extra):
        rows = await _rows(db, scope, {"entity": "invoice", "metrics": ["count"],
                                       "where": [*where, *extra],
                                       **({"period": query["period"]} if query.get("period") else {})})
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
    if prepay == 0:
        # "没有将它们标记为部分已付，这可能是数据不一致" (ISS-0059): none is on a
        # prepayment PA, so not one of them should be Partially Paid.
        _overrule(result, "paid_pa_invoice_status", [
            r"^(?=.*(部分已?付|部分付款|部分支付|partially[ _-]?paid))"
            r"(?=.*(不一致|异常|没有|未|不是|inconsisten|\bnot\b|n't|mismatch|wrong))"], first=False)


# ── ISS-0013 ①: Issued is not "the vendor has it" ────────────────────────────

async def _how_it_was_placed(db, scope, message, query, result) -> None:
    if result.get("entity") != "purchase_order" or not _detail(result):
        return
    if not asks("sent", message):
        return
    numbers = await _numbers(db, scope, query, result, ("number",))
    if not numbers or len(numbers) > _MAX_DOCS:
        return
    await say_how_placed(db, scope, numbers, result)


# "是的，已经发给供应商了" over a PO EPMS only records as Issued (ISS-0013 ①).
_SENT_TO_VENDOR = (
    r"(已经?|已)(经)?(发|发送|寄|送|传|邮寄|转)(给|到|至|达)(了)?\S{0,4}(供应商|厂商|厂家|对方|vendor|supplier)",
    r"(供应商|厂商|厂家|对方)\S{0,4}(已经?|已)(经)?(收到|接收|确认|签收|拿到|收悉)",
    r"(已经?|已)(经)?(送达|发出|寄出)", r"送达(了)?供应商",
    r"\b(has|have|was|were|been)\s+(already\s+)?(sent|delivered|emailed|transmitted|issued|forwarded)"
    r"\s+to\s+the\s+(vendor|supplier)",
    r"\b(vendor|supplier)\s+(has\s+|have\s+)?(already\s+)?(received|got|acknowledged|confirmed)\b")


async def say_how_placed(db, scope, numbers: list[str], result: dict) -> None:
    """How each PO was placed, said first; a sentence saying the vendor has
    it is taken out — EPMS records at most that an email was sent."""
    rows = await _rows(db, scope, {
        "entity": "purchase_order",
        "select": ["number", "status", "place_order_method", "place_order_email_to", "placed_at"],
        "where": [{"field": "number", "op": "in", "value": numbers}]})
    en, zh = [], []
    for r in rows:
        when = text_date(r.get("placed_at"))
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
        _overrule(result, "issued_is_not_received", _SENT_TO_VENDOR, opening=True)


# ── ISS-0043 / ISS-0066: when a PA was paid, from which account, remittance ──

async def _pa_payment(db, scope, message, query, result) -> None:
    if result.get("entity") != "payment_application" or not _detail(result):
        return
    if not asks("payment", message):
        return
    numbers = await _numbers(db, scope, query, result, ("pa_number",))
    if not numbers or len(numbers) > 3:
        return
    await say_payment(db, scope, numbers, message, result)


async def say_payment(db, scope, numbers: list[str], message: str, result: dict) -> None:
    """When each PA was paid, from which account, and its remittance advice,
    said first; the narrator's sentences that deny what was read go."""
    facts = [await payment_facts(db, scope, n) for n in numbers]
    facts = [f for f in facts if f]
    if not facts:
        return
    _from_code(result, "payment", facts)
    remittance = asks("remittance", message)
    said = [payment_sentences(f, remittance=remittance) for f in facts]
    _say(result, "pa_payment", " ".join(s[0] for s in said), "".join(s[1] for s in said))
    paid = [f for f in facts if f["status"] in ("processed", "paid")]
    drop: list[str] = []
    if len(paid) == len(facts):
        drop += _NO_PAY_DATE
        # The account was read, or code says it needs View Finance (ISS-0043:
        # "数据中没有" three times).
        if all(f.get("finance") == "needs_view_finance" or any(
                p.get("bank_account.name") or p.get("bank_account.account_masked")
                for p in f.get("payments") or []) for f in facts):
            drop += _NO_ACCOUNT
        if remittance and all(f.get("finance") != "needs_view_finance" for f in facts):
            sent = [any(s["status"] == "sent" for s in f.get("remittance") or []) for f in facts]
            # ISS-0066 ②: "是的，汇款通知已经发给供应商了" from the payment alone.
            drop += _NO_REMITTANCE_SENT if all(sent) else (
                _REMITTANCE_SENT if not any(sent) else [])
        elif remittance:
            drop += _REMITTANCE_SENT
    elif not paid:
        drop += _PAID
    _overrule(result, "pa_payment", drop, opening=remittance)


_NO_PAY_DATE = [
    r"(没有|未|不)(包含|显示|提供|记录|返回)\S{0,8}(付款|支付)(时间|日期)",
    r"(付款|支付)(时间|日期)\S{0,8}(不可用|不详|没有|未(包含|显示|提供|记录))",
    r"没有找到\S{0,25}(付款|支付)", r"\b(no|not|n't)\b[^.]{0,30}\b(payment|paid) (date|time)\b"]
_NO_ACCOUNT = [
    r"(没有|未|不|无)(包含|显示|提供|记录|返回|列出)?\S{0,10}(账户|账号|银行)",
    r"(账户|账号)\S{0,10}(不可用|无法|不详|不明|未知|没有|未(包含|显示|提供|记录))",
    r"\b(no|not|n't)\b[^.]{0,40}\b(account|bank)\b",
    r"\b(account|bank)\b[^.]{0,30}\b(not (available|included|shown|recorded|known)|unavailable|unknown)\b"]
_REMITTANCE_SENT = [
    r"汇款(通知|单)?\S{0,8}(已经?|已)(经)?(发|发送|寄|送)", r"(已经?|已)(经)?(发|发送)\S{0,6}汇款",
    r"\bremittance\b[^.]{0,40}\b(has been|was|been|were)\s+(sent|emailed|delivered)\b",
    r"\b(sent|emailed)\b[^.]{0,20}\bremittance\b"]
_NO_REMITTANCE_SENT = [
    r"汇款(通知|单)?\S{0,8}(没有|未|尚未|还没)(有)?(发|发送)", r"(没有|未|尚未)(发|发送)\S{0,6}汇款",
    r"\bno remittance\b", r"\bremittance\b[^.]{0,40}\b(not|n't)\s+(been\s+)?(sent|emailed)\b"]
_PAID = [r"(?<![未没])已经?(付款|支付|付清)", r"\b(was|has been|have been)\s+paid\b"]


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
    # Name kept for its callers; the format is the on-screen one (UI-DATE-002).
    return text_datetime(iso)


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
_PLAIN = (_year_on_year, _did_you_mean, _catalog_prices)


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
           "say_payment", "say_how_placed", "settle", "without_opinions", "asks", "doc_numbers"]
