"""Replies the code writes whole, from the data, for questions the narrator
kept getting wrong even with the right rows in front of it (the 11-11 follow-up, the
points left over from round 2).

The bar (user, 11-10): with Haiku not cooperating at all the reply must still
be right, and it must never carry a sentence that contradicts the code's
facts. Appending the right figure under a wrong one does not meet it, so
each question here gets a reply written entirely by code — no narrator:

  * "供应商 Amazon 还有多少可用贷项？" asked three times: 14 credits each time,
    totals 600.20 / 600.19 / 570.09, the page CAD 570.11. The narrator was
    adding up the rows itself (ISS-0021 ④). Count and total per currency are
    now summed by the controlled query (the Remaining column the Vendor
    Credits → Available tab shows).
  * "PA-20261016-0005 扣掉贷项后实际付多少？" — "数据中并未显示有贷项", then on
    the follow-up "Amazon 没有任何可用贷项", against 14 on the page. A PA that
    is not paid yet has no credits on it: they are netted when the payment
    is executed (VC-005 / PAY-002). Said, with the vendor's available credits
    in the PA's currency (ISS-0056).
  * "PO-576-2610-01 是邮件下单还是在线下单？发给了谁？什么时候发的？" — "下单方式…
    没有记录", the recipient given as the vendor's name, while the History
    tab says "Issue by procurement officer — Forough Salehi — Order placed
    online". The message also splits into three parts, the last two with no
    PO in them. Answered whole from the PO's placement record and its issue
    event (ISS-0045). The email subject is not stored (decided 21/10).
  * "有几个承运商？" — counted on Freight → Carriers (ISS-0021 ③).

And for lists the narrator writes itself, `complete_listing` makes the list
as long as the count it was given (ISS-0050: "18 家" over a table of 17).

Every read goes through the caller's gates: documents via
document_chain.find (the entity's permission and row scope), figures via the
controlled query (both gates). What the caller may not see is left to the
ordinary route, which says so.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from app.core import doc_numbers
from app.core.delegation import PLANT_TIMEZONE
from app.services import controlled_query as cq
from app.services import code_answers, document_chain, question_routes, rule_registry

log = logging.getLogger(__name__)

_PLANT_TZ = ZoneInfo(PLANT_TIMEZONE)

# ── Recognising the questions ────────────────────────────────────────────────

_CREDIT = re.compile(r"贷项|贷记|\bvendor credits?\b|\bcredit(?:s|\s+notes?)?\b", re.I)
# A question about a vendor's credits that asks what there is — not "what do I
# need to record a manual credit" (a rule card) or "how are they netted".
_CREDIT_WHAT = re.compile(
    r"多少|哪些|哪几|几张|几笔|有没有|是否有|有.{0,6}吗|可用|余额|剩|还有|明细|列出|"
    r"\bhow (?:much|many)\b|\bwhich\b|\bwhat credits\b|\bavailable\b|\bremaining\b|"
    r"\blist\b|\bany\b", re.I)
_HOW = re.compile(r"怎么|如何|规则|\bhow (?:are|is|do|does)\b|\brule", re.I)
# Which credits a vendor question is about; available unless it says otherwise.
_CREDIT_STATUS = (
    ("pending_review", re.compile(r"待审|审核|pending", re.I)),
    ("void", re.compile(r"作废|\bvoid", re.I)),
    ("exhausted", re.compile(r"用完|用尽|exhausted|used up", re.I)),
)
_PLACED = re.compile(
    r"邮件下单|在线下单|网上下单|下单方式|怎么下的单|如何下的单|谁下的单|"
    r"什么时候下(?:的)?单|哪天下(?:的)?单|下单时间|"
    r"发给了?谁|发给哪|发到哪|发送给|收件人|什么时候发|何时发|几时发|哪天发|发送时间|"
    r"\bplaced\b|\bplace(?:d)? (?:the )?order|\bby e-?mail\b|\bonline\b|\bsent\b|\brecipient\b",
    re.I)
# A rule question that happens to use placing words (round 2 misroute check).
_RULE_ASKED = re.compile(r"谁可以|谁能|能不能|可不可以|怎么|如何|\bwho can\b|\bcan i\b|\bhow (?:do|can|to)\b", re.I)
# About this page's order itself, when only the page names the PO.
_PLACED_HERE = re.compile(
    r"(?:这张|这个|该|此|本)\s*(?:PO|单|订单|采购订单)|邮件下单|在线下单|网上下单|下单方式|谁下的单|"
    r"发给了?谁|收件人|\bthis (?:po|order)\b", re.I)


_CARRIERS = re.compile(
    r"^(?:系统里|系统中|现在|目前|一共|总共|freight\s*里)?\s*(?:有|共有)?\s*(?:几|多少|哪些|哪几)个?"
    r"(?:快递)?(?:承运商|快递公司)|^\s*(?:how many|which|what) (?:courier )?carriers\b",
    re.I)


def _lang(message: str) -> str:
    return code_answers.lang_of(message)


def _money(value) -> str:
    return f"{Decimal(value or 0):,.2f}"


def _dmy(value) -> str:
    return "/".join(reversed(str(value)[:10].split("-"))) if value else ""


def _local(moment: datetime | None) -> str:
    """A moment as the pages show it: plant time (America/Toronto)."""
    if moment is None:
        return ""
    if moment.tzinfo is not None:
        moment = moment.astimezone(_PLANT_TZ)
    return moment.strftime("%d/%m/%Y %H:%M")


async def _rows(db, scope, query: dict) -> list[dict] | None:
    """The controlled query's rows, or None when the caller may not see them."""
    try:
        out = await cq.execute(db, query, scope)
    except cq.QueryRejected:  # pragma: no cover — the queries here are fixed
        log.exception("fact_replies | fixed query rejected | %s", query)
        return None
    return None if out.get("denied") else (out.get("rows") or [])


