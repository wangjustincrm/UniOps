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

from app.api.v1.assistant_query import assistant_schema
from app.core.access_scope import build_scope
from app.core.deps import CurrentUserPayload, SessionDep
from app.services import assistant_llm
from app.services import controlled_query as cq

log = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant", tags=["assistant"])


class ChatContext(BaseModel):
    app: str | None = None
    route: str | None = None
    doc_type: str | None = None
    doc_id: str | None = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    context: ChatContext | None = None


@router.post("/chat")
async def chat(body: ChatRequest, db: SessionDep, user: CurrentUserPayload) -> dict:
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

    try:
        planned = await assistant_llm.plan(schema, body.message, context)
    except assistant_llm.LlmUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))

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
                schema, body.message, context, retry_error=str(exc))
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
