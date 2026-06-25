"""Badge printing + template endpoints (PRD §6.5.3).

  - `POST /api/v1/visits/{id}/print-badge` — atomic print + check-in.
  - `GET  /api/v1/visits/{id}/badge-history` — chronological prints.
  - `GET  /api/v1/badge/templates` — list configured templates.
  - `PUT  /api/v1/badge/templates/{name}` — admin upserts a template.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from app.core.deps import CurrentUserPayload, SessionDep, require_roles
from app.core.request_meta import load_request_meta
from app.crud import audit as audit_crud
from app.crud import badge as badge_crud
from app.crud import visit as visit_crud
from app.crud import visitor as visitor_crud
from app.models.user_mirror import User
from app.models.visitor import Visitor
from app.models.vms_config import VmsConfig
from app.schemas.badge_print import (
    BadgePrintCreate,
    BadgePrintResponse,
    BadgeTemplate,
)
from app.schemas.visit import VisitResponse
from app.services import compliance as compliance_svc
from app.services import notifications as notifications_svc

# Two routers: badge prints (per-visit, nested under /visits) + template admin.
visit_badge_router = APIRouter(prefix="/visits", tags=["badge"])
template_router = APIRouter(prefix="/badge", tags=["badge"])

AdminDep = Depends(require_roles("system_admin"))


# ── Print + check-in ────────────────────────────────────────────────────────-

@visit_badge_router.post(
    "/{visit_id}/print-badge",
    response_model=VisitResponse,
    status_code=status.HTTP_201_CREATED,
)
async def print_visit_badge(
    visit_id: uuid.UUID,
    payload: BadgePrintCreate,
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
):
    """Returns the (possibly newly-checked-in) Visit row.

    Atomically:
      1. Verifies the caller can see the visit (PRD §3.2 scope).
      2. Inserts a `vms_badge_prints` row.
      3. If this is the first print → sets `actual_arrival` and flips
         `status` to `checked_in`.
      4. Writes one audit log entry — action_type either `visit.check_in`
         (first print) or `badge.reprint` (subsequent).
    """
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

    # Training + PPE are NOT a pre-print gate. A visitor must check in and get
    # their badge first, enter the site, and only then receive PPE / training.
    # So badge printing is never blocked on compliance; instead we open the
    # confirm tasks (and send the HR / Janitor heads-up) at check-in — see the
    # post-print block below.
    before = audit_crud.snapshot(visit)
    _, was_first = await badge_crud.print_badge(
        db,
        visit=visit,
        printed_by=meta.user_id,
        template_used=payload.template_used,
        reprint_reason=payload.reprint_reason,
    )
    after = audit_crud.snapshot(visit)

    # Check-in compliance follow-ups (GMP / Lab / all). Fire once, on the first
    # print (= check-in). Training/PPE happen after the visitor is on-site, so
    # these are post-entry confirmations, not entry gates.
    if was_first and compliance_svc.visit_requires_compliance(visit):
        all_visitor_ids = [visit.visitor_id, *(visit.additional_visitor_ids or [])]
        wanted: list = []
        for vid in all_visitor_ids:
            try:
                wanted.append(uuid.UUID(str(vid)))
            except (ValueError, TypeError):
                continue
        visitor_rows = (await db.execute(
            select(Visitor).where(Visitor.id.in_(wanted))
        )).scalars().all()
        for v in visitor_rows:
            if not compliance_svc.training_fresh(v):
                await compliance_svc.open_compliance_task(
                    db, kind="training", visitor=v, triggering_visit=visit,
                )
            if not compliance_svc.ppe_fresh(v):
                await compliance_svc.open_compliance_task(
                    db, kind="ppe", visitor=v, triggering_visit=visit,
                )
        # HR training heads-up email (primary visitor + host for context).
        primary = next((v for v in visitor_rows if v.id == visit.visitor_id), None)
        host_user = (await db.execute(
            select(User).where(User.id == visit.host_id)
        )).scalar_one_or_none()
        if primary is not None:
            await notifications_svc.maybe_dispatch_visit_notifications(
                db, visit=visit, visitor=primary, host=host_user,
            )

    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="visit.check_in" if was_first else "badge.reprint",
        entity_type="visit",
        entity_id=visit.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        old_value=before,
        new_value=after,
        notes=payload.reprint_reason if not was_first else None,
    )

    return VisitResponse.model_validate(visit)


@visit_badge_router.get(
    "/{visit_id}/badge-history",
    response_model=list[BadgePrintResponse],
)
async def get_badge_history(
    visit_id: uuid.UUID,
    request: Request,
    db: SessionDep,
    user: CurrentUserPayload,
):
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

    rows = await badge_crud.list_badge_prints(db, visit_id)
    return [BadgePrintResponse.model_validate(r) for r in rows]


# ── Templates ──────────────────────────────────────────────────────────────--

async def _load_config(db) -> VmsConfig:
    cfg = (await db.execute(select(VmsConfig).limit(1))).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(
            status_code=500,
            detail="vms_config singleton missing — re-run migrations",
        )
    return cfg


@template_router.get("/templates", response_model=dict[str, BadgeTemplate])
async def list_badge_templates(
    db: SessionDep,
    _: CurrentUserPayload,
):
    """All currently-configured badge templates, keyed by name.

    Open to any authenticated user — the frontend renderer needs them so
    Hosts can see what badge layout will print.
    """
    cfg = await _load_config(db)
    templates: dict = cfg.badge_templates or {}
    out: dict[str, BadgeTemplate] = {}
    for name, t in templates.items():
        if not isinstance(t, dict):
            continue
        out[name] = BadgeTemplate(
            name=t.get("name", name),
            html=t.get("html", ""),
            css=t.get("css", ""),
            is_default=bool(t.get("is_default", False)),
        )
    return out


@template_router.put("/templates/{name}", response_model=BadgeTemplate)
async def upsert_badge_template(
    name: str,
    payload: BadgeTemplate,
    request: Request,
    db: SessionDep,
    user: dict = AdminDep,
):
    """Admin upserts a template by name (PRD §2.3 VMS-LB-010).

    For S1, this is mostly a parking spot — the VMS Admin UI ships in S2-C.
    The endpoint exists now so the JSONB field has a write path and audit
    logs are wired from day one.
    """
    cfg = await _load_config(db)
    templates = dict(cfg.badge_templates or {})

    before = templates.get(name)
    templates[name] = {
        "name": name,
        "html": payload.html,
        "css": payload.css,
        "is_default": payload.is_default,
    }
    cfg.badge_templates = templates
    await db.flush()

    meta = await load_request_meta(db, user, request)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="badge_template.upsert" if before is None else "badge_template.update",
        entity_type="badge_template",
        entity_id=cfg.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        old_value={"name": name, **(before or {})} if before else None,
        new_value=templates[name],
        notes=name,
    )

    return BadgeTemplate(**templates[name])
