"""The wording of the emails Safety sends to people in the plant (UAT 13).

Kirk: "Create a way to change the default email templates that are sent to
workers to ensure proper grammar and sentence structure. the wording for
assigned CAPA's should include the priority, the original incident number,
CAPA number, the 'what will be done', the detail, and the due date. The
incident and CAPA numbers are clickable."

Each email here has a default subject and body with {placeholders}. HSE may
replace either under Settings → Email wording; what they write is kept in
`ehs_config.email_templates` as {key: {subject, body}}. A placeholder HSE did
not use is simply not in the email; one that does not exist is refused when
saved, so a mistyped name never reaches anybody as "{ownr_name}".

HSE's own notices (an incident reported, a clock escalated) and the JHSC
recommendation (which is composed and previewed as it is sent) are not here.
"""
from __future__ import annotations

import string
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    audience: str
    when: str
    subject: str
    body: str
    placeholders: dict[str, str] = field(default_factory=dict)
    sample: dict[str, str] = field(default_factory=dict)


_ACTION = {
    "owner_name": "Who owns it now",
    "action_no": "The corrective action's number (a link in the email)",
    "source_no": "The incident or record it was raised on (a link in the email)",
    "priority": "Low, Normal or High",
    "title": "What will be done",
    "detail": "The detail",
    "due_date": "The due date",
    "location": "Where the incident happened",
    "cause": "The cause it addresses",
}
_ACTION_SAMPLE = {
    "owner_name": "Dario Okafor", "action_no": "ACT-2026-0142", "source_no": "INC-2026-0134",
    "priority": "High", "title": "Fit an interlocked guard to the capping head",
    "detail": "Order the guard from the OEM and fit it before the line restarts.",
    "due_date": "20 November 2026", "location": "Production › 104 – Packaging › Line 1",
    "cause": "Guard removed for cleaning and not refitted",
}

TEMPLATES: dict[str, Template] = {t.key: t for t in (
    Template(
        "action_owner_told", "A corrective action is assigned",
        "Its owner, and whoever Settings → Who is told → Corrective actions adds",
        "When a corrective action is raised for somebody",
        "Corrective action {action_no} assigned to {owner_name}",
        "Hello {owner_name},\n\n"
        "A corrective action has been assigned to you.\n\n"
        "Corrective action: {action_no}\n"
        "Raised on: {source_no}\n"
        "Priority: {priority}\n"
        "Due: {due_date}\n\n"
        "What will be done:\n{title}\n\n"
        "Detail:\n{detail}\n\n"
        "The cause it addresses: {cause}\n\n"
        "Please complete it by the due date and record your progress in Safety.",
        _ACTION, _ACTION_SAMPLE),
    Template(
        "action_reassigned", "A corrective action is reassigned",
        "The new owner, the previous owner, and whoever Who is told adds",
        "When a corrective action is given to somebody else, or handed over from somebody who left",
        "Corrective action {action_no} reassigned to {owner_name}",
        "Hello {owner_name},\n\n"
        "Corrective action {action_no} has been reassigned from {previous_owner} to you "
        "by {reassigned_by}.\n"
        "Reason: {reason}\n\n"
        "Raised on: {source_no}\n"
        "Priority: {priority}\n"
        "Due: {due_date}\n\n"
        "What will be done:\n{title}\n\n"
        "Detail:\n{detail}",
        {**_ACTION, "previous_owner": "Who had it before", "reassigned_by": "Who reassigned it",
         "reason": "Why it was moved"},
        {**_ACTION_SAMPLE, "previous_owner": "Priya Raman", "reassigned_by": "Marie Tremblay",
         "reason": "Priya is on leave until December"}),
    Template(
        "action_escalation", "A corrective action is overdue",
        "Its owner and their supervisor; the HSE Manager at the last step",
        "When a corrective action passes the overdue steps under Settings → Rules",
        "Overdue {days_late} days: corrective action {action_no}",
        "Corrective action {action_no} was due on {due_date} and is still open.\n\n"
        "Owner: {owner_name}\n"
        "Priority: {priority}\n\n"
        "What will be done:\n{title}\n\n"
        "Detail:\n{detail}",
        {**_ACTION, "days_late": "How many days overdue it is"},
        {**_ACTION_SAMPLE, "days_late": "7"}),
    Template(
        "toolbox_chase", "A toolbox talk is still to deliver",
        "The person the talk was handed to",
        "Every fortnight until it is signed off, and when HSE sends a reminder",
        "Reminder: toolbox talk “{talk_title}” is still outstanding",
        "Hello {supervisor_name},\n\n"
        "The toolbox talk “{talk_title}” for {team} has not been signed off yet.\n\n"
        "Please hold it with your team and collect everyone’s signature on one device afterwards.",
        {"supervisor_name": "Who it was handed to", "talk_title": "The talk", "team": "The team or department"},
        {"supervisor_name": "Ken Boucher", "talk_title": "Ladder safety", "team": "Packaging"}),
    Template(
        "course_reminder", "A course is tomorrow",
        "Everyone booked on the session",
        "The day before a course session",
        "Tomorrow: {course}",
        "This is a reminder that {course} runs tomorrow, {date}, starting at {time}{place}.",
        {"course": "The session’s title", "date": "Its date", "time": "Its start time",
         "place": "“ at ” and where it is, when known"},
        {"course": "First Aid — CPR C", "date": "17 November 2026", "time": "09:00",
         "place": " at Training Room 2"}),
    Template(
        "document_published", "A document to read and sign",
        "Everyone the document is published to",
        "When a document that needs acknowledging is published",
        "Please read and sign: {document}",
        "{document} (version {version}) has been published and needs your acknowledgement.\n\n"
        "Please open it, read it, and sign at the bottom of the page.",
        {"document": "The document’s title", "version": "Its version"},
        {"document": "Lockout / Tagout Policy", "version": "3"}),
    Template(
        "inspection_due", "An inspection is due",
        "The person a scheduled inspection is assigned to",
        "When a scheduled inspection comes due",
        "Due {due_date}: {form}{where}",
        "{form}{where} is due on {due_date}.",
        {"form": "The form", "where": "“ — ” and the department, when there is one", "due_date": "When it is due"},
        {"form": "Monthly eyewash check", "where": " — Packaging", "due_date": "20 November 2026"}),
    Template(
        "feedback_answered", "HSE replies to feedback",
        "The person who left the comment",
        "When HSE answers a comment on Feedback",
        "HSE has replied to your feedback",
        "You wrote:\n\n{comment}\n\nHSE replied:\n\n{reply}",
        {"comment": "What they wrote", "reply": "HSE’s reply"},
        {"comment": "The eyewash at Line 2 is blocked by pallets.", "reply": "Thank you — cleared and marked off."}),
)}


