"""The translation layer: natural language in, a validated query description out.

The model's job is deliberately small. It does not write SQL, it does not decide
who may see what, and it does not do arithmetic — it picks an entity, some
fields, and a handful of operators from a list it was handed. Everything that
could be wrong in a way nobody notices stays in code.

Two calls per question:

  1. plan — the model reads the schema it is allowed to see and either produces a
     query description or says it cannot answer. Those are the only two outcomes:
     tool_choice forces one of them, so "make something up" is not reachable.
  2. narrate — the model is given the question, the query that ran, and the rows
     that came back, and writes the reply. It is told, and the prompt is built so
     that, the rows are the only facts available to it.

Between the two, app/services/controlled_query.py validates every name against
the ontology and staples on the row scope. A plan naming a column that is not in
the schema is rejected before it reaches the database.

MODEL is hardcoded. Every Anthropic call in UniOps is Haiku — a standing rule,
not a default — and it is not a config option on purpose: a settings key is an
invitation to raise it "just for this one case", and one such case is enough to
exhaust the shared quota and take invoice OCR down with it.
"""
import json
import logging
from typing import Any

from app.core.config import settings

log = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"

# The plan call returns a small JSON object; 1024 is generous for it. Narration
# is prose about at most a few hundred rows of summary.
_PLAN_MAX_TOKENS = 1024
_NARRATE_MAX_TOKENS = 1024

_PLAN_SYSTEM = """\
You turn a colleague's question about purchasing data into ONE query description.

You are working inside UniOps, a purchasing system. The schema you are given is
already filtered to what this person is allowed to see — if an entity is not
listed, do not mention it and do not guess at it.

Rules:
- Use ONLY entity names, field names and metric names that appear in the schema.
- Prefer metrics + group_by when the question is about totals, counts or
  rankings. Use select when the question asks for specific records.
- For "recent", "last N months", "this year" use period, never a hand-built
  date filter.
- If the question cannot be answered from this schema — it is about something
  not modelled, or it needs data that is not here — call cannot_answer. Saying
  so is a correct answer. Guessing is not.
- If the question is about WHY something is blocked or what someone should do
  next, call cannot_answer with reason "not_a_data_question".

  In that explanation, do NOT guess who they should ask or what the cause might
  be. You cannot see the gates, and naming the wrong person costs them a trip.
  Say only that you cannot answer why-questions yet and that the document's own
  page shows what is blocking it. There is a preflight check in the system that
  knows the real reason; it is simply not wired into this conversation yet.
"""

_NARRATE_SYSTEM = """\
You are answering a colleague's question inside UniOps, a purchasing system.

You are given their question, the query that was run, and its results. The
results are the only facts you have.

- Answer in the language the question was asked in.
- Be direct. Lead with the answer, not with a description of what you did.
- Money arrives as a string to preserve precision. Write it as a plain number in
  your reply — never wrapped in quotation marks. Include the currency if the rows
  carry one.
- If the result was truncated, say that what you are showing is the first N, not
  all of them.
- If the results are empty, say so plainly — do not speculate about why.
- Never introduce a number, name, date or status that is not in the results. If
  the question asked for something the results do not contain, say what is
  missing rather than filling it in.
"""

_QUERY_TOOL = {
    "name": "run_query",
    "description": (
        "Run one query against the purchasing data and answer from its results. "
        "Every name used must come from the schema provided."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "entity": {"type": "string", "description": "Entity name from the schema"},
            "select": {
                "type": "array", "items": {"type": "string"},
                "description": "Field names to return. Omit when using metrics.",
            },
            "where": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "op": {
                            "type": "string",
                            "enum": ["eq", "ne", "in", "not_in", "like", "gt",
                                     "gte", "lt", "lte", "between"],
                        },
                        "value": {},
                    },
                    "required": ["field", "op", "value"],
                },
            },
            "group_by": {"type": "array", "items": {"type": "string"}},
            "metrics": {"type": "array", "items": {"type": "string"}},
            "period": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "last_n_months": {"type": "integer"},
                    "last_n_days": {"type": "integer"},
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                },
            },
            "order_by": {
                "type": "object",
                "properties": {"field": {"type": "string"}, "desc": {"type": "boolean"}},
                "required": ["field"],
            },
            "limit": {"type": "integer"},
        },
        "required": ["entity"],
    },
}

_CANNOT_TOOL = {
    "name": "cannot_answer",
    "description": (
        "Use when the question cannot be answered from the schema provided. "
        "This is a correct outcome, not a failure."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "enum": ["not_in_schema", "not_a_data_question", "too_ambiguous"],
            },
            "explanation": {
                "type": "string",
                "description": (
                    "One sentence, addressed to the person who asked, IN THE "
                    "LANGUAGE THEY USED. This text is shown to them verbatim — "
                    "it does not pass through the narration step, so it is the "
                    "whole reply."
                ),
            },
        },
        "required": ["reason", "explanation"],
    },
}


