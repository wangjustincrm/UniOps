"""Training records, certifications, and the compliance gap report."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.deps import BearerToken, SessionDep
from app.core.permissions import (
    CanManageSettings, CanReportIncident, CanReadTraining, CanWriteTraining,
)
from app.crud import training as crud
from sqlalchemy import select

from uniops_authz import has_permission
from app.models.config import EhsConfig
from app.services import certificate_pdf, materials, training_pdf, training_xlsx
from app.services.http_headers import content_disposition

from app.crud.incident import _resolve_name
from app.models.course import CourseMaterial
from app.models.training import Course
from app.models.expiry_notice import CertExpiryRule
from app.services.attachments import (
    MAX_FILE_SIZE, proxy_download, upload_to_file_server,
)
from app.schemas.training import (
    CertificationIn,
    CertificationOut,
    CourseIn,
    CourseOut,
    CoursePatch,
    GapReport,
    TrainingRecordIn,
    TrainingRecordOut,
)

COURSE_MATERIAL_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}

router = APIRouter()


def _user_id(payload: dict) -> uuid.UUID:
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token carries no usable subject") from None


def _cert_out(cert, today: date) -> CertificationOut:
    out = CertificationOut.model_validate(cert)
    if cert.expires_on:
        out.is_expired = cert.expires_on < today
        out.days_until_expiry = (cert.expires_on - today).days
    return out


class RenewIn(BaseModel):
    issued_on: date | None = None
    expires_on: date = Field(description="The new expiry — must be in the future")


@router.get("/courses", response_model=list[CourseOut])
async def list_courses(
    db: SessionDep,
    user: CanReadTraining,  # noqa: ARG001
    statutory_only: bool = False,
):
    return [CourseOut.model_validate(c) for c in
            await crud.list_courses(db, statutory_only=statutory_only)]


@router.post("/courses", response_model=CourseOut, status_code=status.HTTP_201_CREATED)
async def create_course(payload: CourseIn, db: SessionDep, user: CanWriteTraining):  # noqa: ARG001
    return CourseOut.model_validate(await crud.create_course(db, payload))


@router.patch("/courses/{course_id}", response_model=CourseOut)
async def update_course(
    course_id: uuid.UUID, payload: CoursePatch, db: SessionDep, user: CanWriteTraining,  # noqa: ARG001
):
    """Change a course — above all, whether everybody has to take it.

    Four are required of every employee, which is HSE's own list, and there was
    no way to change it. A list nobody can edit is a decision taken once and
    enforced forever, while the compliance report counts every employee in the
    plant against it: the first acceptance round asked plainly to "remove the
    mandatory courses from all staff" and there was nothing to click.

    Whether a course is statutory stays fixed. That follows from the
    regulation, not from us.
    """
    course = await crud.get_course(db, course_id)
    return CourseOut.model_validate(
        await crud.update_course(db, course, payload.model_dump(exclude_unset=True)))


@router.post("/courses/{course_id}/material", response_model=CourseOut)
async def attach_course_material(
    course_id: uuid.UUID, file: UploadFile,
    db: SessionDep, user: CanWriteTraining, token: BearerToken,  # noqa: ARG001
):
    """The course's own material, as opposed to one session's handout.

    The columns for it were added when the course table was, and nothing ever
    wrote to them — so the material had to be attached to each session
    separately, or not at all.
    """
    course = await crud.get_course(db, course_id)
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE // 1024 // 1024} MB limit")
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in COURSE_MATERIAL_TYPES:
        raise HTTPException(415, f"Unsupported file type '{ctype}'. Allowed: PDF or an image.")
    course.material_file_id = await upload_to_file_server(
        data, file.filename or "course.pdf", ctype, "ehs_crs", course.id, token)
    course.material_filename = file.filename or "course.pdf"
    await db.flush()
    return CourseOut.model_validate(course)


class MaterialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    file_size: int
    # Set for a link, in which case there is no file to download. Round 6.
    url: str | None = None
    uploaded_by_name: str | None
    created_at: datetime


class LinkIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    title: str | None = Field(default=None, max_length=255)


@router.get("/courses/{course_id}/materials", response_model=list[MaterialOut])
async def list_course_materials(
    course_id: uuid.UUID, db: SessionDep, user: CanReadTraining,  # noqa: ARG001
):
    """Everything attached to the course, oldest first."""
    await crud.get_course(db, course_id)
    return list((await db.execute(
        select(CourseMaterial)
        .where(CourseMaterial.course_id == course_id)
        .order_by(CourseMaterial.created_at)
    )).scalars().all())


@router.post("/courses/{course_id}/materials", response_model=MaterialOut,
             status_code=status.HTTP_201_CREATED)
async def add_course_material(
    course_id: uuid.UUID, file: UploadFile,
    db: SessionDep, user: CanWriteTraining, token: BearerToken,
):
    """Another file on the course, rather than replacing the one there.

    A course is rarely one file — the deck, the handout, the sign-off sheet —
    and uploading a second used to overwrite the first with nothing saying so.
    Any type but a page or a program, round 6.
    """
    course = await crud.get_course(db, course_id)
    return await materials.add_file(
        db, file, course_id=course.id, user_id=_user_id(user),
        user_name=await _resolve_name(db, _user_id(user)), token=token)


@router.post("/courses/{course_id}/materials/link", response_model=MaterialOut,
             status_code=status.HTTP_201_CREATED)
async def add_course_link(
    course_id: uuid.UUID, payload: LinkIn, db: SessionDep, user: CanWriteTraining,
):
    """A link — to a video, a site, a document kept elsewhere. Round 6."""
    course = await crud.get_course(db, course_id)
    return await materials.add_link(
        db, payload.url, payload.title, course_id=course.id, user_id=_user_id(user),
        user_name=await _resolve_name(db, _user_id(user)))


@router.get("/courses/{course_id}/materials/{material_id}")
async def download_course_material_item(
    course_id: uuid.UUID, material_id: uuid.UUID,
    db: SessionDep, user: CanReadTraining, token: BearerToken,  # noqa: ARG001
):
    row = await db.get(CourseMaterial, material_id)
    if row is None or row.course_id != course_id:
        raise HTTPException(404, "No such material on this course")
    if row.file_id is None:
        raise HTTPException(409, f"{row.filename} is a link: {row.url}")
    return await proxy_download(row.file_id, token)


@router.delete("/courses/{course_id}/materials/{material_id}",
               status_code=status.HTTP_204_NO_CONTENT)
async def remove_course_material(
    course_id: uuid.UUID, material_id: uuid.UUID,
    db: SessionDep, user: CanWriteTraining, token: BearerToken,  # noqa: ARG001
):
    row = await db.get(CourseMaterial, material_id)
    if row is None or row.course_id != course_id:
        raise HTTPException(404, "No such material on this course")
    # ★ Sessions hold the same file; it is deleted only when none of them does.
    await materials.remove(db, row, token)


@router.get("/courses/{course_id}/material")
async def download_course_material(
    course_id: uuid.UUID, db: SessionDep, user: CanReadTraining, token: BearerToken,  # noqa: ARG001
):
    course = await crud.get_course(db, course_id)
    if course.material_file_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"{course.name} has no material attached")
    return await proxy_download(course.material_file_id, token)


async def _with_who_ran_them(db, records) -> list[TrainingRecordOut]:
    """Records, each carrying who ran the session and who taught it.

    One query for the lot. Filled from the session rather than from the record,
    so a corrected instructor name shows everywhere at once.
    """
    ran = await crud.who_ran_them(db, records)
    out = []
    for r in records:
        item = TrainingRecordOut.model_validate(r)
        item.run_by_name, item.instructor_name, item.length_minutes = ran.get(
            r.id, (None, None, None))
        out.append(item)
    return out


@router.get("/records/mine", response_model=list[TrainingRecordOut])
async def my_records(db: SessionDep, user: CanReportIncident):
    """What this person has been trained on.

    ★ Its own endpoint, gated on being an employee. Reading the register is a
    much narrower permission than reading your own record, and an ordinary
    worker holds none of the training permissions — so without this the
    certificate the module issues them would exist and be unreachable, which is
    where this module keeps ending up.
    """
    return await _with_who_ran_them(db, await crud.list_training(
        db, user_id=_user_id(user), order="date"))


@router.get("/records", response_model=list[TrainingRecordOut])
async def list_records(
    db: SessionDep,
    user: CanReadTraining,  # noqa: ARG001
    user_id: uuid.UUID | None = None,
    course_id: uuid.UUID | None = None,
    search: str | None = Query(default=None, description="Person or course name"),
    completed_from: date | None = None,
    completed_to: date | None = None,
    order: str = Query(default="date", pattern="^(date|person|topic)$"),
):
    """The register. "Who has done this course", "what has this person done"
    and "what happened in March" are three questions of the same list."""
    return await _with_who_ran_them(db, await crud.list_training(
        db, user_id=user_id, course_id=course_id, search=search,
        completed_from=completed_from, completed_to=completed_to, order=order))


async def _register_for_export(
    db, user, *, user_id, course_id, search, completed_from, completed_to, order,
) -> tuple[list[dict], list[str], str | None]:
    """The rows, the filter in words, and who asked for them.

    Shared by the printed page and the spreadsheet so the two cannot disagree
    about what was asked for — which, on a document somebody mails on, is the
    whole thing that makes it evidence.
    """
    rows = await crud.list_training(
        db, user_id=user_id, course_id=course_id, search=search,
        completed_from=completed_from, completed_to=completed_to, order=order)
    ran = await crud.who_ran_them(db, rows)

    # ★ The names from their own tables, not off the first row: filtered to one
    # person who has done nothing there is no first row, and the page would say
    # nothing about who it is about.
    person = await _resolve_name(db, user_id) if user_id else None
    course = None
    if course_id:
        course = (await db.execute(
            select(Course.name).where(Course.id == course_id))).scalar()

    out = [{
        "user_name": r.user_name,
        "course_label": r.course_label,
        "completed_on": r.completed_on,
        "expires_on": r.expires_on,
        "run_by_name": ran.get(r.id, (None, None, None))[0],
        "instructor_name": ran.get(r.id, (None, None, None))[1],
        "length_minutes": ran.get(r.id, (None, None, None))[2],
        "recorded_by_name": r.recorded_by_name,
        "provider": r.provider,
    } for r in rows]
    described = training_pdf.describe(
        search=search, completed_from=completed_from, completed_to=completed_to,
        person=person, course=course)
    return out, described, await _resolve_name(db, _user_id(user))


@router.get("/records/export")
async def export_records(
    db: SessionDep,
    user: CanReadTraining,
    user_id: uuid.UUID | None = None,
    course_id: uuid.UUID | None = None,
    search: str | None = Query(default=None, description="Person or course name"),
    completed_from: date | None = None,
    completed_to: date | None = None,
    order: str = Query(default="date", pattern="^(date|person|topic)$"),
):
    """The same register as a spreadsheet. Round 5, TC-08.

    Same parameters as the list and the printed page, for the same reason: three
    documents that can disagree about what was asked for are worse than one.
    """
    rows, described, by = await _register_for_export(
        db, user, user_id=user_id, course_id=course_id, search=search,
        completed_from=completed_from, completed_to=completed_to, order=order)
    book = training_xlsx.render(rows=rows, filters=described, printed_by=by)
    return Response(
        content=book,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition(
            "training-records", extension="xlsx")})


@router.get("/records/print")
async def print_records(
    db: SessionDep,
    user: CanReadTraining,
    user_id: uuid.UUID | None = None,
    course_id: uuid.UUID | None = None,
    search: str | None = Query(default=None, description="Person or course name"),
    completed_from: date | None = None,
    completed_to: date | None = None,
    order: str = Query(default="date", pattern="^(date|person|topic)$"),
):
    """The same list, as a page somebody can hand over.

    Round 4, TC-10: "create a way to print training records based on the filter
    created." It took the same parameters as the list on purpose — a print that
    can disagree with the screen it was printed from is worse than no print.

    ★ The filter is printed on the page. "These twelve people are trained" and
    "these twelve records match a search for forklift in March" are different
    claims, and a page of rows with nothing saying which one it is will be read
    as the first.
    """
    rows, described, by = await _register_for_export(
        db, user, user_id=user_id, course_id=course_id, search=search,
        completed_from=completed_from, completed_to=completed_to, order=order)
    pdf = training_pdf.render(
        # Dates as text for the page; the spreadsheet keeps them as dates so the
        # column sorts.
        rows=[{**r,
               "completed_on": r["completed_on"].isoformat() if r["completed_on"] else None,
               "expires_on": (r["expires_on"].isoformat() if r["expires_on"]
                              else "Does not expire")}
              for r in rows],
        filters=described, printed_by=by)
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": content_disposition("training-records")})


@router.post("/records/{record_id}/certificate", response_model=TrainingRecordOut)
async def attach_certificate(
    record_id: uuid.UUID, file: UploadFile,
    db: SessionDep, user: CanWriteTraining, token: BearerToken,  # noqa: ARG001
):
    """The certificate for a course taken outside.

    The column has been on the record since it was built and accepted at
    create time, which meant a caller had to already know a file id — there
    was no way to produce one. So an external ticket, the thing a record of
    external training exists to hold, could not be attached at all.
    """
    record = await crud.get_training_record(db, record_id)
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE // 1024 // 1024} MB limit")
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in COURSE_MATERIAL_TYPES:
        raise HTTPException(415, f"Unsupported file type '{ctype}'. Allowed: PDF or an image.")
    record.certificate_file_id = await upload_to_file_server(
        data, file.filename or "certificate.pdf", ctype, "ehs_cert", record.id, token)
    await db.flush()
    return TrainingRecordOut.model_validate(record)


@router.get("/records/{record_id}/certificate")
async def download_certificate(
    record_id: uuid.UUID, db: SessionDep, user: CanReadTraining, token: BearerToken,  # noqa: ARG001
):
    record = await crud.get_training_record(db, record_id)
    if record.certificate_file_id is None:
        raise HTTPException(404, "No certificate is attached to this record")
    return await proxy_download(record.certificate_file_id, token)


@router.get("/records/{record_id}/certificate/issued")
async def issued_certificate(
    record_id: uuid.UUID, db: SessionDep, user: CanReportIncident,
):
    """The certificate this plant issues for its own training.

    Generated rather than stored: it is a rendering of the record, so it cannot
    drift from it, and a record corrected next week produces a corrected
    certificate rather than leaving a stale PDF in a file server.

    ★ Visible to the person who earned it and to whoever can read the training
    register — the third round asked for both. Somebody else's certificate is
    not yours to fetch.

    ★ Gated on being an employee, not on reading the register, because an
    ordinary worker holds no training permission and this is *their* document.
    The narrowing happens below. Guarding the route with the register
    permission would have been the permit-signature mistake again: a person
    allowed to have the thing, and not allowed to fetch it.
    """
    record = await crud.get_training_record(db, record_id)
    me = _user_id(user)
    if record.user_id != me and not await has_permission(
            db, me, user.get("role", ""), "ehs.training.read"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only open your own certificate")

    if record.delivery == "external":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This course was taken elsewhere, so the certificate is theirs to "
            "issue. Attach the one they gave you instead.")

    config = (await db.execute(select(EhsConfig).where(EhsConfig.id == 1))).scalar_one_or_none()
    pdf = certificate_pdf.render(
        person_name=record.user_name,
        course_name=record.course_label,
        course_code=None,
        completed_on=record.completed_on,
        expires_on=record.expires_on,
        record_no=str(record.id)[:8].upper(),
        verify_url=None,
        heading=config.certificate_heading if config else None,
        body=config.certificate_body if config else None,
        signatory=config.certificate_signatory if config else None,
    )
    # ★ Through the helper: HTTP headers are latin-1, so a name that needed the
    # CJK font cannot go in one — the download 500s for exactly the people this
    # work was about.
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(
            f"certificate-{record.user_name.replace(' ', '-')}")})


@router.post("/records", response_model=TrainingRecordOut, status_code=status.HTTP_201_CREATED)
async def record_training(payload: TrainingRecordIn, db: SessionDep, user: CanWriteTraining):
    record = await crud.record_training(db, payload, recorded_by=_user_id(user))
    return TrainingRecordOut.model_validate(record)


@router.get("/certifications", response_model=list[CertificationOut])
async def list_certifications(
    db: SessionDep,
    user: CanReadTraining,  # noqa: ARG001
    user_id: uuid.UUID | None = None,
    expiring_within_days: int | None = Query(
        default=None, ge=0, le=365,
        description="Certifications expiring within this many days, expired ones included."),
):
    today = date.today()
    rows = await crud.list_certifications(
        db, user_id=user_id, expiring_within_days=expiring_within_days, today=today)
    return [_cert_out(c, today) for c in rows]


@router.post("/certifications", response_model=CertificationOut,
             status_code=status.HTTP_201_CREATED)
async def add_certification(payload: CertificationIn, db: SessionDep, user: CanWriteTraining):  # noqa: ARG001
    cert = await crud.add_certification(db, payload)
    return _cert_out(cert, date.today())


@router.post("/certifications/{cert_id}/renew", response_model=CertificationOut)
async def renew_certification(
    cert_id: uuid.UUID, payload: RenewIn, db: SessionDep, user: CanWriteTraining,  # noqa: ARG001
):
    """Push the expiry out and reset the clock that was chasing it."""
    cert = await crud.get_certification(db, cert_id)
    if cert.expires_on and payload.expires_on <= cert.expires_on:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"The new expiry ({payload.expires_on}) must be later than the current "
            f"one ({cert.expires_on}) — renewing cannot shorten a certification",
        )
    await crud.renew_certification(
        db, cert, issued_on=payload.issued_on, expires_on=payload.expires_on)
    return _cert_out(cert, date.today())


@router.get("/gaps", response_model=GapReport)
async def training_gaps(db: SessionDep, user: CanReadTraining):  # noqa: ARG001
    """Who is missing which required training.

    Normally the first record a Ministry inspector asks for, so it is computed
    from the requirement itself — courses that apply to everyone plus those a
    person's position requires — rather than from a hand-maintained list.
    """
    return await crud.training_gaps(db)


# ── Certificate expiry notices (round 8) ─────────────────────────────────────


class ExpiryRuleIn(BaseModel):
    email: EmailStr
    days_before: int = Field(ge=1, le=365)
    is_active: bool = True


class ExpiryRulePatch(BaseModel):
    email: EmailStr | None = None
    days_before: int | None = Field(default=None, ge=1, le=365)
    is_active: bool | None = None


class ExpiryRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    days_before: int
    is_active: bool
    created_at: datetime


async def _rule(db, rule_id: uuid.UUID) -> CertExpiryRule:
    rule = await db.get(CertExpiryRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such expiry notice rule")
    return rule


@router.get("/expiry-rules", response_model=list[ExpiryRuleOut])
async def list_expiry_rules(db: SessionDep, user: CanManageSettings):  # noqa: ARG001
    """Who is emailed, how many days ahead of a certificate expiring."""
    return (await db.execute(select(CertExpiryRule).order_by(
        CertExpiryRule.days_before, CertExpiryRule.email))).scalars().all()


@router.post("/expiry-rules", response_model=ExpiryRuleOut, status_code=201)
async def create_expiry_rule(payload: ExpiryRuleIn, db: SessionDep, user: CanManageSettings):
    """Round 8: "notifications … emailed to a specified email address and have
    a field for the number of days notice". The first sweep after it is made
    lists everything already inside its window; after that, only what enters."""
    rule = CertExpiryRule(
        id=uuid.uuid4(), email=str(payload.email).strip(),
        days_before=payload.days_before, is_active=payload.is_active,
        created_by=_user_id(user))
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return rule


@router.patch("/expiry-rules/{rule_id}", response_model=ExpiryRuleOut)
async def update_expiry_rule(
    rule_id: uuid.UUID, payload: ExpiryRulePatch, db: SessionDep,
    user: CanManageSettings,  # noqa: ARG001
):
    rule = await _rule(db, rule_id)
    if payload.email is not None:
        rule.email = str(payload.email).strip()
    if payload.days_before is not None:
        rule.days_before = payload.days_before
    if payload.is_active is not None:
        rule.is_active = payload.is_active
    await db.flush()
    await db.refresh(rule)
    return rule


@router.delete("/expiry-rules/{rule_id}", status_code=204)
async def delete_expiry_rule(
    rule_id: uuid.UUID, db: SessionDep, user: CanManageSettings,  # noqa: ARG001
):
    await db.delete(await _rule(db, rule_id))
    await db.flush()
    return Response(status_code=204)
