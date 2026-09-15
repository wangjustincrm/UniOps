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
from datetime import date, datetime, timezone
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

Answer in the language the question was asked in. This applies to
cannot_answer's explanation as well — that text is shown verbatim and is the
whole reply, so a Chinese question must not come back in English just because
the schema and these instructions are in English.

Rules:
- Use ONLY entity names, field names and metric names that appear in the schema.
- Prefer metrics + group_by when the question is about totals, counts or
  rankings. Use select when the question asks for specific records.
- For "recent", "last N months", "this year" use period with last_n_months or
  last_n_days — never a hand-built date filter.
- For a NAMED month or an explicit range ("September", "Q2", "since June"), use
  period with from/to, and take the year from today's date given below. Getting
  the year wrong returns zero rows and reads exactly like "there were none".
- To COMPARE two things — two versions, two months, two vendors — fetch both in
  ONE query and select the column that tells them apart. Filter with `in` on
  that column and put it in `select`; the comparison is then done from the rows.
  Asking for one side and then the other is not possible: one question runs one
  query.
- In a counting question, WHAT IS BEING COUNTED picks the entity. "How many
  people per department" counts people: query `user`, group by
  `department.name`. Querying `department` and counting it returns one per
  department — twelve departments, one each — which reads as a perfectly
  reasonable answer and is not one. Same for orders per vendor, components per
  product, invoices per PO: the thing after "how many" is the entity, the thing
  after "per" is the grouping, reached through a link.
- Detail lives on its own entity. "What goes into this product", "which items
  are on this order" are questions about the CHILD entity — filter it by the
  parent through the link, as `bom.product_material_code`, and select the parent
  columns you need the same way. Querying the parent alone returns the header
  and none of the detail, which is an answer that describes the question back.
- A field with `value_labels` stores a code that stands for something: type 4
  means Service. Filter on the CODE and report the MEANING — a question about
  service purchases becomes type=4, and a result of 4 is read back as "Service",
  never as "4". Asked what the types are, list them from this mapping; it is the
  answer, not a reason to say the system has no such concept.
- When a field lists `values`, those are the ONLY values in the data. Match the
  question to one of them and filter on that — never on the words the question
  used. Someone asking about 工程部 or "the eng department" means the value
  recorded as "Engineering"; filtering on their phrasing returns nothing, and
  nothing is indistinguishable from "that department made no purchases". If
  nothing in the list plausibly matches, say which values exist rather than
  querying for one that does not.
- People name things loosely. Someone asking about "Cintas" means the vendor
  recorded as "Cintas Canada Limited"; someone asking about "the Camfil order"
  is not quoting a title. For name-like text fields — vendor_name, title,
  received_by, any human name — use `like` with the fragment they gave, NOT
  `eq`. Reserve `eq` for values they clearly quoted exactly: document numbers,
  statuses, currencies, codes.
  This matters more than it looks: `eq` on a shortened name matches nothing, and
  nothing is indistinguishable from "we never bought from them".
- purchase_order.created_at is when the order was IMPORTED for most of the
  history, not when it was raised: nothing in the table is dated before
  2024-01-03 even though 986 orders carry 2023 numbers, and the PMS migration
  stamped its own run date on hundreds at a time. A period filter over it is
  still the best available and is fine for "recent" questions, but if someone
  asks about a specific year before 2026, say that the date recorded is the
  import date and that the count is therefore not a count of that year's
  purchasing. Returning 0 for 2023 without saying that reads as "we bought
  nothing", which is false.
- When the question asks for what was ON a document — line items, "包括
  LineItem", "with the line items", what was ordered, what was requested, the
  detail behind the total — query the LINE entity (po_line, pr_line), not the
  header. One row per line, with the header's fields reached across the link:
    po_line  select ["order.number", "order.vendor_name", "description",
                     "qty", "unit_price", "line_total"]
  Returning the headers and saying the lines need a separate query is not an
  answer. It is the answer that made someone ask twice, and the second time
  there was nothing left to ask for.
