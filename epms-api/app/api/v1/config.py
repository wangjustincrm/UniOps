"""Company / Workflow Config endpoints."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.deps import CurrentUserPayload, SessionDep, require_permission, require_roles
from app.crud import config as config_crud
from app.schemas.config import (
    ConfigResponse,
    ConfigUpdate,
    TempAssignmentCreate,
    TempAssignmentResponse,
    CustomRoleCreate,
    CustomRoleUpdate,
    CustomRoleResponse,
    RolePermissionsUpdate,
)
from app.crud.config import LOCKED_PERMISSIONS, PERMISSION_KEYS
from app.services.email import send_email


class TestSmtpRequest(BaseModel):
    to: str
    # "task" → internal task-notification profile (smtp_*).
    # "po"   → PO-to-vendor profile (po_smtp_*) with per-field fallback to smtp_*.
    # Defaults to "task" so existing callers keep working.
    kind: str = "task"


class PublicBrandingResponse(BaseModel):
    name: str
    tagline: str
    logo_data_url: str | None
    module: str | None = None

router = APIRouter(prefix="/config", tags=["config"])

AdminDep = Annotated[dict, Depends(require_permission("admin_panel"))]


async def _full_response(db, user_payload: dict) -> ConfigResponse:
    """Build a ConfigResponse with embedded temp_assignments."""
    cfg = await config_crud.get_or_create(db)
    temp_assignments = await config_crud.list_temp_assignments(db)
    response = ConfigResponse.model_validate(cfg)
    response.temp_assignments = [
        TempAssignmentResponse.model_validate(ta) for ta in temp_assignments
    ]
    return response


# ── Config endpoints ─────────────────────────────────────────────────────────

@router.get("/public/branding", response_model=PublicBrandingResponse)
async def get_public_branding(db: SessionDep, module: str | None = None):
    """Public endpoint — company name, logo, and the tagline for `module`.

    `tagline` column is the Portal tagline; other modules live in
    `module_taglines`. A missing/blank module entry falls back to the Portal
    tagline so a module never renders an empty subtitle. No `module` param =
    Portal tagline (backward compatible).
    """
    cfg = await config_crud.get_or_create(db)
    if module and module != "portal":
        tagline = (cfg.module_taglines or {}).get(module) or cfg.tagline
    else:
        tagline = cfg.tagline
    return PublicBrandingResponse(
        name=cfg.name,
        tagline=tagline,
        logo_data_url=cfg.logo_data_url,
        module=module,
    )


@router.get("", response_model=ConfigResponse)
async def get_config(db: SessionDep, user: CurrentUserPayload):
    """Return the company config singleton (all authenticated users can read)."""
    cfg = await config_crud.get_or_create(db)
    await db.commit()   # persist if just created
    return await _full_response(db, user)


@router.post("/test-smtp", status_code=200)
async def test_smtp(body: TestSmtpRequest, db: SessionDep, _: AdminDep):
    """Send a test email using one of the SMTP profiles.

    `kind="task"` (default) — internal task notifications (smtp_*).
    `kind="po"` — PO-to-vendor (po_smtp_*) with per-field fallback to smtp_*.
    """
    cfg = await config_crud.get_or_create(db)
    if body.kind == "po":
        smtp_kwargs = {
            "smtp_host":     cfg.po_smtp_host     if cfg.po_smtp_host     is not None else cfg.smtp_host,
            "smtp_port":     cfg.po_smtp_port     if cfg.po_smtp_port     is not None else cfg.smtp_port,
            "smtp_user":     cfg.po_smtp_user     if cfg.po_smtp_user     is not None else cfg.smtp_user,
            "smtp_password": cfg.po_smtp_password if cfg.po_smtp_password is not None else cfg.smtp_password,
            "smtp_use_tls":  cfg.po_smtp_use_tls  if cfg.po_smtp_use_tls  is not None else cfg.smtp_use_tls,
            "smtp_from":     cfg.po_smtp_from     if cfg.po_smtp_from     is not None else cfg.smtp_from,
        }
        label = "PO-to-Vendor"
    else:
        smtp_kwargs = {
            "smtp_host": cfg.smtp_host,
            "smtp_port": cfg.smtp_port,
            "smtp_user": cfg.smtp_user,
            "smtp_password": cfg.smtp_password,
            "smtp_use_tls": cfg.smtp_use_tls,
            "smtp_from": cfg.smtp_from,
        }
        label = "Task Notification"
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto">
      <h2 style="color:#085E5E">EPMS — SMTP Test ({label})</h2>
      <p>This is a test email to verify your SMTP configuration is working correctly.</p>
      <p style="color:#999;font-size:12px">Sent from EPMS Admin Panel.</p>
    </div>
    """
    try:
        await send_email(body.to, f"EPMS — SMTP Test ({label})", html, **smtp_kwargs)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"message": f"Test email sent to {body.to}"}