# ── Entry point ──────────────────────────────────────────────────────────────

async def answer(db, scope: dict, message: str, context: dict | None,
                 history: list | None = None, whole: bool = True) -> dict | None:
    """The code's whole reply, or None to let the question take its route.

    `whole`: the message as typed (not one part of a split one). A message
    that asks several things is left to be split — except a PO placement
    question, whose parts ("发给了谁？", "什么时候发的？") name no PO of their own.
    """
    numbers = doc_numbers.find_all(message)
    parts = question_routes.split(message) if whole else [message]
    if len(parts) >= 2:
        if all(_PLACED.search(p) for p in parts):
            return await _placement(db, scope, message, numbers, context)
        return None
    if _PLACED.search(message) and (out := await _placement(db, scope, message, numbers,
                                                            context)) is not None:
        return out
    if _CREDIT.search(message) and not rule_registry.pinned_for(message):
        pas = [n for p, n, *_ in numbers if p == "PA"]
        others = [n for p, n, *_ in numbers if p != "PA"]
        if others:
            return None
        if len(pas) == 1:
            return await _pa_credits(db, scope, message, pas[0])
        if not pas:
            return await _vendor_credits(db, scope, message, history)
        return None
    if not numbers and _CARRIERS.search(message.strip()):
        return await _carriers(db, scope, message)
    return None


# ── ISS-0045: how a PO was placed, to whom, when ─────────────────────────────

