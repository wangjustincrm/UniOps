"""Form templates and submissions.

Building a template is an HSE job (`ehs.settings.manage`); filling one in is
everybody's (`ehs.incident.report`, which every role holds). That split is the
whole access model here — the inspection nobody can fill in is worse than the
template anyone can edit.
"""
import uuid
from datetime import datetime
from typing import Any

from datetime import date
from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.incidents import _user_id
from app.core.deps import BearerToken, SessionDep
from app.core.permissions import CanManageSettings, CanReportIncident
from sqlalchemy import select

from app.crud import form as crud
from app.crud.incident import _resolve_name
from app.models.form import FormAttachment
from app.models.schedule import FormSchedule, FormScheduleRun
from app.services.attachments import (
    MAX_FILE_SIZE, proxy_download, upload_to_file_server,
)
from app.services import form_engine

router = APIRouter(prefix="/forms")

# What a phone camera and a scanner produce, and nothing else.
ALLOWED_ATTACHMENT_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "application/pdf",
}


# ── Schemas ──────────────────────────────────────────────────────────────────

class QuestionTypeOut(BaseModel):
    code: str
    label: str
    answerable: bool
    can_fail: bool


class TemplateIn(BaseModel):
    code: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    category: str = Field(default="inspection", max_length=30)


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    category: str
    is_active: bool
    # ★ Whether it can actually be filled in, and whether filling it in can
    # fail. Round 4 asked for "a pass of fail criteria set to ensure the item is
    # inspected properly" — so anywhere a template is chosen has to be able to
    # say whether that template has any, and an unpublished one cannot be
    # started at all. Derived, not stored.
    published: bool = False
    fail_criteria: int = 0
    # ★ Whether this kind of form has a verdict at all. Minutes, a talk and a
    # job analysis record what was said; an inspection records whether
    # something was found acceptable. The editor and the screens read this
    # rather than each deciding for themselves.
    can_fail: bool = True


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version_no: int
    status: str
    schema: list
    published_at: datetime | None
    published_by_name: str | None
    change_note: str | None


class SchemaIn(BaseModel):
    schema: list[dict]


class PublishIn(BaseModel):
    change_note: str | None = None


class StartIn(BaseModel):
    location_id: uuid.UUID | None = None
    occurred_at: datetime | None = None


class AnswersIn(BaseModel):
    """Answers keyed by question. Values are whatever the question type takes."""

    values: dict[str, Any] = Field(default_factory=dict)


class AnswerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    question_key: str
    question_label: str
    question_type: str
    value_text: str | None
    value_number: float | None
    value_date: Any | None
    value_bool: bool | None
    value_ids: list
    value_labels: list
    is_failure: bool
    action_id: uuid.UUID | None


class SubmissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    submission_no: str
    template_id: uuid.UUID
    status: str
    schema_snapshot: list
    occurred_at: datetime | None
    location_id: uuid.UUID | None
    location_path: str | None
    submitted_at: datetime | None
    submitted_by_name: str | None
    failed_count: int
    # What the form is for, so it can say so. Without this a set of committee
    # minutes opened from its meeting showed only its own reference number, and
    # an unfilled one was indistinguishable from a fresh blank form.
    source_type: str | None = None
    source_id: uuid.UUID | None = None
    source_ref: str | None = None


class SubmissionListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    submission_no: str
    template_id: uuid.UUID
    status: str
    occurred_at: datetime | None
    location_path: str | None
    submitted_by_name: str | None
    failed_count: int


class FailureCount(BaseModel):
    question_key: str
    question_label: str
    failures: int


# ── Question types ───────────────────────────────────────────────────────────

@router.get("/question-types", response_model=list[QuestionTypeOut])
async def question_types():
    """What a template may contain. Served so the builder cannot drift from the
    engine that has to interpret what it produces."""
    return [QuestionTypeOut(code=t.code, label=t.label, answerable=t.answerable,
                            can_fail=t.can_fail) for t in form_engine.TYPES]


