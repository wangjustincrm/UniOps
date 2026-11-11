"""What Safety emails, and to whom.

Every function here answers one question — who needs to be told about this —
and hands the answer to `mail.queue`, which files it and schedules the send.
Keeping the two apart means the routing rules are readable in one place and
testable without a mail server.

The rule this module is built around, from an earlier UniOps post-mortem: a
task in the inbox is for the person who has to *do* something. Email is for
everybody else — the escalation, the copy, the notice. So the two are not
parallel channels. Where somebody must act, a task is opened and an email
merely tells them it is there; where somebody must merely know, there is an
email and no task, because a task nobody can complete never clears.

Recipient lists come from `ehs_config.incident_notify_groups` where HSE has set
them, and fall back to role membership where they have not. Falling back rather
than sending nothing is deliberate: an unconfigured group must not mean an
injury goes unreported to anyone.
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config import EhsConfig
from app.models.mirrors import User
from app.services import mail
from app.services import tasks as task_service

logger = logging.getLogger(__name__)

# Who hears about an incident when no group has been configured for its
# category. HSE and their coordinator, always — they own the statutory clocks.
DEFAULT_INCIDENT_ROLES = ("ehs_manager", "ehs_coordinator")


async def by_roles(db: AsyncSession, roles: tuple[str, ...]) -> list[User]:
    """Active holders of any of these roles, primary or additional.

    Reads `user_roles` as well as `users.role` for the same reason
    `uniops_authz` does: a first aider or JHSC member holds that in addition to
    their job, and looking only at the primary role would miss all of them.

    Public because the toolbox talk needed the same answer and asked it a
    different way, looking at primary roles only — so a supervisor holding that
    role the way it is designed to be held received nothing, and nothing looked
    wrong because the completion rate counted only the deliveries that existed.
    One implementation, since two of them had already drifted.
    """
    from sqlalchemy import text

    extra = set((await db.execute(
        text("SELECT ur.user_id FROM user_roles ur "
             " JOIN role_defs rd ON rd.code = ur.role_code AND rd.is_active "
             " WHERE ur.role_code = ANY(:roles)"),
        {"roles": list(roles)})).scalars().all())

    rows = (await db.execute(
        select(User).where(User.is_active.is_(True)))).scalars().all()
    return [u for u in rows if u.role in roles or u.id in extra]


async def roles_held_by(db: AsyncSession, user_id: uuid.UUID, primary: str) -> set[str]:
    """Every role this person holds — the one on their record and the ones
    granted alongside it.

    The mirror of `by_roles`, asked from the other end. Same reason: a role
    held in addition to somebody's job is how a JHSC member or a first aider
    holds theirs, and reading only the primary role misses all of them.
    """
    from sqlalchemy import text

    extra = set((await db.execute(
        text("SELECT ur.role_code FROM user_roles ur "
             " JOIN role_defs rd ON rd.code = ur.role_code AND rd.is_active "
             " WHERE ur.user_id = :uid"),
        {"uid": user_id})).scalars().all())
    return extra | ({primary} if primary else set())


async def _config(db: AsyncSession) -> EhsConfig | None:
    return (await db.execute(select(EhsConfig).where(EhsConfig.id == 1))).scalar_one_or_none()


async def _incident_audience(db: AsyncSession, incident_type: str | None) -> list[User]:
    """Who hears about an incident of this kind.

    ★ Keyed on the incident type — first_aid, lost_time, near_miss and the rest
    of HSE's twelve — not on the category.
    #
    Two reasons, and the second is the one that matters. The parameter used to be
    named `category_code` while the caller passed `incident.category_label`, so
    no configured group could ever have matched; nothing had noticed because
    there was no screen to configure one with. But keying it on the category at
    all would have been the same fault wearing a fix: on the dev database 60
    incidents carry 4 categories between them, because only five of the twelve
    types imply one. A list configured per category would have applied to four
    incidents and silently fallen through for the other fifty-six.
    #
    Every incident has a type — 59 of those 60 — it is what the reporter picks on
    the form, and its code is an enum in this codebase rather than a vocabulary
    entry, so it cannot be renamed out from under a recipient list.
    """
    cfg = await _config(db)
    groups = (cfg.incident_notify_groups if cfg else None) or {}
    configured = groups.get(incident_type or "", []) or groups.get("*", [])
    if configured:
        picked = await mail.recipients(
            db, [uuid.UUID(str(u)) for u in configured])
        if picked:
            return picked
    return await mail.recipients(
        db, [u.id for u in await by_roles(db, DEFAULT_INCIDENT_ROLES)])


def _include_flags(cfg, key: str) -> dict:
    return ((cfg.incident_notify_include if cfg else None) or {}).get(key) or {}


def _report_list_key(cfg, incident_type: str | None) -> str:
    """Which new-report list is in force, so its ticks are the ones read.

    The kind of incident's own list when it has been set up at all — somebody
    named or a box ticked — and "Any incident" otherwise, the same fall-through
    the names follow.
    """
    key = incident_type or ""
    groups = (cfg.incident_notify_groups if cfg else None) or {}
    if groups.get(key) or any(_include_flags(cfg, key).values()):
        return key
    return "*"


async def reporter_and_supervisor(
    db: AsyncSession, incident, flags: dict,
) -> tuple[list[User], list[str]]:
    """The reporter and their supervisor, where the list asks for them.

    The supervisor is the one recorded against the reporter under Portal →
    Admin → Users; where none is recorded, the person the reporter said they
    told on the form. Returns the people and a line for each saying why they
    are on it.

    ★ Neither for an anonymous report. Copying the reporter would put their
    address on the same email as everybody else's, and copying their supervisor
    would tell the supervisor who reported it — either undoes the anonymity the
    reporter asked for.
    """
    if incident.is_anonymous or not any(flags.values()):
        return [], []
    ids: list[uuid.UUID] = []
    why: list[str] = []
    reporter = await db.get(User, incident.reported_by) if incident.reported_by else None
    if flags.get("reporter") and reporter is not None:
        ids.append(reporter.id)
        why.append("the person who reported it")
    if flags.get("supervisor"):
        boss = reporter.supervisor_id if reporter is not None else None
        boss = boss or incident.reported_to_id
        if boss:
            ids.append(boss)
            why.append("their supervisor")
    return await mail.recipients(db, ids), why


# ── Incidents ────────────────────────────────────────────────────────────────

async def incident_reported(db: AsyncSession, incident) -> None:
    """Tell the people who need to know that something happened.

    Not a task: nobody on this list has been assigned anything yet. Whoever
    ends up investigating gets a task when the incident is classified.
    """
    kind = (incident.extra or {}).get("incident_type")
    people = await _incident_audience(db, kind)

    # ★ Round 6, TC-07: the reporter and their supervisor, where the list in
    # force has them ticked. Added to whoever else is told, never instead.
    cfg = await _config(db)
    also, also_why = await reporter_and_supervisor(
        db, incident, _include_flags(cfg, _report_list_key(cfg, kind)))
    known_ids = {u.id for u in people}
    people = people + [u for u in also if u.id not in known_ids]

    # ★ And whoever is answerable for the area it happened in. Round 4: "when the
    # location where the incident is recorded on a form, ensure the area
    # responsible owner is included in the initial notification message."
    #
    # An owner named for a building is included for anything inside it, so the
    # notice reaches the right supervisor without HSE forwarding it. No task:
    # nobody on this list has been assigned the investigation, and a task
    # nobody can complete never clears.
    from app.services import area_scope

    owners = await area_scope.owners_of(db, incident.location_id)
    named_here = ""
    if owners:
        extra = await mail.recipients(db, [uid for uid, _, _ in owners])
        known = {u.id for u in people}
        people = people + [u for u in extra if u.id not in known]
        # ★ Named in the third person, because this is one email to everybody.
        # It read "You are receiving this because you are down for …", which is
        # addressed to the area owner and goes to HSE and the notify group as
        # well — so most recipients were told they are responsible for an area
        # they are not. Found in review by uniops-9a. Still stated, because an
        # email with no reason reads as a circular and the area is the reason.
        who = ", ".join(sorted({name for _, name, _ in owners if name}))
        where = ", ".join(sorted({path for _, _, path in owners}))
        named_here = (f"{who} is copied as the area owner for {where}."
                      if who else f"Nobody is down for {where}.")

    if not people:
        return

    where = incident.location_path or "the plant"
    lines = [
        f"{incident.incident_no} was reported at {where}.",
        "",
        (incident.description or "")[:600],
    ]
    if named_here:
        lines += ["", named_here]
    if also_why:
        lines += ["", f"Copied to {' and '.join(also_why)}."]
    if incident.mol_reportable:
        lines += ["", "This has been flagged as reportable to the Ministry. "
                      "OHSA s.51(1) allows forty-eight hours from the occurrence "
                      "for the written report."]
    if incident.injury_class:
        lines += ["", f"Recorded as: {incident.injury_class.replace('_', ' ')}."]

    await mail.queue(
        db,
        to=[u.email for u in people],
        subject=f"Safety incident {incident.incident_no}",
        body="\n".join(lines),
        doc_type=task_service.DOC_INCIDENT,
        doc_id=incident.id,
        template_key="incident_reported",
        link=mail.module_link(f"/incidents/{incident.id}"),
        link_label="Open the incident",
    )


# The status changes HSE can name people for. Round 5, TC-15. "Reported" is not
# here: it already has its own lists, per kind of incident, above.
STATUS_EVENTS: dict[str, str] = {
    "under_investigation": "Investigation started",
    "hse_reviewed": "Reviewed by HSE",
    "closed": "Closed",
    "reopened": "Reopened",
    # Round 8, TC-13: "add a notification that the incident class has changed
    # and email it. log the communication on the form."
    "kind_changed": "Kind of incident changed",
}


async def incident_status_changed(
    db: AsyncSession, incident, event: str, *, by_name: str | None,
    detail: str | None = None,
) -> None:
    """Tell the people HSE named for this change that it happened.

    Round 5, TC-15: "provide a way to manage the recipients list on incidents
    when every status changes on an incident form."

    ★ An empty list sends nothing, unlike a new report. A report must never go
    to nobody, so it falls back to HSE; these changes never sent anything
    before the lists existed, and HSE is usually who made the change.

    Email and no task: nobody on the list is being asked to do anything.
    """
    label = STATUS_EVENTS[event]
    cfg = await _config(db)
    ids = ((cfg.incident_status_notify if cfg else None) or {}).get(event) or []
    people = await mail.recipients(db, [uuid.UUID(str(i)) for i in ids])
    # ★ Round 6: "when there are status changes on the incident (investigation,
    # closed) notify the reporter and their supervisor. Make this a rule that
    # can be modified down the road." The two boxes on each list are the rule.
    also, also_why = await reporter_and_supervisor(
        db, incident, _include_flags(cfg, f"status:{event}"))
    known_ids = {u.id for u in people}
    people = people + [u for u in also if u.id not in known_ids]
    if not people:
        return

    lines = [f"{incident.incident_no} — {label.lower()}"
             + (f" by {by_name}." if by_name else "."), ""]
    if detail:
        lines += [detail, ""]
    if incident.title:
        lines.append(incident.title)
    if incident.location_path:
        lines.append(f"Where: {incident.location_path}")
    if also_why:
        lines += ["", f"Copied to {' and '.join(also_why)}, as Settings → Who is told asks."]
    lines += ["", "Sent to the list for this change under Settings → Who is told."]

    await mail.queue(
        db,
        to=[u.email for u in people],
        subject=f"Safety incident {incident.incident_no}: {label.lower()}",
        body="\n".join(lines),
        doc_type=task_service.DOC_INCIDENT,
        doc_id=incident.id,
        template_key=f"incident_{event}",
        link=mail.module_link(f"/incidents/{incident.id}"),
        link_label="Open the incident",
    )


async def statutory_escalated(db: AsyncSession, deadline, *, overdue: bool) -> None:
    """A legal clock is close, or has passed.

    Goes to HSE by email as well as by task, because this is the one category
    where an inbox somebody has not opened today is not good enough.
    """
    people = await mail.recipients(
        db, [u.id for u in await by_roles(db, DEFAULT_INCIDENT_ROLES)])
    if not people:
        return

    what = deadline.kind.replace("_", " ")
    when = "has passed" if overdue else "is approaching"
    await mail.queue(
        db,
        to=[u.email for u in people],
        subject=(f"{'OVERDUE' if overdue else 'Due soon'}: {what} "
                 f"— {deadline.source_ref or ''}").strip(),
        body=(f"The {what} deadline for {deadline.source_ref or 'a Safety record'} "
              f"{when}.\n\n"
              f"{deadline.regulation_ref or 'Statutory obligation'}. "
              f"Due {deadline.due_at.isoformat(timespec='minutes')}."),
        doc_type=task_service.DOC_INCIDENT,
        doc_id=deadline.source_id,
        template_key="statutory_escalation",
        link=mail.module_link("/calendar"),
        link_label="Open the compliance calendar",
    )


# ── Corrective actions ───────────────────────────────────────────────────────

async def action_assigned(
    db: AsyncSession, action, *, previous_owner_id: uuid.UUID | None = None,
    reason: str | None = None, by_name: str | None = None,
) -> None:
    """★ Round 12, WHO-01 / TC-13 / D13: whenever a corrective action is given
    to somebody — raised, reassigned, or handed over from somebody who left —
    its owner is emailed, and Settings → Who is told → Corrective actions adds
    the rest: people named there, the previous owner, the person who reported
    it and the owner's supervisor.

    Until now only an action on an incident was emailed (round 7, TC-09), and
    a reassignment told nobody. An action on an incident is still filed on the
    incident, so it is listed under its Notifications sent; any other is filed
    on the action itself.

    ★ "Reported it" is the incident's reporter, never for an anonymous report:
    putting them on the mail, or naming them in it, undoes the anonymity they
    asked for. On anything else it is whoever raised the action (D13).
    """
    from app.models.incident import Cause, Incident

    cfg = await _config(db)
    rule = (cfg.action_assigned_notify if cfg else None) or {}
    owner = await db.get(User, action.owner_id) if action.owner_id else None
    if owner is None:
        return

    cause = await db.get(Cause, action.cause_id) if action.cause_id else None
    # Found by its id, directly or through the cause — not by the source_type
    # sent (round 7): an id that is not an incident's finds nothing.
    incident_id = cause.incident_id if cause and cause.incident_id else action.source_id
    incident = await db.get(Incident, incident_id) if incident_id else None

    ids: list[uuid.UUID] = [owner.id]
    ids += [uuid.UUID(str(i)) for i in rule.get("people") or []]
    if previous_owner_id and rule.get("previous_owner", True):
        ids.append(previous_owner_id)
    if rule.get("supervisor") and owner.supervisor_id:
        ids.append(owner.supervisor_id)
    if rule.get("reporter"):
        if incident is not None:
            if not incident.is_anonymous and incident.reported_by:
                ids.append(incident.reported_by)
        elif action.raised_by:
            ids.append(action.raised_by)
    people = await mail.recipients(db, list(dict.fromkeys(ids)))
    if not people:
        return

    moved = previous_owner_id is not None
    previous = await db.get(User, previous_owner_id) if moved else None
    lines = [
        (f"Corrective action {action.action_no} has been given to {owner.full_name}"
         + (f" (it was {previous.full_name}'s)" if previous else "") + "."
         if moved else f"Corrective action {action.action_no} is owned by {owner.full_name}."),
        "",
        action.title,
        f"Due {action.due_date.strftime('%-d %B %Y')}.",
    ]
    where = incident.incident_no if incident is not None else action.source_ref
    if where:
        lines.append(f"It was raised on {where}"
                     + (f" at {incident.location_path}" if incident is not None
                        and incident.location_path else "") + ".")
    if cause is not None:
        lines.append(f"It addresses the cause: {cause.label}.")
    if moved:
        lines += ["", f"Reassigned by {by_name or 'Safety'}" + (f": {reason}" if reason else ".")]

    on_incident = incident is not None
    await mail.queue(
        db,
        to=[u.email for u in people],
        subject=(f"Corrective action {action.action_no} reassigned to {owner.full_name}" if moved
                 else f"Corrective action {action.action_no}"
                 + (f" on {incident.incident_no}" if on_incident else f": {action.title}")),
        body="\n".join(lines),
        doc_type=task_service.DOC_INCIDENT if on_incident else task_service.DOC_ACTION,
        doc_id=incident.id if on_incident else action.id,
        template_key="action_reassigned" if moved else "action_owner_told",
        link=mail.module_link(f"/actions/{action.id}"),
        link_label="Open the corrective action",
    )


async def action_escalated(
    db: AsyncSession, action, *, level: int, days_late: int,
    supervisor_id: uuid.UUID | None,
) -> None:
    """HSE's escalation ladder, by email as well as by task.

    Level 1 is the owner's own reminder and is not mailed — they already hold
    the task, and a mail saying "the thing in your list is due tomorrow" is the
    kind of message that teaches people to ignore the sender.
    """
    if level < 2:
        return

    ids: list[uuid.UUID | None] = [action.owner_id]
    if level >= 2:
        ids.append(supervisor_id)
    role_people: list[User] = []
    if level >= 3:
        role_people = await by_roles(db, ("ehs_manager",))
    people = await mail.recipients(db, ids + [u.id for u in role_people])
    if not people:
        return

    await mail.queue(
        db,
        to=[u.email for u in people],
        subject=f"Overdue {days_late} days: {action.title}",
        body=(f"{action.action_no} was due {action.due_date.isoformat()} and is "
              f"still open.\n\nOwner: {action.owner_name or 'unassigned'}.\n\n"
              f"{(action.description or '')[:400]}"),
        doc_type=task_service.DOC_ACTION,
        doc_id=action.id,
        template_key="action_escalation",
        link=mail.module_link(f"/actions/{action.id}"),
        link_label="Open the action",
    )


# ── Scheduled work ───────────────────────────────────────────────────────────

async def inspection_due(
    db: AsyncSession, run, *, template_name: str, assignee_id: uuid.UUID | None,
) -> None:
    """HSE: due inspections go to the inbox *and* by email.

    The assignee comes from the schedule rather than from the run — a run is
    the occurrence, and who does it is a property of the schedule. A schedule
    assigned to a role rather than a person has nobody to email, and the task
    is then the only channel, which is the correct behaviour rather than a gap.
    """
    people = await mail.recipients(db, [assignee_id])
    if not people:
        return
    where = f" — {run.department_name}" if run.department_name else ""
    await mail.queue(
        db,
        to=[u.email for u in people],
        subject=f"Due {run.due_on.isoformat()}: {template_name}{where}",
        body=f"{template_name}{where} is due on {run.due_on.isoformat()}.",
        doc_type="ehs_run",
        doc_id=run.id,
        template_key="inspection_due",
        link=mail.module_link("/forms"),
        link_label="Open the form",
    )


async def toolbox_talk_chase(db: AsyncSession, delivery, *, talk_title: str) -> None:
    """The fortnightly nudge HSE asked for, until the talk is signed off."""
    people = await mail.recipients(db, [delivery.supervisor_id])
    if not people:
        return
    await mail.queue(
        db,
        to=[u.email for u in people],
        subject=f"Still outstanding: {talk_title}",
        body=(f"The toolbox talk for {delivery.department_name or 'your team'} has "
              "not been signed off yet.\n\nHold it with your team and collect "
              "everyone's signature on one device afterwards."),
        doc_type="ehs_tbt",
        doc_id=delivery.id,
        template_key="toolbox_chase",
        link=mail.module_link("/toolbox-talks"),
        link_label="Open toolbox talks",
    )


async def course_tomorrow(db: AsyncSession, delivery, attendee_ids: list[uuid.UUID]) -> None:
    """HSE's step six: attendees are reminded the day before."""
    people = await mail.recipients(db, list(attendee_ids))
    if not people:
        return
    when = delivery.start_time.strftime("%H:%M") if delivery.start_time else "the usual time"
    where = f" at {delivery.location_text}" if delivery.location_text else ""
    await mail.queue(
        db,
        to=[u.email for u in people],
        subject=f"Tomorrow: {delivery.title}",
        body=(f"{delivery.title} runs tomorrow, {delivery.session_date.isoformat()}, "
              f"starting at {when}{where}."),
        doc_type="ehs_crs",
        doc_id=delivery.id,
        template_key="course_reminder",
        link=mail.module_link(f"/training/sessions/{delivery.id}"),
        link_label="See the session",
    )


