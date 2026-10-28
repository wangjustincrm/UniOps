"""Form templates, versions and submissions.

The submit path is where the engine earns its keep: answers are normalised into
typed rows, each is tested against its question's fail condition, and every
failure raises a corrective action against the submission. A checklist that
records a failure and does nothing about it is a filing exercise.
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.action import create as create_action
from app.services.day_range import plant_today
from app.crud.incident import _resolve_location_path, _resolve_name
from app.models.config import EhsConfig
from app.models.form import FormAnswer, FormSubmission, FormTemplate, FormVersion
from app.models.schedule import FormSchedule, FormScheduleRun
from app.schemas.action import ActionCreate
from app.services import form_engine
from app.services import tasks as task_service
from app.services.numbering import next_number

# How long the owner of a failed check has to deal with it. Deliberately short:
# a failed inspection item is a known hazard from the moment it is written down.
FAILURE_DUE_DAYS = 7


# ── Templates ────────────────────────────────────────────────────────────────

async def list_templates(
    db: AsyncSession, *, category: str | None = None, active_only: bool = True
) -> list[FormTemplate]:
    q = select(FormTemplate)
    if category:
        q = q.where(FormTemplate.category == category)
    if active_only:
        q = q.where(FormTemplate.is_active.is_(True))
    return list((await db.execute(q.order_by(FormTemplate.name))).scalars().all())


async def get_template(db: AsyncSession, template_id: uuid.UUID) -> FormTemplate:
    t = await db.get(FormTemplate, template_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Form template not found")
    return t


async def create_template(
    db: AsyncSession, *, code: str, name: str, description: str | None,
    category: str, creator_id: uuid.UUID,
) -> FormTemplate:
    dupe = (await db.execute(
        select(FormTemplate).where(FormTemplate.code == code))).scalar_one_or_none()
    if dupe is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"A template with code '{code}' exists")

    t = FormTemplate(id=uuid.uuid4(), code=code, name=name, description=description,
                     category=category, created_by=creator_id)
    db.add(t)
    await db.flush()
    # Every template starts with an empty draft version, so there is always
    # somewhere to put questions without a separate "create version" step.
    db.add(FormVersion(id=uuid.uuid4(), template_id=t.id, version_no=1, schema=[]))
    await db.flush()
    return t


# ── Versions ─────────────────────────────────────────────────────────────────

async def list_versions(db: AsyncSession, template_id: uuid.UUID) -> list[FormVersion]:
    return list((await db.execute(
        select(FormVersion)
        .where(FormVersion.template_id == template_id)
        .order_by(FormVersion.version_no.desc())
    )).scalars().all())


async def draft_version(db: AsyncSession, template_id: uuid.UUID) -> FormVersion:
    """The editable version, creating the next one if the last is published.

    A published version is never edited — submissions reference it, and the
    audit trail HSE asked for is precisely the sequence of published versions.
    """
    latest = (await db.execute(
        select(FormVersion)
        .where(FormVersion.template_id == template_id)
        .order_by(FormVersion.version_no.desc()).limit(1)
    )).scalar_one_or_none()

    if latest is not None and latest.status == "draft":
        return latest

    nxt = FormVersion(
        id=uuid.uuid4(), template_id=template_id,
        version_no=(latest.version_no + 1) if latest else 1,
        # A new draft starts from the published one, because edits are almost
        # always tweaks rather than a rewrite.
        schema=list(latest.schema) if latest else [],
    )
    db.add(nxt)
    await db.flush()
    return nxt


async def save_draft_schema(
    db: AsyncSession, template_id: uuid.UUID, schema: list[dict]
) -> FormVersion:
    try:
        form_engine.validate_schema(schema)
    except form_engine.SchemaError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None

    # ★ Round 5, TC-09: "minutes are a documentation of a meeting, there is no
    # pass or fail in the form nor in the minutes."
    #
    # Refused rather than quietly stripped. A fail condition that is saved and
    # never fires is the shape this module has been caught by twice — a field
    # built and never read — and the editor does not offer the box for these
    # kinds, so the only way one arrives is from a template that changed kind
    # or from a caller that should be told.
    template = await get_template(db, template_id)
    if not form_engine.kind_can_fail(template.category):
        offending = [q.get("key") for q in schema if q.get("fail_on")]
        if offending:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"A {template.category} form has no pass or fail — it records what "
                f"was said or agreed, not a verdict. Remove the fail condition on: "
                f"{', '.join(str(k) for k in offending)}.",
            )

    v = await draft_version(db, template_id)
    v.schema = schema
    await db.flush()
    return v


async def publish_version(
    db: AsyncSession, template_id: uuid.UUID, *, publisher_id: uuid.UUID,
    change_note: str | None,
) -> FormVersion:
    v = await draft_version(db, template_id)
    if not v.schema:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Add at least one question before publishing")
    try:
        form_engine.validate_schema(v.schema)
    except form_engine.SchemaError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None

    v.status = "published"
    v.published_at = datetime.now(timezone.utc)
    v.published_by = publisher_id
    v.published_by_name = await _resolve_name(db, publisher_id)
    v.change_note = change_note
    await db.flush()
    return v


async def published_version(db: AsyncSession, template_id: uuid.UUID) -> FormVersion:
    v = (await db.execute(
        select(FormVersion)
        .where(FormVersion.template_id == template_id, FormVersion.status == "published")
        .order_by(FormVersion.version_no.desc()).limit(1)
    )).scalar_one_or_none()
    if v is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This form has no published version yet — publish it before it can be filled in",
        )
    return v


# ── Submissions ──────────────────────────────────────────────────────────────

async def start_submission(
    db: AsyncSession, template_id: uuid.UUID, *, user_id: uuid.UUID,
    location_id: uuid.UUID | None, occurred_at: datetime | None,
) -> FormSubmission:
    await start_submission_allowed(await get_template(db, template_id))
    version = await published_version(db, template_id)

    sub = FormSubmission(
        id=uuid.uuid4(),
        submission_no=await next_number(db, "form"),
        template_id=template_id,
        version_id=version.id,
        # Copied, not referenced: the version row is immutable today, but a
        # snapshot is what survives a template being deleted years from now.
        schema_snapshot=list(version.schema),
        location_id=location_id,
        location_path=await _resolve_location_path(db, location_id),
        occurred_at=occurred_at or datetime.now(timezone.utc),
        submitted_by=user_id,
        submitted_by_name=await _resolve_name(db, user_id),
    )
    db.add(sub)
    await db.flush()
    return sub


async def get_submission(db: AsyncSession, submission_id: uuid.UUID) -> FormSubmission:
    s = await db.get(FormSubmission, submission_id)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Submission not found")
    return s


async def answers_for(db: AsyncSession, submission_id: uuid.UUID) -> list[FormAnswer]:
    return list((await db.execute(
        select(FormAnswer).where(FormAnswer.submission_id == submission_id)
        .order_by(FormAnswer.created_at)
    )).scalars().all())


async def save_answers(
    db: AsyncSession, submission: FormSubmission, values: dict[str, object]
) -> list[FormAnswer]:
    """Replace the answers. Judged against the snapshot, never the live template.

    Someone can be halfway through a form when a new version is published; the
    questions in front of them must keep being the ones they are answering.
    """
    if submission.status == "submitted":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This form has been submitted and cannot be edited")

    by_key = {q["key"]: q for q in form_engine.answerable(submission.schema_snapshot)}

    # ★★ Whether this kind of form can fail at all, decided once here — at the
    # point the column is written rather than at each place it is read.
    #
    # `normalise` is handed one question and knows nothing about the template, so
    # a leftover condition on a minutes form or a toolbox talk would mark answers
    # as failures. Three things then read that column: `failed_count` on the
    # submission, the "which checks fail most often" report, and the corrective
    # actions raised on submit. Fixing it at the submit path would have left the
    # first two saying a job analysis had two failed checks while the template
    # list said it has no criteria at all — the same disagreement one level down.
    # Raised in review by uniops-41, against my first attempt at exactly that.
    template = await get_template(db, submission.template_id)
    can_fail = form_engine.kind_can_fail(template.category)

    existing = {a.question_key: a for a in await answers_for(db, submission.id)}
    for key, value in values.items():
        question = by_key.get(key)
        if question is None:
            # Silently ignoring it would lose an answer without saying so.
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"'{key}' is not a question on this form",
            )
        row = form_engine.normalise(question, value)
        if question.get("type") == "vocabulary" and row["value_ids"]:
            # The wording kept is the list's, not what the client displayed.
            from app.crud.vocabulary import label_for
            try:
                ids = [uuid.UUID(i) for i in row["value_ids"]]
            except ValueError:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                    f"'{key}' is not an entry on the list") from None
            # An entry already in the answer keeps the wording it was given
            # then; only a newly chosen one takes the list's (uniops-8b).
            before = existing.get(key)
            kept = (dict(zip(before.value_ids or [], before.value_labels or []))
                    if before is not None and len(before.value_ids or []) == len(before.value_labels or [])
                    else {})
            row["value_labels"] = [kept.get(str(i)) or await label_for(db, question["vocabulary"], i)
                                   for i in ids]
        if not can_fail:
            row["is_failure"] = False
        target = existing.get(key)
        if target is None:
            target = FormAnswer(id=uuid.uuid4(), submission_id=submission.id)
            db.add(target)
        for field, v in row.items():
            setattr(target, field, v)

    await db.flush()
    return await answers_for(db, submission.id)


async def submit(
    db: AsyncSession, submission: FormSubmission, *, user_id: uuid.UUID,
    raise_actions: bool = True,
) -> FormSubmission:
    """Finish a form: check required answers, then act on every failure."""
    if submission.status == "submitted":
        return submission

    answers = {a.question_key: a for a in await answers_for(db, submission.id)}
    questions = form_engine.answerable(submission.schema_snapshot)

    # ★ A form with nothing in it at all. The required-question check below
    # cannot catch this, because a template whose questions are all optional —
    # which HSE's minutes form is — lets a completely blank form through, and it
    # is then immutable. Three sets of committee minutes were filed that way:
    # marked submitted, containing nothing, and impossible to correct. A blank
    # statutory record is worse than a missing one, because it looks done.
    if not any(not _is_blank(a) for a in answers.values()):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Nothing has been filled in on this form yet. Answer at least one "
            "question before submitting it — a submitted form cannot be edited.",
        )

    missing = [
        q["label"] for q in questions
        if q.get("required") and _is_blank(answers.get(q["key"]))
    ]
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Unanswered required questions: " + "; ".join(missing[:5])
            + (f" (and {len(missing) - 5} more)" if len(missing) > 5 else ""),
        )

    # A form whose kind has no verdict has no failing answers to find: that is
    # settled in `save_answers`, where the column is written, so everything that
    # reads it agrees.
    failures = [a for a in answers.values() if a.is_failure]
    submission.failed_count = len(failures)
    submission.status = "submitted"
    submission.submitted_at = datetime.now(timezone.utc)
    submission.submitted_by = user_id
    submission.submitted_by_name = await _resolve_name(db, user_id)
    await db.flush()

    # A failed check with nobody assigned to it is how a known hazard stays a
    # known hazard. Owner is whoever submitted, because they are on the spot and
    # HSE reassigns from the action itself.
    # ★ A form filled in for a piece of equipment records that item's check,
    # and the form's own fail conditions decide whether it passed. Round 4:
    # "there should be a pass of fail criteria set to ensure the item is
    # inspected properly." The criteria are the template's; this is what makes
    # them mean something — a failed answer takes the item out of service
    # through the same path as a failed check recorded by hand.
    if submission.source_type == "asset" and submission.source_id is not None:
        from app.crud import asset as asset_crud

        item = await asset_crud.get_asset(db, submission.source_id)
        await asset_crud.record_ad_hoc(
            db, item,
            # ★ Spelled as every other path spells them. "fail" was never
            # "failed", so a failed checklist left its item in service.
            result="failed" if failures else "passed",
            notes=(f"Recorded on {submission.submission_no}"
                   + (f" — {len(failures)} failed check(s)" if failures else "")),
            user_id=user_id,
            submission_id=submission.id,
        )

    if raise_actions:
        due = plant_today() + timedelta(days=FAILURE_DUE_DAYS)
        for a in failures:
            if a.action_id:
                continue
            action = await create_action(db, ActionCreate(
                source_type="inspection",
                source_id=submission.id,
                title=f"{a.question_label[:180]} — failed on {submission.submission_no}",
                description=(
                    f"Recorded as a failure on {submission.submission_no}"
                    + (f" at {submission.location_path}" if submission.location_path else "")
                ),
                priority="high",
                owner_id=user_id,
                due_date=due,
                location_id=submission.location_id,
                department_id=submission.department_id,
            ), source_ref=submission.submission_no)
            a.action_id = action.id
            # Round 10: a check of a safety equipment item — the action is
            # about that item, and verifying it (with the others) puts it back.
            if submission.source_type == "asset":
                action.asset_id = submission.source_id
        await db.flush()

    # If this form came from a schedule, that occurrence is now done and its
    # task closes with it. Imported here to keep the sweep's dependency on the
    # form CRUD one-directional.
    from app.services.schedule_sweep import complete_run_for_submission
    await complete_run_for_submission(db, submission.id)

    return submission


def _is_blank(answer: FormAnswer | None) -> bool:
    if answer is None:
        return True
    return (
        answer.value_text in (None, "")
        and answer.value_number is None
        and answer.value_date is None
        and answer.value_bool is None
        and not answer.value_ids
    )


async def list_submissions(
    db: AsyncSession, *, template_id: uuid.UUID | None = None,
    status_in: list[str] | None = None, limit: int = 50, offset: int = 0,
    search: str | None = None,
    viewer_id: uuid.UUID | None = None,
) -> list[FormSubmission]:
    q = select(FormSubmission)
    # ★ Review (uniops-8b): a draft is the person filling it in, and nobody
    # else's — the register listed everybody's unsent forms to every employee,
    # as the incident register and reports did before 35f1a1e4. Your own drafts
    # are still listed, so they can be found and finished.
    if viewer_id is not None:
        q = q.where(or_(FormSubmission.status != "draft",
                        FormSubmission.submitted_by == viewer_id))
    if template_id:
        q = q.where(FormSubmission.template_id == template_id)
    if search and search.strip():
        # ★ Round 10 (open box): "entering a form called JHSC does not return
        # results and it should. make the field searchable by name, number,
        # area, or who filed it." The form's name was not among what the
        # register matched, and it only ever matched the rows already loaded.
        # ★ Review (uniops-8b): % and _ typed in are literal, not wildcards;
        # and the status is matched too, as the page's own filter did before
        # the search moved to the server ("draft" found nothing).
        term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{term}%"

        def m(col):
            return col.ilike(like, escape="\\")
        q = q.where(or_(
            m(FormSubmission.submission_no),
            m(FormSubmission.location_path),
            m(FormSubmission.submitted_by_name),
            m(FormSubmission.status),
            select(FormTemplate.id).where(FormTemplate.id == FormSubmission.template_id,
                                          m(FormTemplate.name)).exists(),
        ))
    if status_in:
        q = q.where(FormSubmission.status.in_(status_in))
    return list((await db.execute(
        q.order_by(FormSubmission.created_at.desc()).limit(limit).offset(offset)
    )).scalars().all())


async def failure_counts(db: AsyncSession, template_id: uuid.UUID) -> list[tuple[str, str, int]]:
    """Which checks fail most often — the question this engine exists to answer."""
    rows = (await db.execute(
        select(FormAnswer.question_key, FormAnswer.question_label, func.count())
        .join(FormSubmission, FormSubmission.id == FormAnswer.submission_id)
        .where(FormSubmission.template_id == template_id, FormAnswer.is_failure.is_(True))
        .group_by(FormAnswer.question_key, FormAnswer.question_label)
        .order_by(func.count().desc())
    )).all()
    return [(k, lbl, n) for k, lbl, n in rows]


# ── Schedules ────────────────────────────────────────────────────────────────

async def start_from_run(
    db: AsyncSession, run, *, user_id: uuid.UUID
) -> FormSubmission:
    """Open the form a scheduled inspection is asking for.

    Reuses the run's submission if one exists: the task is tapped more than
    once — someone starts an inspection, walks away, comes back — and a second
    tap must return to what they were filling in rather than start a blank one
    beside it.
    """
    if run.submission_id:
        return await get_submission(db, run.submission_id)

    sub = await start_submission(
        db, run.template_id, user_id=user_id,
        location_id=run.location_id, occurred_at=None,
    )
    # The occurrence's department, not the filler's — this is whose turn it was.
    sub.department_id = run.department_id
    run.submission_id = sub.id
    await db.flush()
    return sub


async def references_to(db: AsyncSession, template_id: uuid.UUID) -> dict[str, int]:
    """What would break if this template went away.

    ★ Counted rather than assumed. Four things point at a template — the forms
    filled in on it, the schedules that raise it, and the safety items that open
    it when their code is scanned — and this module has twice shipped a change
    that forgot one whole consumer.
    """
    from app.models.asset import SafetyAsset

    async def count(model, column) -> int:
        return (await db.execute(
            select(func.count()).select_from(model).where(column == template_id)
        )).scalar() or 0

    # ★ The committee minutes form is named in Settings by its *code*, not by id,
    # so it is not a foreign key and nothing would have stopped it being archived
    # — the next meeting would simply have found no minutes form. Raised in
    # review by uniops-41. Counted the same way as the rest so one answer covers
    # every caller.
    template = await get_template(db, template_id)
    config = (await db.execute(
        select(EhsConfig).where(EhsConfig.id == 1))).scalar_one_or_none()
    is_minutes_form = bool(
        config and config.jhsc_minutes_template_code
        and config.jhsc_minutes_template_code == template.code)

    return {
        "submissions": await count(FormSubmission, FormSubmission.template_id),
        "schedules": await count(FormSchedule, FormSchedule.template_id),
        "equipment": await count(SafetyAsset, SafetyAsset.inspection_template_id),
        "settings": 1 if is_minutes_form else 0,
    }


async def set_template_active(
    db: AsyncSession, template_id: uuid.UUID, *, active: bool
) -> FormTemplate:
    """Archive a template, or bring it back.

    Archiving stops it being offered and leaves everything filled in on it
    exactly where it is — a submitted form is a record, and the template is how
    you read it.

    ★ Refused while a piece of equipment still opens it by QR code. Archiving it
    then would leave somebody scanning an eyewash station and landing on a form
    nobody maintains, which is worse than leaving the template in the list. The
    message says how many, so the fix is obvious.

    ★ Its schedules are switched off with it. A schedule that keeps raising
    inspections on an archived form is the "task outlives the thing" shape this
    module keeps finding, and it would be invisible — the template is no longer
    in the list to notice.
    """
    template = await get_template(db, template_id)
    if not active:
        refs = await references_to(db, template_id)
        if refs.get("settings"):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "This is the form the committee's minutes are written on, named "
                "under Settings. Point the committee at another form first, or "
                "the next meeting will find no minutes form at all.")
        if refs["equipment"]:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{refs['equipment']} piece(s) of equipment open this form when "
                f"their code is scanned. Point them at another form first, or "
                f"they will scan to a form nobody maintains.")
        for sched in (await db.execute(
                select(FormSchedule).where(
                    FormSchedule.template_id == template_id,
                    FormSchedule.is_active.is_(True)))).scalars():
            sched.is_active = False

        # ★ And the rounds it has already raised. Switching the schedule off
        # stops new ones; the pending ones and their inbox tasks were left behind,
        # so people kept being chased to fill in a form that is no longer in any
        # list — and invisibly, because the template has left the list where
        # somebody would notice. Raised in review by uniops-41.
        #
        # Cancelled rather than completed: nobody filled them in.
        # ★ `missed` as well as `pending`. A missed round means it was chased and
        # not done — and once the form is archived there is nothing left to chase
        # it for. Left alone they stayed on the forms list as overdue work, and
        # pressing Start answered "this form has been archived". Raised in review
        # by uniops-41, who also checked that `cancelled` disappears cleanly:
        # the column has no constraint, the sweep only moves `pending` to
        # `missed`, and the list reads neither.
        outstanding = (await db.execute(
            select(FormScheduleRun).where(
                FormScheduleRun.template_id == template_id,
                FormScheduleRun.status.in_(("pending", "missed"))))).scalars().all()
        for run in outstanding:
            run.status = "cancelled"
            await task_service.close_tasks_for(
                db, doc_type=task_service.DOC_RUN, doc_id=run.id,
                task_type=task_service.TASK_DO_FORM)

    template.is_active = active
    await db.flush()
    return template


# ★ Restoring deliberately does **not** switch the schedules back on, and the
# screen says so. A monthly form archived six months ago and restored today would
# otherwise raise six overdue rounds the moment it came back — work nobody
# decided to ask for. Starting the schedule again is its own decision, taken by
# somebody looking at the calendar. Raised in review by uniops-41; stated here
# because an undocumented omission and a deliberate one look identical.


async def start_submission_allowed(template: FormTemplate) -> None:
    """Refuse to start a form on an archived template.

    ★ Archiving took it off every list and nothing stopped a direct link, a
    bookmark or a QR code from starting one anyway — so a form could be filled
    in and submitted on a template that had been retired, which is a record
    nobody will look for. Raised in review by uniops-41.
    """
    if not template.is_active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This form has been archived and is no longer in use. Ask HSE which "
            "form replaces it.")


async def delete_template(db: AsyncSession, template_id: uuid.UUID) -> None:
    """Remove a template outright — only one nothing has ever used.

    Round 5, box 7: "none of the forms are finalized and specifically the ones
    mentioned they are scratch templates and can be deleted, in fact provide a
    way to delete or archive all the forms."

    ★ A template with a submission against it is never deleted, whatever is
    asked. The submission holds its own snapshot of the questions, but the
    template is the thing that says what the form *is*, and a statutory record
    whose form no longer exists is the kind of gap an auditor finds. Archiving is
    offered in the same breath and does the job for everything except a scratch
    template nobody ever filled in.
    """
    from app.models.form import FormVersion

    template = await get_template(db, template_id)
    refs = await references_to(db, template_id)
    blocking = {k: v for k, v in refs.items() if v}
    if blocking:
        detail = ", ".join(f"{v} {k}" for k, v in blocking.items())
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This form cannot be deleted — {detail} point at it. Archive it "
            f"instead: it stops being offered and everything filled in on it "
            f"stays readable.")

    for version in (await db.execute(
            select(FormVersion).where(FormVersion.template_id == template_id))).scalars():
        await db.delete(version)
    await db.delete(template)
    await db.flush()