# ── Templates ────────────────────────────────────────────────────────────────

async def _with_criteria(db, templates: list) -> list[TemplateOut]:
    """Each template with whether it is published and how many answers can fail.

    One query for the published versions rather than one per template.
    """
    from sqlalchemy import select as sa_select

    from app.models.form import FormVersion

    if not templates:
        return []
    rows = (await db.execute(
        sa_select(FormVersion.template_id, FormVersion.schema, FormVersion.version_no)
        .where(FormVersion.template_id.in_([t.id for t in templates]),
               FormVersion.status == "published")
        .order_by(FormVersion.version_no.desc())
    )).all()
    latest: dict = {}
    for template_id, schema, _ in rows:
        latest.setdefault(template_id, schema or [])

    out = []
    for t in templates:
        item = TemplateOut.model_validate(t)
        schema = latest.get(t.id)
        item.published = schema is not None
        item.can_fail = form_engine.kind_can_fail(item.category)
        item.fail_criteria = (
            sum(1 for q in (schema or []) if q.get("fail_on"))
            if item.can_fail else 0)
        out.append(item)
    return out


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(
    db: SessionDep, user: CanReportIncident,  # noqa: ARG001
    category: str | None = None, include_inactive: bool = False,
):
    return await _with_criteria(db, await crud.list_templates(
        db, category=category, active_only=not include_inactive))


@router.post("/templates", response_model=TemplateOut, status_code=201)
async def create_template(payload: TemplateIn, db: SessionDep, user: CanManageSettings):
    # ★ Through the same enrichment as the list and the single read, so a
    # template describes itself the same way however it was fetched. It did
    # not, and the builder opened straight from a create would have been told
    # a minutes form has a verdict.
    created = await crud.create_template(
        db, code=payload.code, name=payload.name, description=payload.description,
        category=payload.category, creator_id=_user_id(user))
    return (await _with_criteria(db, [created]))[0]