async def _placement(db, scope, message, numbers, context) -> dict | None:
    from sqlalchemy import select

    from app.crud.current_step import role_label
    from app.models.approval import ApprovalEvent
    from app.models.user import User

    pos = [n for p, n, *_ in numbers if p == "PO"]
    if len(numbers) != len(pos) or len(pos) > 1:
        return None
    if _RULE_ASKED.search(message):
        return None     # "谁可以执行 Place Order？" is a rule question, not this PO's record
    if not pos and (context or {}).get("doc_type") == "po" and (context or {}).get("doc_number"):
        # Only the open page names the PO: the question must be about this
        # order's placing ("每日摘要邮件什么时候发？" asked on a PO page is not).
        if not _PLACED_HERE.search(message):
            return None
        pos = [context["doc_number"]]
    if not pos:
        return None
    po, _why = await document_chain.find(db, scope, "po", pos[0])
    if po is None:
        return None     # "not found / not yours" is said by the ordinary route
    lang = _lang(message)
    event = (await db.execute(
        select(ApprovalEvent, User.full_name)
        .outerjoin(User, User.id == ApprovalEvent.actor_id)
        .where(ApprovalEvent.document_type == "po", ApprovalEvent.document_id == po.id,
               ApprovalEvent.action == "issue")
        .order_by(ApprovalEvent.created_at.desc()).limit(1))).first()
    who, role = None, None
    if event is not None:
        who, role = event[1], role_label(event[0].actor_role) if event[0].actor_role else None
    when = _local(po.placed_at or (event[0].created_at if event is not None else None))
    method = po.place_order_method
    n = po.number
    by_en = f" by {who}" + (f" ({role})" if role else "") if who else ""
    by_zh = f"由 {who}" + (f"（{role}）" if role else "") if who else ""
    if method == "email":
        to = po.place_order_email_to
        en = (f"{n} was placed by email{by_en} on {when}. The order email was sent to "
              f"{to or '(no address recorded)'} at that time ({when}, plant time). EPMS does "
              f"not record whether the vendor received or read it, and does not keep the "
              f"email's subject.")
        zh = (f"{n} 是邮件下单：{when} {by_zh}在 EPMS 下单，订单邮件当时（{when}，工厂本地时间）"
              f"发送到 {to or '（未记录地址）'}。EPMS 不记录供应商是否收到或已读，也不保存邮件主题。")
    elif method == "online":
        ref = po.place_order_reference
        en = (f"{n} was placed online{by_en} on {when} (History: \"Order placed online\""
              + (f", portal reference {ref}" if ref else "") + "). An online order is placed on "
              f"the vendor's own portal and only confirmed in EPMS, so EPMS sent no email — "
              f"there is no recipient or send time.")
        zh = (f"{n} 是在线下单：{when} {by_zh}在 EPMS 确认下单（History 记录为 \"Order placed online\""
              + (f"，门户订单参考号 {ref}" if ref else "") + "）。在线下单是在供应商自己的网站/门户上下单，"
              f"EPMS 只做确认，没有发送邮件，所以没有收件人和发送时间。")
    elif method == "nc":
        en = (f"{n} was placed in NC (an order mirrored from NC){' on ' + when if when else ''}; "
              f"EPMS sent no email for it, so there is no recipient or send time in EPMS.")
        zh = (f"{n} 是在 NC 下单的（从 NC 同步过来的订单）{'，下单时间 ' + when if when else ''}；"
              f"EPMS 没有为它发送邮件，所以 EPMS 里没有收件人和发送时间。")
    elif po.status in ("draft", "submitted", "in_review", "approved", "returned", "rejected"):
        en = (f"{n} has not been placed with the vendor yet (status {po.status}), so it has no "
              f"placement method, recipient or send time.")
        zh = f"{n} 还没有向供应商下单（状态 {po.status}），所以还没有下单方式、收件人和发送时间。"
    else:
        en = (f"{n} (status {po.status}) has no EPMS placement record — no method, recipient "
              f"or send time was recorded for it (for example an order carried over from the "
              f"old system).")
        zh = (f"{n}（状态 {po.status}）在 EPMS 里没有下单记录——没有记录下单方式、收件人或发送时间"
              f"（例如从旧系统迁移过来的订单）。")
    return code_answers.reply(zh if lang == "zh" else en, lang, kind="answer",
                              sources={"document": n, "complete": True})


# ── ISS-0021 ④ / ISS-0056: vendor credits, summed by the query ───────────────

async def _vendor_names_in(db, message: str) -> list[str]:
    """Vendor names written in the message: a credit's vendor first, else one
    of the vendor master. Longest first, so "Amazon Canada" beats "Amazon"."""
    text = (message or "").lower()
    found: list[str] = []
    for sql in ("SELECT DISTINCT vendor_name AS n FROM vendor_credits",
                "SELECT name AS n FROM business_partners WHERE is_supplier"):
        try:
            async with db.begin_nested():
                names = (await db.execute(sa.text(
                    f"SELECT n FROM ({sql}) s WHERE length(n) >= 3 "
                    "AND strpos(:m, lower(n)) > 0 ORDER BY length(n) DESC"),
                    {"m": text})).scalars().all()
        except Exception:  # noqa: BLE001 — a table finance owns may be absent
            log.info("fact_replies | vendor names not readable", exc_info=True)
            continue
        if names:
            found = list(names)
            break
    return found[:1]


async def _credit_figures(db, scope, vendor: str, currency: str | None = None) -> dict | None:
    """Available count and Remaining total per currency, the status counts, and
    the credits in `status` — all from the controlled query."""
    where = [{"field": "vendor_name", "op": "eq", "value": vendor, "because": "vendor"}]
    if currency:
        where.append({"field": "currency", "op": "eq", "value": currency, "because": "currency"})
    by_status = await _rows(db, scope, {"entity": "vendor_credit", "group_by": ["status"],
                                        "metrics": ["count"], "where": where})
    if by_status is None:
        return None
    available = await _rows(db, scope, {
        "entity": "vendor_credit", "metrics": ["count", "remaining"],
        "where": [*where, {"field": "status", "op": "eq", "value": "available",
                           "because": "available"}]})
    return {"vendor": vendor, "by_status": {r["status"]: int(r["count"]) for r in by_status},
            "available": [r for r in available or [] if int(r.get("count") or 0)]}


