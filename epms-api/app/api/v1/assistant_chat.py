"""Ask a question, get an answer built from rows this person is allowed to see.

The loop is: plan → validate → execute → narrate. Every step in the middle is
code, and the two model calls at the ends cannot reach past it:

  * the schema handed to the planner is already filtered by permission, so an
    entity this caller may not view is never even named to it;
  * the plan is validated name-by-name against the ontology, and the row scope
    is stapled on by the query layer, not by anything the model produced;
  * the narrator is given the rows and nothing else.

So the worst a confused model can do here is answer badly, or say it cannot
answer. It cannot answer with someone else's data.

Responses carry the query that ran. An answer nobody can check is not worth much
in a finance system, and this is the cheapest possible form of showing the work.
"""
import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import datetime as _dt
import uuid as _uuid
from decimal import Decimal

from sqlalchemy import select

from app.api.v1.assistant_preflight import preflight as run_preflight
from app.api.v1.assistant_query import assistant_schema
from app.core.access_scope import build_scope, is_pr_visible
from app.core.delegation import local_today
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.core.ontology import REGISTRY
from app.models.pr import PurchaseRequest
from app.services import assistant_llm
from app.services import reply_language
from app.services import doc_type_guide, guide_view, plan_view
from app.services import po_action_check, rule_registry
from app.services import producible_view
from app.services import budget_variance
from app.services import report_lineage
from app.services import workflow_view
from app.services import controlled_query as cq
from app.services import caller_view
from app.services import page_document
from app.services import document_chain

log = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant", tags=["assistant"])


class ChatContext(BaseModel):
    app: str | None = None
    route: str | None = None
    doc_type: str | None = None
    doc_id: str | None = None


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    text: str = Field(max_length=4000)
    # The query an assistant turn ran, as the panel received it. Without it a
    # follow-up — "drop the second rule" — has only the previous answer's prose
    # to rebuild from, and the prose does not say which filters ran. Asked that,
    # the planner kept the vendor and dropped "not fully received" along with
    # the rule it was told to drop.
    query: dict | None = None


def _turn_content(t: ChatTurn) -> str:
    if t.role != "assistant" or not t.query:
        return t.text
    ran = json.dumps(t.query, ensure_ascii=False, default=str)
    if len(ran) > 4000:
        # A runaway query should cost a truncated receipt, not the whole turn.
        ran = ran[:4000] + "…"
    return f"{t.text}\n\n[query that ran: {ran}]"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    context: ChatContext | None = None
    # Prior turns, oldest first. Only used to resolve what the person is
    # referring to; it cannot widen what a query reaches, which the row scope
    # settles regardless of what the model was told.
    history: list[ChatTurn] | None = None


# Enough for "it" to have an antecedent without paying for the whole session on
# every question. Six turns is roughly three exchanges.
_MAX_HISTORY_TURNS = 6


async def _execute_in_schema(db, query: dict, scope: dict, schema: list[dict]):
    """Run a planned query, but only against an entity the planner was shown.

    The schema handed to the planner leaves out what this person cannot see
    and what this app does not use; a plan naming anything else is sent back
    for one retry like any other invalid plan.
    """
    names = {e["name"] for e in schema}
    if query.get("entity") not in names:
        raise cq.QueryRejected(
            f"Unknown entity '{query.get('entity')}'. Available: {', '.join(sorted(names))}")
    if (query.get("period") or {}).get("compare_previous_year"):
        return await _compare_with_previous_year(db, query, scope)
    return await cq.execute(db, query, scope)


async def _compare_with_previous_year(db, query: dict, scope: dict) -> dict:
    """The same totals for the same period a year earlier, and the change.

    "How much has this quarter's purchasing grown on the same period last
    year" had no answer: one question runs one query, so the planner reached
    for the budget comparison instead (ISS-0021 #5). The subtraction is done
    here, per currency, so the model never does it.
    """
    period = {k: v for k, v in query["period"].items() if k != "compare_previous_year"}
    if query.get("group_by"):
        raise cq.QueryRejected(
            "compare_previous_year works on totals: drop group_by and ask for metrics.")
    if period.get("calendar"):
        earlier = {**period, "shift_years": int(period.get("shift_years") or 0) - 1}
    elif period.get("from") and period.get("to"):
        earlier = {**period,
                   "from": cq._shift_years(_dt.date.fromisoformat(period["from"]), -1).isoformat(),
                   "to": cq._shift_years(_dt.date.fromisoformat(period["to"]), -1).isoformat()}
    else:
        raise cq.QueryRejected(
            "compare_previous_year needs period.calendar or from/to dates.")
    current = await cq.execute(db, {**query, "period": period}, scope)
    previous = await cq.execute(db, {**query, "period": earlier}, scope)

    entity = REGISTRY[query["entity"]]
    split = current.get("currency_split") or previous.get("currency_split")
    additive = [k for k in query.get("metrics") or []
                if k in entity.metrics and entity.metrics[k].fn in ("sum", "count")]

    def by_key(result):
        return {(r.get(split) if split else None): r for r in result.get("rows") or []}

    now, before = by_key(current), by_key(previous)
    comparison = []
    for key in sorted(set(now) | set(before), key=lambda k: str(k)):
        for metric in additive:
            a = Decimal(str((now.get(key) or {}).get(metric) or 0))
            b = Decimal(str((before.get(key) or {}).get(metric) or 0))
            change = a - b
            comparison.append({
                **({split: key} if split else {}), "metric": metric,
                "current": str(a), "previous_year": str(b), "change": str(change),
                "change_pct": (str((change / b * 100).quantize(Decimal("0.1")))
                               if b else None)})
    return {**current, "comparison": comparison,
            "previous_year": {"rows": previous.get("rows"),
                              "resolved_period": previous.get("resolved_period")}}


async def _timed_out(db, query: dict) -> dict:
    """The query ran past its time limit. An answer, not a 500: the 500 this
    used to be reached the browser without CORS headers and was shown as
    "Failed to fetch", and asking again usually worked because the second run
    found the data cached (ISS-0052). The request's transaction is aborted by
    the cancel, so roll it back before anything else touches the session."""
    await db.rollback()
    log.warning("assistant | query timed out | %s", query)
    return {
        "_fixed_text": True,
        "answer": "That query took too long and was stopped. Try a shorter "
                  "period or a narrower question, or ask again in a moment.",
        "kind": "cannot_answer", "reason": "timeout",
        # No query: it would offer Export to Excel for a query that cannot run.
        "query": None, "sources": None,
    }