@router.get("/templates/{template_id}", response_model=TemplateOut)
async def get_template(template_id: uuid.UUID, db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    """One template. The builder needs it to say which form is being edited —
    it showed only "Form builder", which with four similarly-named inspections
    open is not enough to tell them apart.

    Through the same enrichment as the list, so `can_fail` and the criteria
    count mean the same thing on both — the builder reads this one, and a
    field present on the list and missing here is how a screen ends up
    deciding for itself.
    """
    return (await _with_criteria(db, [await crud.get_template(db, template_id)]))[0]


@router.get("/templates/{template_id}/versions", response_model=list[VersionOut])
async def list_versions(template_id: uuid.UUID, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    return await crud.list_versions(db, template_id)


@router.put("/templates/{template_id}/schema", response_model=VersionOut)
async def save_schema(
    template_id: uuid.UUID, payload: SchemaIn, db: SessionDep, user: CanManageSettings,  # noqa: ARG001
):
    """Save the draft. Editing a published version creates the next draft."""
    return await crud.save_draft_schema(db, template_id, payload.schema)


@router.post("/templates/{template_id}/publish", response_model=VersionOut)
async def publish(
    template_id: uuid.UUID, payload: PublishIn, db: SessionDep, user: CanManageSettings,
):
    return await crud.publish_version(
        db, template_id, publisher_id=_user_id(user), change_note=payload.change_note)


@router.get("/templates/{template_id}/failures", response_model=list[FailureCount])
async def failures(template_id: uuid.UUID, db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    """Which checks fail most often on this form."""
    return [FailureCount(question_key=k, question_label=lbl, failures=n)
            for k, lbl, n in await crud.failure_counts(db, template_id)]


# ── Submissions ──────────────────────────────────────────────────────────────

@router.post("/templates/{template_id}/submissions", response_model=SubmissionOut,
             status_code=201)
async def start(
    template_id: uuid.UUID, payload: StartIn, db: SessionDep, user: CanReportIncident,
):
    """Begin filling one in. The snapshot is taken here, not at submit."""
    return await crud.start_submission(
        db, template_id, user_id=_user_id(user),
        location_id=payload.location_id, occurred_at=payload.occurred_at)


@router.get("/submissions", response_model=list[SubmissionListItem])
async def list_submissions(
    db: SessionDep, user: CanReportIncident,  # noqa: ARG001
    template_id: uuid.UUID | None = None,
    status_in: list[str] | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, le=200), offset: int = 0,
):
    return await crud.list_submissions(
        db, template_id=template_id, status_in=status_in, limit=limit, offset=offset)


@router.get("/submissions/{submission_id}", response_model=SubmissionOut)
async def get_submission(submission_id: uuid.UUID, db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    return await crud.get_submission(db, submission_id)


@router.get("/submissions/{submission_id}/answers", response_model=list[AnswerOut])
async def get_answers(submission_id: uuid.UUID, db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    await crud.get_submission(db, submission_id)
    return await crud.answers_for(db, submission_id)


@router.put("/submissions/{submission_id}/answers", response_model=list[AnswerOut])
async def save_answers(
    submission_id: uuid.UUID, payload: AnswersIn, db: SessionDep, user: CanReportIncident,  # noqa: ARG001
):
    submission = await crud.get_submission(db, submission_id)
    return await crud.save_answers(db, submission, payload.values)


@router.post("/submissions/{submission_id}/submit", response_model=SubmissionOut)
async def submit(submission_id: uuid.UUID, db: SessionDep, user: CanReportIncident):
    """Finish it. Every failed check raises a corrective action."""
    submission = await crud.get_submission(db, submission_id)
    return await crud.submit(db, submission, user_id=_user_id(user))

# ── Schedules and their occurrences ──────────────────────────────────────────

class ScheduleIn(BaseModel):
    template_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    frequency: str = Field(pattern="^(daily|weekly|monthly|quarterly|annual)$")
    # "one department per month, in turn" is HSE's JHSC rotation.
    rotation: str = Field(default="none", pattern="^(none|department)$")
    department_ids: list[uuid.UUID] = Field(default_factory=list)
    location_ids: list[uuid.UUID] = Field(default_factory=list)
    assigned_role: str | None = Field(default=None, max_length=50)
    assigned_user_id: uuid.UUID | None = None
    lead_days: int = Field(default=7, ge=0, le=90)
    next_due_on: date
    is_active: bool = True


class ScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    template_id: uuid.UUID
    name: str
    frequency: str
    rotation: str
    department_ids: list
    location_ids: list
    rotation_index: int
    assigned_role: str | None
    lead_days: int
    next_due_on: date
    last_generated_on: date | None
    is_active: bool


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    schedule_id: uuid.UUID
    template_id: uuid.UUID
    due_on: date
    department_name: str | None
    status: str
    submission_id: uuid.UUID | None
    completed_at: datetime | None


@router.get("/schedules", response_model=list[ScheduleOut])
async def list_schedules(db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    return list((await db.execute(
        select(FormSchedule).order_by(FormSchedule.next_due_on))).scalars().all())


@router.post("/schedules", response_model=ScheduleOut, status_code=201)
async def create_schedule(payload: ScheduleIn, db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    await crud.get_template(db, payload.template_id)
    sched = FormSchedule(
        id=uuid.uuid4(), template_id=payload.template_id, name=payload.name,
        frequency=payload.frequency, rotation=payload.rotation,
        department_ids=[str(d) for d in payload.department_ids],
        location_ids=[str(loc) for loc in payload.location_ids],
        assigned_role=payload.assigned_role, assigned_user_id=payload.assigned_user_id,
        lead_days=payload.lead_days, next_due_on=payload.next_due_on,
        is_active=payload.is_active,
    )
    db.add(sched)
    await db.flush()
    return sched


@router.patch("/schedules/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    schedule_id: uuid.UUID, payload: ScheduleIn, db: SessionDep, user: CanManageSettings,  # noqa: ARG001
):
    sched = await db.get(FormSchedule, schedule_id)
    if sched is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Schedule not found")
    sched.name = payload.name
    sched.frequency = payload.frequency
    sched.rotation = payload.rotation
    sched.department_ids = [str(d) for d in payload.department_ids]
    sched.location_ids = [str(loc) for loc in payload.location_ids]
    sched.assigned_role = payload.assigned_role
    sched.assigned_user_id = payload.assigned_user_id
    sched.lead_days = payload.lead_days
    sched.next_due_on = payload.next_due_on
    sched.is_active = payload.is_active
    await db.flush()
    return sched


@router.get("/runs", response_model=list[RunOut])
async def list_runs(
    db: SessionDep, user: CanReportIncident,  # noqa: ARG001
    status_in: list[str] | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, le=300),
):
    """Scheduled inspections. Overdue first — that is what needs attention."""
    q = select(FormScheduleRun)
    if status_in:
        q = q.where(FormScheduleRun.status.in_(status_in))
    return list((await db.execute(
        q.order_by(FormScheduleRun.due_on).limit(limit))).scalars().all())


@router.post("/runs/{run_id}/start", response_model=SubmissionOut)
async def start_run(run_id: uuid.UUID, db: SessionDep, user: CanReportIncident):
    """Open the form this occurrence is asking for, resuming if already started."""
    run = await db.get(FormScheduleRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scheduled inspection not found")
    return await crud.start_from_run(db, run, user_id=_user_id(user))


# ── Attachments ──────────────────────────────────────────────────────────────

class FormAttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    question_key: str | None
    filename: str
    content_type: str
    file_size: int
    uploaded_by_name: str | None


@router.get("/submissions/{submission_id}/attachments",
            response_model=list[FormAttachmentOut])
async def list_form_attachments(
    submission_id: uuid.UUID, db: SessionDep, user: CanReportIncident,  # noqa: ARG001
):
    await crud.get_submission(db, submission_id)
    return list((await db.execute(
        select(FormAttachment)
        .where(FormAttachment.submission_id == submission_id)
        .order_by(FormAttachment.created_at))).scalars().all())


@router.post("/submissions/{submission_id}/attachments",
             response_model=FormAttachmentOut, status_code=201)
async def upload_form_attachment(
    submission_id: uuid.UUID, file: UploadFile, db: SessionDep,
    user: CanReportIncident, token: BearerToken,
    question_key: str | None = Query(default=None, max_length=80),
):
    submission = await crud.get_submission(db, submission_id)
    if submission.status == "submitted":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This form has been submitted and cannot be changed")

    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE // 1024 // 1024} MB limit")
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in ALLOWED_ATTACHMENT_TYPES:
        raise HTTPException(415, f"Unsupported file type '{ctype}'. Allowed: images and PDF.")

    storage_key = await upload_to_file_server(
        data, file.filename or "photo.jpg", ctype, "ehs_form", submission_id, token)
    att = FormAttachment(
        id=uuid.uuid4(), submission_id=submission_id, question_key=question_key,
        storage_key=storage_key, filename=file.filename or "photo.jpg",
        content_type=ctype, file_size=len(data),
        uploaded_by=_user_id(user),
        uploaded_by_name=await _resolve_name(db, _user_id(user)),
    )
    db.add(att)
    await db.flush()
    await db.refresh(att)
    return att


@router.get("/submissions/{submission_id}/attachments/{attachment_id}/download")
async def download_form_attachment(
    submission_id: uuid.UUID, attachment_id: uuid.UUID, db: SessionDep,
    user: CanReportIncident, token: BearerToken,  # noqa: ARG001
):
    await crud.get_submission(db, submission_id)
    att = await db.get(FormAttachment, attachment_id)
    if att is None or att.submission_id != submission_id:
        raise HTTPException(404, "Attachment not found")
    return await proxy_download(att.storage_key, token)
