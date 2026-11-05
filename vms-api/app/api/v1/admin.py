"""VMS Admin endpoints (system_admin only).

Access zones (replaced the Quality Manager roster, 2026-10):
  GET    /api/v1/admin/zones
  POST   /api/v1/admin/zones
  PATCH  /api/v1/admin/zones/{zone_id}
  DELETE /api/v1/admin/zones/{zone_id}

Notification contacts + health questions (W11 / S2-C):
  GET  /api/v1/admin/notification-contacts
  PUT  /api/v1/admin/notification-contacts
  GET  /api/v1/admin/health-questions
  PUT  /api/v1/admin/health-questions

Badge templates are managed via /api/v1/badge/templates (W5 stub) — same
admin gate, different router.

Cross-system data-maintenance admin (browse/edit/cascade-delete VMS
records — visits, visitors, health declarations):
  GET    /api/v1/admin/entities
  GET    /api/v1/admin/{entity}
  GET    /api/v1/admin/{entity}/{record_id}
  PATCH  /api/v1/admin/{entity}/{record_id}
  DELETE /api/v1/admin/{entity}/{record_id}
  POST   /api/v1/admin/{entity}/bulk-delete

NOTE: the data-maintenance routes below use path parameters (`{entity}`,
`{entity}/{record_id}`) and are registered AFTER the static routes above so
Starlette matches the more specific static paths first.
"""
import re
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, func, select

from app.admin import service
from app.admin.registry import REGISTRY
from app.core.deps import SessionDep, require_roles
from app.core.request_meta import load_request_meta
from app.crud import audit as audit_crud
from app.models.user_mirror import User
from app.models.visit import Visit
from app.models.vms_config import VmsConfig
from app.models.zone import Zone, ZoneManager
from app.schemas.zone import ZoneCreate, ZoneManagerOut, ZoneOut, ZoneUpdate
from app.schemas.vms_config import (
    HealthQuestionsUpdate,
    NotificationContactsUpdate,
    SmtpSettingsUpdate,
    SmtpTestRequest,
    SmtpTestResponse,
)
from app.services.health_questions import DEFAULT_HEALTH_QUESTIONS
from app.services import notifications as notifications_svc
from app.schemas.badge_config import BadgeConfig
from app.services.badge_config import DEFAULT_BADGE_CONFIG, deep_merge

router = APIRouter(prefix="/admin", tags=["admin"])

AdminDep = Annotated[dict, Depends(require_roles("system_admin"))]


async def _load_config(db: SessionDep) -> VmsConfig:
    cfg = (await db.execute(select(VmsConfig).limit(1))).scalar_one_or_none()
    if cfg is None:
        raise HTTPException(
            status_code=500,
            detail="vms_config singleton missing — re-run migrations",
        )
    return cfg


# ── Access zones (VMS Admin → Zones) ────────────────────────────────────────

async def _zone_out(db, zone: Zone) -> ZoneOut:
    managers = (await db.execute(
        select(User.id, User.full_name, User.email, User.is_active)
        .join(ZoneManager, ZoneManager.user_id == User.id)
        .where(ZoneManager.zone_id == zone.id)
        .order_by(ZoneManager.created_at, User.full_name)
    )).all()
    visit_count = (await db.execute(
        select(func.count(Visit.id)).where(Visit.access_area == zone.code)
    )).scalar_one()
    return ZoneOut(
        id=zone.id, code=zone.code, name=zone.name, gmp_grade=zone.gmp_grade,
        is_active=zone.is_active, sort_order=zone.sort_order,
        managers=[
            ZoneManagerOut(id=m.id, name=m.full_name, email=m.email, is_active=m.is_active)
            for m in managers
        ],
        visit_count=visit_count,
    )


def _zone_snapshot(zone: Zone, manager_ids: list[str]) -> dict:
    return {
        "code": zone.code, "name": zone.name, "gmp_grade": zone.gmp_grade,
        "is_active": zone.is_active, "sort_order": zone.sort_order,
        "manager_ids": manager_ids,
    }


async def _current_manager_ids(db, zone_id: uuid.UUID) -> list[str]:
    return [str(u) for u in (await db.execute(
        select(ZoneManager.user_id).where(ZoneManager.zone_id == zone_id)
        .order_by(ZoneManager.created_at, ZoneManager.user_id)
    )).scalars().all()]