def _answered_exchanges(turns: list[ChatTurn]) -> list[ChatTurn]:
    """Only question-and-answer pairs, in order, so roles strictly alternate
    and the history (and any even-length tail of it) starts with a question.

    A question whose request failed comes back with no answer after it. Kept,
    it made consecutive user turns, which the model reads as one request: after
    two failures a PO question was planned as the earlier goods-receipt
    question (ISS-0071). The panel drops such pairs itself; this holds for any
    caller.
    """
    out: list[ChatTurn] = []
    for question, answer in zip(turns, turns[1:]):
        if (question.role == "user" and answer.role == "assistant"
                and question.text.strip() and answer.text.strip()):
            out.extend((question, answer))
    return out


@router.post("/chat")
async def chat(body: ChatRequest, db: SessionDep, user: CurrentUserPayload,
               token: BearerToken) -> dict:
    """Answer, then make sure a reply written by this code — a refusal, a
    "which document do you mean" — reaches the person in their language.

    Those replies are English constants. Every model-written reply already
    follows reply_language; these did not, so "采购协议的审批流程是怎样的？",
    "用中文回答" and a Spanish question all got the same English sentence back
    (ISS-0001). They are marked `_fixed_text` where they are written.
    """
    out = await _chat(body, db, user, token)
    return await _finish(out, body.message)


async def _finish(out: dict, message: str) -> dict:
    """A reply on its way out: markup stripped, a code-written reply rendered
    in the language of `message`, system names kept. Applied to the whole
    reply, and to each part of a split one with that part's own question."""
    if isinstance(out.get("answer"), str):
        out["answer"] = assistant_llm.strip_markup(out["answer"])
    if out.pop("_fixed_text", False) and out.get("answer"):
        language = reply_language.detect(message)
        plainly_english = language == "English" or (
            language is None and message.isascii())
        if not plainly_english:
            try:
                said = await assistant_llm.render_fixed_reply(
                    out["answer"], message, language)
                out["answer"] = said["text"] or out["answer"]
            except assistant_llm.LlmUnavailable:
                pass  # the English sentence is still a correct answer
    if isinstance(out.get("answer"), str):
        # Whatever wrote the reply, NC leaves as NC (ISS-0051).
        out["answer"] = reply_language.keep_system_names(out["answer"])
    return out


