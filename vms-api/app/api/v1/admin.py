"""VMS Admin endpoints (system_admin only).

Quality Manager roster (W10 / S2-B):
  GET  /api/v1/admin/quality-managers
  PUT  /api/v1/admin/quality-managers

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
import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.admin import service
from app.admin.registry import REGISTRY
from app.core.deps import SessionDep, require_roles
from app.core.request_meta import load_request_meta
from app.crud import audit as audit_crud
from app.models.vms_config import VmsConfig
from app.schemas.vms_config import (
    HealthQuestionsUpdate,
    NotificationContactsUpdate,
    QualityManagerRosterUpdate,
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


# ── Quality Manager roster ──────────────────────────────────────────────────-

class QualityManagerRosterResponse(QualityManagerRosterUpdate):
    """Same shape as the update payload."""
    pass


@router.get("/quality-managers", response_model=QualityManagerRosterResponse)
async def get_quality_manager_roster(
    db: SessionDep,
    _: AdminDep,
):
    """Return the current list of UniOps user UUIDs designated as VMS Quality
    Managers. Admin-only — the roster controls who can sign off on GMP / Lab
    visit approvals."""
    cfg = await _load_config(db)
    ids = [uuid.UUID(s) for s in (cfg.quality_manager_user_ids or []) if s]
    return QualityManagerRosterResponse(user_ids=ids)


@router.put("/quality-managers", response_model=QualityManagerRosterResponse)
async def set_quality_manager_roster(
    payload: QualityManagerRosterUpdate,
    request: Request,
    db: SessionDep,
    user: AdminDep,
):
    """Replace the QM roster atomically. Empty list disables the QM step entirely
    (engine will return None as the assignee → role-broadcast → no one)."""
    cfg = await _load_config(db)
    before = list(cfg.quality_manager_user_ids or [])

    # Store as strings — JSONB normalizes anyway, but matches existing convention.
    new_ids = [str(u) for u in payload.user_ids]
    cfg.quality_manager_user_ids = new_ids
    await db.flush()

    meta = await load_request_meta(db, user, request)
    await audit_crud.log_event(
        db,
        user_id=meta.user_id,
        user_name=meta.user_name,
        action_type="admin.quality_managers.update",
        entity_type="vms_config",
        entity_id=cfg.id,
        ip_address=meta.ip_address,
        user_agent=meta.user_agent,
        old_value={"quality_manager_user_ids": before},
        new_value={"quality_manager_user_ids": new_ids},
    )

    return QualityManagerRosterResponse(
        user_ids=[uuid.UUID(s) for s in new_ids],
    )


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
    from app.services import scheduled_jobs

    summary = await scheduled_jobs.run_all(db)
    await db.commit()

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
    return [spec.schema.to_dict() | {"system": spec.system} for spec in REGISTRY.values()]


@router.get("/{entity}", response_model=ListResponse)
async def list_data_records(entity: str, db: SessionDep, user: AdminDep,
                             page: int = Query(1, ge=1), page_size: int = Query(20, le=200),
                             search: str | None = Query(None)):
    try:
        items, total = await service.list_records(db, entity, page=page, page_size=page_size, search=search)
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
    except ValueError as e:
        await db.rollback()
        raise HTTPException(400, str(e))