class LlmUnavailable(Exception):
    """The model could not be reached or is not configured."""


def _client():
    if not settings.anthropic_api_key:
        raise LlmUnavailable(
            "ANTHROPIC_API_KEY is not configured — the assistant is unavailable"
        )
    import anthropic  # imported lazily so the rest of the API runs without it
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def _context_note(context: dict | None) -> str:
    """What the user is looking at, so "this PO" has a referent."""
    if not context:
        return ""
    bits = [f"{k}={v}" for k, v in context.items() if v]
    return ("\n\nThe person is currently looking at: " + ", ".join(bits) +
            ". Resolve 'this'/'that' against it when it fits the question.") if bits else ""


async def plan(schema: list[dict], message: str, context: dict | None = None,
               retry_error: str | None = None) -> dict:
    """Ask for a query description.

    Returns {"kind": "query", "query": {...}} or
            {"kind": "cannot", "reason": ..., "explanation": ...}.
    `retry_error` feeds a validator rejection back for one more attempt — the
    rejection names what is available, so the second try is usually right.
    """
    import anthropic

    user_text = message + _context_note(context)
    if retry_error:
        user_text += (
            f"\n\nYour previous query was rejected: {retry_error}\n"
            "Correct it using only names from the schema, or call cannot_answer."
        )

    system = [
        {"type": "text", "text": _PLAN_SYSTEM},
        # The schema is the stable half of the prompt and by far the larger one,
        # so it sits in its own block for caching.
        {"type": "text", "text": "Schema:\n" + json.dumps(schema, ensure_ascii=False),
         "cache_control": {"type": "ephemeral"}},
    ]

    try:
        resp = await _client().messages.create(
            model=MODEL,
            max_tokens=_PLAN_MAX_TOKENS,
            system=system,
            tools=[_QUERY_TOOL, _CANNOT_TOOL],
            # Forcing a tool call removes the third option — prose that sounds
            # like an answer but was never checked against any data.
            tool_choice={"type": "any"},
            messages=[{"role": "user", "content": user_text}],
        )
    except anthropic.APIConnectionError as exc:
        raise LlmUnavailable(f"Could not reach the model: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LlmUnavailable("The shared API quota is exhausted right now") from exc
    except anthropic.APIStatusError as exc:
        log.error("plan call failed %s: %s", exc.status_code, exc.message)
        raise LlmUnavailable(f"Model returned {exc.status_code}") from exc

    _log_usage("plan", resp)

    for block in resp.content:
        if block.type != "tool_use":
            continue
        if block.name == "run_query":
            return {"kind": "query", "query": dict(block.input), "usage": _usage(resp)}
        if block.name == "cannot_answer":
            return {"kind": "cannot", **dict(block.input), "usage": _usage(resp)}

    # tool_choice=any should make this unreachable; treat it as "no answer"
    # rather than inventing one.
    return {"kind": "cannot", "reason": "too_ambiguous",
            "explanation": "I could not turn that into a query.",
            "usage": _usage(resp)}


async def narrate(message: str, query: dict, result: dict) -> dict:
    """Write the reply from the rows. Returns {"text": ..., "usage": {...}}."""
    import anthropic

    payload = {
        "question": message,
        "query_that_ran": query,
        "row_count": result.get("row_count"),
        "truncated": result.get("truncated"),
        "rows": result.get("rows", []),
    }
    try:
        resp = await _client().messages.create(
            model=MODEL,
            max_tokens=_NARRATE_MAX_TOKENS,
            system=_NARRATE_SYSTEM,
            messages=[{"role": "user",
                       "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        )
    except anthropic.APIConnectionError as exc:
        raise LlmUnavailable(f"Could not reach the model: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LlmUnavailable("The shared API quota is exhausted right now") from exc
    except anthropic.APIStatusError as exc:
        log.error("narrate call failed %s: %s", exc.status_code, exc.message)
        raise LlmUnavailable(f"Model returned {exc.status_code}") from exc

    _log_usage("narrate", resp)
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return {"text": text, "usage": _usage(resp)}


def _usage(resp: Any) -> dict:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    return {
        "input_tokens": getattr(u, "input_tokens", 0),
        "output_tokens": getattr(u, "output_tokens", 0),
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
    }


def _log_usage(stage: str, resp: Any) -> None:
    """One line per call. This is the only visibility into spend until a real
    quota lands, and it is also how we find out whether prompt caching is
    actually working — a cache_read of zero across repeated questions means the
    schema block is being invalidated by something."""
    u = _usage(resp)
    log.info("assistant | stage=%s | model=%s | in=%s | out=%s | cache_read=%s",
             stage, MODEL, u.get("input_tokens"), u.get("output_tokens"),
             u.get("cache_read_input_tokens"))