async def _chat(body: ChatRequest, db, user: dict, token: str,
                part: dict | None = None) -> dict:
    schema = (await assistant_schema(db, user))["entities"]
    # Entities this app's assistant does not use (ISS-0015: Finance's AP copy
    # of the invoices is kept out of EPMS).
    app = body.context.app if body.context else None
    if app:
        schema = [e for e in schema if app not in REGISTRY[e["name"]].hidden_in_apps]
    if not schema:
        # No visible entities at all. Say so rather than letting the planner
        # hallucinate against an empty schema.
        return {
            "_fixed_text": True, "answer": "You do not currently have access to any purchasing data "
                      "I can query.",
            "kind": "denied", "query": None, "sources": None,
        }

    context = body.context.model_dump(exclude_none=True) if body.context else {}
    scope = await build_scope(db, user)
    context = await _with_document_number(db, scope, context)
    actor = _uuid.UUID(str(user.get("sub"))) if user.get("sub") else None
    exchanges = _answered_exchanges(body.history or [])
    history = [
        {"role": t.role, "content": _turn_content(t)}
        for t in exchanges[-_MAX_HISTORY_TURNS:]
    ]
    # Whether anything came before, and how much of it the planner sees
    # (ISS-0038, ISS-0067).
    context = {**context, "conversation": {"earlier_questions": len(exchanges) // 2,
                                           "shown_questions": len(history) // 2}}
    # Who is asking (ISS-0073).
    me = await caller_view.describe(db, user)
    if me:
        context["caller"] = me
    if part:
        # One question of several in the message (ISS-0037, ISS-0069).
        context["question_part"] = part

    try:
        planned = await assistant_llm.plan(schema, body.message, context,
                                           history=history)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "plan", planned.get("usage") or {})

    if planned["kind"] == "split":
        return await _answer_parts(body, db, user, token, planned, part)

    if planned["kind"] == "recall":
        return _answer_recall(body, planned, exchanges)

    if planned["kind"] == "me":
        return _answer_who_am_i(me)

    if planned["kind"] == "trace":
        return await _answer_trace(db, scope, body, planned, context, actor)

    if planned["kind"] == "next":
        return await _answer_whats_next(db, user, body, actor, me)

    if planned["kind"] == "process":
        return await _answer_process(db, user, token, body, planned, actor)

    if planned["kind"] == "doc_types":
        return await _answer_doc_types(db, body, planned, actor, token=token)

    if planned["kind"] == "rules":
        return await _answer_rules(db, token, body, planned.get("areas") or [], actor)

    if planned["kind"] == "dashboard":
        return await _answer_dashboard(db, user, body, actor)

    if planned["kind"] == "modules":
        return await _answer_modules(db, body, actor)

    if planned["kind"] == "report":
        return await _answer_report_lineage(db, scope, token, body, planned, actor)

    if planned["kind"] == "vendor_summary":
        return await _answer_vendor_summary(db, scope, body, planned, actor, schema)

    if planned["kind"] == "receiving":
        return await _answer_receiving_report(db, scope, body, planned, actor)

    if planned["kind"] == "variance":
        return await _answer_budget_variance(db, scope, token, body, planned, actor)

    if planned["kind"] == "producible":
        return await _answer_producible(db, scope, body, planned, actor)

    if planned["kind"] == "plan":
        return await _answer_plan_question(db, scope, body, planned, actor)

    if planned["kind"] == "workflow":
        return await _answer_workflow_question(db, user, token, scope, body, planned)

    if planned["kind"] == "check":
        return await _answer_gate_question(db, user, token, scope, body, planned)

    if planned["kind"] == "cannot":
        # A refusal is a real answer, and worth returning as one — "I can't
        # answer that" beats a confident wrong reply, and beats a 500. But a
        # question about how the system behaves gets the rules first (ISS-0022).
        return await _refusal(db, token, body, planned, actor)

    query = planned["query"]
    try:
        result = await _execute_in_schema(db, query, scope, schema)
    except cq.QueryTimedOut:
        return await _timed_out(db, query)
    except cq.QueryRejected as exc:
        # Hand the rejection back once. It names what IS available, so the
        # second attempt usually lands; a second failure is reported rather
        # than retried forever.
        log.info("assistant | plan rejected, retrying once | %s", exc)
        try:
            planned = await assistant_llm.plan(
                schema, body.message, context, retry_error=str(exc), history=history)
        except assistant_llm.LlmUnavailable as exc2:
            raise HTTPException(status_code=503, detail=str(exc2))
        if planned["kind"] == "cannot":
            return await _refusal(db, token, body, planned, actor)
        query = planned["query"]
        try:
            result = await _execute_in_schema(db, query, scope, schema)
        except cq.QueryTimedOut:
            return await _timed_out(db, query)
        except cq.QueryRejected as exc2:
            log.warning("assistant | plan rejected twice | %s", exc2)
            return {
                "_fixed_text": True, "answer": "I could not build a valid query for that. "
                          "Try naming the document type or the field explicitly.",
                "kind": "cannot_answer", "reason": "invalid_plan",
                "query": None, "sources": None,
            }

    if result.get("denied"):
        # Permission gate fired between planning and execution — possible if the
        # matrix changed, and the honest answer is the gate's, not a guess.
        return {
            "_fixed_text": True, "answer": "You do not have permission to view that kind of document.",
            "kind": "denied", "query": query, "sources": None,
        }

    try:
        told = await assistant_llm.narrate(body.message, query, result)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate", told.get("usage") or {})

    return {
        "answer": await _with_listing_notes(
            _without_internal_notes(told["text"], result), result, body.message),
        "kind": "answer",
        # The receipt: what ran, over how many rows, and whether the cap bit.
        "query": query,
        "sources": {
            "entity": result.get("entity"),
            "row_count": result.get("row_count"),
            "truncated": result.get("truncated"),
            "total_rows": result.get("total_rows"),
        },
    }


def _without_internal_notes(text: str, result: dict) -> str:
    """A code's note is for the narrator; it is told never to copy one, and
    any it copies anyway is taken out here (ISS-0013: the PO status note,
    field name included, was pasted into a table column and into a sentence).
    A copy standing alone (a table cell) becomes the code's page name; one in
    brackets after the name is dropped. Only verbatim copies can be caught —
    the prompt covers the rest."""
    labels = result.get("value_labels") or {}
    pairs = {}
    for column, by_code in (result.get("value_notes") or {}).items():
        for code, note in by_code.items():
            if note:
                pairs[note] = (labels.get(column) or {}).get(code) or ""
    for note in sorted(pairs, key=len, reverse=True):
        if note in text:
            for bracketed in (f" ({note})", f"({note})", f"（{note}）"):
                text = text.replace(bracketed, "")
            text = text.replace(note, pairs[note])
    return text


async def _with_listing_notes(text: str, result: dict, message: str) -> str:
    """The sentences a list of these values must always carry, added under the
    narrator's reply in code (ISS-0008: "系统里有哪几种采购类型？" answered as a
    count per type left out, 1 time in 7, that Type 1 cannot be chosen in
    EPMS). The narrator is told they will be added and not to repeat them.
    English and Chinese are written in the ontology; any other language is
    rendered from the English, which stays if that fails."""
    notes = result.get("said_with_this_list") or []
    if not notes:
        return text
    language = reply_language.detect(message)
    said = []
    for _rule, english, chinese in notes:
        if language == "Chinese (Simplified)":
            said.append(chinese)
        elif language in (None, "English"):
            said.append(english)
        else:
            try:
                said.append((await assistant_llm.render_fixed_reply(
                    english, message, language))["text"] or english)
            except assistant_llm.LlmUnavailable:
                said.append(english)
    return "\n\n".join([text.rstrip(), *said])


async def _with_document_number(db, scope, context: dict | None) -> dict | None:
    """Add the on-screen document's NUMBER to the context.

    The panel sends the document's id, and the schema the planner queries by is
    keyed on numbers — so on PR-20261015-0003 "这张单的供应商是谁？金额多少？"
    could not be written as a query at all, and the planner fell back to the one
    route that accepts "the document on screen" without a number: its approval
    history (ISS-0004). Resolved with the same visibility rule as everything
    else, so a document this person cannot see adds nothing.

    Every detail page, not only PR / PO / PA: GR, invoice, agreement and
    agreement-receipt pages were not recognised at all (ISS-0068) — see
    page_document.
    """
    if not context or context.get("doc_number"):
        return context
    found = await page_document.resolve(db, scope, context.get("doc_type"),
                                        context.get("doc_id"))
    return {**context, **found} if found else context


async def _resolve_pr(db, scope, number: str | None, context: dict | None):
    """Find the PR the question is about, honouring visibility.

    A document this caller cannot see resolves to nothing — the same answer as a
    document that does not exist. Telling someone their colleague's PR is
    blocked on a missing vendor leaks both its existence and its state.
    """
    if number:
        pr = (await db.execute(select(PurchaseRequest).where(
            PurchaseRequest.number == number.strip()))).scalar_one_or_none()
    elif context and context.get("doc_type") == "pr" and context.get("doc_id"):
        try:
            pr_id = _uuid.UUID(context["doc_id"])
        except ValueError:
            return None
        pr = (await db.execute(select(PurchaseRequest).where(
            PurchaseRequest.id == pr_id))).scalar_one_or_none()
    else:
        return None

    if pr is None or not await is_pr_visible(db, pr.id, scope):
        return None
    return pr


async def _answer_gate_question(db, user, token, scope, body, planned) -> dict:
    """Run the real gates and explain them. No guessing at the reason."""
    doc_type = planned.get("doc_type", "pr")
    context = body.context.model_dump(exclude_none=True) if body.context else None

    actor = _uuid.UUID(str(user.get("sub"))) if user.get("sub") else None
    if doc_type == "po":
        return await _answer_po_check(db, user, token, scope, body, planned, actor)
    if doc_type != "pr":
        # Was a fixed "I can only check purchase requests" (ISS-0017).
        return (await _rules_instead(db, token, body, actor)
                or _no_rule_reply())

    pr = await _resolve_pr(db, scope, planned.get("doc_number"), context)
    if pr is None:
        asked = planned.get("doc_number")
        if not asked and (rescued := await _rules_instead(db, token, body, actor)):
            return rescued
        return {
            "_fixed_text": True, "answer": (f"I could not find {asked}." if asked else
                       "Tell me which document you mean — a PR number works best."),
            "kind": "cannot_answer", "reason": "document_not_found",
            "query": None, "sources": None,
        }

    # The same endpoint the UI would call: both halves of the gates, all of them
    # evaluated rather than stopping at the first.
    result = await run_preflight(doc_type="pr", doc_id=str(pr.id), db=db, user=user,
                                 token=token, action="submit")
    try:
        told = await assistant_llm.narrate_preflight(body.message, pr.number, result)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(
        db, _uuid.UUID(str(user.get("sub"))) if user.get("sub") else None,
        "narrate_preflight", told.get("usage") or {})

    return {
        "answer": told["text"],
        "kind": "preflight",
        "query": None,
        # The receipt for a gate question is the gate list itself.
        "preflight": result,
        "sources": {"document": pr.number, "allowed": result["allowed"],
                    "complete": result["complete"]},
    }


async def _answer_workflow_question(db, user, token, scope, body, planned) -> dict:
    """Where the document stands, from the engine's chain and its own history."""
    doc_type = (planned.get("doc_type") or "").lower()
    context = body.context.model_dump(exclude_none=True) if body.context else None

    actor = _uuid.UUID(str(user.get("sub"))) if user.get("sub") else None
    if doc_type not in workflow_view.SUPPORTED:
        # "协议 … 这个月的服务确认了吗？" got "I can only trace approvals for
        # PR, PO, PA" (ISS-0022 #1). A rule question gets the rules.
        if rescued := await _rules_instead(db, token, body, actor):
            return rescued
        return {
            "_fixed_text": True, "answer": f"I can only trace approvals for "
                      f"{', '.join(t.upper() for t in workflow_view.SUPPORTED)}.",
            "kind": "cannot_answer", "reason": "not_supported",
            "query": None, "sources": None,
        }

    # Fall back to the document on screen only when it is the same type, so
    # "who is this with?" on a PO page cannot resolve to an unrelated PR.
    ctx_id = (context or {}).get("doc_id") if (context or {}).get("doc_type") == doc_type else None
    doc = await workflow_view.resolve(db, scope, doc_type, planned.get("doc_number"), ctx_id)
    if doc is None:
        asked = planned.get("doc_number")
        # No document named and none on screen: a general question ("what do
        # I see if someone else approved first?") gets the rule rather than
        # "which document do you mean" (ISS-0061, ISS-0026).
        if not asked and not ctx_id and (
                rescued := await _rules_instead(db, token, body, actor)):
            return rescued
        return {
            "_fixed_text": True, "answer": (f"I could not find {asked}." if asked else
                       "Tell me which document you mean — its number works best."),
            "kind": "cannot_answer", "reason": "document_not_found",
            "query": None, "sources": None,
        }

    view = await workflow_view.build(db, doc_type, doc, token)
    try:
        told = await assistant_llm.narrate_workflow(body.message, view)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(
        db, _uuid.UUID(str(user.get("sub"))) if user.get("sub") else None,
        "narrate_workflow", told.get("usage") or {})

    return {
        "answer": told["text"],
        "kind": "workflow",
        "query": None,
        "workflow": view,
        "sources": {
            "document": view["document_number"],
            "status": view["status"],
            "steps": len(view["steps"]),
            "events": len(view["history"]),
            "complete": view["steps_available"],
        },
    }


async def _answer_whats_next(db, user, body, actor, me: dict | None = None) -> dict:
    """Their own inbox, explained. No arguments — it is always about the asker."""
    mine = await guide_view.my_tasks(user.get("role", ""), actor) if actor else []
    # What the EPMS Task Inbox lists, by its own filter; other modules' tasks
    # are reported apart (ISS-0011).
    tasks, elsewhere = guide_view.split_epms_inbox(mine)
    # Counted here, not by the model — see guide_view.task_summary (ISS-0011).
    payload = {"question": body.message, "kind": "tasks",
               **guide_view.task_summary(tasks, elsewhere)}
    if me:
        # Asked "who am I" and routed here, the reply guessed roles from the
        # tasks (ISS-0073). The roles are given, so nothing is left to guess.
        payload["me"] = {**{k: v for k, v in me.items() if k != "email"}, "roles_are": (
            "the complete list of this person's roles — primary_role plus "
            "additional_roles. A task's assigned_role is the role the task was "
            "sent to, not a role of theirs: never name a role from a task.")}
    try:
        told = await assistant_llm.narrate_guide(body.message, payload)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate_guide", told.get("usage") or {})

    return {
        "answer": told["text"], "kind": "tasks", "query": None,
        "tasks": tasks,
        "sources": {"open_tasks": len(tasks)},
    }


async def _answer_plan_question(db, scope, body, planned, actor) -> dict:
    """Why the production plan says what it says.

    Gated on the same permission as every other MRP entity — this reads plan
    rows, and reaching them through an explanation rather than a query does not
    make them less restricted. The gate is checked here rather than inside
    plan_view so the refusal reads like every other refusal.
    """
    if not (scope.get("perms") or {}).get("mrp.report.view", False):
        return {
            "_fixed_text": True, "answer": "I cannot see the production plan.",
            "kind": "denied", "reason": "no_permission",
            "query": None, "sources": None,
        }

    lines = await plan_view.find_lines(
        db,
        material=planned.get("material_code"),
        month=planned.get("month"),
        week=planned.get("week_start"),
    )
    if not lines:
        return {
            "_fixed_text": True, "answer": "There is no line in the plan currently in force for "
                      "that. Note that only the plan in force is readable — a "
                      "superseded run is not.",
            "kind": "cannot_answer", "reason": "no_such_line",
            "query": None, "sources": None,
        }

    explained = [plan_view.explain(row) for row in lines]
    try:
        told = await assistant_llm.narrate_plan(body.message, explained)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate_plan",
                                     told.get("usage") or {})

    return {
        "answer": told["text"], "kind": "plan", "query": None,
        "plan_lines": explained,
        "sources": {"entity": "mrp_mps_line", "row_count": len(explained),
                    "complete": all(e["arithmetic_accounts_for_it"]
                                    or e["reasons"] for e in explained)},
    }