- You CAN reach fields on related entities. Each entity lists `links`; address a
  field across one as "<link>.<field>", and chain up to three of them. Examples:
    purchase_order  group_by ["originating_pr.department_name"]
    invoice         group_by ["order.originating_pr.department_name"]
    goods_receipt   select   ["number", "order.vendor_name"]
  Use this whenever the fact lives on the other side. purchase_orders has no
  department of its own — grouping orders by department means going through
  originating_pr, not settling for something else.
- NEVER substitute a different column for the one that was asked for. If someone
  asks for department and there is no department reachable, say so; do not group
  by budget_code and label the result "department". A near-enough column is not
  an approximation, it is a different fact, and nothing in the answer will show
  that the swap happened.
- If the question cannot be answered from this schema — it is about something
  not modelled, or it needs data that is not here — call cannot_answer. Saying
  so is a correct answer. Guessing is not.
- If they are asking what THEY should do — what is waiting for them, what is in
  their inbox, what they owe — call whats_next.
- "How many X can we make from what we have", "which material runs out first",
  "what is stopping us making more X" is how_many_can_we_make, never a query.
  It needs the recipe and the stock and arithmetic over both; a query returns
  one table and cannot do it. Listing the stock of a few components and adding
  it up is not an answer — the components that constrain production are usually
  several recipe levels below the ones a single query would reach, and a total
  across different materials is not a quantity of anything.
- "Why does the plan say X" is explain_plan, not a query. The reasoning is
  recorded — forecast, opening stock, carry-in and the engine's flags are all on
  the line — so answering that the system does not record it is wrong.
- explain_process is about the APPROVAL CHAIN — who signs off, in what order.
  It is not about the data, and it is not about the form.
- "What PR types are there", "what is the difference between them", "what do I
  fill in for a type 5", "which type should I pick" are questions about the
  FORM: call explain_document_types. Answering these with the approval chain is
  wrong, and it is the mistake this route exists to stop — someone asking what
  distinguishes the types does not want to be told who approves them.
- "What modules are there", "what can I do in this system", "where do I claim
  an expense", "what is MRP for" are about the SYSTEM, not one document: call
  explain_modules. It is the layer above explain_document_types — use it when
  they have not narrowed to a document yet, and when someone is plainly in the
  wrong place and needs pointing at the right module.
- The same route covers the other documents: PO types, the two kinds of goods
  receipt, the four kinds of payment application, what the invoice statuses
  mean, and the three kinds of purchase agreement. "What is a house account",
  "what is the difference between a prepayment and a settlement PA", "why is
  this invoice in exception" — all explain_document_types.
- "How many PRs of each type", "which type do we raise most", "how many invoices
  are in exception" are questions about the DATA: run a query grouped by the
  field. The dividing line is whether they are asking what a value MEANS or how
  many there ARE. "What does exception mean" is the guide; "which ones are in
  exception" is a query.
- "What statuses exist", "what fields does an invoice have" remain data
  questions: query them, grouping by the field in question so the reply carries
  how many of each there are, which is more use than the list alone.
- If they are asking how a process works in general, rather than about one
  document, call explain_process. Many people here were never trained on this
  system, so "how does this work" is a real question and deserves the configured
  answer, not a guess.
- If the question is about a document's APPROVAL PROGRESS — which step it is on,
  who it is waiting on, who approved it already, what the chain is — call
  explain_workflow. The chain, its history and the current assignee ARE
  recorded; answering that the system does not track approvers is wrong.
- Status alone (is it approved? what state is it in?) is a data question: query
  it. Anything about WHO or WHICH STEP is explain_workflow.
- If the question is about whether a specific document can be submitted, or why
  it cannot, call check_document. Do not try to answer it by querying — the
  gates are not visible in the data, and a guess at the reason sends someone to
  fix the wrong thing.
- check_document currently covers purchase requests and the submit action only.
  For a why-is-this-blocked question about anything else, call cannot_answer
  with reason "not_a_data_question" and say plainly that that document type is
  not covered yet. Do not guess at the cause or at who to ask.
