"""What happens next in a purchase, and who does it — worked out in code
(ISS-0030, ISS-0247, ISS-0248, ISS-0250).

The Assistant was left to infer the next step, and on production it:

  * answered "PR-20261023-0002 现在到哪一步了？下一步该谁做什么？" from the PR
    alone — "已获得所有必需批准，无需进一步操作" — while its PA was approved
    and waiting for the Payment Officer (ISS-0030);
  * said that after invoice matching "the PA goes into approval", leaving out
    that the PR requester has to create it first (ISS-0247);
  * answered "my PR is approved, what now" with a table of 379 open tasks, or
    with "your PR now needs these approvals" (ISS-0248);
  * called the remittance advice a manual step outside the system, or said
    nothing follows payment (ISS-0250).

Two answers come from here, both written in code:

  * `FLOW` is the purchase from requisition to remittance, step by step, with
    who does each step — PRD EPMS §3.7.1 (the task table), §3.3.4, GR-P-006,
    GR-P-008, REM-001. A general question ("the invoice is matched — what
    next?") gets the steps from the point it names to the end (`after`).
  * For one document, `for_chain` reads where it stands from the Document
    Chain (services/document_chain) and the open tasks on those documents:
    an open task IS the next step, with its holder; where there is none, the
    documents' statuses say which step of FLOW comes next.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select

from app.crud.current_step import ROLE_LABELS
from app.models.task import Task
from app.models.user import User
from app.services.guide_view import TASK_TYPE_LABELS


@dataclass(frozen=True)
class Step:
    key: str
    task_types: tuple[str, ...]  # the Task Inbox tasks this step is done through
    who_en: str
    who_zh: str
    what_en: str
    what_zh: str
    prd: str


# The order of a purchase. Each task type here is one the system raises —
# tests/test_assistant_asst2_c.py fails when one is not a known task type.
FLOW: tuple[Step, ...] = (
    Step("pr_approval", ("approve_pr",),
         "the approvers of the PR's chain, step by step", "PR 审批链上的审批人（逐级）",
         "approve the purchase request", "审批请购单（PR）",
         "EPMS §3.3"),
    Step("create_po", ("create_po",),
         "the Procurement Officer pool", "采购员（Procurement Officer）",
         "create the purchase order from the approved PR (the PR PDF is generated "
         "when the PR is approved)",
         "根据已批准的 PR 建采购订单（PO）（PR 批准时系统自动生成 PR PDF）",
         "EPMS §3.3.4, §3.7.1 create_po"),
    Step("po_approval", ("approve_po", "sign_po"),
         "the PO's approvers (a PO imported from NC is signed off instead)",
         "PO 审批人（从 NC 导入的 PO 走签字确认）",
         "approve the purchase order", "审批采购订单",
         "EPMS §3.3, §3.12.8"),
    Step("place_order", ("place_order",),
         "the Procurement Officer pool", "采购员（Procurement Officer）",
         "place the order with the vendor", "向供应商下单（Place Order）",
         "EPMS §3.3.4, §3.7.1 place_order"),
    Step("receive", ("acknowledge_gr", "collect_goods", "confirm_service_gr", "confirm_receipt"),
         "the warehouse records the goods receipt; then the PR requester acknowledges and "
         "collects it (a service or project PO: the PR's Service Owner, else the "
         "requester, confirms the service)",
         "仓库登记收货单（GR）；之后由 PR 申请人确认（Acknowledge）并领取（Collect）"
         "（服务 / 项目类 PO：由 PR 的 Service Owner，没有则由申请人确认服务）",
         "receive the goods or confirm the service", "收货 / 确认服务",
         "EPMS GR-P-001, GR-P-006, §3.7.1"),
    Step("match_invoice", ("match_invoice", "review_match", "resolve_exception"),
         "AP (the AP user the invoice is assigned to; exceptions go to the AP Clerk pool)",
         "应付（AP：发票指派到的 AP 人员；匹配异常由 AP Clerk 处理）",
         "upload the vendor's invoice and match it to the PO", "上传供应商发票并与 PO 匹配",
         "EPMS §3.7.1 match_invoice"),
    Step("create_pa", ("create_pa",),
         "the PR requester (for a PO imported from NC with no PR: the ERP PA Officer pool)",
         "PR 申请人（没有 PR 的 NC 导入 PO：ERP PA Officer）",
         "create and submit the payment application (PA) — the system raises a "
         "Create Payment Application task once the invoice is matched on a received PO",
         "建付款申请（PA）并提交——发票与已收货的 PO 三单匹配后，系统给他派 "
         "Create Payment Application 任务",
         "EPMS §3.7.1 create_pa, GR-P-008"),
    Step("pa_approval", ("approve_pa",),
         "the approvers of the PA's chain, step by step", "PA 审批链上的审批人（逐级）",
         "approve the payment application", "审批付款申请",
         "EPMS §3.15.3"),
    Step("process_pa", ("process_pa",),
         "the Payment Officer pool (a Finance Manager or Finance BP may also pay; never "
         "the person who created the PA)",
         "付款专员（Payment Officer；Finance Manager / Finance BP 也可以付；PA 的创建人不能付自己的 PA）",
         "pay the PA (Mark as Processed). The system sets no payment date: the "
         "approved PA waits as a Process Payment Application task until it is paid",
         "付款（Mark as Processed）。系统不设定付款日期：已批准的 PA 作为 Process "
         "Payment Application 任务等待付款",
         "EPMS §3.3.4, §3.7.1 process_pa"),
    Step("remittance", (),
         "Finance (an AP Clerk or whoever executed the payment)",
         "财务（AP Clerk 或执行付款的人）",
         "send the remittance advice email to the vendor from the system (Finance → "
         "Payments, or the payment batch); it is not sent automatically",
         "在系统里给供应商发汇款通知邮件（Finance → Payments 或付款批次）；系统不会自动发送",
         "EPMS REM-001 – REM-003"),
)
_INDEX = {s.key: i for i, s in enumerate(FLOW)}

# Pools a task can be sent to, as people know them.
_ROLE_NAMES = {
    **ROLE_LABELS,
    "procurement_officer": "Procurement Officer", "requester": "PR requester",
    "erp_pa_officer": "ERP PA Officer", "warehouse": "Warehouse Staff",
    "warehouse_staff": "Warehouse Staff", "gm": "GM", "opm": "OPM",
    "system_admin": "System Admin",
}


# ── A general question: "X is done — what comes next?" ──────────────────────

# Asking what comes next — strong phrases only, so "how long after approval"
# (a data question) is not caught.
_ASKS_NEXT = ("下一步", "接下来", "然后呢", "还要做什么", "还需要做什么", "我要做什么", "要做什么",
              "会发生什么", "谁来做", "谁来付", "谁来建", "谁负责", "还有什么步骤", "还有哪些步骤",
              "什么时候付款", "到哪一步", "哪一步了", "到哪了", "进度", "进展",
              r"\bnext step", r"\bwhat happens next\b", r"\bwhat happens after\b",
              r"\bwhat comes next\b", r"\bwhat'?s next\b", r"\bwhat do i (?:do|need to do)\b",
              # "who will" only with a step of the purchase, and no "what else": "Who
              # will receive the goods for this PO?", "What else do I need to fill in
              # for this PR?" ask something else (round 2 fix, 2026-11-11).
              r"\bwho (?:does|pays|creates|has to)\b",
              r"\bwho will (?:pay|do (?:it|this|that)|create the|handle it|take it from here)\b",
              r"\bwhere is .* (?:up to|now)\b")
# The point of the purchase a question starts from, earliest first.
_STAGES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    # (next step, document words, event words)
    ("pr_approval", ("请购", r"\bpr\b", "requisition", "purchase request"), ("提交", r"\bsubmitted\b")),
    ("create_po", ("请购", r"\bpr\b", "requisition", "purchase request"),
     ("批准", "批了", "审批通过", "批完", r"\bapproved\b")),
    ("place_order", ("采购订单", r"\bpo\b", "purchase order"), ("批准", "审批通过", "批完", r"\bapproved\b")),
    ("receive", ("下单", "订单发出", "order (?:was |is )?placed", "placed the order"), ("",)),
    ("match_invoice", ("收货", "到货", "货到了", "收到货", "goods (?:were |have been |are )?received",
                       "goods arrived", r"\bgr\b"), ("",)),
    ("create_pa", ("发票",  r"\binvoice"), ("匹配", r"\bmatch")),
    ("pa_approval", ("付款申请", r"\bpa\b", "payment application"), ("提交", r"\bsubmitted\b")),
    ("process_pa", ("付款申请", r"\bpa\b", "payment application"),
     ("批准", "批完", "审批完", "审批通过", r"\bapproved\b")),
    ("remittance", ("付完", "付款完成", "已付款", "付款后", "付了款", r"\bpaid\b", "after payment"), ("",)),
)


def _fold(message: str) -> str:
    return " ".join((message or "").lower().split())


def _found(text: str, patterns) -> bool:
    return any(p == "" or re.search(p, text) for p in patterns)


# A blocked action is check_document's question, not the flow's.
_BLOCKED = ("不了", "不能", "无法", "失败", "报错", "为什么", "怎么办", r"\bcan'?t\b", r"\bcannot\b",
            r"\bwhy\b", r"\berror\b", r"\bfail")
# Who approved / the approval record is explain_workflow's.
_HISTORY = ("谁批", "审批记录", "审批历史", "批过", "谁审批了", r"\bwho approved\b",
            r"\bapproval history\b", r"\bapproved by\b")
_DOC_NUMBER = re.compile(r"(?<![A-Za-z0-9])(PR|PO|GR|PA|INV|AGR)-[A-Za-z0-9][A-Za-z0-9-]*", re.I)
_DOC_TYPE = {"PR": "pr", "PO": "po", "GR": "gr", "PA": "pa", "INV": "invoice", "AGR": "agreement"}
_THIS_DOC = ("这张", r"这个(?!月|季|星期|礼拜|年)", "这份", "这单", "本单",
             r"\bthis (?!month|week|quarter|year)")


def asks_next(message: str) -> bool:
    return _found(_fold(message), [p for p in _ASKS_NEXT if p])


def document_named(message: str) -> tuple[str, str] | None:
    """(doc_type, number) of the first document number in the message."""
    m = _DOC_NUMBER.search(message or "")
    if not m:
        return None
    return _DOC_TYPE[m.group(1).upper()], m.group(0).rstrip("-")


def route(message: str, page_doc: tuple[str, str] | None) -> tuple[str, object] | None:
    """How a "where does it stand / what's next" question is answered, decided
    in code before the planner sees it:
      ("chain", (doc_type, number)) — one document: its chain and next step;
      ("flow", step_key)            — no document: the steps from that point.
    None when it is not that question."""
    text = _fold(message)
    if not asks_next(message) or _found(text, [p for p in _BLOCKED if p]):
        return None
    if _found(text, [p for p in _HISTORY if p]):
        return None
    named = document_named(message)
    if named:
        return "chain", named
    if page_doc and _found(text, [p for p in _THIS_DOC if p]):
        return "chain", page_doc
    step = stage_of(message)
    if step:
        return "flow", step
    return None


def stage_of(message: str) -> str | None:
    """The first step still to come in a general "what's next" question —
    from the EARLIEST point it names ("PA 批完了……付完以后呢" starts at paying)."""
    text = _fold(message)
    for step, docs, events in _STAGES:
        if _found(text, [p for p in docs if p]) and _found(text, events):
            return step
    return None


def after(step_key: str) -> list[Step]:
    return list(FLOW[_INDEX[step_key]:])


def _numbered(steps: list[Step], zh: bool, mine: bool) -> list[str]:
    lines = []
    for i, s in enumerate(steps, start=1):
        if zh:
            yours = "（你）" if mine and s.key in ("receive", "create_pa") else ""
            lines.append(f"{i}. {s.who_zh}{yours}：{s.what_zh}")
        else:
            yours = " (you)" if mine and s.key in ("receive", "create_pa") else ""
            lines.append(f"{i}. {s.who_en}{yours}: {s.what_en}.")
    return lines


def general_answer(step_key: str, message: str) -> tuple[str, str]:
    """(English, Chinese): the steps from `step_key` to the end of the purchase."""
    steps = after(step_key)
    mine = bool(re.search(r"我|\bmy\b|\bi\b|\bme\b", _fold(message)))
    en_head = {
        "create_po": "The PR's approval is finished — it needs no further approval. What happens next:",
        "create_pa": "Once the invoice is matched, the next step is to CREATE the payment "
                     "application; its approval starts only after it is submitted. In order:",
        "process_pa": "Once the payment application is fully approved, it is paid and the "
                      "vendor is sent a remittance advice. In order:",
        "remittance": "After the payment:",
    }.get(step_key, "What happens next, in order:")
    zh_head = {
        "create_po": "PR 的审批已经结束，不需要再审批。接下来：",
        "create_pa": "发票匹配好后，下一步是先【建】付款申请（PA）；PA 提交后才进入审批。依次是：",
        "process_pa": "付款申请全部批完后，接下来是付款，付款后给供应商发汇款通知。依次是：",
        "remittance": "付款完成后：",
    }.get(step_key, "接下来依次是：")
    en_tail, zh_tail = [], []
    if mine and step_key in ("create_po", "place_order", "receive"):
        en_tail.append("Until the goods arrive there is nothing for the requester to do.")
        zh_tail.append("货到之前，申请人不需要做什么。")
    en_tail.append("Each step reaches its holder as a task in the Task Inbox (the "
                   "remittance advice is sent from Finance → Payments).")
    zh_tail.append("每一步都会作为任务出现在负责人的 Task Inbox 里（汇款通知在 Finance → Payments 发送）。")
    en = "\n".join([en_head, *_numbered(steps, False, mine), *en_tail])
    zh = "\n".join([zh_head, *_numbered(steps, True, mine), *zh_tail])
    return en, zh


# ── One document: where it stands and who acts next ─────────────────────────

def _numbers(chain: dict) -> list[str]:
    out = []
    pr = chain.get("purchase_request") or {}
    if pr.get("number"):
        out.append(pr["number"])
    for branch in (chain.get("orders") or []) + (chain.get("agreements") or []):
        for key in ("purchase_order", "agreement"):
            if (branch.get(key) or {}).get("number"):
                out.append(branch[key]["number"])
        for layer in ("goods_receipts", "invoices", "payment_applications"):
            out += [i["number"] for i in (branch.get(layer) or {}).get("items") or []
                    if i.get("number")]
    return out


async def open_tasks(db, chain: dict) -> list[dict]:
    """Open tasks on the documents of the chain, with who holds each."""
    numbers = _numbers(chain)
    if not numbers:
        return []
    rows = (await db.execute(
        select(Task, User.full_name)
        .outerjoin(User, User.id == Task.assigned_user_id)
        .where(Task.document_number.in_(numbers), Task.is_completed.is_(False))
        .order_by(Task.created_at))).all()
    out = []
    for task, name in rows:
        if task.type not in TASK_TYPE_LABELS:
            continue
        out.append({"type": task.type, "label": TASK_TYPE_LABELS[task.type],
                    "document": task.document_number,
                    "holder": name or _ROLE_NAMES.get(task.assigned_role,
                                                      task.assigned_role.replace("_", " ").title()),
                    "pool": name is None})
    return out


def _derived(chain: dict) -> list[tuple[str, str, str]]:
    """(step key, document, why) for what comes next, from statuses alone."""
    pr = chain.get("purchase_request") or {}
    if pr and pr.get("status") in ("draft", "returned"):
        return [("pr_approval", pr["number"], "draft")]
    if pr and pr.get("status") in ("submitted", "in_review"):
        return [("pr_approval", pr["number"], "in_approval")]
    if pr and pr.get("status") in ("rejected", "cancelled"):
        return [("ended", pr["number"], pr["status"])]
    if not chain.get("orders") and not chain.get("agreements"):
        return [("create_po", pr.get("number"), "no_po")] if pr else []
    out: list[tuple[str, str, str]] = []
    for branch in chain.get("orders") or []:
        po = branch.get("purchase_order") or {}
        if po.get("not_visible"):
            continue
        status = po.get("status")
        if status in ("draft", "returned", "submitted", "pending_approval", "nc_pending"):
            out.append(("po_approval", po["number"], status))
            continue
        if status == "approved":
            out.append(("place_order", po["number"], status))
            continue
        if status in ("cancelled", "closed"):
            out.append(("ended", po["number"], status))
            continue
        grs = (branch.get("goods_receipts") or {}).get("items") or []
        invoices = (branch.get("invoices") or {}).get("items") or []
        pas = (branch.get("payment_applications") or {}).get("items") or []
        live_pas = [p for p in pas if p.get("status") not in ("cancelled", "rejected")]
        for gr in grs:
            if gr.get("status") in ("pending_ack", "collection_pending"):
                out.append(("receive", gr["number"], gr["status"]))
        received = (po.get("facts") or {}).get("anything_received") or bool(grs)
        if not received and status in ("issued", "partially_received"):
            out.append(("receive", po["number"], "not_received"))
        for inv in invoices:
            if inv.get("status") in ("unmatched", "exception", "match_review"):
                out.append(("match_invoice", inv["number"], inv["status"]))
            elif inv.get("status") == "matched" and not inv.get("on_payment_applications"):
                out.append(("create_pa", inv["number"], "matched_no_pa"))
        if received and not invoices and (branch.get("invoices") or {}).get("count") == 0:
            out.append(("match_invoice", po["number"], "no_invoice"))
        for pa in live_pas:
            st = pa.get("status")
            if st in ("draft", "returned", "submitted", "in_review"):
                out.append(("pa_approval", pa["number"], st))
            elif st == "approved":
                out.append(("process_pa", pa["number"], st))
            elif st == "processed":
                out.append(("remittance", pa["number"], st))
        if status == "partially_received":
            out.append(("receive", po["number"], "partially_received"))
    for branch in chain.get("agreements") or []:
        for pa in (branch.get("payment_applications") or {}).get("items") or []:
            st = pa.get("status")
            if st in ("draft", "returned", "submitted", "in_review"):
                out.append(("pa_approval", pa["number"], st))
            elif st == "approved":
                out.append(("process_pa", pa["number"], st))
            elif st == "processed":
                out.append(("remittance", pa["number"], st))
    return out


_WHY = {
    "draft": ("is a draft — its creator submits it", "是草稿——由创建人提交"),
    "returned": ("was returned — its creator revises and resubmits it", "被退回——由创建人修改后重新提交"),
    "in_approval": ("is in approval", "在审批中"),
    "submitted": ("is in approval", "在审批中"),
    "in_review": ("is in approval", "在审批中"),
    "pending_approval": ("is in approval", "在审批中"),
    "nc_pending": ("is waiting for approval in NC", "在 NC 等待审批"),
    "no_po": ("is approved and has no purchase order yet", "已批准，还没有采购订单"),
    "approved": ("is approved", "已批准"),
    "pending_ack": ("is waiting to be acknowledged", "等待确认（Acknowledge）"),
    "collection_pending": ("is waiting to be collected / confirmed", "等待领取 / 确认"),
    "not_received": ("has nothing received yet", "还没有收货"),
    "partially_received": ("is only partly received — more receipts may follow",
                           "只收了部分货——后面可能还有收货"),
    "unmatched": ("is not matched yet", "还没匹配"),
    "exception": ("is in match exception", "匹配异常"),
    "match_review": ("is waiting for its match to be confirmed", "匹配待确认"),
    "matched_no_pa": ("is matched and is on no payment application yet", "已匹配，还没有付款申请"),
    "no_invoice": ("has goods received and no invoice yet — the vendor's invoice is awaited",
                   "已收货，还没有发票——等供应商开票"),
    "processed": ("is paid", "已付款"),
    "rejected": ("was rejected — the purchase stops here", "被拒绝——流程到此结束"),
    "cancelled": ("was cancelled — the purchase stops here", "已取消——流程到此结束"),
    "closed": ("is closed", "已关闭"),
}


def for_chain(chain: dict, tasks: list[dict]) -> tuple[str, str]:
    """(English, Chinese) "where it stands / next" for one document's chain."""
    en = ["Where it stands and what happens next (from the document chain and the Task Inbox):"]
    zh = ["当前进度与下一步（系统根据单据链和任务表得出）："]
    if tasks:
        for t in tasks:
            pool_en = " (anyone holding that role)" if t["pool"] else ""
            pool_zh = "（该角色的任何人）" if t["pool"] else ""
            en.append(f"- Next: {t['label']} on {t['document']} — {t['holder']}{pool_en}.")
            zh.append(f"- 下一步：{t['document']} 的「{t['label']}」——由 {t['holder']}{pool_zh} 处理。")
    derived = _derived(chain)
    task_steps = {s.key for s in FLOW for t in tasks if t["type"] in s.task_types}
    for key, document, why in derived:
        why_en, why_zh = _WHY.get(why, (why, why))
        if key == "ended":
            en.append(f"- {document} {why_en}.")
            zh.append(f"- {document} {why_zh}。")
            continue
        if key in task_steps:
            continue  # the open task above already says it, with its holder
        step = FLOW[_INDEX[key]]
        en.append(f"- {document} {why_en}. Next: {step.who_en} — {step.what_en}.")
        zh.append(f"- {document} {why_zh}。下一步：{step.who_zh}——{step.what_zh}。")
    if not tasks and not derived:
        en.append("- Nothing is waiting on anyone: no open task on these documents.")
        zh.append("- 目前没有待处理的事项：这些单据上没有未完成的任务。")
    # The rest of the purchase after the earliest step still to come.
    coming = task_steps | {k for k, _, _ in derived if k != "ended"}
    if coming:
        first = min(_INDEX[k] for k in coming)
        later = [s for s in FLOW[first + 1:] if s.key not in coming]
        if later:
            en.append("After that: " + "; ".join(f"{s.who_en} — {s.what_en}" for s in later) + ".")
            zh.append("之后：" + "；".join(f"{s.who_zh}——{s.what_zh}" for s in later) + "。")
    return "\n".join(en), "\n".join(zh)