async def _credit_list(db, scope, vendor: str, status: str, currency: str | None = None) -> list:
    where = [{"field": "vendor_name", "op": "eq", "value": vendor, "because": "vendor"},
             {"field": "status", "op": "eq", "value": status, "because": "status"}]
    if currency:
        where.append({"field": "currency", "op": "eq", "value": currency, "because": "currency"})
    return await _rows(db, scope, {
        "entity": "vendor_credit",
        "select": ["credit_number", "vendor_credit_number", "credit_date", "currency",
                   "total_amount", "remaining_amount"],
        "where": where, "order_by": {"field": "credit_date"}, "limit": 50}) or []


_STATUS_WORDS = {"pending_review": ("Pending Review", "待审核（Pending Review）"),
                 "available": ("Available", "可用（Available）"),
                 "exhausted": ("Exhausted", "已用完（Exhausted）"),
                 "void": ("Void", "已作废（Void）")}


def _credit_sentences(f: dict, lang: str, status: str, listed: list,
                      currency: str | None = None) -> str:
    v = f["vendor"]
    totals = f["available"]
    count = sum(int(r["count"]) for r in totals)
    in_cur = f" {currency}" if currency else ""
    if lang == "zh":
        in_cur = f"{in_cur} " if currency else ""
        if count:
            head = (f"{v} 当前可用（Available）的{in_cur}供应商贷项共 {count} 张，可抵扣余额（Remaining）合计 "
                    + "；".join(f"{r['currency']} {_money(r['remaining'])}" for r in totals)
                    + "（与 Vendor Credits → Available 页面一致）。")
        else:
            head = f"{v} 当前没有可用（Available）的{in_cur}供应商贷项，可抵扣金额为 0。"
        others = [f"{_STATUS_WORDS[s][1]} {c} 张" for s, c in f["by_status"].items()
                  if s != "available" and c]
        if others:
            head += "其他状态：" + "，".join(others) + "（这些不能用于抵扣）。"
    else:
        if count:
            head = (f"{v} has {count} Available{in_cur} vendor credit{'s' if count != 1 else ''}, "
                    f"Remaining in total "
                    + "; ".join(f"{r['currency']} {_money(r['remaining'])}" for r in totals)
                    + " (as on Vendor Credits → Available).")
        else:
            head = f"{v} has no Available{in_cur} vendor credit; nothing can be netted."
        others = [f"{_STATUS_WORDS[s][0]} {c}" for s, c in f["by_status"].items()
                  if s != "available" and c]
        if others:
            head += " Other statuses: " + ", ".join(others) + " (these cannot be netted)."
    if not listed:
        return head
    label = _STATUS_WORDS[status][1 if lang == "zh" else 0]
    lines = [f"{i}. {r['credit_number']}"
             + (f"（{r['vendor_credit_number']}）" if lang == "zh" and r.get("vendor_credit_number")
                else f" ({r['vendor_credit_number']})" if r.get("vendor_credit_number") else "")
             + f" · {_dmy(r.get('credit_date'))} · {r['currency']} {_money(r['remaining_amount'])}"
             for i, r in enumerate(listed, start=1)]
    lead = (f"{label}贷项明细（按贷项日期，金额为 Remaining）：" if lang == "zh"
            else f"{label} credits (by credit date, Remaining amount):")
    return head + "\n\n" + lead + "\n" + "\n".join(lines)


def _asked_status(message: str) -> str:
    for status, words in _CREDIT_STATUS:
        if words.search(message):
            return status
    return "available"


def _pa_in_history(history: list | None) -> str | None:
    """A follow-up — "那 Amazon 有可用贷项吗？" is caught by its name; "有可用的
    贷项吗？" names nothing, and is about the PA asked of just before."""
    for turn in reversed(history or []):
        text = getattr(turn, "text", None) if not isinstance(turn, dict) else turn.get("text")
        pas = [n for p, n, *_ in doc_numbers.find_all(text or "") if p == "PA"]
        if pas:
            return pas[-1]
    return None