"""

_NARRATE_SYSTEM = """\
You are answering a colleague's question inside UniOps, a purchasing system.

You are given their question, the query that was run, and its results. The
results are the only facts you have.

- `near_misses` appears only when the query found NOTHING and a name search was
  involved. It lists values that do exist and are close to what they typed. Do
  not report "no records" on its own in that case — say nothing matched the name
  as given, and name the close values, because the usual cause is a shortened or
  slightly wrong name rather than an absence. These came from rows this person
  can already see, so offering them leaks nothing.

- Answer in the language the question was asked in.
- Be direct. Lead with the answer, not with a description of what you did.
- If a filter value in query_that_ran differs from how the question phrased it —
  they asked about 工程部 and it ran against "Engineering" — name the value that
  was actually used, once, in passing. They are the only one who can tell you
  the mapping was wrong, and they cannot if it is not shown.
- A coded column (procurement type and the like) comes back as its number. The
  query that ran carries `value_labels` where one applies — report the meaning,
  not the code. "4" tells the reader nothing.
- An `assumption` means the query was narrowed on the reader's behalf because
  they did not say which one they meant. Say so in the reply, in one clause —
  they are looking at a subset and have no way to tell from the numbers alone.
- `value_labels` lists every code the system defines; the rows list only the
  ones that occur. Asked what kinds exist, that difference is part of the
  answer: say the ones with no rows exist but are unused, rather than dropping
  them and reporting a shorter list of kinds than the system actually has.
- A quantity that belongs to a batch is not a quantity per unit. A BOM line's
  `qty_per` is what one build of that recipe consumes, and the recipe produces
  `bom.batch_output_qty` — never 1 in this data, usually 1000. Report both and
  say which is which. Do not divide them: that is arithmetic, and the reader can
  do it knowing their own batch size.
- Money arrives as a string to preserve precision. Write it as a plain number in
  your reply — never wrapped in quotation marks. Include the currency if the rows
  carry one.
- If the result was truncated, say that what you are showing is the first N, not
  all of them.
- If the results are empty, say so plainly — do not speculate about why.
- Empty is "nothing matched this query", NOT "this never happened". When the
  query filtered on a name the person typed, say that no records matched that
  name and give the filter back to them, so they can see it was spelled or
  shortened differently. Never conclude from an empty result that an event did
  not occur.
- matched_rows, when present, is how many records the query actually matched. A
  total of null with matched_rows 0 means there were no such records — say that
  directly. Do not offer alternative explanations for a null you have been told
  the cause of.
- Do NOT do arithmetic. When a grand total is wanted, `totals` already holds it,
  computed over the whole result rather than the page you can see. Adding the
  rows up yourself produces a number that looks right and is not: nine correct
  subtotals were once summed to 2,000 over, with nothing in the reply to show
  it. If `totals` is absent, say the total is not available rather than
  supplying one.
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

_CHECK_TOOL = {
    "name": "check_document",
    "description": (
        "Use when someone asks whether a specific document can be submitted, or "
        "why it cannot. This runs the real gates against that document — it does "
        "not query data, and it is the ONLY correct way to answer a "
        "why-is-this-blocked question. Never guess at the reason yourself."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_type": {"type": "string", "enum": ["pr"],
                         "description": "Only purchase requests are wired up so far."},
            "doc_number": {
                "type": "string",
                "description": (
                    "The document number as the person wrote it, e.g. "
                    "PR-20260823-0001. Omit only when they are clearly referring "
                    "to the document they are currently looking at."
                ),
            },
            "action": {"type": "string", "enum": ["submit"]},
        },
        "required": ["doc_type", "action"],
    },
}

_WORKFLOW_TOOL = {
    "name": "explain_workflow",
    "description": (
        "Use for questions about a specific document's APPROVAL PROGRESS: which "
        "step it is on, who it is waiting on, who has already approved it, or "
        "what the chain looks like. The approval chain, its history and the "
        "current assignee are all recorded — never say they are not."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_type": {"type": "string", "enum": ["pr", "po", "pa"]},
            "doc_number": {
                "type": "string",
                "description": (
                    "The document number as written, e.g. PO-400-2608-10. Omit "
                    "only when they clearly mean the document on screen."
                ),
            },
        },
        "required": ["doc_type"],
    },
}