async def _answer_producible(db, scope, body, planned, actor) -> dict:
    """How many of a product the material on hand would support.

    Gated on mrp.report.view — the same permission as every other MRP entity.
    This reads recipes and warehouse stock, and reaching them through a
    calculation rather than a query does not make them less restricted.
    """
    if not (scope.get("perms") or {}).get("mrp.report.view", False):
        return {
            "_fixed_text": True, "answer": "I cannot see recipes or warehouse stock.",
            "kind": "denied", "reason": "no_permission",
            "query": None, "sources": None,
        }

    product = (planned.get("product") or "").strip()
    if not product:
        return {
            "_fixed_text": True, "answer": "Tell me which product — its material code works best.",
            "kind": "cannot_answer", "reason": "no_product",
            "query": None, "sources": None,
        }

    try:
        result = await producible_view.how_many_can_we_make(
            db, product, set(planned.get("exclude") or []))
    except producible_view.AmbiguousRecipe as exc:
        # Refused rather than resolved — see the exception's own docstring.
        return {
            "_fixed_text": True, "answer": (f"{exc.product} has {len(exc.options)} recipes marked as "
                       f"the default ({', '.join(exc.options)}). I cannot tell "
                       f"which one applies, and picking one would give you a "
                       f"confident answer about the wrong recipe."),
            "kind": "cannot_answer", "reason": "ambiguous_recipe",
            "query": None, "sources": None,
        }

    if result is None:
        return {
            "_fixed_text": True, "answer": (f"There is no default recipe for {product}, so there is "
                       f"nothing to explode. If it is a raw material rather "
                       f"than something we make, ask about its stock instead."),
            "kind": "cannot_answer", "reason": "no_recipe",
            "query": None, "sources": None,
        }

    payload = {"question": body.message, "kind": "producible", **result}
    try:
        told = await assistant_llm.narrate_guide(body.message, payload)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate_guide", told.get("usage") or {})

    return {
        "answer": told["text"], "kind": "producible", "query": None,
        "producible": result,
        "sources": {
            "entity": "bom_line + wms_inventory_lot",
            "row_count": len(result["materials"]),
            "levels_deep": result["levels_deep"],
            "complete": not result["could_not_compute"],
        },
    }


