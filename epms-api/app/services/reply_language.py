"""Which language a reply is written in, decided in code rather than by the model.

Every narrator prompt used to carry "answer in the language the question was
asked in" and nothing else. That held for a single question and broke inside a
conversation: after two Spanish questions a Chinese one came back in Spanish,
and "用中文回答" (answer in Chinese) came back in English (ISS-0001, ISS-0003).
The model weighs the whole conversation and its English payload; the rule is
about the CURRENT question only, so the current question is read here and the
decision is handed to the model as a fact.

Script is decisive and cheap: any Han character with no kana or Hangul is
Chinese. Latin-script languages cannot be told apart this way without a real
detector, so for those we only state the rule ("this question, not earlier
turns") and let the model apply it — the failure we saw was CJK drifting, and
an explicit "reply in Spanish/English" request is matched outright.
"""
import re

_HAN = re.compile(r"[一-鿿㐀-䶿]")
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯ᄀ-ᇿ]")

# Explicit requests win over the script of the sentence they are written in:
# "please answer in English" is an English sentence asking for English, but
# "用英文回答" is a Chinese sentence asking for English.
_EXPLICIT = (
    (re.compile(r"(用|以)?(英文|英语)(回答|回复|说)|in english", re.I), "English"),
    (re.compile(r"(用|以)?(中文|汉语|普通话)(回答|回复|说)|in chinese", re.I), "Chinese (Simplified)"),
    (re.compile(r"en español|in spanish|(用|以)?西班牙语(回答|回复)?", re.I), "Spanish"),
    (re.compile(r"en français|in french|(用|以)?法语(回答|回复)?", re.I), "French"),
)


def detect(message: str) -> str | None:
    """The language to reply in, or None when only the model can tell
    (a Latin-script question with no explicit request)."""
    text = message or ""
    for pattern, language in _EXPLICIT:
        if pattern.search(text):
            return language
    if _HANGUL.search(text):
        return "Korean"
    if _KANA.search(text):
        return "Japanese"
    if _HAN.search(text):
        return "Chinese (Simplified)"
    return None


def is_language_only_request(message: str) -> bool:
    """"用中文回答" / "answer in English" with nothing else to ask — a request to
    repeat the previous answer in another language, not a new question."""
    text = (message or "").strip()
    if not text or len(text) > 40:
        return False
    for pattern, _ in _EXPLICIT:
        if not pattern.search(text):
            continue
        rest = _FILLER.sub("", pattern.sub("", text))
        # Whatever is left once the request and the politeness are gone must be
        # punctuation — any word left over is a question of its own.
        return not re.search(r"[A-Za-z一-鿿]", rest)
    return False


_FILLER = re.compile(r"请|麻烦|再|一下|吧|呢|please|again|can you|could you|answer|reply|responde|contesta|répondez|réponds", re.I)


# ── Fixed Chinese terms for roles and documents (ISS-0007) ────────────────────
#
# Left to itself the model translated "Director" three ways in one afternoon —
# 总监 (right), 主管 (Supervisor: a different approval level) and 董事 (board
# director) — and "AP Review" as 账应收审查, accounts RECEIVABLE. A wrong role
# name is not a style problem: it describes a different approval chain from the
# English answer to the same question. These are the names the plant uses; the
# English stays in parentheses so the reader can match it to the screen.
#
# Keyed by the English label a payload carries. Labels come from workflow
# configuration and from crud.current_step.ROLE_LABELS — a label added there
# without an entry here is caught by tests/test_reply_language.py.
TERMS_ZH: dict[str, str] = {
    # people / approval levels
    "Requester": "申请人",
    "Supervisor": "主管",
    "Dept Manager": "部门经理",
    "Department Manager": "部门经理",
    "Director": "总监",
    "GM": "总经理",
    "General Manager": "总经理",
    "OPM": "运营经理",
    "Operations Manager": "运营经理",
    "GM / OPM": "总经理/运营经理",
    "GM/OPM": "总经理/运营经理",
    "Procurement Manager": "采购经理",
    "Purchasing Manager": "采购经理",
    "Finance BP": "财务BP",
    "Finance Manager": "财务经理",
    "Finance Director": "财务总监",
    "Finance Director of Oversea Department": "海外部财务总监",
    "Vendor Manager": "供应商经理",
    "AP Clerk": "应付会计",
    "AP Review": "应付审核",
    "Payment Officer": "付款专员",
    "Quality Manager": "质量经理",
    "System Admin": "系统管理员",
    "Dept Admin": "部门管理员",
    "Over-Budget — Finance Manager": "超预算审批 — 财务经理",
    "Over-Budget — GM / OPM": "超预算审批 — 总经理/运营经理",
    # documents
    "Purchase Request": "请购单",
    "Purchase Requisition": "请购单",
    "PR": "请购单",
    "Purchase Order": "采购订单",
    "PO": "采购订单",
    "PO sign-off": "采购订单签核",
    "Goods Receipt": "收货单",
    "GR": "收货单",
    "Invoice": "发票",
    "Payment Application": "付款申请",
    "Direct Payment Application": "直接付款申请",
    "PA": "付款申请",
    "Purchase Agreement": "采购协议",
    "Agreement": "采购协议",
    "Expense Claim": "费用报销单",
    "EXP": "费用报销单",
    "Mileage Claim": "里程报销单",
    "MIL": "里程报销单",
    "Travel Expense": "差旅报销单",
    "TRV": "差旅报销单",
    "Travel Application": "出差申请",
    "TRA": "出差申请",
    "Budget Plan": "预算计划",
    "Visitor Request": "访客申请",
    "Not to Exceed": "金额上限",
    "NTE": "金额上限",
    "Over-Budget": "超预算",
}


def instructions(message: str) -> dict:
    """What a narrator payload carries about language. Merged into every payload,
    so each narrator gets the same decision for the same question."""
    language = detect(message)
    out: dict = {"reply_in": language or "the language of `question` (this question only)"}
    if language == "Chinese (Simplified)":
        out["terms"] = TERMS_ZH
    return out


# Prepended to every narrator's system prompt, so the rule is stated once.
LANGUAGE_RULES = """\
Language — this overrides anything below that says otherwise:
- Write the WHOLE reply in `reply_in`. It was decided from the current question
  alone. Earlier turns of the conversation, the language of the data, and the
  language of these instructions do not count.
- When `terms` is present, every role, approval level and document name that
  appears in it MUST be written with exactly that translation, followed by the
  English name in parentheses the first time it appears, e.g. 总监 (Director).
  Never translate one of these names any other way. A name that is not in
  `terms` is kept in English as it appears in the data.
- Document numbers, codes, people's names and vendor names are identifiers:
  print them exactly as given, never translated.

"""