_NEXT_TOOL = {
    "name": "whats_next",
    "description": (
        "Use when someone asks what they should be doing, what is waiting for "
        "them, what is in their inbox, or what they owe. Returns their own open "
        "tasks. Takes no arguments — it is always about the person asking."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

_PROCESS_TOOL = {
    "name": "explain_process",
    "description": (
        "Use when someone asks how a process works in general — 'how does "
        "purchasing work', 'what happens after I submit a PR', 'who approves a "
        "payment'. This reads the approval chain as it is actually configured. "
        "For a SPECIFIC document's progress use explain_workflow instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_type": {"type": "string", "enum": ["pr", "po", "pa", "vms_visit"]},
        },
        "required": ["doc_type"],
    },
}

_PRODUCIBLE_TOOL = {
    "name": "how_many_can_we_make",
    "description": (
        "Use when someone asks how many of a product could be produced from "
        "the material currently in stock — '现存的原料能生产多少S0102', 'how "
        "many X can we make', 'what is stopping us making more X', 'which "
        "material runs out first'. Explodes the product's recipe through every "
        "sub-recipe to the raw materials, reads what the warehouse actually "
        "has available, and does the division in code. Never attempt this with "
        "a query: it needs the recipe AND the stock AND arithmetic across "
        "both, and a query returns one table."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "product": {
                "type": "string",
                "description": "The product's material code, e.g. 'S0102'.",
            },
            "exclude": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Material codes to leave out of the calculation, when the "
                    "question says to ignore one — '不考虑CR0059'. A material "
                    "excluded here takes everything below it in the recipe "
                    "with it."
                ),
            },
        },
        "required": ["product"],
    },
}

_MODULES_TOOL = {
    "name": "explain_modules",
    "description": (
        "Use when someone asks what the system as a whole does, what parts it "
        "has, or where a thing belongs — 'what modules are there', 'what can I "
        "do in UniOps', 'where do I go to claim an expense', 'what is the "
        "difference between OA and Procurement', 'what is MRP for'. Answers "
        "with what each module covers, who uses it, and what you typically do "
        "there. For the types and fields of one document, use "
        "explain_document_types instead."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

_DOC_TYPES_TOOL = {
    "name": "explain_document_types",
    "description": (
        "Use when someone asks what KINDS of a document exist and how they "
        "differ, or what a document IS and what you have to fill in for it — "
        "'what PR types are there', 'what changes when I create a type 5', "
        "'what is the difference between a prepayment and a settlement', "
        "'what do the invoice statuses mean', 'what is a house account', "
        "'what is the difference between a physical and a service receipt'. "
        "Answers with what each kind is for and what the system actually makes "
        "you supply, read off the create schema and the submit gates rather "
        "than from any description of them. This is a question about the FORM "
        "and the document itself, not about who signs off — explain_process "
        "answers the second and is the wrong answer to the first."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_type": {
                "type": "string",
                "enum": ["pr", "po", "gr", "pa", "invoice", "agreement"],
                "description": (
                    "pr purchase request · po purchase order · gr goods "
                    "receipt or service confirmation · pa payment application "
                    "(regular/prepayment/settlement/balance) · invoice the "
                    "matching states · agreement purchase agreement "
                    "(house account/recurring/milestone)"
                ),
            },
        },
        "required": ["doc_type"],
    },
}

