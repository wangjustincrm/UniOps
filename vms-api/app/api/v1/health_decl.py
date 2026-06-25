"""Health declaration endpoints (PRD §6.5.4 / §2.2.2 VMS-CI-010..013).

Three routes:
  - GET  /api/v1/health-questions                  — active questionnaire template
  - POST /api/v1/visits/{id}/health-declaration    — submit a filled-in declaration
  - GET  /api/v1/visits/{id}/health-declaration    — read a filed declaration

S2-A scope: authenticated Host-fills-in-on-behalf path only. The kiosk
signed-URL flow is deferred to Phase 3 per S2_ARCHITECTURE_REVIEW.md F7.
"""
import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from app.core.deps import CurrentUserPayload, SessionDep
from app.core.request_meta import load_request_meta
from app.crud import audit as audit_crud
from app.crud import health_decl as health_crud
from app.crud import visit as visit_crud
from app.schemas.health_declaration import (
    HealthDeclarationCreate,
    HealthDeclarationListItem,
    HealthDeclarationListResponse,
    HealthDeclarationResponse,
)
from app.services.health_questions import get_template

# Routers: nested under /visits for submit/read, top-level for the template,
# and a top-level browser for the standalone declarations list.
visit_health_router = APIRouter(prefix="/visits", tags=["health-declaration"])
template_router = APIRouter(prefix="/health-questions", tags=["health-declaration"])
declarations_router = APIRouter(prefix="/health-declarations", tags=["health-declaration"])


# ── Template ────────────────────────────────────────────────────────────────-

class HealthQuestion(BaseModel):
    id: str
    text: str
    fail_on: str = "yes"


class HealthQuestionsTemplate(BaseModel):
    version: int
    questions: list[HealthQuestion]


@template_router.get("", response_model=HealthQuestionsTemplate)
async def get_health_questions(db: SessionDep, _: CurrentUserPayload):
    """Active questionnaire template. Open to any authenticated user (the
    frontend needs to render it for the Host)."""
    return await get_template(db)


# ── Submit + read ───────────────────────────────────────────────────────────-

class HealthDeclarationSubmit(BaseModel):
    """Payload from the HealthDeclForm.

    `answers` is a list of `{id, answer}` pairs keyed by template question id.
    The server bakes the question text into each answer at submit time so the
    historical record survives Admin template edits (PRD VMS-AU-013).

    `signature` is a base64-encoded PNG from `react-signature-canvas`
    (`signatureRef.current.toDataURL("image/png")`).

    `visitor_id` names which person on the visit this declaration is for.
    Omitted → the visit's primary visitor (single-visitor convenience).
    """
    answers: list[dict[str, Any]]
    safety_training_confirmed: bool = False
    signature: str | None = None
    visitor_id: uuid.UUID | None = None


@visit_health_router.post(
    "/{visit_id}/health-declaration",
    response_model=HealthDeclarationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_health_declaration(
    visit_id: uuid.UUID,
    payload: HealthDeclarationSubmit,
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
):
    """Submit a filled-in health declaration. The result (passed / failed) is
    computed server-side from the template's `fail_on` rules; clients don't
    decide the verdict. Auditor scope can read but not write."""
    visit = await visit_crud.get_visit(db, visit_id)
    if visit is None:
        raise HTTPException(status_code=404, detail="Visit not found")

    meta = await load_request_meta(db, user, request)
    host_dept = await visit_crud.fetch_host_department(db, visit.host_id)
    if not visit_crud.is_visible(
        visit,
        user_id=meta.user_id, role=meta.role,
        department_id=meta.department_id, host_department_id=host_dept,
    ):
        raise HTTPException(status_code=404, detail="Visit not found")

    # Auditor is read-only.
    if meta.role == "auditor":
        raise HTTPException(
            status_code=403,
            detail="Auditors cannot submit health declarations",
        )

    # Resolve which visitor this declaration is for (default: primary) and
    # reject ids that aren't on this appointment.
    target_visitor_id = payload.visitor_id or visit.visitor_id
    if target_visitor_id not in health_crud.visit_visitor_ids(visit):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="visitor_id is not part of this visit",
        )

    # Snapshot the active template into the declaration row so the historical
    # record stays valid even after Admin edits the template.
    template = await get_template(db)
    baked_answers = health_crud.snapshot_answers(template, payload.answers)
    result = health_crud.compute_result(template["questions"], baked_answers)

    questionnaire_data = {
        "template_version": template.get("version", 1),
        "answers": baked_answers,
        "computed_result": result.value,
    }

    before = audit_crud.snapshot(visit)
    row = await health_crud.submit_declaration(
        db,
        visit=visit,
        visitor_id=target_visitor_id,
        questionnaire_data=questionnaire_data,
        result=result,
        signature=payload.signature,
        safety_training_confirmed=payload.safety_training_confirmed,
    )

    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="health_decl.submit",
        entity_type="visit",
        entity_id=visit.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        old_value=before,
        new_value=audit_crud.snapshot(visit),
        notes=f"result={result.value}",
    )

    return HealthDeclarationResponse.model_validate(row)


@visit_health_router.get(
    "/{visit_id}/health-declaration",
    response_model=list[HealthDeclarationResponse],
)
async def list_health_declarations(
    visit_id: uuid.UUID,
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
):
    """All filed declarations for the visit — one per visitor who has signed.
    Same visibility scope as visits (auditor sees everything; Host sees only
    own / own-dept). Empty list when none filed yet."""
    visit = await visit_crud.get_visit(db, visit_id)
    if visit is None:
        raise HTTPException(status_code=404, detail="Visit not found")

    meta = await load_request_meta(db, user, request)
    host_dept = await visit_crud.fetch_host_department(db, visit.host_id)
    if not visit_crud.is_visible(
        visit,
        user_id=meta.user_id, role=meta.role,
        department_id=meta.department_id, host_department_id=host_dept,
    ):
        raise HTTPException(status_code=404, detail="Visit not found")

    rows = await health_crud.get_all_for_visit(db, visit_id)
    return [HealthDeclarationResponse.model_validate(r) for r in rows]


# ── Standalone browser (all signed declarations, visibility-scoped) ─────────-

@declarations_router.get("", response_model=HealthDeclarationListResponse)
async def browse_health_declarations(
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
):
    """Browse signed health declarations across visits. Visibility-scoped like
    /visits — auditor / system_admin see all, hosts see their own, dept-managers
    see their department. Filter by visit-date range and visitor name/company."""
    meta = await load_request_meta(db, user, request)
    rows, total = await health_crud.list_declarations(
        db,
        user_id=meta.user_id,
        role=meta.role,
        department_id=meta.department_id,
        date_from=date_from,
        date_to=date_to,
        q=q,
        page=page,
        page_size=page_size,
    )
    items = [
        HealthDeclarationListItem(
            id=decl.id,
            visit_id=decl.visit_id,
            visitor_id=decl.visitor_id,
            visitor_name=f"{vis.first_name} {vis.last_name}".strip(),
            company=vis.company_name,
            visit_date=visit.visit_date,
            host_name=host_name or "",
            access_area=visit.access_area.value,
            result=decl.result,
            questionnaire_data=decl.questionnaire_data,
            signature=decl.signature,
            created_at=decl.created_at,
            updated_at=decl.updated_at,
        )
        for decl, vis, visit, host_name in rows
    ]
    return HealthDeclarationListResponse(items=items, total=total)
