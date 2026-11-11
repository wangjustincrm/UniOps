"""Answers about one named document that the code writes, from the data
(round 2 of the issue review, 2026-11-10).

The model was trusted to take each of these questions down the right route and
to say the rule's conclusion. On production it did not:

  * "发票 INV-2026-0631 能取消匹配吗？" — a fixed "I have no rule" twice, and on
    the invoice's own page the general rule list, never that this invoice is
    Paid and on PA-20261013-0003 and so cannot be unmatched (ISS-0022 #1);
  * "What does the PO number PO-ABC-2604-03 mean?" — the English fixed list
    twice, while the Chinese question was answered (ISS-0022 #2);
  * "发票 INV-2026-0658 的到期日是怎么算的？" — "invoice date + 30 days", once
    called "the standard payment terms"; neither the rule nor that the system
    does not record which case applied (ISS-0034);
  * "PA-20261016-0005 扣掉贷项后实际付多少？" — "未找到付款申请", because the
    query looked in the payments made (none yet) and an empty result read as
    "no such PA"; "PR-… 和 PA-… 现在是什么状态？" — the PA looked up among
    purchase requests (ISS-0069, ISS-0056).

Each is decided and written here. Every document is read within its own
entity's permission gate and row scope (document_chain.find), so a document
the caller may not see is "not found", exactly as everywhere else.
"""
from __future__ import annotations

import json
import re

from app.core import doc_numbers
from app.services import document_chain, reply_language, rule_registry

# ── Language ─────────────────────────────────────────────────────────────────


def lang_of(message: str) -> str:
    """"zh" when the reply is Chinese, else "en" — an English text then goes
    out as a fixed reply, rendered in any other language on its way out."""
    return "zh" if reply_language.detect(message) == "Chinese (Simplified)" else "en"


def reply(text: str, lang: str, kind: str = "rules", **extra) -> dict:
    out = {"answer": text, "kind": kind, "query": None, "sources": None, **extra}
    if lang == "en":
        out["_fixed_text"] = True
    return out


_TYPE_WORDS = {
    "pr": ("purchase request", "请购单"), "po": ("purchase order", "采购订单"),
    "gr": ("goods receipt", "收货单"), "invoice": ("invoice", "发票"),
    "pa": ("payment application", "付款申请"), "agreement": ("agreement", "协议"),
}


def _type_word(doc_type: str, lang: str) -> str:
    en, zh = _TYPE_WORDS[doc_type]
    return zh if lang == "zh" else en


# ── What a document number means (ISS-0022 #2) ───────────────────────────────

_MEANING = re.compile(
    r"什么意思|啥意思|的意思|含义|代表什么|代表的?是|怎么读|怎么看|编号规则|"
    r"\bmean(?:s|ing)?\b|\bstand\s+for\b|\bdecode\b", re.I)