_EXPLAIN_PLAN_TOOL = {
    "name": "explain_plan",
    "description": (
        "Use when someone asks WHY the production plan says what it says — "
        "'why are we making 20 tonnes of X in the second week of October', "
        "'where does this quantity come from', 'why is this scheduled so "
        "late'. Reads the derivation off the plan line: forecast, opening "
        "stock, carry-in, and the flags the engine set. For the plan's numbers "
        "themselves, query mrp_mps_line instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "material_code": {
                "type": "string",
                "description": "The material, e.g. 'S0102'. Give it whenever "
                               "the question names one.",
            },
            "month": {
                "type": "string",
                "description": "Demand month as YYYY-MM, e.g. '2026-10'.",
            },
            "week_start": {
                "type": "string",
                "description": "Monday of the plan week as YYYY-MM-DD. Only "
                               "when the question names a specific week AND "
                               "you know the date; do not guess which Monday "
                               "'the second week' is — pass the month and let "
                               "the weeks come back.",
            },
        },
        "required": [],
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


def _today_note() -> str:
    """Tell the model what day it is.

    Without this it dates things from its own training data: asked for "the
    September orders" it produced 2024-09-01..2024-09-30 and reported, quite
    confidently, that there were none. The query was valid, the answer was
    empty, and nothing about the reply suggested the year had been invented.

    Deliberately in the user turn rather than the cached system block, so the
    schema prefix keeps its cache across the day boundary.
    """
    now = datetime.now(timezone.utc)
    return (
        f"\n\nToday is {now:%Y-%m-%d} ({now:%A}). Resolve every date against "
        f"this, never against anything you remember. A month or day given "
        f"without a year means the most recent one that has already started — "
        f"normally {now.year}, or {now.year - 1} if {now.year} would put it in "
        f"the future."
    )


def _context_note(context: dict | None) -> str:
    """What the user is looking at, so "this PO" has a referent."""
    if not context:
        return ""
    bits = [f"{k}={v}" for k, v in context.items() if v]
    return ("\n\nThe person is currently looking at: " + ", ".join(bits) +
            ". Resolve 'this'/'that' against it when it fits the question.") if bits else ""


async def plan(schema: list[dict], message: str, context: dict | None = None,
               retry_error: str | None = None,
               history: list[dict] | None = None) -> dict:
    """Ask for a query description.

    Returns {"kind": "query", "query": {...}} or
            {"kind": "cannot", "reason": ..., "explanation": ...}.
    `retry_error` feeds a validator rejection back for one more attempt — the
    rejection names what is available, so the second try is usually right.

    `history` is the conversation so far, which is what lets "it" and "that one"
    resolve. Someone who has just asked about PO-400-2608-10 and then asks "who
    is it with?" is not going to repeat the number, and without the prior turns
    the only honest answer is to ask which document they mean — correct, and
    useless. The history only shapes what the model UNDERSTANDS; it never widens
    what the query may reach, which is settled downstream by the row scope.
    """
    import anthropic

    user_text = message + _today_note() + _context_note(context)
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
            tools=[_QUERY_TOOL, _CHECK_TOOL, _WORKFLOW_TOOL, _NEXT_TOOL,
                   _PROCESS_TOOL, _EXPLAIN_PLAN_TOOL, _DOC_TYPES_TOOL,
                   _MODULES_TOOL, _PRODUCIBLE_TOOL, _CANNOT_TOOL],
            # Forcing a tool call removes the third option — prose that sounds
            # like an answer but was never checked against any data.
            tool_choice={"type": "any"},
            messages=(history or []) + [{"role": "user", "content": user_text}],
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
        if block.name == "whats_next":
            return {"kind": "next", "usage": _usage(resp)}
        if block.name == "explain_process":
            return {"kind": "process", **dict(block.input), "usage": _usage(resp)}
        if block.name == "how_many_can_we_make":
            return {"kind": "producible", **dict(block.input), "usage": _usage(resp)}
        if block.name == "explain_modules":
            return {"kind": "modules", "usage": _usage(resp)}
        if block.name == "explain_document_types":
            return {"kind": "doc_types", **dict(block.input), "usage": _usage(resp)}
        if block.name == "explain_plan":
            return {"kind": "plan", **dict(block.input), "usage": _usage(resp)}
        if block.name == "explain_workflow":
            return {"kind": "workflow", **dict(block.input), "usage": _usage(resp)}
        if block.name == "check_document":
            return {"kind": "check", **dict(block.input), "usage": _usage(resp)}
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
    if "matched_rows" in result:
        payload["matched_rows"] = result["matched_rows"]
    if "totals" in result:
        payload["totals"] = result["totals"]
    if "assumption" in result:
        payload["assumption"] = result["assumption"]
    if "value_labels" in result:
        # The payload is a whitelist, so a key added to the query result does
        # not reach this call by itself — which is how the mapping got built,
        # threaded through, and still reported back as "type 2".
        payload["value_labels"] = result["value_labels"]
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


