"""Workplace labels for chemical containers.

The gate is the whole feature: `GET /labels/{id}/pdf` refuses anything that has
not been approved. HSE's answer to question 6.8 was "HSE approval required
before any label can be printed", and it is enforced where the bytes are
produced rather than by hiding a button.

Extraction from the data sheet is an assist and is allowed to be unavailable.
Where no key is configured the endpoint says so plainly and the draft is filled
in from the chemical record instead — which is a transcription either way, and
nothing prints without approval regardless.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.incidents import _user_id
from app.core.deps import BearerToken, SessionDep
from app.core.permissions import CanManageDocument, CanReadDocument
from app.crud import chemical as chemical_crud
from app.crud import label as crud
from app.services.http_headers import content_disposition
from app.services import ghs, label_pdf, pictograms
from app.services.attachments import fetch_file_bytes, proxy_download

router = APIRouter()


class Statement(BaseModel):
    # Long enough for a combined precautionary code. GHS routinely joins them
    # — "P305+P351+P338" is the standard eye-contact statement and is fourteen
    # characters; a tighter cap rejects the most common ones on a label.
    code: str | None = Field(default=None, max_length=40)
    text: str = Field(min_length=1, max_length=400)


class LabelPatch(BaseModel):
    product_identifier: str | None = Field(default=None, max_length=250)
    supplier: str | None = Field(default=None, max_length=200)
    signal_word: str | None = Field(default=None, pattern="^(Danger|Warning)$")
    pictograms: list[str] | None = None
    hazard_statements: list[Statement] | None = None
    precautionary_statements: list[Statement] | None = None
    first_aid: str | None = None
    ppe_note: str | None = None


class LabelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    chemical_id: uuid.UUID
    sds_id: uuid.UUID | None
    product_identifier: str
    supplier: str | None
    signal_word: str | None
    pictograms: list
    hazard_statements: list
    precautionary_statements: list
    first_aid: str | None
    ppe_note: str | None
    source: str
    extraction: dict | None
    status: str
    approved_at: datetime | None
    approved_by_name: str | None
    print_count: int
    last_printed_at: datetime | None
    # What still needs filling in. Shown, not enforced, beyond the two things
    # whose absence makes it not a label.
    missing: list[str] = []


class ExtractionOut(BaseModel):
    available: bool
    applied: bool
    values: dict = {}
    confidence: dict = {}
    # Fields the model was least sure about. A pointer for the reviewer, not a
    # threshold anything branches on.
    check_closely: list[str] = []
    model: str | None = None
    note: str | None = None


async def _out(db, label) -> LabelOut:
    item = LabelOut.model_validate(label)
    item.missing = await crud.missing_for_approval(db, label)
    return item


@router.get("/labels", response_model=list[LabelOut])
async def list_labels(
    db: SessionDep, user: CanReadDocument,  # noqa: ARG001
    chemical_id: uuid.UUID | None = None,
    status_in: list[str] | None = Query(default=None, alias="status"),
):
    return [await _out(db, row) for row in
            await crud.list_labels(db, chemical_id=chemical_id, status_in=status_in)]


@router.post("/chemicals/{chemical_id}/labels", response_model=LabelOut,
             status_code=status.HTTP_201_CREATED)
async def draft_label(
    chemical_id: uuid.UUID, db: SessionDep, user: CanManageDocument,
):
    """Start a draft from what the inventory already knows.

    Not a poor relation to extraction: the product name, supplier, signal word,
    pictograms, PPE and first-aid note were all entered when the chemical was
    registered. Only the H- and P-statements need the data sheet.
    """
    chemical = await chemical_crud.get_chemical(db, chemical_id)
    label = await crud.draft_from_chemical(db, chemical, creator_id=_user_id(user))
    return await _out(db, label)


@router.get("/labels/{label_id}", response_model=LabelOut)
async def get_label(label_id: uuid.UUID, db: SessionDep, user: CanReadDocument):  # noqa: ARG001
    return await _out(db, await crud.get_label(db, label_id))


@router.patch("/labels/{label_id}", response_model=LabelOut)
async def update_label(
    label_id: uuid.UUID, payload: LabelPatch, db: SessionDep, user: CanManageDocument,  # noqa: ARG001
):
    label = await crud.get_label(db, label_id)
    fields = payload.model_dump(exclude_unset=True)
    for key in ("hazard_statements", "precautionary_statements"):
        if key in fields and fields[key] is not None:
            fields[key] = [s for s in fields[key]]
    return await _out(db, await crud.update_label(db, label, fields))


@router.post("/labels/{label_id}/read-sds", response_model=ExtractionOut)
async def read_from_sds(
    label_id: uuid.UUID, db: SessionDep, user: CanManageDocument,  # noqa: ARG001
    token: BearerToken,
):
    """Read the label elements off the product's current data sheet.

    Fills only what is still empty — somebody who has already corrected a field
    has made a judgement, and re-running this must not quietly undo it.
    """
    label = await crud.get_label(db, label_id)
    if not ghs.is_available():
        return ExtractionOut(
            available=False, applied=False,
            note="Reading data sheets automatically is not configured on this "
                 "server. Fill the label in from the sheet — it is a "
                 "transcription either way, and nothing prints without approval.")

    sheet = await chemical_crud.current_sheet(db, label.chemical_id)
    if sheet is None or sheet.file_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "This product has no data sheet on file to read.")

    response = await proxy_download(sheet.file_id, token)
    content = response.body if hasattr(response, "body") else b""
    media = (response.headers.get("content-type") or "application/pdf").split(";")[0]

    try:
        result = await ghs.extract_from_sds(content, media)
    except ghs.ExtractionUnavailable as exc:
        return ExtractionOut(available=False, applied=False, note=str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    await crud.apply_extraction(db, label, result)
    return ExtractionOut(
        available=True, applied=True,
        values=result.get("values", {}),
        confidence=result.get("confidence", {}),
        check_closely=result.get("check_closely", []),
        model=result.get("model"),
        note="Read from the data sheet and filled into the empty fields. Check "
             "it against the sheet before approving — nothing prints until you do.",
    )


@router.post("/labels/{label_id}/approve", response_model=LabelOut)
async def approve_label(label_id: uuid.UUID, db: SessionDep, user: CanManageDocument):
    """The gate. Approving says you have read it against the data sheet."""
    label = await crud.get_label(db, label_id)
    return await _out(db, await crud.approve(db, label, user_id=_user_id(user)))


@router.get("/labels/{label_id}/pdf")
async def label_pdf_download(
    label_id: uuid.UUID, db: SessionDep, user: CanReadDocument, token: BearerToken,  # noqa: ARG001
    copies: int = Query(default=2, ge=1, le=20),
):
    """The printable sheet.

    Refuses an unapproved label here, where the bytes are produced, rather than
    by hiding a button — a hidden button is a suggestion, and this is a rule.
    """
    label = await crud.get_label(db, label_id)
    if label.status == "superseded":
        # ★ A newer data sheet has arrived since this was approved. The hazard
        # classification may have changed with it, and the label on the drum is
        # what somebody reads before opening it — printing the old one is a
        # WHMIS breach dressed up as a working feature.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A newer safety data sheet has been filed since this label was "
            "approved. Make a new label from the current sheet — the hazards "
            "may have changed.")
    if label.status not in ("approved", "printed"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This label has not been approved. Nothing prints until somebody has "
            "checked it against the data sheet.")

    # Fetch the artwork for whatever classes this label claims. Keyed by the
    # vocabulary code so both shapes on file resolve — the reader writes the
    # code, a hand-filled label writes the class name.
    artwork: dict[str, bytes] = {}
    for pic in await pictograms.resolve(db, label.pictograms):
        if pic.file_id is None:
            continue
        data = await fetch_file_bytes(pic.file_id, token)
        if data is None:
            continue
        for key in {pic.code.upper(), pic.label.strip().upper()}:
            artwork[key] = data

    pdf = label_pdf.render(label, chemical_id=str(label.chemical_id), copies=copies,
                           artwork=artwork)
    await crud.record_print(db, label)
    return Response(
        content=pdf,
        media_type="application/pdf",
        # ★ Through the helper: a Chinese product name cannot go in a header
        # as-is, and this printed the label fine and then answered 500 on the
        # way out.
        headers={"Content-Disposition": content_disposition(
            f"label-{label.product_identifier[:40]}")},
    )