class _Missing(dict):
    def __missing__(self, key):  # never reached after validation; a safety net
        return ""


def placeholders_in(text: str) -> list[str]:
    """The names used in {braces}. Raises ValueError on a brace out of place."""
    return [name for _lit, name, _spec, _conv in string.Formatter().parse(text) if name is not None]


def problems(key: str, subject: str, body: str) -> list[str]:
    """What is wrong with HSE's wording, in their words — empty when it is fine."""
    t = TEMPLATES[key]
    out: list[str] = []
    if not subject.strip():
        out.append("The subject is empty.")
    if not body.strip():
        out.append("The message is empty.")
    for label, text in (("subject", subject), ("message", body)):
        try:
            names = placeholders_in(text)
        except ValueError:
            out.append(f"The {label} has a {{ or }} that does not pair up. "
                       "Write a placeholder as {name}.")
            continue
        unknown = sorted({n for n in names if n not in t.placeholders})
        if unknown:
            out.append(f"The {label} uses " + ", ".join("{" + n + "}" for n in unknown)
                       + ", which this email does not have. It can use "
                       + ", ".join("{" + n + "}" for n in t.placeholders) + ".")
    return out


def fill(text: str, values: dict) -> str:
    return text.format_map(_Missing({k: ("" if v is None else str(v)) for k, v in values.items()}))


async def wording(db: AsyncSession, key: str) -> tuple[str, str]:
    """HSE's subject and body for this email, or the defaults."""
    from app.models.config import EhsConfig
    t = TEMPLATES[key]
    cfg = await db.get(EhsConfig, 1)
    mine = ((cfg.email_templates if cfg else None) or {}).get(key) or {}
    subject, body = mine.get("subject") or t.subject, mine.get("body") or t.body
    if problems(key, subject, body):
        # Saved before a placeholder was withdrawn, say: the default, not a broken email.
        return t.subject, t.body
    return subject, body


async def compose(db: AsyncSession, key: str, values: dict) -> tuple[str, str]:
    subject, body = await wording(db, key)
    return fill(subject, values).strip(), fill(body, values).strip()
