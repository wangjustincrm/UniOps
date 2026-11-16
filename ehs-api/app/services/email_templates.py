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

★ UAT 14: "on the email wording registry, include all email templates. new
incident is missing." Every email Safety sends is here now — HSE's own
notices (an incident reported, a status change, a legal clock), the JHSC
ones, the hand-over of a leaver's work and the certificate expiry list too.
For the JHSC recommendation sent to named people, this is the wording the
sender's preview opens on; they may still change that one email as they send.

★ A line whose placeholders are all empty is left out. Many emails say a thing
only when there is something to say — "Where: …" on a record with no place,
the Ministry sentence on an incident that is not reportable — and HSE writes
those as ordinary lines instead of conditions.
"""
from __future__ import annotations

import re
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
    # The heading it is listed under on Settings → Email wording.
    group: str = "Corrective actions"
    # The placeholders whose value becomes a link to the record in the email.
    linked: tuple[str, ...] = ()


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
_ACTION_LINKED = ("action_no", "source_no")

_INCIDENT_SAMPLE = {
    "incident_no": "INC-2026-0134", "kind": "First Aid Injury", "title": "Cut on the left hand at the capping head",
    "where": "Production › 104 – Packaging › Line 1", "occurred_at": "16 November 2026, 09:40",
}

# ★ UAT 14: the emails that were not in the registry. Each default is the
# wording the email had, so nothing changes until HSE changes it.
_MORE: tuple[Template, ...] = (
    Template(
        "incident_reported", "A new incident is reported",
        "HSE and the people under Settings → Who is told for that kind of incident, the area's owner, "
        "and the reporter and their supervisor where ticked",
        "When an incident is submitted",
        "Safety incident {incident_no}",
        "{incident_no} was reported at {where}.\n\n"
        "{description}\n\n"
        "{area_owner_note}\n\n"
        "{copied_note}\n\n"
        "{ministry_note}\n\n"
        "Recorded as: {injury_class}.",
        {"incident_no": "The incident's number (a link in the email)",
         "kind": "What kind of incident it is", "title": "Its title",
         "where": "Where it happened (“the plant” when no place was given)",
         "occurred_at": "When it happened",
         "reported_by": "Who reported it (“Anonymous” when they asked not to be named)",
         "description": "What the reporter wrote (the first 600 characters)",
         "area_owner_note": "Who is copied as the owner of the area, as a sentence — empty when nobody is",
         "copied_note": "“Copied to the reporter and their supervisor.” — when they are",
         "ministry_note": "The sentence about the Ministry's 48 hours — only on one that must be reported",
         "injury_class": "first aid, medical aid or lost time — when recorded"},
        {**_INCIDENT_SAMPLE, "reported_by": "Dario Okafor",
         "description": "Reached past the guard to clear a jammed cap and caught my hand.",
         "area_owner_note": "Ken Boucher is copied as the area owner for Production/104 – Packaging.",
         "copied_note": "Copied to the reporter and their supervisor.",
         "ministry_note": "", "injury_class": "first aid"},
        group="Incidents", linked=("incident_no",)),
    Template(
        "incident_status", "An incident's status changes",
        "The people under Settings → Who is told for that change, and the reporter and their supervisor where ticked",
        "When an investigation starts, HSE reviews it, it is closed or reopened, or its kind is changed",
        "Safety incident {incident_no}: {change}",
        "{incident_no} — {change}{by}.\n\n"
        "{detail}\n\n"
        "{title}\n"
        "Where: {where}\n\n"
        "{copied_note}\n\n"
        "Sent to the list for this change under Settings → Who is told.",
        {"incident_no": "The incident's number (a link in the email)",
         "change": "investigation started, reviewed by HSE, closed, reopened or kind of incident changed",
         "by": "“ by ” and who made the change, when known",
         "detail": "What changed, where there is more to say (the old and new kind, say)",
         "title": "The incident's title", "where": "Where it happened",
         "kind": "What kind of incident it is", "occurred_at": "When it happened",
         "copied_note": "“Copied to the reporter and their supervisor, as Settings → Who is told asks.” — when they are"},
        {**_INCIDENT_SAMPLE, "change": "closed", "by": " by Marie Tremblay", "detail": "",
         "copied_note": "Copied to the reporter, as Settings → Who is told asks."},
        group="Incidents", linked=("incident_no",)),
    Template(
        "statutory_escalation", "A legal deadline is close or has passed",
        "HSE",
        "As a Ministry, WSIB or JHSC deadline comes close, and again when it passes",
        "{state}: {what} — {record}",
        "The {what} deadline for {record} {when}.\n\n"
        "{regulation}. Due {due}.",
        {"state": "“Due soon” or “OVERDUE”", "what": "Which deadline (MOL report, WSIB Form 7…)",
         "record": "The record it is for", "when": "“is approaching” or “has passed”",
         "regulation": "The regulation that sets it", "due": "When it is due"},
        {"state": "Due soon", "what": "mol report", "record": "INC-2026-0134",
         "when": "is approaching", "regulation": "OHSA s.51(1)", "due": "2026-11-18T09:40"},
        group="Incidents", linked=("record",)),
    Template(
        "feedback_left", "Somebody leaves feedback",
        "HSE",
        "When anybody leaves a comment on Feedback",
        "Safety feedback from {who}",
        "{who} left a comment{page}:\n\n{comment}",
        {"who": "Who left it", "page": "“ (from ” and the page they were on, when known",
         "comment": "What they wrote"},
        {"who": "Ken Boucher", "page": " (from /incidents)",
         "comment": "The eyewash at Line 2 is blocked by pallets."},
        group="Feedback"),
    Template(
        "jhsc_recommendation", "The JHSC makes a recommendation",
        "HSE",
        "When a recommendation is added to a JHSC meeting",
        "JHSC recommendation {meeting_no} #{seq}",
        "Where: {where}\n\n"
        "{recommendation}\n\n"
        "OHSA s.9(20) requires a written response within twenty-one days of the recommendation.",
        {"meeting_no": "The meeting's number", "seq": "The recommendation's number in the meeting",
         "where": "Where it is about, when given", "recommendation": "What the committee recommends"},
        {"meeting_no": "JHSC-2026-0011", "seq": "2", "where": "Production › 104 – Packaging",
         "recommendation": "Fit an interlocked guard to the capping head on Line 1."},
        group="JHSC"),
    Template(
        "jhsc_recommendation_sent", "A JHSC recommendation is sent for a reply",
        "The people the committee sends it to",
        "When the committee sends a recommendation to named people — the sender sees this in the preview and may change it",
        "JHSC recommendation {meeting_no} #{seq} — a written response is owed",
        "Where: {where}\n\n"
        "{recommendation}\n\n"
        "OHSA s.9(20) requires a written response within twenty-one days of the recommendation being made.",
        {"meeting_no": "The meeting's number", "seq": "The recommendation's number in the meeting",
         "where": "Where it is about, when given", "recommendation": "What the committee recommends"},
        {"meeting_no": "JHSC-2026-0011", "seq": "2", "where": "Production › 104 – Packaging",
         "recommendation": "Fit an interlocked guard to the capping head on Line 1."},
        group="JHSC"),
    Template(
        "jhsc_recommendation_answered", "A JHSC recommendation is answered",
        "The people under Settings → Who is told → JHSC, and whoever sent it",
        "When somebody answers a recommendation",
        "JHSC recommendation {meeting_no} #{seq} answered",
        "Where: {where}\n\n"
        "{recommendation}\n\n"
        "Answered by {answered_by}.\n\n"
        "What will be done: {action_title}\n"
        "{action_detail}\n"
        "Owner: {action_owner} · due {action_due} · {action_priority} priority · {action_no}\n\n"
        "{response}",
        {"meeting_no": "The meeting's number", "seq": "The recommendation's number in the meeting",
         "where": "Where it is about, when given", "recommendation": "What the committee recommended",
         "answered_by": "Who answered",
         "action_title": "What will be done — when the answer is a corrective action",
         "action_detail": "The action's detail", "action_owner": "Who owns the action",
         "action_due": "The action's due date", "action_priority": "Low, Normal or High",
         "action_no": "The action's number (a link in the email)",
         "response": "The written answer — when it is not a corrective action"},
        {"meeting_no": "JHSC-2026-0011", "seq": "2", "where": "Production › 104 – Packaging",
         "recommendation": "Fit an interlocked guard to the capping head on Line 1.",
         "answered_by": "Marie Tremblay", "action_title": "Fit an interlocked guard to the capping head",
         "action_detail": "Order from the OEM.", "action_owner": "Dario Okafor",
         "action_due": "2026-11-30", "action_priority": "high", "action_no": "ACT-2026-0142",
         "response": ""},
        group="JHSC", linked=("action_no",)),
    Template(
        "jhsc_minutes", "JHSC minutes are published",
        "The committee's members and HSE",
        "When a meeting's minutes are published",
        "JHSC minutes — {held_on}",
        "{minutes}",
        {"held_on": "The day of the meeting", "meeting_no": "The meeting's number",
         "minutes": "The minutes (the first 2,000 characters)"},
        {"held_on": "2026-11-12", "meeting_no": "JHSC-2026-0011",
         "minutes": "Present: … \n\n1. Guarding on Line 1 — recommendation #2 raised."},
        group="JHSC"),
    Template(
        "jhsc_reassigned", "A JHSC reply is handed to somebody else",
        "The person who now owes the reply",
        "When HSE hands a leaver's work to somebody else and it includes a recommendation awaiting their reply",
        "JHSC recommendation {reference} now awaits your reply",
        "{leaver}'s work has been reallocated, and HSE has asked you to reply to this "
        "JHSC recommendation in their place.\n\n"
        "{recommendation}\n\n"
        "{due_note}\n\n"
        "Why: {reason}",
        {"leaver": "Whose work it was", "reference": "The recommendation (JHSC-2026-0011 #2)",
         "recommendation": "What the committee recommended",
         "due_note": "When the written reply is due, as a sentence",
         "reason": "Why HSE moved it"},
        {"leaver": "Priya Raman", "reference": "JHSC-2026-0011 #2",
         "recommendation": "Fit an interlocked guard to the capping head on Line 1.",
         "due_note": "The written reply is due 3 Dec 2026 09:00 (OHSA s.9(20), 21 days from when it was sent).",
         "reason": "Priya has left the company."},
        group="Handing over work"),
    Template(
        "work_reassigned", "A leaver's work is handed over",
        "Each person something of the leaver's went to",
        "When HSE hands a leaver's open work to other people",
        "You now have {leaver}'s open work in Safety",
        "{leaver}'s work has been reallocated, and HSE ({by}) has handed you their open work in Safety:\n\n"
        "{work}\n\n"
        "Why: {reason}",
        {"leaver": "Whose work it was", "by": "Who in HSE handed it over",
         "work": "The list of what was handed over, a line each", "reason": "Why HSE moved it"},
        {"leaver": "Priya Raman", "by": "Marie Tremblay",
         "work": "Inspections — 2\n  • INS-2026-0040 Monthly eyewash check\n  • INS-2026-0041 Forklift pre-use",
         "reason": "Priya has left the company."},
        group="Handing over work"),
    Template(
        "cert_expiry_notice", "Certificates are about to expire",
        "The address on each rule under Settings → Training notifications",
        "Once for each certificate, as it comes within the rule's number of days",
        "{count} expiring within {days} days",
        "These certificates expire within {days} days:\n\n"
        "{certificates}\n\n"
        "Each certificate is listed once per rule. Rules are kept under Settings → Training notifications.",
        {"count": "How many, as “3 certificates”", "days": "The rule's number of days",
         "certificates": "The list, a line each: who, what, when it expires"},
        {"count": "2 certificates", "days": "30",
         "certificates": "• Ken Boucher — First Aid — CPR C — expires 2026-12-01 (in 15 days)\n"
                         "• Priya Raman — Forklift — expires 2026-12-10 (in 24 days)"},
        group="Training"),
)

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
        _ACTION, _ACTION_SAMPLE, linked=_ACTION_LINKED),
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
         "reason": "Priya is on leave until December"}, linked=_ACTION_LINKED),
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
        {**_ACTION_SAMPLE, "days_late": "7"}, linked=_ACTION_LINKED),
    Template(
        "toolbox_chase", "A toolbox talk is still to deliver",
        "The person the talk was handed to",
        "Every fortnight until it is signed off, and when HSE sends a reminder",
        "Reminder: toolbox talk “{talk_title}” is still outstanding",
        "Hello {supervisor_name},\n\n"
        "The toolbox talk “{talk_title}” for {team} has not been signed off yet.\n\n"
        "Please hold it with your team and collect everyone’s signature on one device afterwards.",
        {"supervisor_name": "Who it was handed to", "talk_title": "The talk", "team": "The team or department"},
        {"supervisor_name": "Ken Boucher", "talk_title": "Ladder safety", "team": "Packaging"},
        group="Toolbox talks and inspections"),
    Template(
        "course_reminder", "A course is tomorrow",
        "Everyone booked on the session",
        "The day before a course session",
        "Tomorrow: {course}",
        "This is a reminder that {course} runs tomorrow, {date}, starting at {time}{place}.",
        {"course": "The session’s title", "date": "Its date", "time": "Its start time",
         "place": "“ at ” and where it is, when known"},
        {"course": "First Aid — CPR C", "date": "17 November 2026", "time": "09:00",
         "place": " at Training Room 2"}, group="Training"),
    Template(
        "document_published", "A document to read and sign",
        "Everyone the document is published to",
        "When a document that needs acknowledging is published",
        "Please read and sign: {document}",
        "{document} (version {version}) has been published and needs your acknowledgement.\n\n"
        "Please open it, read it, and sign at the bottom of the page.",
        {"document": "The document’s title", "version": "Its version"},
        {"document": "Lockout / Tagout Policy", "version": "3"}, group="Documents"),
    Template(
        "inspection_due", "An inspection is due",
        "The person a scheduled inspection is assigned to",
        "When a scheduled inspection comes due",
        "Due {due_date}: {form}{where}",
        "{form}{where} is due on {due_date}.",
        {"form": "The form", "where": "“ — ” and the department, when there is one", "due_date": "When it is due"},
        {"form": "Monthly eyewash check", "where": " — Packaging", "due_date": "20 November 2026"},
        group="Toolbox talks and inspections"),
    Template(
        "feedback_answered", "HSE replies to feedback",
        "The person who left the comment",
        "When HSE answers a comment on Feedback",
        "HSE has replied to your feedback",
        "You wrote:\n\n{comment}\n\nHSE replied:\n\n{reply}",
        {"comment": "What they wrote", "reply": "HSE’s reply"},
        {"comment": "The eyewash at Line 2 is blocked by pallets.", "reply": "Thank you — cleared and marked off."},
        group="Feedback"),
    *_MORE,
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


def fill(text: str, values: dict, *, drop_empty_lines: bool = True) -> str:
    """The text with its placeholders filled in.

    A line whose placeholders are all empty is left out, and the blank lines
    that leaves behind close up (see the module's note). The subject is filled
    with `drop_empty_lines=False`: it is one line, and never left out.
    """
    vals = _Missing({k: ("" if v is None else str(v)) for k, v in values.items()})
    if not drop_empty_lines:
        return text.format_map(vals)
    out: list[str] = []
    for line in text.split("\n"):
        try:
            names = placeholders_in(line)
        except ValueError:   # refused when saved; a safety net
            names = []
        if names and all(not vals.get(n, "").strip() for n in names):
            continue
        out.append(line.format_map(vals))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out))


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
    return fill(subject, values, drop_empty_lines=False).strip(), fill(body, values).strip()