async def _set_managers(db, zone: Zone, manager_ids: list[uuid.UUID]) -> None:
    wanted = list(dict.fromkeys(manager_ids))  # de-dup, keep order
    if wanted:
        found = set((await db.execute(
            select(User.id).where(User.id.in_(wanted), User.is_active.is_(True))
        )).scalars().all())
        missing = [str(u) for u in wanted if u not in found]
        if missing:
            raise HTTPException(
                status_code=422,
                detail=f"Not active UniOps users: {', '.join(missing)}",
            )
    await db.execute(delete(ZoneManager).where(ZoneManager.zone_id == zone.id))
    for uid in wanted:
        db.add(ZoneManager(zone_id=zone.id, user_id=uid))
    await db.flush()


async def _unique_code(db, name: str) -> str:
    """The zone's permanent code, made from its name ("Cold Room 2" →
    "cold_room_2"). Never changes afterwards, even if the zone is renamed."""
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:32] or "zone"
    taken = set((await db.execute(
        select(Zone.code).where(Zone.code.like(f"{base}%"))
    )).scalars().all())
    code, n = base, 2
    while code in taken:
        code, n = f"{base}_{n}", n + 1
    return code


async def _name_taken(db, name: str, *, exclude: uuid.UUID | None = None) -> bool:
    stmt = select(Zone.id).where(func.lower(Zone.name) == name.lower())
    if exclude is not None:
        stmt = stmt.where(Zone.id != exclude)
    return (await db.execute(stmt)).first() is not None


async def _audit_zone(db, user, request, *, action: str, zone_id, old, new) -> None:
    meta = await load_request_meta(db, user, request)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type=action,
        entity_type="vms_zone",
        entity_id=zone_id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        old_value=old,
        new_value=new,
    )


@router.get("/zones", response_model=list[ZoneOut])
async def list_zones(db: SessionDep, _: AdminDep):
    """Every zone, retired ones included, in display order."""
    zones = (await db.execute(
        select(Zone).order_by(Zone.sort_order, Zone.name)
    )).scalars().all()
    return [await _zone_out(db, z) for z in zones]


@router.post("/zones", response_model=ZoneOut, status_code=201)
async def create_zone(payload: ZoneCreate, request: Request, db: SessionDep, user: AdminDep):
    if await _name_taken(db, payload.name):
        raise HTTPException(status_code=409, detail=f"A zone named '{payload.name}' already exists")
    zone = Zone(
        code=await _unique_code(db, payload.name), name=payload.name,
        gmp_grade=payload.gmp_grade, sort_order=payload.sort_order,
        updated_by=uuid.UUID(str(user["sub"])),
    )
    db.add(zone)
    await db.flush()
    await _set_managers(db, zone, payload.manager_ids)
    await _audit_zone(db, user, request, action="admin.zone.create", zone_id=zone.id,
                      old=None, new=_zone_snapshot(zone, await _current_manager_ids(db, zone.id)))
    return await _zone_out(db, zone)


@router.patch("/zones/{zone_id}", response_model=ZoneOut)
async def update_zone(
    zone_id: uuid.UUID, payload: ZoneUpdate, request: Request, db: SessionDep, user: AdminDep,
):
    """Edit a zone. Changing its managers affects visits submitted from now
    on; a visit already in approval keeps the managers it was routed to."""
    zone = (await db.execute(select(Zone).where(Zone.id == zone_id))).scalar_one_or_none()
    if zone is None:
        raise HTTPException(status_code=404, detail="Zone not found")
    before = _zone_snapshot(zone, await _current_manager_ids(db, zone.id))
    if payload.name is not None and payload.name != zone.name:
        if await _name_taken(db, payload.name, exclude=zone.id):
            raise HTTPException(status_code=409, detail=f"A zone named '{payload.name}' already exists")
        zone.name = payload.name
    if payload.gmp_grade is not None:
        zone.gmp_grade = payload.gmp_grade
    if payload.is_active is not None:
        zone.is_active = payload.is_active
    if payload.sort_order is not None:
        zone.sort_order = payload.sort_order
    zone.updated_by = uuid.UUID(str(user["sub"]))
    await db.flush()
    if payload.manager_ids is not None:
        await _set_managers(db, zone, payload.manager_ids)
    await _audit_zone(db, user, request, action="admin.zone.update", zone_id=zone.id,
                      old=before, new=_zone_snapshot(zone, await _current_manager_ids(db, zone.id)))
    return await _zone_out(db, zone)