@router.patch("", response_model=ConfigResponse)
async def update_config(body: ConfigUpdate, db: SessionDep, user: AdminDep):
    """Update company config (system_admin only)."""
    cfg = await config_crud.get_or_create(db)
    await config_crud.update(db, cfg, body, uuid.UUID(user["sub"]))
    await db.commit()
    return await _full_response(db, user)


# ── Temp Assignment endpoints ────────────────────────────────────────────────

@router.get("/temp-assignments", response_model=list[TempAssignmentResponse])
async def list_temp_assignments(db: SessionDep, _: CurrentUserPayload):
    return await config_crud.list_temp_assignments(db)


@router.post("/temp-assignments", response_model=TempAssignmentResponse, status_code=201)
async def create_temp_assignment(
    body: TempAssignmentCreate, db: SessionDep, user: AdminDep
):
    """Create a temporary role assignment (system_admin only)."""
    if body.end_date < body.start_date:
        raise HTTPException(status_code=422, detail="end_date must be >= start_date")
    ta = await config_crud.create_temp_assignment(db, body, uuid.UUID(user["sub"]))
    await db.commit()
    await db.refresh(ta)
    return TempAssignmentResponse.model_validate(ta)


@router.delete("/temp-assignments/{assignment_id}", status_code=204)
async def delete_temp_assignment(
    assignment_id: uuid.UUID, db: SessionDep, _: AdminDep
):
    """Delete a temporary assignment (system_admin only)."""
    ta = await config_crud.get_temp_assignment(db, assignment_id)
    if ta is None:
        raise HTTPException(status_code=404, detail="Temp assignment not found")
    await config_crud.delete_temp_assignment(db, ta)
    await db.commit()


# ── Role Permissions endpoints ───────────────────────────────────────────────

@router.get("/locked-permissions")
async def get_locked_permissions(_: CurrentUserPayload) -> dict:
    """Return the locked permissions map (all authenticated users can read)."""
    return {role: list(perms) for role, perms in LOCKED_PERMISSIONS.items()}


@router.get("/permission-keys")
async def get_permission_keys(_: CurrentUserPayload) -> list[str]:
    """Return the ordered list of permission column keys."""
    return PERMISSION_KEYS


@router.get("/role-permissions")
async def get_role_permissions(db: SessionDep, _: CurrentUserPayload) -> dict:
    """Return the effective permission matrix (merged with defaults)."""
    cfg = await config_crud.get_or_create(db)
    return config_crud.get_effective_role_permissions(cfg)


@router.patch("/role-permissions", response_model=dict)
async def update_role_permissions(
    body: RolePermissionsUpdate, db: SessionDep, user: AdminDep
):
    """Update permission matrix cells (system_admin only)."""
    cfg = await config_crud.get_or_create(db)
    await config_crud.update_role_permissions(db, cfg, body, uuid.UUID(user["sub"]))
    await db.commit()
    await db.refresh(cfg)
    return config_crud.get_effective_role_permissions(cfg)


# ── Custom Role endpoints ────────────────────────────────────────────────────

@router.get("/roles", response_model=list[dict])
async def list_roles(db: SessionDep, _: CurrentUserPayload):
    """List all roles (built-in + custom)."""
    cfg = await config_crud.get_or_create(db)
    return config_crud.list_all_roles(cfg)


@router.post("/roles", response_model=dict, status_code=201)
async def create_role(body: CustomRoleCreate, db: SessionDep, _: AdminDep):
    """Create a new custom role (system_admin only)."""
    cfg = await config_crud.get_or_create(db)
    role = await config_crud.create_custom_role(db, cfg, body)
    await db.commit()
    return role


@router.patch("/roles/{role_code}", response_model=dict)
async def update_role(role_code: str, body: CustomRoleUpdate, db: SessionDep, _: AdminDep):
    """Update a custom role (system_admin only)."""
    cfg = await config_crud.get_or_create(db)
    role = await config_crud.update_custom_role(db, cfg, role_code, body)
    await db.commit()
    return role


@router.delete("/roles/{role_code}", status_code=204)
async def delete_role(role_code: str, db: SessionDep, _: AdminDep):
    """Delete a custom role (system_admin only)."""
    cfg = await config_crud.get_or_create(db)
    await config_crud.delete_custom_role(db, cfg, role_code)
    await db.commit()