async def feedback_left(db: AsyncSession, row) -> None:
    """HSE hears about a general comment.

    ★ An email and nothing else. Nobody is assigned a comment: it may turn out
    to be a defect, a request, or a misunderstanding, and raising a task for
    something that might not be work leaves an item in somebody's list that
    they cannot complete. HSE triages it on the screen instead.
    """
    people = await by_roles(db, DEFAULT_INCIDENT_ROLES)
    if not people:
        return
    who = row.submitted_by_name or "Somebody"
    where = f" (from {row.page_path})" if row.page_path else ""
    await mail.queue(
        db,
        to=[u.email for u in people],
        subject=f"Safety feedback from {who}",
        body=f"{who} left a comment{where}:\n\n{row.body}",
        doc_type=None,
        doc_id=None,
        template_key="feedback_left",
        link=mail.module_link("/feedback"),
        link_label="Open feedback",
    )


async def feedback_answered(db: AsyncSession, row) -> None:
    """The person who said something hears that somebody read it.

    ★ Raised in review by uniops-9a: without this, "did anybody look at my
    comment" is answered only by going back to the screen — which is another
    invisible state, and the reason people stop reporting things.
    """
    people = await mail.recipients(db, [row.submitted_by])
    if not people:
        return
    await mail.queue(
        db,
        to=[u.email for u in people],
        subject="HSE has replied to your feedback",
        body=(f"You wrote:\n\n{row.body}\n\nHSE replied:\n\n{row.reply}"),
        doc_type=None,
        doc_id=None,
        template_key="feedback_answered",
        link=mail.module_link("/feedback"),
        link_label="See the reply",
    )