async def _vendor_credits(db, scope, message: str, history: list | None) -> dict | None:
    if not _CREDIT_WHAT.search(message):
        return None
    names = await _vendor_names_in(db, message)
    if not names:
        pa = _pa_in_history(history)
        return await _pa_credits(db, scope, message, pa, follow_up=True) if pa else None
    figures = await _credit_figures(db, scope, names[0])
    if figures is None:
        return None
    lang = _lang(message)
    status = _asked_status(message)
    listed = await _credit_list(db, scope, names[0], status)
    text = _credit_sentences(figures, lang, status, listed)
    if _HOW.search(message):
        rule, _ = await rule_registry.say(["vendor_credit_netting"], lang, db)
        text = text + ("\n\n" + rule if rule else "")
    return code_answers.reply(text, lang,
                              kind="answer", sources={"entity": "vendor_credit",
                                                      "row_count": len(listed)})


async def _pa_credits(db, scope, message: str, number: str,
                      follow_up: bool = False) -> dict | None:
    from app.services import answer_facts

    pa, _why = await document_chain.find(db, scope, "pa", number)
    if pa is None:
        return None     # found_elsewhere / the ordinary route says what is there
    lang = _lang(message)
    paid = pa.status in ("processed", "paid") or pa.paid_at is not None
    gross = f"{pa.currency} {_money(pa.payment_amount)}"
    out: list[str] = []
    if paid:
        facts = await answer_facts.payment_facts(db, scope, pa.pa_number)
        if facts:
            en, zh = answer_facts.payment_sentences(facts, remittance=False)
            out.append(zh if lang == "zh" else en)
        if not (facts or {}).get("payments"):
            out.append(f"{pa.pa_number} 的付款申请金额为 {gross}；扣掉的贷项和实付净额记录在付款记录上。"
                       if lang == "zh" else
                       f"{pa.pa_number} is for {gross}; the credits applied and the net paid are "
                       f"on its payment record.")
    else:
        status = document_chain.STATUS_LABELS.get("pa", {}).get(pa.status) or pa.status
        if not follow_up:
            out.append(
                f"{pa.pa_number}（{pa.vendor_name}）金额 {gross}，状态 {status}，尚未付款。贷项不在 PA 上扣，"
                f"所以 PA 上没有“扣贷项后”的金额：付款执行时才按下面的规则抵扣，实付净额在付款对话框里显示"
                f"（Gross / Vendor credits applied / Net）。" if lang == "zh" else
                f"{pa.pa_number} ({pa.vendor_name}) is for {gross}, status {status}, not paid yet. "
                f"Credits are not deducted on the PA, so it has no after-credit amount: they are "
                f"netted when the payment is executed, by the rule below, and the net shows in the "
                f"payment dialog (Gross / Vendor credits applied / Net).")
        rule, _ = await rule_registry.say(["vendor_credit_netting"], lang, db)
        if rule and not follow_up:
            out.append(rule)
    figures = await _credit_figures(db, scope, pa.vendor_name, pa.currency)
    if figures is not None and not paid:
        listed = await _credit_list(db, scope, pa.vendor_name, "available", pa.currency)
        lead = ("该供应商同币种的可用贷项（付款时会被抵扣的就是这些）：" if lang == "zh"
                else "The vendor's credits in the PA's currency (these are what the payment "
                     "would net):")
        out.append(lead + "\n" + _credit_sentences(figures, lang, "available", listed,
                                                   currency=pa.currency))
    elif figures is None and not paid:
        out.append("你没有查看供应商贷项的权限（Vendor Credits 需要 View Invoices），所以这里列不出可用贷项。"
                   if lang == "zh" else
                   "You cannot see vendor credits (Vendor Credits needs View Invoices), so the "
                   "available credits cannot be listed here.")
    return code_answers.reply("\n\n".join(out), lang, kind="answer",
                              sources={"document": pa.pa_number, "complete": True})


# ── ISS-0021 ③: how many carriers ────────────────────────────────────────────

async def _carriers(db, scope, message: str) -> dict | None:
    rows = await _rows(db, scope, {"entity": "freight_carrier",
                                   "select": ["code", "name", "is_active"],
                                   "order_by": {"field": "name"}, "limit": 100})
    if rows is None:
        return None
    lang = _lang(message)
    inactive = [r["name"] for r in rows if not r.get("is_active")]
    names = "、".join(r["name"] for r in rows) if lang == "zh" else ", ".join(r["name"] for r in rows)
    if lang == "zh":
        text = (f"Freight → Carriers 里共有 {len(rows)} 个承运商" + (f"：{names}" if rows else "")
                + (f"（其中停用 {len(inactive)} 个：{'、'.join(inactive)}）" if inactive else "") + "。")
    else:
        text = (f"Freight → Carriers lists {len(rows)} carrier{'s' if len(rows) != 1 else ''}"
                + (f": {names}" if rows else "")
                + (f" ({len(inactive)} inactive: {', '.join(inactive)})" if inactive else "") + ".")
    return code_answers.reply(text, lang, kind="answer",
                              sources={"entity": "freight_carrier", "row_count": len(rows)})