_PREFLIGHT_SYSTEM = """\
You are explaining to a colleague why a document can or cannot be submitted.

You are given the document number and the result of running the real gates
against it. Each check says whether it passed, what it says when it fails, and
crucially whether it is something THIS PERSON can fix.

- Answer in the language the question was asked in.
- Lead with the verdict: can it be submitted, or not.
- List only the checks that FAILED. Passing ones are noise.
- Separate what they can fix themselves from what they cannot. For anything with
  fixable_by_user false, say plainly that it is not theirs to fix and name the
  owner if one is given. Getting this wrong sends someone to change a setting
  they have no access to.
- If a failed check has a fix_route, mention where to go.
- If complete is false, the approval engine could not be reached, so the list is
  partial — say so rather than implying it is the whole picture.
- Do not invent reasons, next steps, or people. The checks are all you know.
"""


async def narrate_preflight(message: str, doc_number: str | None,
                            preflight: dict) -> dict:
    """Turn a gate result into a reply. Same no-new-facts rule as narrate()."""
    import anthropic

    payload = {
        "question": message,
        "document": doc_number,
        "allowed": preflight.get("allowed"),
        "complete": preflight.get("complete"),
        "checks": preflight.get("checks", []),
    }
    try:
        resp = await _client().messages.create(
            model=MODEL,
            max_tokens=_NARRATE_MAX_TOKENS,
            system=_PREFLIGHT_SYSTEM,
            messages=[{"role": "user",
                       "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        )
    except anthropic.APIConnectionError as exc:
        raise LlmUnavailable(f"Could not reach the model: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LlmUnavailable("The shared API quota is exhausted right now") from exc
    except anthropic.APIStatusError as exc:
        log.error("preflight narration failed %s: %s", exc.status_code, exc.message)
        raise LlmUnavailable(f"Model returned {exc.status_code}") from exc

    _log_usage("narrate_preflight", resp)
    return {"text": "".join(b.text for b in resp.content if b.type == "text").strip(),
            "usage": _usage(resp)}


_WORKFLOW_SYSTEM = """\
You are telling a colleague where a document stands in its approval chain.

You are given the chain's steps, everything already done to it, and where it is
sitting right now.

- Answer in the language the question was asked in.
- Lead with the answer to what they asked. If they asked who it is with, name
  that first; do not open with a recap of the whole chain.
- current_step.approver_name is the person holding it. When that is null the
  step is broadcast to everyone holding the role — say it is with whoever holds
  that role (current_step.label), not with nobody.
- An approver_name containing "standing in for" means someone is covering a
  delegation. Say so; it matters to whoever is chasing it.
- history is what has already happened, most useful as "approved by X on DATE".
  Include comments when they are there — a returned document's comment is
  usually the reason.
- current_step is null when the document is not awaiting approval. Then say what
  its status is instead of inventing a waiting party.
- If steps_available is false the engine could not be reached, so you have the
  history but not the full chain — say that rather than implying the chain is
  short.
- Never invent a name, a step, a date or a role that is not in what you were
  given.
"""


_NARRATE_PLAN_SYSTEM = """\
You explain why a production plan schedules what it does, to a planner who
knows the product and not the algorithm.

The derivation arrives as ordered steps read off the plan line, plus any flags
the planning engine set. Walk the steps in order — demand, what was already
available, what is left to make — and then give the flags, which are the reasons
a line departs from that plain subtraction.

Rules:
- Every figure is already in the data. Do not compute new ones, and do not
  round: a planner reconciles these against their own spreadsheet.
- The flag meanings given are the engine's own descriptions. Use them; do not
  soften or reinterpret them.
- If `arithmetic_accounts_for_it` is false and no flag explains the difference,
  say plainly that the quantity does not follow from the recorded figures and
  that something outside them moved it. Do not invent a reason to close the gap.
- If several lines come back, the question did not identify one. Show them and
  ask which — a plan can carry several lines for one material and month.
- Answer in the language the question was asked in.
"""


async def narrate_plan(question: str, explained: list[dict],
                       history=None) -> dict:
    """Turn one or more derivations into an explanation."""
    payload = {"question": question, "lines": explained}
    client = _client()
    resp = await client.messages.create(
        model=MODEL, max_tokens=1200,
        system=[{"type": "text", "text": _NARRATE_PLAN_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=(history or []) + [{
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, default=str),
        }],
    )
    return {"text": "".join(b.text for b in resp.content if b.type == "text").strip(),
            "usage": _usage(resp)}


async def narrate_workflow(message: str, view: dict) -> dict:
    """Explain an approval chain. Same no-new-facts rule as the other narrators."""
    import anthropic

    try:
        resp = await _client().messages.create(
            model=MODEL,
            max_tokens=_NARRATE_MAX_TOKENS,
            system=_WORKFLOW_SYSTEM,
            messages=[{"role": "user",
                       "content": json.dumps(view, ensure_ascii=False, default=str)}],
        )
    except anthropic.APIConnectionError as exc:
        raise LlmUnavailable(f"Could not reach the model: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LlmUnavailable("The shared API quota is exhausted right now") from exc
    except anthropic.APIStatusError as exc:
        log.error("workflow narration failed %s: %s", exc.status_code, exc.message)
        raise LlmUnavailable(f"Model returned {exc.status_code}") from exc

    _log_usage("narrate_workflow", resp)
    return {"text": "".join(b.text for b in resp.content if b.type == "text").strip(),
            "usage": _usage(resp)}


_GUIDE_SYSTEM = """\
You are helping a colleague who may never have been trained on this system.

- Answer in the language the question was asked in. Match it exactly: a
  question in Chinese is answered in Chinese, not in another CJK language —
  one run of this prompt answered a Chinese question in Korean, which reads as
  a broken system even when every number in it is right.
- Write for someone who does not know the jargon. Say "the person who approves
  for your department", not "the dept_manager node".
- For a task list: lead with how many things are waiting and what the most
  urgent one is. Group by what they have to DO, not by document type. Mention
  the document number so they can find it.
- A task marked assigned_to_me_personally false reached them through a role they
  hold — worth saying, because "why is this mine?" is the next question.
- For a process: walk the steps in order, in plain sentences. A step with
  `conditional` true does NOT always run — give its `condition` verbatim in your
  own words, because someone told a step is mandatory will wait for an approval
  that is never coming. A step without that flag always runs.
- If steps_available is false the engine could not be reached; say the chain
  could not be read rather than describing one from memory. You do not know
  this process except from what you were given.
- For document types: this is the form, not the approval chain. Do not describe
  who approves anything — that was not asked and is a different question.
  Give the types as a table when there are more than three, one row each, and
  make the columns the things that actually differ. `covers` and `when_to_pick`
  say what the type is for; `required_before_submit` is what the system will
  stop them on; `receipt` is how the purchase gets confirmed as received.
  Read `requirements_are` before describing either requirement list — it says
  exactly what each covers. `required_to_create_any` applies to every kind;
  `required_before_submit` is the EXTRA gates checked when the document leaves
  draft, and an empty list there means no extra gates, never "no requirements".
  Neither list is every difference between the kinds, so do not present it as
  one. `what_it_is` explains the document itself — lead with it when they seem
  to be asking what the thing is, not just how the kinds differ.
  If `how_types_are_set` is present, say so: for most documents the kind is not
  chosen by the person asking, and someone hunting for a dropdown that does not
  exist needs telling.
  If a type carries `notes`, work them in; they are there because they matter.
  End with the one line a requester most needs: which type their case sounds
  like, if the question named one.
- For "how many can we make": the number in `can_make` is the answer and it was
  computed for you — never recompute it, never adjust it, and never add a
  material's quantity to another's. Lead with the number, then name what is
  limiting it (`limited_by`) and what that material's situation is, because
  "what do I do about it" is the next question. A material whose problem says
  stock exists but none is usable is a different problem from one with no stock
  at all — say which. Mention anything in `excluded` and `could_not_compute`;
  a figure computed with a material left out is only honest if the omission is
  stated. Pass on `basis` in your own words — this is a rough reference, and
  presenting it as a production commitment would be the wrong reading.
- For modules: answer what they asked and no more. Someone asking where to
  claim an expense wants OA named and a sentence on why, not a tour of eight
  modules. Give the full list only when they asked for the full list.
  `can_explain_further` is the only place you may offer to go deeper; offering
  a document kind that is not in it promises something that will then be
  refused.
- Never invent a step, a role, a rule, a task, a module, or a field. If a type
  has no requirement beyond what every type has, say that plainly — "nothing
  extra" is an answer, and inventing a distinction to fill the row is not.
"""


async def narrate_guide(message: str, payload: dict) -> dict:
    """Explain tasks or a process. Same no-new-facts rule as the others."""
    import anthropic

    try:
        resp = await _client().messages.create(
            model=MODEL,
            max_tokens=_NARRATE_MAX_TOKENS,
            system=_GUIDE_SYSTEM,
            messages=[{"role": "user",
                       "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        )
    except anthropic.APIConnectionError as exc:
        raise LlmUnavailable(f"Could not reach the model: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LlmUnavailable("The shared API quota is exhausted right now") from exc
    except anthropic.APIStatusError as exc:
        log.error("guide narration failed %s: %s", exc.status_code, exc.message)
        raise LlmUnavailable(f"Model returned {exc.status_code}") from exc

    _log_usage("narrate_guide", resp)
    return {"text": "".join(b.text for b in resp.content if b.type == "text").strip(),
            "usage": _usage(resp)}


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
    """Best-effort log line. Do NOT rely on it.

    This used to be the whole accounting story, and it recorded nothing: the
    application logger has no handler on the request path in the dev deployment,
    so every one of these went to a logger with nowhere to write. Spend is
    persisted by record_usage() instead; this line is only convenient when
    logging happens to be configured.
    """
    u = _usage(resp)
    log.info("assistant | stage=%s | model=%s | in=%s | out=%s | cache_read=%s",
             stage, MODEL, u.get("input_tokens"), u.get("output_tokens"),
             u.get("cache_read_input_tokens"))


async def record_usage(db, user_id, stage: str, usage: dict) -> None:
    """Persist what one call cost, on a session of its own.

    Not the request's session, and not for tidiness: controlled_query runs its
    statement inside `SET LOCAL TRANSACTION READ ONLY`, so an INSERT afterwards
    fails for the rest of that transaction. That is exactly what happened —
    plan's usage was recorded, narrate's silently was not, and the failure went
    to log.exception, which in this deployment writes nowhere. Bookkeeping that
    can be disabled by the thing it is measuring is not bookkeeping.

    Never raises: losing a row here must not lose an answer the user waited for
    and the key was already billed for.
    """
    if not usage:
        return
    try:
        from app.db.session import AsyncSessionLocal
        from app.models.assistant_usage import AssistantUsage
        async with AsyncSessionLocal() as own:
            own.add(AssistantUsage(
                user_id=user_id, stage=stage, model=MODEL,
                input_tokens=usage.get("input_tokens", 0) or 0,
                output_tokens=usage.get("output_tokens", 0) or 0,
                cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
            ))
            await own.commit()
    except Exception:  # noqa: BLE001 — bookkeeping must never break the reply
        log.exception("assistant: could not record usage for stage=%s", stage)