# ── Documents and the committee ──────────────────────────────────────────────

async def document_published(db: AsyncSession, document, audience_ids: list[uuid.UUID]) -> None:
    """A policy that needs acknowledging.

    One message to the whole audience rather than one each: this is a notice,
    the audience is everybody, and two hundred separate sends would take longer
    than the request that triggered them.
    """
    people = await mail.recipients(db, list(audience_ids))
    if not people:
        return
    await mail.queue(
        db,
        to=[u.email for u in people],
        subject=f"Please read and sign: {document.title}",
        body=(f"{document.title} version {document.version} has been published "
              "and needs your acknowledgement.\n\nOpen it, read it, and sign at "
              "the bottom of the page."),
        doc_type=task_service.DOC_DOCUMENT,
        doc_id=document.id,
        template_key="document_published",
        link=mail.module_link(f"/documents/{document.id}"),
        link_label="Read and sign it",
    )


async def jhsc_recommendation(db: AsyncSession, meeting, recommendation) -> None:
    """The committee has recommended something; the employer owes a reply."""
    people = await mail.recipients(
        db, [u.id for u in await by_roles(db, DEFAULT_INCIDENT_ROLES)])
    if not people:
        return
    await mail.queue(
        db,
        to=[u.email for u in people],
        subject=f"JHSC recommendation {meeting.meeting_no} #{recommendation.seq}",
        body=(f"{_where(recommendation)}{recommendation.recommendation}\n\n"
              "OHSA s.9(20) requires a written response within twenty-one days "
              "of the recommendation."),
        doc_type=task_service.DOC_JHSC,
        doc_id=meeting.id,
        template_key="jhsc_recommendation",
        link=mail.module_link(f"/jhsc/meetings/{meeting.id}"),
        link_label="Open the meeting",
    )