_ABOUT_THE_NUMBER = re.compile(r"编号|单号|号码|编码|这个号|\bnumber\b|\bcode\b", re.I)
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def asks_number_meaning(message: str) -> tuple[str, str] | None:
    """(prefix, number) when the question asks what a document number means:
    a meaning word together with "number" / "编号", or right after the number
    ("PO-ABC-2604-03 是什么意思"). "PO-… 的状态 Issued 是什么意思" is not."""
    found = doc_numbers.find_all(message)
    if not found or not _MEANING.search(message):
        return None
    prefix, number, _start, end = found[0]
    right_after = _MEANING.search(message[end:end + 12])
    if _ABOUT_THE_NUMBER.search(message) or right_after:
        return prefix, number
    return None


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def number_meaning(prefix: str, number: str, lang: str) -> str | None:
    """The parts of a number, read off the format the numbering code writes
    (doc_numbers; the pr / po / gr / pa / invoice / agreement numbering cards
    say the same)."""
    zh = lang == "zh"
    if prefix == "PO":
        m = re.match(r"^PO-([A-Z0-9]+?)-(\d{2})(\d{2})-(\d{2,})(?:-(\d+))?$", number)
        if not m:
            return None
        vendor, yy, mm, seq, suffix = m.groups()
        year, month, n = 2000 + int(yy), int(mm), int(seq)
        if not 1 <= month <= 12:
            return None
        if zh:
            text = (f"{number} 的格式是 PO-<供应商代码>-<年月>-<序号>：{vendor} 是供应商代码"
                    f"（供应商的 POID），{yy}{mm} 表示 {year} 年 {month} 月创建（按创建时的 UTC 日期），"
                    f"{seq} 表示这是当月给这个供应商开的第 {n} 张 PO。")
            if suffix:
                text += (f"末尾的 -{suffix} 是从 NC 同步过来时加的：这张 NC 订单的号码在 EPMS 里已被"
                         f"另一张 PO 用了，所以加上下一个空着的后缀（-2、-3……最多到 -50）。")
            return text
        text = (f"{number} reads PO-<vendor code>-<YYMM>-<NN>: {vendor} is the vendor's "
                f"code (its POID), {yy}{mm} means it was created in {_MONTHS[month - 1]} "
                f"{year} (the UTC date at creation), and {seq} means it was the "
                f"{_ordinal(n)} PO raised for that vendor that month.")
        if suffix:
            text += (f" The -{suffix} at the end was added when it was mirrored from NC: "
                     f"the NC order's number was already used by another PO in EPMS, so "
                     f"it took the next free suffix (-2, -3, … up to -50).")
        return text
    if prefix in ("PR", "GR", "PA"):
        m = re.match(rf"^{prefix}-(\d{{4}})(\d{{2}})(\d{{2}})-(\d{{4}})$", number)
        if not m:
            return None
        y, mo, d, seq = m.groups()
        word = _type_word(document_chain.PREFIX_TYPES[prefix], lang)
        if zh:
            return (f"{number} 的格式是 {prefix}-<年月日>-<序号>：{y}{mo}{d} 是这张{word}创建的日期"
                    f"（{int(y)} 年 {int(mo)} 月 {int(d)} 日，按创建时的 UTC 日期——多伦多时间晚上 "
                    f"8 点（冬令时 7 点）以后建的单会是第二天的日期），{seq} 是当天的第 {int(seq)} 张，"
                    f"序号每天从 1 重新开始。")
        return (f"{number} reads {prefix}-<YYYYMMDD>-<NNNN>: {y}{mo}{d} is the date the "
                f"{word} was created ({int(d)} {_MONTHS[int(mo) - 1]} {y}, the UTC date — "
                f"one created after 20:00 Toronto time, 19:00 in winter, carries the next "
                f"day's date), and {seq} means it was the {_ordinal(int(seq))} of that day; "
                f"the sequence restarts each day.")
    if prefix == "INV":
        m = re.match(r"^INV-(\d{4})-(\d{4,})$", number)
        if not m:
            return None
        y, seq = m.groups()
        if zh:
            return (f"{number} 是 EPMS 给发票的内部编号 INV-<年份>-<序号>：{y} 是录入的年份，{seq} 是"
                    f"当年的流水号；编号不会重复使用，发票删除后也不会再给别人。供应商自己的发票号另外"
                    f"按原样保存。")
        return (f"{number} is EPMS's internal reference INV-<year>-<number>: {y} is the "
                f"year the invoice was entered and {seq} the next number of that year; a "
                f"reference is never given out twice, even after its invoice is deleted. "
                f"The vendor's own invoice number is kept separately, as typed.")
    if prefix == "AGR":
        m = re.match(r"^AGR-(\d{4})(\d{2})-(\d{4})$", number)
        if not m:
            return None
        y, mo, seq = m.groups()
        if zh:
            return (f"{number} 的格式是 AGR-<年月>-<序号>：{y}{mo} 表示 {int(y)} 年 {int(mo)} 月创建"
                    f"（UTC），{seq} 是当月的第 {int(seq)} 份协议，序号每月从 1 重新开始。")
        return (f"{number} reads AGR-<YYYYMM>-<NNNN>: created in "
                f"{_MONTHS[int(mo) - 1]} {y} (UTC), the {_ordinal(int(seq))} agreement of "
                f"that month; the sequence restarts each month.")
    return None


# ── The invoice a question is about ──────────────────────────────────────────

def _invoice_number(message: str, context: dict | None) -> str | None:
    for prefix, number, *_ in doc_numbers.find_all(message):
        if prefix == "INV":
            return number
    if doc_numbers.find_all(message):
        return None              # another document is named: not the page's invoice
    context = context or {}
    if context.get("doc_type") == "invoice" and context.get("doc_number"):
        return context["doc_number"]
    return None


def _not_found(number: str, lang: str) -> str:
    return (f"我找不到发票 {number}（不存在，或你没有权限查看）。" if lang == "zh"
            else f"I could not find invoice {number}.")


# ── Can this invoice be unmatched (ISS-0022 #1) ──────────────────────────────

