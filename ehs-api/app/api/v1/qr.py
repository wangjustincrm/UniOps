"""One QR resolver for the whole module.

Four things carry a printed code — a plant area, a piece of safety equipment, a
document, and a chemical's data sheet — and each one was going to grow its own
scan endpoint and its own front-end route. That is four ways to be wrong about
an unknown token, and four places to add the fifth kind.

Instead: one token space, one endpoint, one screen. The resolver says what the
token is and where to go; the scanner does not have to know what it is pointing
at before it points at it.

Two deliberate choices:

**A token is looked up, never parsed.** No prefixes, no embedded ids. A code
photographed off a wall is an opaque string, and making its meaning derivable
would let anyone construct one.

**An unknown token is a 404 with a plain sentence**, not an error page. Labels
outlive the things they were stuck to, and the person holding the phone needs
to be told the label is stale rather than that something went wrong.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.config import settings
from app.services import qr_svg
from app.core.deps import SessionDep
from app.core.permissions import CanReportIncident
from app.crud import asset as asset_crud
from app.crud import chemical as chemical_crud
from app.api.v1.incidents import _user_id
from app.crud import document as document_crud
from app.crud import permit as permit_crud
from app.crud import quiz as quiz_crud
from app.models.mirrors import Location

router = APIRouter(prefix="/qr")


class QrTarget(BaseModel):
    kind: str
    id: uuid.UUID
    # When this person already acknowledged this version, if they have. Only a
    # document carries it.
    signed_at: datetime | None = None
    label: str
    # Where the front end should go. Relative, because the same token resolves
    # inside the module and inside the Portal shell.
    path: str
    # What can be done from here, so the landing screen offers the right
    # buttons rather than a fixed set that is wrong for three of the four.
    actions: list[str]


@router.get("/{token}", response_model=QrTarget)
async def resolve(token: str, db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    """What this printed code points at.

    Gated on ehs.incident.report, which every active role holds: the whole
    point of a code on a wall is that whoever is standing in front of it can
    use it.
    """
    location = (await db.execute(
        select(Location).where(Location.qr_token == token))).scalar_one_or_none()
    if location is not None:
        return QrTarget(
            kind="location", id=location.id, label=location.path or location.name,
            # Prefills the area, which is the reason to scan rather than type.
            path=f"/report?locationId={location.id}",
            actions=["report_incident", "start_inspection"],
        )

    asset = await asset_crud.by_qr_token(db, token)
    if asset is not None:
        return QrTarget(
            kind="safety_asset", id=asset.id, label=f"{asset.asset_no} · {asset.name}",
            # The register, narrowed to this item — there is no per-asset page,
            # and inventing one for a screen reached by camera would be a page
            # nobody navigates to any other way.
            path=f"/equipment?asset={asset.id}",
            # ★ Whether this item is checked on a form. Round 4: "when a user
            # scans the eye wash station qr code the eye wash inspection form
            # shall open with the name of the station on the form automatic."
            # Reported rather than acted on here, because the landing page is
            # what decides where a camera sends somebody — and an item with no
            # form still wants the quick dialog.
            actions=(["inspection_form", "report_incident"]
                     if asset.inspection_template_id is not None
                     else ["record_inspection", "report_incident"]),
        )

    document = await document_crud.by_qr_token(db, token)
    if document is not None:
        # ★ Whether this person has already signed *this version*. Without it
        # the code by the door asks somebody to sign something they signed in
        # August, and pressing the button quietly returns the old record — so
        # the screen would say "Signed" about an act that did not just happen.
        signed_at = None
        if document.requires_ack:
            signed_at = await document_crud.acknowledged_at(
                db, document, user_id=_user_id(user))
        return QrTarget(
            kind="document", id=document.id,
            label=f"{document.title} v{document.version}",
            path=f"/documents/{document.id}",
            actions=(["read", "acknowledge"] if document.requires_ack and not signed_at
                     else ["read"]),
            signed_at=signed_at,
        )

    quiz = await quiz_crud.by_qr_token(db, token)
    if quiz is not None:
        # The code the trainer puts on the screen at the end of a session.
        return QrTarget(
            kind="quiz", id=quiz.id, label=quiz.title,
            path=f"/quizzes/{quiz.id}/take",
            actions=["take_quiz"],
        )

    permit = await permit_crud.by_qr_token(db, token)
    if permit is not None:
        # The code on the permit board by the space. Whoever scans it wants one
        # thing: is this still valid. The computed status is what they get.
        state = permit_crud.effective(permit)
        return QrTarget(
            kind="permit", id=permit.id,
            label=f"{permit.permit_no} · {permit.type_label} — {state}",
            path=f"/permits/{permit.id}",
            actions=["view_permit"] if state != "issued"
                    else ["view_permit", "record_gas", "close_permit"],
        )

    sheet = await chemical_crud.by_qr_token(db, token)
    if sheet is not None:
        # Straight to the sheet. A code on a drum is scanned by somebody who
        # needs to know what is in it, usually in a hurry, and a menu between
        # them and the answer is the wrong thing to build.
        chemical = await chemical_crud.get_chemical(db, sheet.chemical_id)
        return QrTarget(
            kind="sds", id=sheet.id,
            label=f"{chemical.name} — safety data sheet",
            path=f"/chemicals/{chemical.id}",
            actions=["read_sds", "report_incident"],
        )

    raise HTTPException(
        status.HTTP_404_NOT_FOUND,
        "This code is not in use. The label may be from something that has "
        "since been removed — check with HSE before relying on it.",
    )


@router.get("/{token}/image", response_class=Response)
async def qr_image(
    token: str, db: SessionDep, user: CanReportIncident,  # noqa: ARG001
    size: int = Query(default=240, ge=96, le=1024),
):
    """The code itself, as SVG, ready to print and stick on something.

    Resolved before it is drawn. A code for a token nothing answers is a
    sticker that goes on a wall and does nothing when scanned, which is worse
    than no sticker — somebody tries it, it fails, and they stop trying the
    others.

    SVG rather than a raster: it is going to be printed at whatever size the
    label maker runs, and a code that has gone soft is one a phone gives up on.
    """
    target = await resolve(token, db, user)          # 404s on an unknown token
    url = f"{settings.SAFETY_WEB_URL.rstrip('/')}/qr/{token}"
    return Response(
        content=qr_svg.svg(url, size_px=size),
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition":
                f'inline; filename="qr-{target.kind}-{token}.svg"',
        },
    )