def _where(recommendation) -> str:
    """Round 8: "include this info on the email" — where it happened, first."""
    path = getattr(recommendation, "location_path", None)
    return f"Where: {' › '.join(path.split('/'))}\n\n" if path else ""


def jhsc_recommendation_message(meeting, recommendation) -> tuple[str, str]:
    """The subject and body of the notice, as one function.

    ★ The committee asked to see the message before it goes out and to be able
    to change it. A preview that composes the text separately from the send
    drifts from it — silently, and in the direction of showing something nicer
    than what was sent. Both call this.
    """
    subject = (f"JHSC recommendation {meeting.meeting_no} #{recommendation.seq} "
               "— a written response is owed")
    body = (f"{_where(recommendation)}{recommendation.recommendation}\n\n"
            "OHSA s.9(20) requires a written response within twenty-one days "
            "of the recommendation being made.")
    return subject, body


async def jhsc_recommendation_sent(
    db: AsyncSession, meeting, recommendation, people, *, body: str | None = None,
) -> None:
    """Named recipients, rather than whoever holds a role.

    The role-based notice above goes out when the recommendation is raised.
    This is the committee putting it in front of particular people and
    recording that they did — which is what makes an unanswered recommendation
    somebody's to answer rather than nobody's.

    ``body`` is what the sender approved in the preview, when they edited it.
    """
    addressable = await mail.recipients(db, [p.id for p in people])
    if not addressable:
        return
    default_subject, default_body = jhsc_recommendation_message(meeting, recommendation)
    await mail.queue(
        db,
        to=[u.email for u in addressable],
        subject=default_subject,
        body=body if body and body.strip() else default_body,
        doc_type=task_service.DOC_JHSC,
        doc_id=meeting.id,
        template_key="jhsc_recommendation_sent",
        # Round 9: to the recommendation, which the people asked can open.
        link=mail.module_link(f"/jhsc/recommendations/{recommendation.id}"),
        link_label="Open the recommendation",
    )