async def _unmatch(db, scope: dict, number: str, lang: str) -> dict:
    from app.crud import invoice as invoice_crud

    zh = lang == "zh"
    inv, _why = await document_chain.find(db, scope, "invoice", number)
    rule, _ = await rule_registry.say(["unmatching_an_invoice"], lang, db)
    if inv is None:
        return reply(_not_found(number, lang) + "\n\n" + rule, lang)
    ref = inv.internal_ref
    label = document_chain._label("invoice", inv.status)
    # The two checks the Unmatch button runs (invoices._guard_unmatch → crud
    # gr_unlink_blocker), and the PA that holds it, named as the refusal names it.
    blocker = await invoice_crud.gr_unlink_blocker(db, inv)
    holding = await invoice_crud._invoice_referenced_by_active_pa(db, inv.id)
    pa_text = ""
    if holding is not None:
        seen = await document_chain.summary(db, scope, "pa", holding.pa_number)
        pa_label = document_chain._label("pa", holding.status)
        if seen is None:
            pa_text = ("一张你无权查看的付款申请" if zh
                       else "a payment application you cannot see")
        else:
            pa_text = (f"付款申请 {holding.pa_number}（{pa_label}）" if zh
                       else f"payment application {holding.pa_number} ({pa_label})")

    if inv.match_route == "freight":
        text = (f"不能。{ref} 是快递/运费发票，按运单匹配，不在这里取消匹配，而是在发票页面的运费面板里处理。"
                if zh else
                f"No. {ref} is a courier invoice, matched to shipments: it is not unmatched "
                f"here but handled in the freight panel of its invoice page.")
    elif blocker is not None and blocker.code == "agreement":
        text = (f"不能从 PO 上取消匹配：{ref} 匹配的是协议 {inv.agreement_number or ''}，不是 PO；"
                f"要在协议的收货/账期面板里解除。" if zh else
                f"Not from a PO: {ref} is matched to agreement {inv.agreement_number or ''}, "
                f"not to a PO; it is unlinked from the agreement's own receipt / billing-period "
                f"panel instead.")
    elif blocker is not None and blocker.code == "in_payment":
        text = (f"不能。{ref} 的状态是 {label}，已经进入付款" + (f"，并且在{pa_text}上" if pa_text else "")
                + "；发票一旦是 Approved 或 Paid，Unmatch 会被拒绝。" if zh else
                f"No. {ref} is {label} — it has entered payment"
                + (f" and is on {pa_text}" if pa_text else "")
                + "; Unmatch is refused once an invoice is Approved or Paid.")
    elif blocker is not None:
        text = (f"不能。{pa_text}还包含 {ref}，要先取消那张付款申请才能取消匹配。" if zh else
                f"No. {pa_text[0].upper() + pa_text[1:]} still includes {ref}; that payment "
                f"application has to be cancelled first.")
    elif inv.po_id is None:
        text = (f"{ref} 目前没有匹配到 PO（状态 {label}），没有可以取消的匹配。" if zh else
                f"{ref} is not matched to a PO (status {label}), so there is nothing to unmatch.")
    else:
        text = (f"可以。{ref}（{label}，匹配到 {inv.po_number}）还没有进入付款，也没有付款申请包含它。"
                f"有发票匹配权限（epms.invoice.match）的人在发票页面点 Unmatch 并填写原因即可，原因记入发票的"
                f" History，发票回到 Unmatched。" if zh else
                f"Yes. {ref} ({label}, matched to {inv.po_number}) has not entered payment and "
                f"no payment application includes it. Someone with invoice matching permission "
                f"(epms.invoice.match) presses Unmatch on the invoice page and gives a reason, "
                f"which is kept in the invoice's History; the invoice goes back to Unmatched.")
    return reply(text + "\n\n" + rule, lang, kind="invoice_check",
                 sources={"document": ref, "complete": True})


# ── How this invoice's due date was set (ISS-0034) ───────────────────────────

_DUE_RULE = re.compile(
    r"(?:到期日|到期时间|到期日期|付款期限|\bdue\s*dates?\b).{0,25}"
    r"(?:怎么|如何|怎样|按什么|根据什么|依据|规则|计算|算出|定的|得出|来的|\bhow\b|\brules?\b|"
    r"calculat|determin|\bworked\s+out\b|\bset\b|come\s+from)"
    r"|(?:怎么|如何|怎样|按什么|\bhow\b).{0,15}(?:到期日|\bdue\s*dates?\b)", re.I)


def asks_due_date_rule(message: str) -> bool:
    return bool(_DUE_RULE.search(message or ""))


