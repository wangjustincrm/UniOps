"""The evidence pack, and the auditor's way in.

Gated on `ehs.incident.read`, which the built-in `auditor` role already holds
along with read access to actions, training, the committee, equipment,
documents and permits. That is the whole point of that role, and adding a key
of its own here would mean granting it separately and eventually forgetting to.

One endpoint returning everything, because an auditor is handed one document —
and because assembling it across nine calls invites a page that renders eight
sections and silently drops the ninth.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.deps import SessionDep
from app.core.permissions import CanReadIncident
from app.services import audit as svc
from app.services.day_range import plant_today

router = APIRouter(prefix="/audit")


class EvidencePack(BaseModel):
    period: dict
    generated_at: str
    incidents: dict
    statutory: dict
    actions: dict
    training: dict
    committee: dict
    inspections: dict
    documents: dict
    high_risk_work: dict
    emergency: dict
    toolbox: dict


@router.get("/evidence-pack", response_model=EvidencePack)
async def evidence_pack(
    db: SessionDep, user: CanReadIncident,  # noqa: ARG001
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
):
    """Everything a COR or ISO 45001 audit asks for, over a period.

    Defaults to the calendar year, which is the period every one of these is
    conducted over.

    Every rate comes back null rather than as a figure when its denominator is
    zero. Zero of zero corrective actions closed on time is not full
    compliance, and showing it as 100% is the most common way a compliance
    dashboard lies.
    """
    today = plant_today()
    return await svc.build_pack(
        db,
        date_from=date_from or date(today.year, 1, 1),
        date_to=date_to or date(today.year + 1, 1, 1),
    )
