"""OA questions, answered from OA's rules — decided in code (ISS-0213).

Asked in OA or on the Portal, most OA questions went wrong in one of three
ways, all of them the planner's choice: "expense claims are handled in OA,
not EPMS, so I have no data", an answer from the purchase-request rules ("can
I submit a claim over budget?" → "No", from the PR over-budget modes), or an
answer from the payment-application rules ("can I claim a refund?" → "No").
Telling the planner harder is not a fix — it is the planner that chose.

So the choice is not left to it:

  * is_oa_question() decides from the words and the app the question was
    asked in whether it is about OA. Such a question never reaches the
    planner.
  * pick() finds the card(s) of app/knowledge/rules/oa.yaml that answer it, by
    each card's `match` patterns, and reply() writes the reply from them. The
    reply is code-written: no model chooses or words the conclusion.
  * An OA question no card answers is narrated from the OA cards alone, and
    a narration that refuses ("not part of EPMS", "check OA") or talks about
    purchase requests / orders / payment applications is replaced by a reply
    naming what can be answered (guarded()).
  * An OA approval chain is written by code from the engine's steps, with
    the OA approval facts after it (process_reply()): the claimant never
    approves, a step only they hold is skipped, one approver on two steps
    approves once, and OA has no Recall.

The Assistant cannot read OA claims themselves (expense_claims is not in the
ontology, and OA's own visibility rule — OA.md §1.4 — would have to be
rebuilt to scope them), so a question naming a claim number is told that,
with the rule that answers the rest of it.
"""
from __future__ import annotations

import functools
import re

from app.services import rule_registry

AREA = "oa"

# The OA document types with an approval chain (approval-api /workflows/{key}).
PROCESSES: dict[str, str] = {
    "exp": "General Expense",
    "mil": "Mileage Claim",
    "trv": "Travel Expense",
    "tra": "Travel Application",
    "cfm": "Custom Form",
}

# A claim number as OA writes it: EXP-20261016-0001, CFM_TRAVEL-20261016-0001.
_OA_NUMBER = re.compile(
    r"\b(EXP|MIL|TRV|TRA|CFM_[A-Z0-9_]+)[-\s]?(\d{8})[-\s]?(\d{1,4})\b", re.I)

_OA_WORDS = re.compile(
    r"expense claims?|expense reports?|\bclaims?\b|\bclaiming\b|reimburs|mileage|"
    r"\bkm\b|kilomet|travel (application|expense|request|claim)s?|\btrips?\b|"
    r"travell?ing|travell?ers?|per[ -]?diem|\bmeals?\b|\bOA\b|\bTRA\b|\bTRV\b|"
    r"\bEXP\b|\bMIL\b|报销|里程|出差|差旅|餐费|公里|报账",
    re.I)

# Words that put a question in Procurement. Present, they hand the question
# back to the planner unless it is plainly about OA as well.
_EPMS_WORDS = re.compile(
    r"\bPRs?\b|\bPOs?\b|\bPAs?\b|\bGRs?\b|purchase (request|requisition|order)|"
    r"payment application|goods receipt|\bvendors?\b|\bsuppliers?\b|\binvoices?\b|"
    r"\bagreements?\b|procurement|\bEPMS\b|freight|\bNC\b|\bMRP\b|\bVMS\b|"
    r"采购|供应商|付款申请|发票|收货|协议",
    re.I)

_PROCESS_WORDS = re.compile(
    r"approval (chain|process|flow|route|routing|workflow|steps?)|"
    r"who (approves|has to approve|needs to approve|must approve)|"
    r"which (steps|approvers)|审批(流程|链|步骤|顺序)|谁(来)?审批|审批人是谁",
    re.I)

_TYPE_WORDS: tuple[tuple[str, re.Pattern], ...] = (
    ("tra", re.compile(r"travel application|travel request|\bTRA\b|出差申请", re.I)),
    ("trv", re.compile(r"travel expense|travel claim|\bTRV\b|差旅", re.I)),
    ("mil", re.compile(r"mileage|\bMIL\b|里程", re.I)),
    ("exp", re.compile(r"general expense|\bEXP\b|一般报销|费用报销", re.I)),
    ("cfm", re.compile(r"custom form|\bCFM|自定义表单", re.I)),
)