async def _answer_modules(db, body, actor) -> dict:
    """What the system is made of. Ungated for the same reason as the types
    below: it describes scope, reads no rows and names no document."""
    guide = doc_type_guide.modules()
    payload = {"question": body.message, "kind": "modules", **guide}
    try:
        told = await assistant_llm.narrate_guide(body.message, payload)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate_guide", told.get("usage") or {})

    return {
        "answer": told["text"], "kind": "modules", "query": None,
        "modules": guide,
        "sources": {"modules": len(guide["modules"])},
    }


async def _answer_doc_types(db, body, planned, actor, token: str | None = None) -> dict:
    """What kinds of a document exist and what differs when you create one.

    Ungated, unlike every other route here, and deliberately: this describes the
    shape of a form, not anyone's documents. It reads no rows, takes no scope
    and names no PR — a requester who cannot see a single purchase request still
    has to know what a type 5 is before they raise one. Gating it would have
    meant the people most likely to ask were the ones who could not.
    """
    doc_type = (planned.get("doc_type") or "").lower()
    if doc_type not in doc_type_guide.SUPPORTED:
        if rescued := await _rules_instead(db, token, body, actor):
            return rescued
        return {
            "_fixed_text": True, "answer": ("I can explain purchase requests, purchase orders, goods "
                       "receipts, payment applications, invoices and purchase "
                       "agreements — not that."),
            "kind": "cannot_answer", "reason": "not_supported",
            "query": None, "sources": None,
        }

    guide = doc_type_guide.build(doc_type)
    # Derived rules carry {placeholders} until filled from the live system.
    guide["rules"], missing = await rule_registry.resolve(guide["rules"], db)
    if missing:
        guide["unavailable"] = missing
    payload = {"question": body.message, "kind": "document_types", **guide}
    try:
        told = await assistant_llm.narrate_guide(body.message, payload)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate_guide", told.get("usage") or {})

    return {
        "answer": told["text"], "kind": "document_types", "query": None,
        "document_types": guide,
        "sources": {"document": guide["label"], "types": len(guide["types"])},
    }


# What a vendor summary covers: (section, entity, [(label, extra where, metrics,
# group_by)]). Each runs as an ordinary scoped query, so nothing here can show
# what the person could not have queried one by one.
_VENDOR_SECTIONS = (
    ("purchase_orders", "purchase_order", (
        ("totals", [], ["count", "amount"], []),
        ("by_status", [], ["count"], ["status"]))),
    ("agreements", "purchase_agreement", (
        ("totals", [], ["count", "consumed"], []),)),
    ("invoices", "invoice", (
        ("totals", [], ["count", "total"], []),
        ("unpaid", [{"field": "status", "op": "not_in", "value": ["paid", "cancelled"]}],
         ["count", "total"], []))),
    ("payment_applications", "payment_application", (
        ("totals", [], ["count", "amount"], []),
        ("paid", [{"field": "status", "op": "eq", "value": "processed"}],
         ["count", "amount"], []))),
    ("vendor_credits", "vendor_credit", (
        ("available", [{"field": "status", "op": "eq", "value": "available"}],
         ["count", "remaining"], []),)),
)


async def _answer_vendor_summary(db, scope, body, planned, actor, schema) -> dict:
    """Everything with one vendor, per kind of document (ISS-0057).

    Summarised from POs alone, with two cancelled orders in the total and no
    currency, the answer left out agreements, invoices, PAs and credits.
    """
    asked = (planned.get("vendor") or "").strip()
    shown = {e["name"] for e in schema}
    found = await cq.execute(db, {"entity": "vendor", "select": ["name"],
                                  "where": [{"field": "name", "op": "like", "value": asked}],
                                  "limit": 10}, scope)
    names = [r["name"] for r in found["rows"]]
    exact = [n for n in names if n.lower() == asked.lower()]
    if len(exact) == 1:
        names = exact
    result: dict = {"vendor_asked": asked}
    if len(names) != 1:
        result["candidates"] = names
    else:
        vendor = names[0]
        result["vendor"] = vendor
        sections = {}
        for section, entity, parts in _VENDOR_SECTIONS:
            if entity not in shown:
                sections[section] = {"not_visible": True}
                continue
            out = {}
            for label, where, metrics, group_by in parts:
                res = await cq.execute(db, {
                    "entity": entity, "metrics": metrics, "group_by": group_by,
                    "where": [{"field": "vendor_name", "op": "eq", "value": vendor}, *where]},
                    scope)
                out[label] = {"rows": res.get("rows"),
                              **({"assumption": res["assumption"]} if res.get("assumption") else {})}
            sections[section] = out
        result["sections"] = sections
    payload = {"question": body.message, "kind": "vendor_summary", **result}
    try:
        told = await assistant_llm.narrate_guide(body.message, payload)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate_guide", told.get("usage") or {})
    return {"answer": told["text"], "kind": "vendor_summary", "query": None,
            "vendor_summary": result,
            "sources": {"entity": "vendor summary", "row_count": len(result.get("sections") or {}),
                        "complete": True}}


