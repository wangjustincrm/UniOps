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

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import SessionDep
from app.core.permissions import CanReportIncident
from app.crud import asset as asset_crud
from app.crud import chemical as chemical_crud
from app.crud import document as document_crud
from app.models.mirrors import Location

router = APIRouter(prefix="/qr")


class QrTarget(BaseModel):
    kind: str
    id: uuid.UUID
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
            actions=["record_inspection", "report_incident"],
        )

    document = await document_crud.by_qr_token(db, token)
    if document is not None:
        return QrTarget(
            kind="document", id=document.id,
            label=f"{document.title} v{document.version}",
            path=f"/documents/{document.id}",
            actions=["read", "acknowledge"] if document.requires_ack else ["read"],
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