# What a narration must not say to an OA question: the refusal this issue is
# about, and the Procurement documents an OA answer was wrongly built from.
_REFUSAL = re.compile(
    r"not (part of|in|handled in|covered by|available in) (the )?(EPMS|purchasing|procurement)|"
    r"outside (of )?(EPMS|purchasing)|(check|ask|look) (in|with|at)? ?(the )?OA\b|"
    r"no data (about|on)|not in my data|I have no (rule|data)|"
    r"purchase requests?|purchase orders?|payment applications?|\bPRs?\b|\bPOs?\b|\bPAs?\b|"
    r"不属于|不在.*(EPMS|采购)|(去|到|请).*OA.*(查|看)|采购申请|采购订单|付款申请",
    re.I)

# The cards said after every OA approval chain.
CHAIN_FACTS = ("oa_claimant_never_approves", "oa_same_approver_next_step", "oa_no_recall")

# Cards a code-written reply may carry at most; more is a page, not an answer.
MAX_CARDS = 4


def numbers(message: str) -> list[str]:
    """OA claim numbers in the message, as OA writes them."""
    return [f"{m.group(1).upper()}-{m.group(2)}-{int(m.group(3)):04d}"
            for m in _OA_NUMBER.finditer(message or "")]


def is_oa_question(message: str, app: str | None) -> bool:
    """Is this a question about OA? Decided here, not by the planner.

    An OA claim number always is. Asked inside OA, anything is, unless it
    names Procurement and nothing of OA. Asked anywhere else, it is when it
    names OA and nothing of Procurement — "what is the difference between OA
    and Procurement" stays with the planner's module guide.
    """
    text = message or ""
    if numbers(text):
        return True
    oa, epms = bool(_OA_WORDS.search(text)), bool(_EPMS_WORDS.search(text))
    if (app or "").lower() == "oa":
        return oa or not epms
    return oa and not epms


def process_types(message: str) -> list[str] | None:
    """The OA chains an approval-chain question asks about — every OA type
    when it names none — or None when it is not about a chain."""
    if not _PROCESS_WORDS.search(message or ""):
        return None
    named = [key for key, rx in _TYPE_WORDS if rx.search(message)]
    return named or ["exp", "mil", "trv", "tra"]


@functools.lru_cache(maxsize=None)
def _patterns() -> tuple[tuple[str, tuple[re.Pattern, ...]], ...]:
    out = []
    for c in rule_registry.areas()[AREA].cards:
        out.append((c["id"], tuple(re.compile(p, re.I) for p in c.get("match") or ())))
    return tuple(out)


def pick(message: str) -> list[str]:
    """The ids of the OA cards that answer this question, in the order the
    rule file gives them."""
    text = message or ""
    return [cid for cid, pats in _patterns() if any(p.search(text) for p in pats)]


async def cards(db, ids: list[str]) -> list[dict]:
    """{id, title, answer} for each id, derived settings filled. A derived card
    whose settings cannot be read is said by its `fallback`."""
    wanted = [{"id": i, "answer": " ".join(rule_registry.card(i)["answer"].split())}
              for i in ids]
    filled, missing = await rule_registry.resolve(wanted, db)
    by_id = {r["id"]: r["answer"] for r in filled}
    out = []
    for i in ids:
        src = rule_registry.card(i)
        answer = by_id.get(i) if i not in missing else " ".join(
            (src.get("fallback") or "").split())
        if answer:
            out.append({"id": i, "title": src.get("title") or i, "answer": answer})
    return out


def not_readable(nums: list[str]) -> str:
    return (f"I cannot read OA claims themselves, so I cannot say where "
            f"{', '.join(nums)} stands or what is on it. Open it in OA → Expense "
            f"Claims (a Travel Application under Travel Applications): its Approval "
            f"Status shows each step, who approved it and the one it is waiting on.")


def titles() -> list[str]:
    return [c.get("title") or c["id"] for c in rule_registry.areas()[AREA].cards]


def what_i_can_answer() -> str:
    return ("I have no OA rule that answers that exactly. About OA I can explain: "
            + "; ".join(titles()) + ".")


def guarded(text: str) -> str:
    """A narrated OA answer, unless it refuses or answers from Procurement's
    documents — then the list of what can be answered, written here."""
    if not text or _REFUSAL.search(text):
        return what_i_can_answer()
    return text


def chain_text(process: dict) -> str:
    """One OA approval chain as the engine serves it, in a sentence."""
    label = process.get("label") or ""
    if not process.get("steps_available"):
        return f"The {label} approval chain could not be read just now."
    steps = process.get("steps") or []
    if not steps:
        return f"The {label} approval chain has no steps configured."
    said = []
    for s in steps:
        bit = f"{s['index']}. {s['label']}"
        if s.get("conditional") and s.get("condition"):
            bit += f" ({s['condition']})"
        said.append(bit)
    return f"{label} approval chain, as configured now: " + "; ".join(said) + "."