async def _answer_receiving_report(db, scope, body, planned, actor) -> dict:
    """The Receiving Report's window totals, from its own crud (ISS-0021 #4, #7).

    Asked as a goods_receipt count it gave 101 "lines" (receipt records)
    against the report's 87 lines / 58 receipts / 56 POs / 18 days.
    """
    from app.crud import gr_report as gr_report_crud

    if not scope["perms"].get("view_gr", False):
        return {"_fixed_text": True,
                "answer": "You do not have permission to view goods receipts.",
                "kind": "denied", "query": None, "sources": None}
    if planned.get("calendar"):
        frm, to = cq.calendar_range(planned["calendar"])
    else:
        frm = _dt.date.fromisoformat(planned["from"]) if planned.get("from") else None
        to = _dt.date.fromisoformat(planned["to"]) if planned.get("to") else None
    _, total, summary, _ = await gr_report_crud.receiving_rows(
        db, date_from=frm, date_to=to, po_ids_subq=scope["po_subq"],
        page=1, page_size=1)
    result = {"period": {"from": frm.isoformat() if frm else None,
                         "to": to.isoformat() if to else None},
              "lines": summary.lines, "receipts": summary.receipts,
              "orders": summary.orders, "avg_lead_days": summary.avg_lead_days}
    payload = {"question": body.message, "kind": "receiving_report", **result}
    try:
        told = await assistant_llm.narrate_guide(body.message, payload)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate_guide", told.get("usage") or {})
    return {"answer": told["text"], "kind": "receiving_report", "query": None,
            "receiving_report": result,
            "sources": {"entity": "Receiving Report", "row_count": total, "complete": True}}


async def _answer_account_balance(db, scope, token, body, planned, actor, year) -> dict:
    """One cost centre × account, from the PR budget widget's own source."""
    result = await budget_variance.account_balance(
        db, scope, fiscal_year=year, cost_center=planned["cost_center"],
        account_code=planned["account_code"], token=token)
    if not result.get("allowed"):
        return {
            "_fixed_text": True, "answer": result.get("why") or "You cannot see budget figures.",
            "kind": "denied", "query": None, "sources": None,
        }
    payload = {"question": body.message, "kind": "budget_account_balance", **result}
    try:
        told = await assistant_llm.narrate_guide(body.message, payload)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate_guide", told.get("usage") or {})
    return {
        "answer": told["text"], "kind": "budget_variance", "query": None,
        "budget_variance": result,
        "sources": {"entity": "budget balance (cost centre × account)",
                    "row_count": 1 if result.get("found") else 0,
                    "complete": "balance_unavailable" not in result},
    }


async def _answer_budget_variance(db, scope, token, body, planned, actor) -> dict:
    """Budget against spending, with the subtraction already done.

    Gated on the budget entities' own keys, inside the service, so this cannot
    hand back through arithmetic what a query would have refused. The row scope
    is the ontology's own department rule for the same reason.
    """
    today = local_today()
    year = int(planned.get("fiscal_year") or today.year)
    if planned.get("cost_center") and planned.get("account_code"):
        return await _answer_account_balance(db, scope, token, body, planned, actor, year)
    # Unasked, the current year runs to the current month: "至 12 月" in
    # October read as though the year were over (ISS-0014).
    through = planned.get("through_month") or (today.month if year == today.year else 12)
    result = await budget_variance.build(
        db, scope, fiscal_year=year, through_month=int(through), token=token)
    if not result.get("allowed"):
        return {
            "_fixed_text": True, "answer": result.get("why") or "You cannot see budget figures.",
            "kind": "denied", "query": None, "sources": None,
        }

    payload = {"question": body.message, "kind": "budget_variance", **result}
    try:
        told = await assistant_llm.narrate_guide(body.message, payload)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate_guide", told.get("usage") or {})

    return {
        "answer": told["text"], "kind": "budget_variance", "query": None,
        "budget_variance": result,
        "sources": {
            "entity": "budget_plan_line + journal_voucher_line",
            "row_count": len(result.get("rows") or []),
            "complete": "actual_unavailable" not in result,
        },
    }


async def _answer_report_lineage(db, scope, token, body, planned, actor) -> dict:
    """Where the numbers on a report come from, and by what rule.

    Half gated, and the halves are drawn where the sensitivity is. How a figure
    is arrived at is explained to anyone — someone reading a budget number is
    owed the rule behind it, and refusing that while showing them the number is
    the worse of the two answers. The mapping rows themselves are gated, inside
    report_lineage, by the same ontology keys the query layer uses, so the guide
    and a direct query cannot disagree about who may see them.
    """
    report = (planned.get("report") or "").lower()
    if report not in report_lineage.SUPPORTED:
        return {
            "_fixed_text": True, "answer": ("I can explain how the Budget Dashboard's Plan vs Actual "
                       "figures are arrived at. I do not have the workings of "
                       "any other report."),
            "kind": "cannot_answer", "reason": "not_supported",
            "query": None, "sources": None,
        }

    guide = await report_lineage.build(db, report, scope.get("perms", {}), token)
    payload = {"question": body.message, "kind": "report_lineage", **guide}
    try:
        told = await assistant_llm.narrate_guide(body.message, payload)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate_guide", told.get("usage") or {})

    mapping = guide["cost_centre_mapping"]
    return {
        "answer": told["text"], "kind": "report_lineage", "query": None,
        "report_lineage": guide,
        "sources": {
            "report": guide["label"],
            "figures": len(guide["figures"]),
            "mapping_rules": mapping.get("count") if mapping.get("available") else None,
            # A half-answer says so here as well as in the text, so the evidence
            # line cannot look complete when it is not.
            "complete": not guide["unavailable"],
        },
    }


async def _answer_process(db, user, token, body, planned, actor) -> dict:
    """How a kind of document flows, read from the engine's configuration."""
    doc_type = (planned.get("doc_type") or "").lower()
    if (doc_type not in guide_view.SUPPORTED_PROCESSES
            and doc_type in doc_type_guide.SUPPORTED):
        # A goods receipt or an invoice has no approval chain, but asked "how
        # does a GR work" the planner still reached for this route, and the
        # reply was only the list of processes (ISS-0029). The document guide
        # carries how it works, so answer from that.
        return await _answer_doc_types(db, body, {"doc_type": doc_type}, actor, token=token)
    if doc_type not in guide_view.SUPPORTED_PROCESSES:
        if rescued := await _rules_instead(db, token, body, actor):
            return rescued
        return {
            # Derived from what is wired up, so it cannot omit one (it left out
            # PO sign-off) or name one that is not.
            "_fixed_text": True,
            "answer": "I can describe these approval processes: "
                      + ", ".join(guide_view.PROCESS_LABELS.values()) + ".",
            "kind": "cannot_answer", "reason": "not_supported",
            "query": None, "sources": None,
        }

    process = await guide_view.process_steps(doc_type, token)
    payload = {"question": body.message, "kind": "process", **process}
    try:
        told = await assistant_llm.narrate_guide(body.message, payload)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate_guide", told.get("usage") or {})

    return {
        "answer": told["text"], "kind": "process", "query": None,
        "process": process,
        "sources": {"process": process["label"], "steps": len(process["steps"]),
                    "complete": process["steps_available"]},
    }