@router.delete("/zones/{zone_id}", status_code=204)
async def delete_zone(zone_id: uuid.UUID, request: Request, db: SessionDep, user: AdminDep):
    """Only a zone no visit has ever used can be deleted; retire the others
    (is_active = false) so their visits keep their zone."""
    zone = (await db.execute(select(Zone).where(Zone.id == zone_id))).scalar_one_or_none()
    if zone is None:
        raise HTTPException(status_code=404, detail="Zone not found")
    used = (await db.execute(
        select(func.count(Visit.id)).where(Visit.access_area == zone.code)
    )).scalar_one()
    if used:
        raise HTTPException(
            status_code=409,
            detail=f"{used} visit(s) use this zone — retire it instead of deleting it",
        )
    before = _zone_snapshot(zone, await _current_manager_ids(db, zone.id))
    await db.delete(zone)
    await db.flush()
    await _audit_zone(db, user, request, action="admin.zone.delete", zone_id=zone_id,
                      old=before, new=None)
    return Response(status_code=204)


# ── Notification contacts (PRD §2.1.2.1 VMS-PR-020..023) ────────────────────-

@router.get("/notification-contacts", response_model=NotificationContactsUpdate)
async def get_notification_contacts(
    db: SessionDep,
    _: AdminDep,
):
    """Current Training + PPE contact emails. Empty fields mean no email goes
    out for that channel (visits proceed but no one upstream gets notified)."""
    cfg = await _load_config(db)
    nc = cfg.notification_contacts or {}
    return NotificationContactsUpdate(
        training_email=nc.get("training_email"),
        ppe_email=nc.get("ppe_email"),
    )


@router.put("/notification-contacts", response_model=NotificationContactsUpdate)
async def set_notification_contacts(
    payload: NotificationContactsUpdate,
    request: Request,
    db: SessionDep,
    user: AdminDep,
):
    """Replace both fields atomically. Either field can be cleared by sending
    null. Audit log records the diff."""
    cfg = await _load_config(db)
    before = dict(cfg.notification_contacts or {})
    new_contacts = {
        "training_email": str(payload.training_email) if payload.training_email else None,
        "ppe_email":      str(payload.ppe_email)      if payload.ppe_email      else None,
    }
    cfg.notification_contacts = new_contacts
    await db.flush()

    meta = await load_request_meta(db, user, request)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="admin.notification_contacts.update",
        entity_type="vms_config",
        entity_id=cfg.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        old_value={"notification_contacts": before},
        new_value={"notification_contacts": new_contacts},
    )
    return NotificationContactsUpdate(**new_contacts)


# ── Health questionnaire template (PRD §2.2.2 VMS-CI-010) ───────────────────-

@router.get("/health-questions", response_model=HealthQuestionsUpdate)
async def get_health_questions_admin(
    db: SessionDep,
    _: AdminDep,
):
    """Admin view of the active questionnaire. Falls back to the default
    template when `vms_config.health_questions` is empty (so the editor shows
    something on a fresh install rather than blanking out)."""
    cfg = await _load_config(db)
    template = cfg.health_questions or {}
    if not template.get("questions"):
        template = DEFAULT_HEALTH_QUESTIONS
    return HealthQuestionsUpdate.model_validate(template)


@router.put("/health-questions", response_model=HealthQuestionsUpdate)
async def set_health_questions(
    payload: HealthQuestionsUpdate,
    request: Request,
    db: SessionDep,
    user: AdminDep,
):
    """Replace the questionnaire template atomically.

    Question ids must be unique within the template — historical declarations
    snapshot the question text at submit time (PRD VMS-AU-013), so changing
    a question's `id` makes historical answers untraceable. The pre-save
    validation catches duplicate ids; renaming an `id` is allowed but flagged
    in the audit log so an auditor sees the break.
    """
    ids = [q.id for q in payload.questions]
    if len(ids) != len(set(ids)):
        raise HTTPException(
            status_code=422,
            detail="Duplicate question ids — each question id must be unique",
        )

    cfg = await _load_config(db)
    before = dict(cfg.health_questions or {})
    new_template = payload.model_dump()
    cfg.health_questions = new_template
    await db.flush()

    meta = await load_request_meta(db, user, request)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="admin.health_questions.update",
        entity_type="vms_config",
        entity_id=cfg.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        old_value={"health_questions": before},
        new_value={"health_questions": new_template},
        notes=f"version={new_template.get('version')}, questions={len(payload.questions)}",
    )
    return payload


# ── VMS SMTP settings (V2.5 — VMS-owned outbound mail) ──────────────────────-

# Mask in the response so the password doesn't leak back through GET. The
# raw value stays in DB; the admin only sees if a value is set.
_PWD_MASK = "********"


