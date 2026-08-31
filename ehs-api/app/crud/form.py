"""Form templates, versions and submissions.

The submit path is where the engine earns its keep: answers are normalised into
typed rows, each is tested against its question's fail condition, and every
failure raises a corrective action against the submission. A checklist that
records a failure and does nothing about it is a filing exercise.
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.action import create as create_action
from app.crud.incident import _resolve_location_path, _resolve_name
from app.models.form import FormAnswer, FormSubmission, FormTemplate, FormVersion
from app.schemas.action import ActionCreate
from app.services import form_engine
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
    await get_template(db, template_id)
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
    if raise_actions:
        due = (datetime.now(timezone.utc) + timedelta(days=FAILURE_DUE_DAYS)).date()
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
) -> list[FormSubmission]:
    q = select(FormSubmission)
    if template_id:
        q = q.where(FormSubmission.template_id == template_id)
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