# ── System rules (ISS-0022) ──────────────────────────────────────────────────
#
# How the system behaves, from the rule registry (services/rule_registry.py):
# each rule tied to the code that enforces it, settings filled in live, the
# approval engine's own rules fetched from approval-api. The refusals above —
# the planner's "not a data question", a document type outside a tool's
# list, "which document do you mean" with none named — come here first.

async def _answer_rules(db, token, body, areas: list[str], actor) -> dict:
    rules = await rule_registry.build(areas, db, token)
    if not rules["areas"]:
        return _no_rule_reply()
    payload = {"question": body.message, "kind": "rules", **rules}
    try:
        told = await assistant_llm.narrate_guide(body.message, payload)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate_guide", told.get("usage") or {})
    return {
        "answer": told["text"], "kind": "rules", "query": None,
        "rules": rules,
        "sources": {"rules": sum(len(a["rules"]) for a in rules["areas"]),
                    "areas": [a["label"] for a in rules["areas"]],
                    "complete": not rules["unavailable"]},
    }


async def _rules_instead(db, token, body, actor) -> dict | None:
    """The rules, when the question turns out to be about them; else None."""
    picked = await assistant_llm.pick_rule_areas(body.message)
    await assistant_llm.record_usage(db, actor, "pick_rule_areas", picked.get("usage") or {})
    if not picked["areas"]:
        return None
    return await _answer_rules(db, token, body, picked["areas"], actor)


def _no_rule_reply() -> dict:
    """Written here, not by the model: no "ask the System Admin", no role
    that does not exist — what CAN be answered instead."""
    areas = "; ".join(c["label"] for c in rule_registry.catalogue())
    return {
        "_fixed_text": True,
        "answer": ("I have no rule or data that answers that. I can explain how "
                   f"the system behaves in these areas: {areas}."),
        "kind": "cannot_answer", "reason": "no_rule",
        "query": None, "sources": None,
    }


async def _refusal(db, token, body, planned, actor) -> dict:
    reason = planned.get("reason")
    if reason in ("not_a_data_question", "not_in_schema"):
        if rescued := await _rules_instead(db, token, body, actor):
            return rescued
    if reason == "not_a_data_question":
        # The model's own text here was "not in my data, ask the System
        # Admin" (ISS-0022) — never shown again.
        return _no_rule_reply()
    return {
        "answer": planned.get("explanation")
                  or "I cannot answer that from the purchasing data.",
        "kind": "cannot_answer", "reason": reason,
        "query": None, "sources": None,
    }


async def _answer_po_check(db, user, token, scope, body, planned, actor) -> dict:
    """Why a PA, a GR or a sign-off cannot be done on this PO (ISS-0017)."""
    action = planned.get("action")
    context = body.context.model_dump(exclude_none=True) if body.context else None
    ctx_id = (context or {}).get("doc_id") if (context or {}).get("doc_type") == "po" else None
    if action not in po_action_check.ACTIONS:
        return await _answer_rules(db, token, body, ["po"], actor)
    po = await workflow_view.resolve(db, scope, "po", planned.get("doc_number"), ctx_id)
    if po is None:
        asked = planned.get("doc_number")
        if not asked and (rescued := await _rules_instead(db, token, body, actor)):
            return rescued
        return {
            "_fixed_text": True, "answer": (f"I could not find {asked}." if asked else
                       "Tell me which purchase order you mean — its number works best."),
            "kind": "cannot_answer", "reason": "document_not_found",
            "query": None, "sources": None,
        }
    result = await po_action_check.check(db, user, po, action, token)
    try:
        told = await assistant_llm.narrate_preflight(body.message, po.number, result)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate_preflight", told.get("usage") or {})
    return {
        "answer": told["text"], "kind": "preflight", "query": None,
        "preflight": result,
        "sources": {"document": po.number, "allowed": result["allowed"],
                    "complete": result["complete"]},
    }


async def _answer_dashboard(db, user, body, actor) -> dict:
    """The asker's own dashboard cards, from the Dashboard page's own code
    (ISS-0022 #2: "my dashboard numbers" was refused as "look at the screen",
    while each card asked singly was answered)."""
    from app.crud import dashboard as dash_crud

    board = await dash_crud.build(db, user.get("role", "requester"), actor)
    result = {
        "role": board.role,
        "kpis": [{"title": k.title, "value": k.value,
                  **({"subtitle": k.subtitle} if k.subtitle else {})} for k in board.kpis],
        **({"users_by_role": [{"role": r.role, "count": r.count} for r in board.users_by_role]}
           if board.users_by_role else {}),
        # What some cards really are (a fixed label, always 0, not FY).
        "rules": rule_registry.static_rules("dashboard") + [
            r for r in rule_registry.static_rules("tasks") if r["id"] == "tasks_have_no_due_date"],
    }
    payload = {"question": body.message, "kind": "dashboard", **result}
    try:
        told = await assistant_llm.narrate_guide(body.message, payload)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate_guide", told.get("usage") or {})
    return {"answer": told["text"], "kind": "dashboard", "query": None,
            "dashboard": result,
            "sources": {"entity": "dashboard", "row_count": len(result["kpis"]),
                        "complete": True}}


# ── The conversation itself (ISS-0067) ────────────────────────────────────────
#
# "我在这次对话里问的第一个问题是什么？" after 20 questions named the 18th: the
# planner sees only the last _MAX_HISTORY_TURNS turns and took the earliest of
# those for the first. The panel sends the whole conversation, so the answer
# is read from that, in code, rather than from what the model was shown.

# How much of an earlier answer is quoted back. The question is "what did you
# answer", not "answer it again"; a table cut mid-row is avoided by cutting at
# a line break when one is close enough.
_RECALL_ANSWER_CHARS = 600