async def jhsc_recommendation_answered(db: AsyncSession, meeting, recommendation, action) -> None:
    """★ Round 9: the people under Settings → Who is told → JHSC, and whoever
    sent the recommendation unless that is switched off, when it is answered.
    Filed against the recommendation itself, so it is listed on it."""
    cfg = await _config(db)
    rule = (cfg.jhsc_answered_notify if cfg else None) or {"people": [], "sender": True}
    ids = [uuid.UUID(str(i)) for i in rule.get("people") or []]
    if rule.get("sender", True) and recommendation.sent_by:
        ids.append(recommendation.sent_by)
    people = await mail.recipients(db, list(dict.fromkeys(ids)))
    if not people:
        return
    lines = [_where(recommendation) + recommendation.recommendation, "",
             f"Answered by {recommendation.responded_by_name or 'someone'}."]
    if action is not None:
        lines += ["", f"What will be done: {action.title}"]
        if action.description:
            lines.append(action.description)
        lines.append(f"Owner: {action.owner_name or '—'} · due {action.due_date.isoformat()}"
                     f" · {action.priority} priority · {action.action_no}")
    elif recommendation.response:
        lines += ["", recommendation.response]
    await mail.queue(
        db,
        to=[u.email for u in people],
        subject=f"JHSC recommendation {meeting.meeting_no} #{recommendation.seq} answered",
        body="\n".join(lines),
        doc_type=task_service.DOC_JHSC,
        doc_id=recommendation.id,
        template_key="jhsc_recommendation_answered",
        link=mail.module_link(f"/jhsc/recommendations/{recommendation.id}"),
        link_label="Open the recommendation",
    )


async def jhsc_minutes_published(db: AsyncSession, meeting, member_ids: list[uuid.UUID]) -> None:
    """HSE: minutes go to management and to every member."""
    people = await mail.recipients(
        db, list(member_ids) + [u.id for u in await by_roles(db, DEFAULT_INCIDENT_ROLES)])
    if not people:
        return
    await mail.queue(
        db,
        to=[u.email for u in people],
        subject=f"JHSC minutes — {meeting.held_on.isoformat()}",
        body=(meeting.minutes or "")[:2000],
        doc_type=task_service.DOC_JHSC,
        doc_id=meeting.id,
        template_key="jhsc_minutes",
        link=mail.module_link(f"/jhsc/meetings/{meeting.id}"),
        link_label="Open the minutes",
    )