async def _due_date(db, scope: dict, number: str, lang: str) -> dict:
    zh = lang == "zh"
    inv, _why = await document_chain.find(db, scope, "invoice", number)
    rule, _ = await rule_registry.say(["invoice_due_date"], lang, db)
    if inv is None:
        return reply(_not_found(number, lang) + "\n\n" + rule, lang)
    ref = inv.internal_ref
    days = (inv.due_date - inv.invoice_date).days
    if zh:
        facts = f"{ref}：发票日期 {inv.invoice_date.isoformat()}，到期日 {inv.due_date.isoformat()}，相差 {days} 天。"
        which = ("系统不记录这张发票的到期日是按哪一种方式得出的，所以只能比较这两个日期：相差 30 天，"
                 "和「留空时默认加 30 天」一致，但也可能是发票上本来写的到期日、NET 30 条款，或上传人手动填写的。"
                 if days == 30 else
                 f"系统不记录这张发票的到期日是按哪一种方式得出的，所以只能比较这两个日期：相差 {days} 天，"
                 f"不是默认的 30 天，所以不是留空时默认得出的——是发票上写的到期日、NET {days} 条款，"
                 f"或上传人手动填写的。")
    else:
        facts = (f"{ref}: invoice date {inv.invoice_date.isoformat()}, due date "
                 f"{inv.due_date.isoformat()} — {days} days apart.")
        which = ("The system does not record which of these gave this invoice its due date, "
                 "so only the two dates can be compared: 30 days apart matches the 30-day "
                 "default for a blank due date, but a due date printed on the invoice, NET 30 "
                 "terms or a date typed by the uploader give the same result."
                 if days == 30 else
                 f"The system does not record which of these gave this invoice its due date, "
                 f"so only the two dates can be compared: {days} days apart is not the 30-day "
                 f"default, so it came from a due date on the invoice, NET {days} terms, or a "
                 f"date typed by the uploader.")
    return reply("\n\n".join([facts, rule, which]), lang, kind="invoice_check",
                 sources={"document": ref, "complete": True})


async def answer(db, scope: dict, message: str, context: dict | None) -> dict | None:
    """The code's own answer to the question, or None to let it be planned."""
    lang = lang_of(message)
    meaning = asks_number_meaning(message)
    if meaning:
        text = number_meaning(*meaning, lang)
        if text:
            return reply(text, lang)
    invoice = _invoice_number(message, context)
    if invoice and "unmatching_an_invoice" in rule_registry.pinned_for(message):
        return await _unmatch(db, scope, invoice, lang)
    if invoice and asks_due_date_rule(message):
        return await _due_date(db, scope, invoice, lang)
    return None


# ── A named document a query did not return (ISS-0069, ISS-0056) ─────────────

async def found_elsewhere(db, scope: dict, message: str, query: dict, result: dict) -> list[dict]:
    """Documents the question names that the query that ran did not answer for,
    but which exist and the caller may see — so "not found" is never said of
    them. A number the query filtered on at its own home entity is left
    alone: there an empty result does mean not found (or not visible)."""
    from app.core.ontology import REGISTRY

    entity = query.get("entity")
    where = json.dumps(query.get("where") or [], ensure_ascii=False, default=str).upper()
    rows = result.get("row_count") or 0
    # Numbers filtered on a number field of ANOTHER kind — a PA number among
    # purchase requests' numbers: whatever else the query found, not that one.
    misplaced: set[str] = set()
    fields = getattr(REGISTRY.get(entity), "fields", {}) or {}
    for clause in query.get("where") or []:
        spec = fields.get(clause.get("field") or "")
        if spec is None or not getattr(spec, "doc_number", None):
            continue
        values = clause.get("value")
        for value in values if isinstance(values, list) else [values]:
            for prefix, number, *_ in doc_numbers.find_all(value):
                if prefix != spec.doc_number:
                    misplaced.add(number.upper())
    out = []
    for prefix, number, *_ in doc_numbers.find_all(message):
        doc_type = document_chain.PREFIX_TYPES[prefix]
        home = document_chain._ENTITY[doc_type]
        mentioned = number.upper() in where
        if number.upper() not in misplaced and mentioned and (home == entity or rows):
            continue
        found = await document_chain.summary(db, scope, doc_type, number)
        if found and found not in out:
            out.append(found)
    return out


def found_elsewhere_text(found: list[dict], lang: str) -> str:
    """The sentence added under the reply, written here."""
    lines = []
    for d in found:
        amount = d.get("amount") or d.get("total")
        money = f"{amount} {d.get('currency') or ''}".strip() if amount else ""
        status = d.get("status_label") or d.get("status")
        word = _type_word(d["doc_type"], lang)
        if lang == "zh":
            pay = {"paid": "，已付款", "awaiting payment": "，已批准、尚未付款"}.get(d.get("payment") or "", "")
            lines.append(f"{d['number']} 是存在的：{word}，状态 {status}{pay}"
                         + (f"，金额 {money}" if money else "") + "。")
        else:
            pay = {"paid": ", paid", "awaiting payment": ", approved and not paid yet"}.get(
                d.get("payment") or "", "")
            lines.append(f"{d['number']} does exist: {word}, status {status}{pay}"
                         + (f", amount {money}" if money else "") + ".")
    return "\n".join(lines)