def _excerpt(text: str, limit: int = _RECALL_ANSWER_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    head, sep, _ = cut.rpartition("\n")
    return (head if sep and len(head) > limit // 2 else cut).rstrip() + "\n\n…"


def _answer_recall(body: ChatRequest, planned: dict, exchanges: list[ChatTurn]) -> dict:
    pairs = [(exchanges[i], exchanges[i + 1]) for i in range(0, len(exchanges) - 1, 2)]
    n = len(pairs)
    try:
        position = int(planned.get("position") or 1)
    except (TypeError, ValueError):
        position = 1
    index = position - 1 if position > 0 else n + position
    if n == 0:
        return {"_fixed_text": True,
                "answer": "This is the first question of this conversation — "
                          "nothing was asked before it.",
                "kind": "cannot_answer", "reason": "nothing_earlier",
                "query": None, "sources": None}
    if not 0 <= index < n:
        return {"_fixed_text": True,
                "answer": f"This conversation has only {n} earlier question"
                          f"{'' if n == 1 else 's'}.",
                "kind": "cannot_answer", "reason": "nothing_earlier",
                "query": None, "sources": None}

    question, answer = pairs[index]
    ordinal = index + 1
    quoted = _excerpt(answer.text)
    if reply_language.detect(body.message) == "Chinese (Simplified)":
        which = ("第一个问题" if ordinal == 1 else
                 "上一个问题" if position == -1 else f"第 {ordinal} 个问题")
        return {"answer": f"你在这次对话里问的{which}是：「{question.text.strip()}」\n\n"
                          f"我当时的回答：\n\n{quoted}",
                "kind": "recall", "query": None, "sources": None}
    which = ("first question" if ordinal == 1 else
             "previous question" if position == -1 else f"question {ordinal}")
    return {"_fixed_text": True,
            "answer": f"Your {which} in this conversation was: “{question.text.strip()}”\n\n"
                      f"My answer was:\n\n{quoted}",
            "kind": "recall", "query": None, "sources": None}


# ── Who is asking (ISS-0073) ──────────────────────────────────────────────────

def _answer_who_am_i(me: dict | None) -> dict:
    """Their own record, written in code: name, department, every role."""
    if not me:
        return {"_fixed_text": True, "answer": "I cannot see who is signed in.",
                "kind": "cannot_answer", "reason": "no_user",
                "query": None, "sources": None}
    return {"_fixed_text": True, "answer": caller_view.as_text(me),
            "kind": "me", "me": me, "query": None, "sources": None}


# ── A purchase from requisition to payment (ISS-0030) ────────────────────────

async def _answer_trace(db, scope, body: ChatRequest, planned: dict, context: dict,
                        actor) -> dict:
    """The whole chain around one document, each document within its own
    entity's scope (services/document_chain)."""
    doc_type = (planned.get("doc_type") or "").lower()
    number = (planned.get("doc_number") or "").strip()
    if not number and context.get("doc_number") and (
            not doc_type or context.get("doc_type") == doc_type):
        # "这张单从请购到付款到哪一步了？" on the document's own page.
        number, doc_type = context["doc_number"], context.get("doc_type") or doc_type
    if doc_type not in document_chain.SUPPORTED:
        return {"_fixed_text": True,
                "answer": "I can follow the chain from a purchase request, purchase "
                          "order, goods receipt, invoice, payment application or "
                          "agreement — tell me its number.",
                "kind": "cannot_answer", "reason": "not_supported",
                "query": None, "sources": None}
    if not number:
        return {"_fixed_text": True,
                "answer": "Tell me which document you mean — its number works best.",
                "kind": "cannot_answer", "reason": "document_not_found",
                "query": None, "sources": None}

    chain = await document_chain.build(db, scope, doc_type, number)
    if not chain["found"]:
        return {"_fixed_text": True, "answer": f"I could not find {number}.",
                "kind": "cannot_answer", "reason": "document_not_found",
                "query": None, "sources": None}
    try:
        told = await assistant_llm.narrate_chain(body.message, chain)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate_chain", told.get("usage") or {})
    hidden = '"not_visible": true' in json.dumps(chain, default=str)
    return {
        "answer": told["text"], "kind": "document_chain", "query": None,
        "document_chain": chain,
        "sources": {"document": chain["asked_about"]["number"],
                    "documents": document_chain.count_documents(chain),
                    "complete": not hidden},
    }


# ── One message, several questions (ISS-0037, ISS-0069) ──────────────────────

_NOT_LOOKED_UP = {
    "Chinese (Simplified)": "以下部分这次没有去查（不代表没有数据），请单独再问一次：",
}
_NOT_LOOKED_UP_DEFAULT = ("Not looked up this time (that does not mean there is no "
                          "data) — please ask these separately:")


async def _answer_parts(body: ChatRequest, db, user: dict, token: str, planned: dict,
                        part: dict | None) -> dict:
    """Plan and answer each question of the message on its own, in order.

    Each part goes through the whole loop — plan, the schema and row scope of
    whatever it reaches, narration — exactly as if it had been asked alone, so
    splitting can neither widen what is read nor skip a check. At most
    MAX_PARTS are answered: every part costs a plan and a narration.
    """
    parts = [str(p).strip() for p in planned.get("parts") or [] if str(p).strip()]
    if part:
        # A part is never split again (the tool is not offered for one).
        return {"_fixed_text": True, "kind": "cannot_answer", "reason": "too_ambiguous",
                "answer": "I could not tell what this part of the question asks.",
                "query": None, "sources": None}
    if len(parts) < 2:
        # A "split" into one part is just the question.
        lone = {"index": 1, "of": 1, "whole": body.message}
        return await _chat(ChatRequest(message=(parts[0] if parts else body.message)[:2000],
                                       context=body.context, history=body.history),
                           db, user, token, part=lone)

    answered, rest = parts[:assistant_llm.MAX_PARTS], parts[assistant_llm.MAX_PARTS:]
    replies = []
    for i, question in enumerate(answered, start=1):
        one = ChatRequest(message=question[:2000], context=body.context, history=body.history)
        try:
            out = await _chat(one, db, user, token,
                              part={"index": i, "of": len(parts), "whole": body.message})
        except HTTPException as exc:
            # One part failing must not lose the others' answers.
            out = {"_fixed_text": True, "kind": "cannot_answer", "reason": "unavailable",
                   "answer": f"This part could not be answered just now ({exc.detail}).",
                   "query": None, "sources": None}
        replies.append({"question": question, **await _finish(out, question)})

    sections = [f"**{i}. {r['question']}**\n\n{r.get('answer') or ''}"
                for i, r in enumerate(replies, start=1)]
    if rest:
        language = reply_language.detect(body.message)
        lead = _NOT_LOOKED_UP.get(language, _NOT_LOOKED_UP_DEFAULT)
        if language not in (None, "English", "Chinese (Simplified)"):
            try:
                lead = (await assistant_llm.render_fixed_reply(
                    lead, body.message, language))["text"] or lead
            except assistant_llm.LlmUnavailable:
                pass
        sections.append(lead + "\n" + "\n".join(f"- {q}" for q in rest))
    return {
        "answer": "\n\n".join(sections),
        "kind": "parts",
        "parts": replies,
        "not_looked_up": rest,
        "query": None, "sources": None,
    }
