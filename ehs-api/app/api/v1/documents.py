"""The safety document and policy library, with Read & Sign.

`ehs.document.read` is held by every active role. That is deliberate and is
explained in identity migration 0015: a policy nobody can open cannot be
acknowledged, and the gate resolves an ungranted key to false for everybody.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.incidents import _user_id
from app.models.mirrors import User
from app.core.deps import BearerToken, SessionDep
from app.core.permissions import (
    CanManageDocument, CanReadDocument, CanReadWorker,
)
from app.crud import document as crud
from app.crud import quiz as quiz_crud
from app.services.attachments import MAX_FILE_SIZE, proxy_download, upload_to_file_server

router = APIRouter(prefix="/documents")

# A policy is read on a phone in a corridor, so the file has to be something a
# phone opens. PDF above all; images because a laminated one-pager is often
# photographed rather than typed up.
ALLOWED_TYPES = {
    "application/pdf", "image/jpeg", "image/png", "image/webp",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# `emergency` is here rather than being its own table: an emergency plan is
# a controlled document with a review cycle and a read-and-sign list, which
# is exactly what this library already is.
_CATEGORY = "^(policy|procedure|emergency|sds|guideline|form|other)$"


class DocumentIn(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    category: str = Field(default="policy", pattern=_CATEGORY)
    summary: str | None = None
    version: str = Field(default="1.0", max_length=20)
    file_id: uuid.UUID | None = None
    filename: str | None = Field(default=None, max_length=255)
    effective_on: date | None = None
    # Months between reviews. 12 for the health and safety policy (OHSA
    # s.25(2)(j)); 36 for an SDS. Null for something that does not lapse.
    review_months: int | None = Field(default=None, ge=1, le=120)
    requires_ack: bool = False
    # Empty = everybody. Role codes narrow it.
    ack_roles: list[str] = Field(default_factory=list)
    # Named people, added to whatever the roles reach rather than narrowing it.
    ack_user_ids: list[uuid.UUID] = Field(default_factory=list)
    owner_id: uuid.UUID | None = None


class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, max_length=250)
    category: str | None = Field(default=None, pattern=_CATEGORY)
    summary: str | None = None
    version: str | None = Field(default=None, max_length=20)
    file_id: uuid.UUID | None = None
    filename: str | None = Field(default=None, max_length=255)
    effective_on: date | None = None
    review_months: int | None = Field(default=None, ge=1, le=120)
    requires_ack: bool | None = None
    ack_roles: list[str] | None = None
    ack_user_ids: list[uuid.UUID] | None = None
    owner_id: uuid.UUID | None = None


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    doc_no: str
    title: str
    category: str
    summary: str | None
    version: str
    file_id: uuid.UUID | None
    filename: str | None
    effective_on: date | None
    review_due_on: date | None
    review_months: int | None
    status: str
    published_at: datetime | None
    published_by_name: str | None
    supersedes_id: uuid.UUID | None
    requires_ack: bool
    ack_roles: list[str]
    ack_user_ids: list[uuid.UUID]
    qr_token: str | None
    owner_name: str | None
    # ★ What replaced this one, when it has been retired by a newer version.
    # Round 5, TC-22: landing on a superseded policy with no way to sign it and
    # nothing saying why is a dead end — and old links and bookmarks keep
    # arriving here long after the task that pointed at it was closed.
    superseded_by_id: uuid.UUID | None = None
    superseded_by_version: str | None = None
    # The quiz this document asks for before signing, and whether the person
    # asking has passed it. Round 6.
    quiz_id: uuid.UUID | None = None
    quiz_title: str | None = None
    quiz_passed: bool = False


class AckIn(BaseModel):
    # Optional: a tick is a valid acknowledgement, a drawn signature is a
    # stronger one. HSE asked for the second where it matters.
    signature: str | None = None


class AckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    user_name: str
    user_role: str | None
    version: str
    acknowledged_at: datetime


class OutstandingPerson(BaseModel):
    user_id: str
    user_name: str


class SigningStatusOut(BaseModel):
    version: str
    audience: int
    signed: int
    percent: int
    outstanding: list[OutstandingPerson]


class NewVersionIn(BaseModel):
    version: str = Field(min_length=1, max_length=20)


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    db: SessionDep, user: CanReadDocument,  # noqa: ARG001
    category: str | None = Query(default=None, pattern=_CATEGORY),
    status_in: list[str] | None = Query(default=None, alias="status"),
):
    return await crud.list_documents(db, category=category, status_in=status_in)


@router.get("/mine", response_model=list[DocumentOut])
async def my_outstanding(db: SessionDep, user: CanReadDocument):
    """What I have been asked to read and have not signed."""
    return await crud.mine_outstanding(db, _user_id(user))


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def create_document(payload: DocumentIn, db: SessionDep, user: CanManageDocument):
    return await crud.create_document(db, payload.model_dump(), owner_id=_user_id(user))


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(document_id: uuid.UUID, db: SessionDep, user: CanReadDocument):
    doc = await crud.get_document(db, document_id)
    out = DocumentOut.model_validate(doc)
    quiz = await quiz_crud.for_document(db, doc.id)
    if quiz is not None:
        out.quiz_id, out.quiz_title = quiz.id, quiz.title
        out.quiz_passed = await quiz_crud.passed_for_document(db, quiz.id, doc, _user_id(user))
    # Only looked up for a retired one: on a current document there is nothing
    # to say, and it would be a query per read for every list.
    if doc.status == "retired":
        replacement = await crud.replacement_for(db, doc)
        if replacement is not None:
            out.superseded_by_id = replacement.id
            out.superseded_by_version = replacement.version
    return out


@router.patch("/{document_id}", response_model=DocumentOut)
async def update_document(
    document_id: uuid.UUID, payload: DocumentPatch, db: SessionDep, user: CanManageDocument,  # noqa: ARG001
):
    doc = await crud.get_document(db, document_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(doc, key, value)
    await db.flush()
    return doc


@router.post("/{document_id}/publish", response_model=DocumentOut)
async def publish(document_id: uuid.UUID, db: SessionDep, user: CanManageDocument):
    """Make it current, start its review clock, and ask the audience to sign."""
    doc = await crud.get_document(db, document_id)
    return await crud.publish(db, doc, user_id=_user_id(user))


@router.post("/{document_id}/new-version", response_model=DocumentOut,
             status_code=status.HTTP_201_CREATED)
async def new_version(
    document_id: uuid.UUID, payload: NewVersionIn, db: SessionDep, user: CanManageDocument,
):
    """A draft that retires this one when it is published."""
    doc = await crud.get_document(db, document_id)
    return await crud.new_version(db, doc, version=payload.version, owner_id=_user_id(user))


@router.post("/{document_id}/retire", response_model=DocumentOut)
async def retire(document_id: uuid.UUID, db: SessionDep, user: CanManageDocument):
    """Archive a document: it stops being in force and asks nobody to sign it.

    Round 7, TC-11: "add an archive option on all documents." A draft or a
    published version; one already retired is left as it is.
    """
    doc = await crud.get_document(db, document_id)
    if doc.status == "retired":
        return doc
    me = await db.get(User, _user_id(user))
    return await crud.retire(db, doc, user_id=_user_id(user), user_name=me.full_name if me else None)


@router.post("/{document_id}/acknowledge", response_model=AckOut,
             status_code=status.HTTP_201_CREATED)
async def acknowledge(
    document_id: uuid.UUID, payload: AckIn, db: SessionDep, user: CanReadDocument,
):
    doc = await crud.get_document(db, document_id)
    return await crud.acknowledge(
        db, doc, user_id=_user_id(user), signature=payload.signature)


@router.get("/{document_id}/acknowledgements", response_model=list[AckOut])
async def list_acknowledgements(
    document_id: uuid.UUID, db: SessionDep, user: CanReadWorker,  # noqa: ARG001
):
    """Everybody who has signed it.

    Round 4 made this readable by anyone who could read the document, which is
    everybody — so an employee could see which of their colleagues had not
    signed the harassment policy. Whether somebody has complied is a management
    fact about them, so round 5 narrowed it to whoever manages documents.

    ★ That went too far the other way, and round 5 said so: "give supervisors
    the whole roster as they may have staff from other departments with them."
    A supervisor who cannot see who on their crew has signed cannot chase them,
    which is most of what the roster is for.

    So it is `ehs.worker.read` — HSE, the coordinator and area supervisors. It
    is the permission for facts about people, which is what this is, and it is
    held by exactly the three roles that chase compliance. The whole roster,
    not their own department: Kirk's reason is that a supervisor runs crews from
    more than one.
    """
    return await crud.acknowledgements(db, await crud.get_document(db, document_id))


@router.get("/{document_id}/signing-status", response_model=SigningStatusOut)
async def signing_status(document_id: uuid.UUID, db: SessionDep, user: CanReadWorker):  # noqa: ARG001
    """Who has signed, and — the half that gets used — who has not.

    The same audience as the roster above, for the same reason: chasing the
    people who have not signed is a supervisor's job as much as HSE's.
    """
    return await crud.signing_status(db, await crud.get_document(db, document_id))


@router.post("/{document_id}/file", response_model=DocumentOut)
async def attach_file(
    document_id: uuid.UUID, file: UploadFile,
    db: SessionDep, user: CanManageDocument, token: BearerToken,  # noqa: ARG001
):
    """Attach the document itself. Replacing it on a published version is
    refused — that would change what people already signed for without
    changing the version they signed against."""
    doc = await crud.get_document(db, document_id)
    if doc.status == "published":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{doc.doc_no} v{doc.version} is published. Start a new version rather "
            "than replacing the file people have already acknowledged.")

    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE // 1024 // 1024} MB limit")
    ctype = (file.content_type or "").split(";")[0].strip().lower()
    if ctype not in ALLOWED_TYPES:
        raise HTTPException(415, f"Unsupported file type '{ctype}'. Allowed: PDF, Word, images.")

    doc.file_id = await upload_to_file_server(
        data, file.filename or "document.pdf", ctype, "ehs_doc", doc.id, token)
    doc.filename = file.filename or "document.pdf"
    await db.flush()
    return doc


@router.get("/{document_id}/file")
async def download_file(
    document_id: uuid.UUID, db: SessionDep, user: CanReadDocument, token: BearerToken,  # noqa: ARG001
):
    doc = await crud.get_document(db, document_id)
    if doc.file_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This document has no file attached yet")
    return await proxy_download(doc.file_id, token)