# ── ISS-0056: "not found" of a document that exists ─────────────────────────

_NOT_FOUND = re.compile(
    r"未找到|没有找到|没找到|找不到|查不到|未查到|不存在|没有.{0,6}记录|"
    r"\bnot found\b|\bcould not find\b|\bcouldn't find\b|\bcannot find\b|\bcan't find\b|"
    r"\bdoes not exist\b|\bdoesn't exist\b|\bno such\b|\bno record|\bno\b[^.]{0,40}\bfound\b",
    re.I)
_SENTENCE = re.compile(r"(?<=[。！？!?\n])|(?<=\.)(?=\s)")


_TYPE_WORDS = {
    "pr": r"请购单|\bPR\b|purchase request", "po": r"采购订单|\bPO\b|purchase order",
    "gr": r"收货单|\bGR\b|goods receipt", "invoice": r"发票|invoice",
    "pa": r"付款申请|\bPA\b|payment application", "agreement": r"协议|agreement",
}


def without_not_found(text: str, found: list[dict]) -> str:
    """The narrator's sentences saying a document is not there, for documents
    the code has just found (code_answers.found_elsewhere): "未找到付款申请
    PA-20261016-0005" over a payments query, with the PA Approved and unpaid
    (ISS-0056). A sentence goes when it says "not found" and names one of
    them — by number, or by its kind ("未找到这份付款申请") with no other
    document number in it. "没有付款记录" stays: that is true."""
    numbers = [str(d.get("number") or "").upper() for d in found if d.get("number")]
    kinds = [re.compile(_TYPE_WORDS[d["doc_type"]], re.I) for d in found
             if d.get("doc_type") in _TYPE_WORDS]

    def about_them(sentence: str) -> bool:
        if not _NOT_FOUND.search(sentence):
            return False
        upper = sentence.upper()
        if any(n in upper for n in numbers):
            return True
        return not doc_numbers.find_all(sentence) and any(k.search(sentence) for k in kinds)
    return "".join(s for s in _SENTENCE.split(text or "") if not about_them(s)).strip()


# ── ISS-0050: a list as long as its count ────────────────────────────────────

_KEYS = ("number", "internal_ref", "pa_number", "credit_number", "name", "code")
_MAX_COMPLETED = 50


def _squeeze(text: str) -> str:
    return re.sub(r"[\s\\*`_|]+", "", str(text or "")).lower()


def complete_listing(text: str, query: dict, result: dict, message: str) -> str:
    """A list the narrator wrote that leaves rows out: the missing ones are
    added by code, so what is listed adds up to what was found. "有哪些供应商已经
    停用？" — "18 家" and a table of 17 names (ISS-0050). Only an uncut detail
    list, of which the reply names at least one row (a reply that only gives
    the count is left as it is)."""
    rows = result.get("rows") or []
    if (query.get("metrics") or query.get("group_by") or result.get("truncated")
            or not 2 <= len(rows) <= _MAX_COMPLETED):
        return text
    labels = result.get("labels") or []
    entity = cq.REGISTRY.get(result.get("entity") or "")
    key = next((label for label in labels if entity is not None and label in entity.fields
                and entity.fields[label].doc_number), None) or next(
                    (k for k in _KEYS if k in labels), None)
    if key is None:
        return text
    body = _squeeze(text)
    named = [r for r in rows if r.get(key) not in (None, "") and _squeeze(r[key]) in body]
    missing = [r for r in rows if r.get(key) not in (None, "") and r not in named]
    if not named or not missing:
        return text
    shown = [k for k in labels if k == key or k in ("status", "vendor_name", "currency")]
    lines = "\n".join("- " + " · ".join(str(r[k]) for k in shown if r.get(k) not in (None, ""))
                      for r in missing)
    if _lang(message) == "zh":
        lead = f"上面漏列了 {len(missing)} 条，补在这里（共 {len(rows)} 条）："
    else:
        lead = f"The list above leaves out {len(missing)} of the {len(rows)}; here they are:"
    return text.rstrip() + "\n\n" + lead + "\n" + lines


__all__ = ["answer", "complete_listing", "without_not_found"]