def _mask(cfg: dict) -> SmtpSettingsUpdate:
    return SmtpSettingsUpdate(
        host=cfg.get("host"),
        port=cfg.get("port"),
        user=cfg.get("user"),
        password=_PWD_MASK if cfg.get("password") else None,
        use_tls=cfg.get("use_tls"),
        from_email=cfg.get("from_email") or None,
    )


@router.get("/smtp-settings", response_model=SmtpSettingsUpdate)
async def get_smtp_settings(
    db: SessionDep,
    _: AdminDep,
):
    """Read VMS-local SMTP settings. Password returned masked."""
    cfg = await _load_config(db)
    return _mask(cfg.smtp_settings or {})


@router.put("/smtp-settings", response_model=SmtpSettingsUpdate)
async def set_smtp_settings(
    payload: SmtpSettingsUpdate,
    request: Request,
    db: SessionDep,
    user: AdminDep,
):
    """Replace VMS SMTP settings atomically.

    Password handling: sending the literal mask (`********`) means "keep the
    existing password" — that's how the masked GET round-trips safely.
    Sending an empty/null password clears the credential entirely.
    """
    cfg = await _load_config(db)
    before = dict(cfg.smtp_settings or {})

    new_password = payload.password
    if new_password == _PWD_MASK:
        new_password = before.get("password")

    new_settings = {
        "host":       payload.host or None,
        "port":       payload.port,
        "user":       payload.user or None,
        "password":   new_password or None,
        "use_tls":    payload.use_tls,
        "from_email": str(payload.from_email) if payload.from_email else None,
    }
    cfg.smtp_settings = new_settings
    await db.flush()

    meta = await load_request_meta(db, user, request)
    # Audit-log the change WITHOUT the raw password — even old/new are masked.
    def _redact(s: dict) -> dict:
        return {**s, "password": _PWD_MASK if s.get("password") else None}
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="admin.smtp_settings.update",
        entity_type="vms_config",
        entity_id=cfg.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        old_value={"smtp_settings": _redact(before)},
        new_value={"smtp_settings": _redact(new_settings)},
    )
    return _mask(new_settings)


@router.post("/smtp-test", response_model=SmtpTestResponse)
async def send_smtp_test(
    payload: SmtpTestRequest,
    request: Request,
    db: SessionDep,
    user: AdminDep,
):
    """Send a small canned message to `to_email` using the current settings.

    Same `_load_smtp_config` / `_send_email` path as production notifications
    so this exercises the exact code path the user is debugging. Returns
    `delivered=False` on any failure with the exception text — easier than
    making the admin trawl container logs.
    """
    cfg_dict = await notifications_svc._load_smtp_config(db)
    if not cfg_dict.get("host"):
        return SmtpTestResponse(
            delivered=False,
            detail="No SMTP host configured — save settings first.",
        )

    subject = "VMS SMTP test"
    body = (
        "This is a test email from the VMS Admin panel.\n"
        "If you can read this, the SMTP settings are correct.\n"
    )
    delivered = await notifications_svc._send_email(
        cfg=cfg_dict, to=str(payload.to_email), subject=subject, body=body,
    )

    meta = await load_request_meta(db, user, request)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="admin.smtp_test",
        entity_type="vms_config",
        entity_id=(await _load_config(db)).id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        new_value={"to_email": str(payload.to_email), "delivered": delivered},
    )

    return SmtpTestResponse(
        delivered=delivered,
        detail=(
            "Test email sent." if delivered
            else "Delivery failed — see vms-api logs for the SMTP error."
        ),
    )


# ── Structured badge config (replaces badge_templates UI) ───────────────────-

@router.get("/badge-config", response_model=BadgeConfig)
async def get_badge_config(
    db: SessionDep,
    _: AdminDep,
):
    """Current badge configuration, deep-merged over code defaults so the
    response is always a complete object even on a fresh install."""
    cfg = await _load_config(db)
    merged = deep_merge(DEFAULT_BADGE_CONFIG, cfg.badge_config or {})
    return BadgeConfig.model_validate(merged)


@router.put("/badge-config", response_model=BadgeConfig)
async def set_badge_config(
    payload: BadgeConfig,
    request: Request,
    db: SessionDep,
    user: AdminDep,
):
    """Replace the badge configuration atomically. Stored as a plain dict;
    the schema has already validated colors, the meta-field whitelist, and
    the alignment enum."""
    cfg = await _load_config(db)
    before = dict(cfg.badge_config or {})
    new_config = payload.model_dump()
    cfg.badge_config = new_config
    await db.flush()

    meta = await load_request_meta(db, user, request)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="admin.badge_config.update",
        entity_type="vms_config",
        entity_id=cfg.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        old_value={"badge_config": before},
        new_value={"badge_config": new_config},
    )
    return payload


