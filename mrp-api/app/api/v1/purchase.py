"""Purchase suggestions (Phase 1C).

`POST /purchase/runs` computes what to buy from the production plan
currently in force and stores it as a run; reads replay that stored run
rather than recomputing, so a suggestion a buyer is working from does not
change under them when stock moves or a rate is edited.

`PATCH /purchase/runs/{id}/lines/{line_id}` sets a line's status
(pending | ordered | ignored). That is the hand-off point for purchase
requisitions in a later round -- for now it lets a buyer mark what they
have already dealt with, which is the difference between a list they use
and a list they re-read from the top every morning.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.authz import require_permission
from app.core.deps import BearerToken, SessionDep
from app.models.mps import MrpMpsRun
from app.models.purchase import MrpPurchaseLine, MrpPurchaseRun
from app.services.numbering import next_timestamped_no
from app.services.purchase_export import build_purchase_workbook
from app.services.purchase_service import compute_suggestions

router = APIRouter(prefix="/purchase", tags=["purchase"])

ReportDep = Annotated[dict, Depends(require_permission("mrp.report.view"))]
WriteDep = Annotated[dict, Depends(require_permission("mrp.run.execute"))]

_LINE_STATUSES = ("pending", "ordered", "ignored")

# Distinct from the MPS run key (see app/services/numbering.py).
_PURCHASE_RUN_NO_LOCK_KEY = 0x4D525031

_XLSX_MEDIA_TYPE = ("application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet")


class PurchaseLineResponse(BaseModel):
    id: uuid.UUID
    material_code: str
    need_week: date
    order_date: date
    gross_qty: Decimal
    available_qty: Decimal
    net_qty: Decimal
    suggested_qty: Decimal
    raised_to_moq: Decimal
    partner_code: str | None
    lead_time_days: int | None
    supplier_missing: bool
    lead_time_missing: bool
    order_date_passed: bool
    status: str


class PurchaseRunResponse(BaseModel):
    id: uuid.UUID
    run_no: str
    source_plan_run_id: uuid.UUID | None
    raw_material_loss_rate: Decimal
    packaging_loss_rate: Decimal
    stats: dict | None
    created_at: datetime
    lines: list[PurchaseLineResponse]


class PurchaseRunSummaryResponse(BaseModel):
    id: uuid.UUID
    run_no: str
    source_plan_run_id: uuid.UUID | None
    stats: dict | None
    created_at: datetime


class LineStatusUpdate(BaseModel):
    status: str


async def _get_run_or_404(db: SessionDep, run_id: uuid.UUID) -> MrpPurchaseRun:
    run = await db.get(MrpPurchaseRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="purchase run not found")
    return run


async def _load_lines(db: SessionDep, run_id: uuid.UUID) -> list[MrpPurchaseLine]:
    return list((await db.execute(
        select(MrpPurchaseLine)
        .where(MrpPurchaseLine.run_id == run_id)
        .order_by(MrpPurchaseLine.order_date, MrpPurchaseLine.material_code)
    )).scalars().all())


def _run_response(run: MrpPurchaseRun, lines: list[MrpPurchaseLine]) -> PurchaseRunResponse:
    return PurchaseRunResponse(
        id=run.id, run_no=run.run_no, source_plan_run_id=run.source_plan_run_id,
        raw_material_loss_rate=run.raw_material_loss_rate,
        packaging_loss_rate=run.packaging_loss_rate,
        stats=run.stats, created_at=run.created_at,
        lines=[PurchaseLineResponse.model_validate(l, from_attributes=True) for l in lines],
    )


@router.get("/runs", response_model=list[PurchaseRunSummaryResponse])
async def list_purchase_runs(db: SessionDep, _: ReportDep):
    """Newest first. Declared before `/runs/{run_id}` so the literal path is
    not parsed as a UUID."""
    rows = (await db.execute(
        select(MrpPurchaseRun).order_by(MrpPurchaseRun.created_at.desc())
    )).scalars().all()
    return rows


@router.post("/runs", response_model=PurchaseRunResponse,
             status_code=status.HTTP_201_CREATED)
async def create_purchase_run(db: SessionDep, payload: WriteDep, token: BearerToken):
    """Compute purchase suggestions from the plan in force.

    409 when no plan is in force: with nothing released there is no demand
    to buy for, and answering with an empty run would read as "nothing to
    order" rather than "no plan yet".
    """
    plan = (await db.execute(
        select(MrpMpsRun).where(MrpMpsRun.is_default.is_(True))
    )).scalars().first()
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no production plan is in force; release a plan before calculating "
                   "purchase suggestions",
        )

    today = datetime.now(timezone.utc).date()
    suggestions, stats = await compute_suggestions(db, token, today=today)

    # Own advisory-lock key: the numbering helper serialises per (table,
    # prefix), and sharing a key with the MPS runs would make two unrelated
    # calculations queue behind each other.
    run_no = await next_timestamped_no(
        db, lock_key=_PURCHASE_RUN_NO_LOCK_KEY,
        column=MrpPurchaseRun.run_no,
        prefix="PUR-",
    )
    run = MrpPurchaseRun(
        run_no=run_no,
        source_plan_run_id=plan.id,
        generated_by=_sub_to_uuid(payload),
        raw_material_loss_rate=Decimal(stats["raw_material_loss_rate"]),
        packaging_loss_rate=Decimal(stats["packaging_loss_rate"]),
        stats=stats,
    )
    db.add(run)
    await db.flush()

    for item in suggestions:
        db.add(MrpPurchaseLine(
            run_id=run.id, material_code=item.material_code,
            need_week=item.need_week, order_date=item.order_date,
            gross_qty=item.gross, available_qty=item.available, net_qty=item.net,
            suggested_qty=item.suggested_qty, raised_to_moq=item.raised_to_moq,
            partner_code=item.partner_code, lead_time_days=item.lead_time_days,
            supplier_missing=item.supplier_missing,
            lead_time_missing=item.lead_time_missing,
            order_date_passed=item.order_date_passed,
        ))
    await db.commit()
    await db.refresh(run)
    return _run_response(run, await _load_lines(db, run.id))


@router.get("/runs/{run_id}", response_model=PurchaseRunResponse)
async def get_purchase_run(run_id: uuid.UUID, db: SessionDep, _: ReportDep):
    run = await _get_run_or_404(db, run_id)
    return _run_response(run, await _load_lines(db, run.id))


@router.get("/runs/{run_id}/export")
async def export_purchase_run(run_id: uuid.UUID, db: SessionDep, _: ReportDep):
    """The suggestions as xlsx, ordered by order date.

    Buyers work from a spreadsheet; a screen they have to transcribe is a
    screen they stop opening."""
    run = await _get_run_or_404(db, run_id)
    content = build_purchase_workbook(run, await _load_lines(db, run.id))
    filename = f"purchase-suggestions-{run.run_no}.xlsx"
    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/runs/{run_id}/lines/{line_id}", response_model=PurchaseLineResponse)
async def update_purchase_line(
    run_id: uuid.UUID, line_id: uuid.UUID, body: LineStatusUpdate,
    db: SessionDep, _: WriteDep,
):
    if body.status not in _LINE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown status {body.status!r}; must be one of {_LINE_STATUSES}",
        )
    line = await db.get(MrpPurchaseLine, line_id)
    if line is None or line.run_id != run_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="purchase line not found in this run")
    line.status = body.status
    await db.commit()
    await db.refresh(line)
    return PurchaseLineResponse.model_validate(line, from_attributes=True)


def _sub_to_uuid(payload: dict) -> uuid.UUID | None:
    """Same never-raises idiom the other modules use: a missing or malformed
    `sub` degrades to an unattributed write rather than a 500."""
    sub = payload.get("sub")
    if not sub:
        return None
    try:
        return uuid.UUID(sub)
    except ValueError:
        return None
