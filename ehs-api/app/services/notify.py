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


# ── Incidents ────────────────────────────────────────────────────────────────

async def incident_reported(db: AsyncSession, incident) -> None:
    """Tell the people who need to know that something happened.

    Not a task: nobody on this list has been assigned anything yet. Whoever
    ends up investigating gets a task when the incident is classified.
    """
    people = await _incident_audience(
        db, (incident.extra or {}).get("incident_type"))

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
        body=(f"{recommendation.recommendation}\n\n"
              "OHSA s.9(20) requires a written response within twenty-one days "
              "of the recommendation."),
        doc_type=task_service.DOC_JHSC,
        doc_id=meeting.id,
        template_key="jhsc_recommendation",
        link=mail.module_link(f"/jhsc/meetings/{meeting.id}"),
        link_label="Open the meeting",
    )


def jhsc_recommendation_message(meeting, recommendation) -> tuple[str, str]:
    """The subject and body of the notice, as one function.

    ★ The committee asked to see the message before it goes out and to be able
    to change it. A preview that composes the text separately from the send
    drifts from it — silently, and in the direction of showing something nicer
    than what was sent. Both call this.
    """
    subject = (f"JHSC recommendation {meeting.meeting_no} #{recommendation.seq} "
               "— a written response is owed")
    body = (f"{recommendation.recommendation}\n\n"
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
        link=mail.module_link(f"/jhsc/meetings/{meeting.id}"),
        link_label="Open the meeting",
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
