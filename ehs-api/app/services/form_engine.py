"""Question types, answer normalisation, and what counts as a failure.

The thirteen types are the ones HSE's own forms use — counted off the Medical,
Equipment, Near Miss, racking inspection and fit test samples — plus `group`,
which their racking inspection needs (nine groups of checks) and which the v0.3
list missed because a group is form structure rather than a question type.

Two things are deliberately not here:

**No expression language for `fail_on`.** A checklist question fails when the
answer is one of a set of values. `{"equals": "no"}` covers every failing
question in every sample HSE sent. A condition language would be more powerful
and would also be a thing to debug at two in the morning when an inspection
stops raising actions.

**Answers normalise into typed columns, not JSON.** "Every reading over 40" has
to be a query. The snapshot keeps the form readable; these rows keep it
countable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException, status


@dataclass(frozen=True)
class QuestionType:
    code: str
    label: str
    # Which typed column the answer lands in.
    storage: str  # text | number | date | bool | ids | none
    # A group or an instruction block holds no answer at all.
    answerable: bool = True
    can_fail: bool = False


TYPES: tuple[QuestionType, ...] = (
    # Structure, not questions.
    QuestionType("group", "Section heading", "none", answerable=False),
    QuestionType("instruction", "Instruction text", "none", answerable=False),

    QuestionType("yes_no_na", "Yes / No / N/A", "text", can_fail=True),
    QuestionType("text", "Short text", "text"),
    QuestionType("textarea", "Long text", "text"),
    QuestionType("number", "Number", "number", can_fail=True),
    QuestionType("date", "Date", "date"),
    # Separate from date because HSE's forms ask them as two fields.
    QuestionType("time", "Time", "text"),
    QuestionType("select", "Single choice", "text", can_fail=True),
    QuestionType("multi_select", "Multiple choice", "ids"),
    QuestionType("person", "One person", "ids"),
    QuestionType("person_multi", "Several people", "ids"),
    QuestionType("department", "Department", "ids"),
    QuestionType("location", "Plant area", "ids"),
    QuestionType("vocabulary", "From a reference list", "ids"),
    QuestionType("photo", "Photographs", "ids"),
    QuestionType("signature", "Signature", "text"),
)

BY_CODE = {t.code: t for t in TYPES}


# ★ Which kinds of form have a verdict at all.
#
# Round 5, TC-09: "minutes are a documentation of a meeting, there is no pass
# or fail in the form nor in the minutes." He is right, and it was not only the
# minutes: the builder offered a fail condition on every yes/no question of
# every template, so a toolbox talk and a job analysis carried the apparatus of
# an inspection verdict too.
#
# An inspection, an audit and a permit check record whether something was found
# acceptable — that is what they are for, and a failure takes equipment out of
# service. Minutes, a talk, an orientation and a job analysis record what was
# said or agreed. Nothing about them passes or fails.
KINDS_WITH_A_VERDICT = frozenset({"inspection", "audit", "permit"})


def kind_can_fail(category: str | None) -> bool:
    return (category or "") in KINDS_WITH_A_VERDICT


class SchemaError(ValueError):
    """A template that cannot be published."""


def validate_schema(schema: list[dict]) -> None:
    """Reject a template that would break at fill-in time rather than at save."""
    if not isinstance(schema, list):
        raise SchemaError("Schema must be a list of questions")

    seen: set[str] = set()
    for i, q in enumerate(schema):
        where = f"question {i + 1}"
        if not isinstance(q, dict):
            raise SchemaError(f"{where}: not an object")

        key = q.get("key")
        if not key or not isinstance(key, str):
            raise SchemaError(f"{where}: needs a key")
        if key in seen:
            # Answers are unique per (submission, question_key); a duplicate key
            # would silently overwrite an answer rather than fail.
            raise SchemaError(f"{where}: duplicate key '{key}'")
        seen.add(key)

        qtype = q.get("type")
        if qtype not in BY_CODE:
            raise SchemaError(f"{where}: unknown type '{qtype}'")
        if not q.get("label"):
            raise SchemaError(f"{where}: needs a label")

        spec = BY_CODE[qtype]
        if q.get("fail_on") is not None and not spec.can_fail:
            raise SchemaError(f"{where}: a {qtype} question cannot have a fail condition")
        if q.get("required") and not spec.answerable:
            raise SchemaError(f"{where}: a {qtype} holds no answer, so it cannot be required")
        if qtype in ("select", "multi_select") and not q.get("options"):
            raise SchemaError(f"{where}: needs options")
        if qtype == "vocabulary" and not q.get("vocabulary"):
            raise SchemaError(f"{where}: needs a vocabulary code")


def _as_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def is_failure(question: dict, value: Any) -> bool:
    """Whether this answer trips the question's fail condition.

    Supported forms, and only these:

        {"equals": "no"}        the answer is exactly this
        {"in": ["no", "n/a"]}   the answer is one of these
        {"gt": 40} / {"lt": 5}  numeric bounds

    An unanswered question never fails. Missing an answer is a gap in the
    inspection, not a defect in the plant, and conflating the two produces
    corrective actions against questions nobody got to.
    """
    cond = question.get("fail_on")
    if not cond or value in (None, "", []):
        return False

    if "equals" in cond:
        return str(value).strip().lower() == str(cond["equals"]).strip().lower()
    if "in" in cond:
        wanted = {str(v).strip().lower() for v in cond["in"]}
        return str(value).strip().lower() in wanted

    num = _as_decimal(value)
    if num is None:
        return False
    if "gt" in cond and num > Decimal(str(cond["gt"])):
        return True
    if "lt" in cond and num < Decimal(str(cond["lt"])):
        return True
    return False


def _as_date(value: Any) -> date:
    """A date answer, as a date.

    ★★ This column is a DATE and was being handed the string. asyncpg asks the
    value for `toordinal()` and a str has none, so every save of a date answer
    answered 500 — and the front end queues today's date into every date
    question the moment a form is opened, so the first keystroke on *any* form
    with a date on it hit this. The submit guard added in round 4 then did its
    job: the batch never cleared, so the form could not be submitted at all.

    Round 5 reported it three times over as "What you typed has not been saved
    yet" — on an equipment inspection, a ladder inspection and the committee
    minutes. One bug, three cases, and the only reason it read as a connection
    problem is that the message on the screen was about the batch rather than
    about the answer.

    A malformed date is 422 rather than 500: it is the caller's input.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        # Trailing time is tolerated — a browser date input sends a bare date,
        # but an imported value may carry one.
        return date.fromisoformat(text[:10])
    except ValueError:
        # ★ The message quotes what was sent, not the first ten characters of
        # it: "'last Tuesd' is not a date" reads like the software lost half
        # the answer.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"'{text[:40]}' is not a date. Dates are yyyy-mm-dd.",
        ) from None


