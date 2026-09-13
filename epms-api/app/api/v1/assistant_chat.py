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
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import uuid as _uuid

from sqlalchemy import select

from app.api.v1.assistant_preflight import preflight as run_preflight
from app.api.v1.assistant_query import assistant_schema
from app.core.access_scope import build_scope, is_pr_visible
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.models.pr import PurchaseRequest
from app.services import assistant_llm
from app.services import workflow_view
from app.services import controlled_query as cq

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


@router.post("/chat")
async def chat(body: ChatRequest, db: SessionDep, user: CurrentUserPayload,
               token: BearerToken) -> dict:
    schema = (await assistant_schema(db, user))["entities"]
    if not schema:
        # No visible entities at all. Say so rather than letting the planner
        # hallucinate against an empty schema.
        return {
            "answer": "You do not currently have access to any purchasing data "
                      "I can query.",
            "kind": "denied", "query": None, "sources": None,
        }

    context = body.context.model_dump(exclude_none=True) if body.context else None
    scope = await build_scope(db, user)
    history = [
        {"role": t.role, "content": t.text}
        for t in (body.history or [])[-_MAX_HISTORY_TURNS:]
        if t.text.strip()
    ]

    actor = _uuid.UUID(str(user.get("sub"))) if user.get("sub") else None

    try:
        planned = await assistant_llm.plan(schema, body.message, context,
                                           history=history)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "plan", planned.get("usage") or {})

    if planned["kind"] == "workflow":
        return await _answer_workflow_question(db, user, token, scope, body, planned)

    if planned["kind"] == "check":
        return await _answer_gate_question(db, user, token, scope, body, planned)

    if planned["kind"] == "cannot":
        # A refusal is a real answer, and worth returning as one — "I can't
        # answer that" beats a confident wrong reply, and beats a 500.
        return {
            "answer": planned.get("explanation")
                      or "I cannot answer that from the purchasing data.",
            "kind": "cannot_answer",
            "reason": planned.get("reason"),
            "query": None, "sources": None,
        }

    query = planned["query"]
    try:
        result = await cq.execute(db, query, scope)
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
            return {
                "answer": planned.get("explanation")
                          or "I cannot answer that from the purchasing data.",
                "kind": "cannot_answer", "reason": planned.get("reason"),
                "query": None, "sources": None,
            }
        query = planned["query"]
        try:
            result = await cq.execute(db, query, scope)
        except cq.QueryRejected as exc2:
            log.warning("assistant | plan rejected twice | %s", exc2)
            return {
                "answer": "I could not build a valid query for that. "
                          "Try naming the document type or the field explicitly.",
                "kind": "cannot_answer", "reason": "invalid_plan",
                "query": None, "sources": None,
            }

    if result.get("denied"):
        # Permission gate fired between planning and execution — possible if the
        # matrix changed, and the honest answer is the gate's, not a guess.
        return {
            "answer": "You do not have permission to view that kind of document.",
            "kind": "denied", "query": query, "sources": None,
        }

    try:
        told = await assistant_llm.narrate(body.message, query, result)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await assistant_llm.record_usage(db, actor, "narrate", told.get("usage") or {})

    return {
        "answer": told["text"],
        "kind": "answer",
        # The receipt: what ran, over how many rows, and whether the cap bit.
        "query": query,
        "sources": {
            "entity": result.get("entity"),
            "row_count": result.get("row_count"),
            "truncated": result.get("truncated"),
        },
    }


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

    if doc_type != "pr":
        return {
            "answer": f"I can only check purchase requests so far, not {doc_type.upper()}s.",
            "kind": "cannot_answer", "reason": "not_supported",
            "query": None, "sources": None,
        }

    pr = await _resolve_pr(db, scope, planned.get("doc_number"), context)
    if pr is None:
        asked = planned.get("doc_number")
        return {
            "answer": (f"I could not find {asked}." if asked else
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

    if doc_type not in workflow_view.SUPPORTED:
        return {
            "answer": f"I can only trace approvals for "
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
        return {
            "answer": (f"I could not find {asked}." if asked else
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