# ── Manual scheduler trigger (ops) ──────────────────────────────────────────-

@router.post("/run-scheduled-jobs")
async def run_scheduled_jobs(
    request: Request,
    db: SessionDep,
    user: AdminDep,
):
    """Force one run of the time-based jobs (no-show / reminders / overdue
    escalation) instead of waiting for the next scheduler tick. Returns the
    same count summary the background loop logs. Idempotent — re-running is
    safe (one-shot flags + status transitions guard against double sends)."""
    from app.services import scheduler

    summary = await scheduler.run_locked(db)
    if summary is None:
        raise HTTPException(
            status_code=409,
            detail="A scheduled run is in progress right now — try again in a minute.",
        )

    meta = await load_request_meta(db, user, request)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="admin.run_scheduled_jobs",
        entity_type="vms_config",
        entity_id=(await _load_config(db)).id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        new_value=summary,
    )
    return summary


# ── Cross-system data-maintenance admin (browse/edit/cascade-delete) ────────-
# Registered after the static routes above so `/admin/{entity}` does not
# shadow `/admin/quality-managers`, `/admin/health-questions`, etc.

class ListResponse(BaseModel):
    items: list[dict]
    total: int


class BulkDeleteRequest(BaseModel):
    ids: list[uuid.UUID]


def _actor(user: dict) -> tuple[uuid.UUID, str]:
    return uuid.UUID(user["sub"]), user.get("email", "")


@router.get("/entities")
async def list_entities(user: AdminDep):
    # sortable_columns: the list columns ?sort= accepts (real columns only).
    return [spec.schema.to_dict() | {"system": spec.system,
                                     "sortable_columns": list(service.SORTABLE[key])}
            for key, spec in REGISTRY.items()]


@router.get("/{entity}", response_model=ListResponse)
async def list_data_records(entity: str, db: SessionDep, user: AdminDep,
                             page: int = Query(1, ge=1), page_size: int = Query(20, le=200),
                             search: str | None = Query(None),
                             sort: str | None = Query(None),
                             dir: Literal["asc", "desc"] = Query("asc")):
    try:
        items, total = await service.list_records(db, entity, page=page, page_size=page_size,
                                                        search=search, sort=sort, dir=dir)
    except service.UnknownSortKey as e:
        raise HTTPException(422, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))
    return ListResponse(items=items, total=total)


@router.get("/{entity}/{record_id}")
async def get_data_record(entity: str, record_id: uuid.UUID, db: SessionDep, user: AdminDep):
    try:
        rec = await service.get_record(db, entity, record_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    if rec is None:
        raise HTTPException(404, "Record not found")
    return rec


@router.patch("/{entity}/{record_id}")
async def edit_data_record(entity: str, record_id: uuid.UUID, db: SessionDep, user: AdminDep,
                            patch: dict = Body(...)):
    actor_id, email = _actor(user)
    try:
        result = await service.edit_record(db, entity, record_id, patch, actor_id=actor_id, actor_email=email)
        await db.commit()
        return result
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))


@router.delete("/{entity}/{record_id}")
async def delete_data_record(entity: str, record_id: uuid.UUID, db: SessionDep, user: AdminDep,
                              preview: int = Query(0)):
    actor_id, email = _actor(user)
    try:
        if preview:
            summary = await service.delete_preview(db, entity, record_id)
            return {"preview": True, "cascade": summary}
        summary = await service.delete_record(db, entity, record_id, actor_id=actor_id, actor_email=email)
        await db.commit()
        return {"preview": False, "cascade": summary}
    except service.DeleteNotAllowed as e:
        await db.rollback()
        raise HTTPException(409, str(e))
    except ValueError as e:
        await db.rollback()
        raise HTTPException(404, str(e))


@router.post("/{entity}/bulk-delete")
async def bulk_delete_data_records(entity: str, db: SessionDep, user: AdminDep, body: BulkDeleteRequest):
    actor_id, email = _actor(user)
    try:
        summary = await service.bulk_delete(db, entity, body.ids, actor_id=actor_id, actor_email=email)
        await db.commit()
        return {"deleted": len(body.ids), "cascade": summary}
    except service.DeleteNotAllowed as e:
        await db.rollback()
        raise HTTPException(409, str(e))
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))