def normalise(question: dict, value: Any) -> dict:
    """One answer, spread across the typed columns.

    Returns the kwargs for a FormAnswer row. The label is snapshotted alongside
    so an answer read on its own still says what was asked.
    """
    qtype = question["type"]
    spec = BY_CODE[qtype]
    row: dict[str, Any] = {
        "question_key": question["key"],
        "question_label": question["label"][:500],
        "question_type": qtype,
        "value_text": None,
        "value_number": None,
        "value_date": None,
        "value_bool": None,
        "value_ids": [],
        "value_labels": [],
        "is_failure": is_failure(question, value),
    }

    if value in (None, "", []):
        return row

    if spec.storage == "number":
        row["value_number"] = _as_decimal(value)
    elif spec.storage == "date":
        row["value_date"] = _as_date(value)
    elif spec.storage == "bool":
        row["value_bool"] = bool(value)
    elif spec.storage == "ids":
        # Accepts a bare id, a list of ids, or [{id, label}] so a picker can
        # send the label it displayed and have it snapshotted.
        items = value if isinstance(value, list) else [value]
        ids, labels = [], []
        for it in items:
            if isinstance(it, dict):
                ids.append(str(it.get("id", "")))
                if it.get("label"):
                    labels.append(str(it["label"]))
            else:
                ids.append(str(it))
        row["value_ids"] = [i for i in ids if i]
        row["value_labels"] = labels
    else:
        row["value_text"] = str(value)

    # yes_no_na is stored as text for reporting, and as a bool for the cases
    # where a plain true/false is what a caller wants.
    if qtype == "yes_no_na":
        row["value_bool"] = {"yes": True, "no": False}.get(str(value).strip().lower())

    return row


def answerable(schema: list[dict]) -> list[dict]:
    """The questions that hold an answer — groups and instructions dropped."""
    return [q for q in schema if BY_CODE[q["type"]].answerable]
