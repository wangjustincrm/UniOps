"""Company / Workflow Config endpoints."""
import logging
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from app.core import authz_client
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep, require_permission, require_roles
from app.crud import config as config_crud
from app.schemas.config import (
    ConfigResponse,
    ConfigUpdate,
    TempAssignmentCreate,
    TempAssignmentResponse,
    RolePermissionsUpdate,
)
from app.crud.config import BUILT_IN_ROLES, LOCKED_PERMISSIONS, PERMISSION_KEYS
from app.services.email import send_email

logger = logging.getLogger(__name__)


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
async def get_locked_permissions(_: CurrentUserPayload, token: BearerToken) -> dict:
    """Return the locked permissions map.

    Proxies GET /authz/defs from identity and transforms permissions[].locked_for
    into the legacy {role: [keys]} shape.  Falls back to the local LOCKED_PERMISSIONS
    constant only when identity is unreachable (httpx.RequestError / connection failure).
    4xx/5xx from identity and transform errors are not swallowed.
    """
    try:
        status_code, body = await authz_client.forward("GET", "/authz/defs", token)
    except httpx.RequestError:
        return {role: list(perms) for role, perms in LOCKED_PERMISSIONS.items()}
    if status_code != 200:
        return {role: list(perms) for role, perms in LOCKED_PERMISSIONS.items()}
    # Transform outside the try so KeyError/TypeError from a malformed response is not swallowed
    result: dict[str, list[str]] = {}
    for perm in body.get("permissions", []):
        for role in perm.get("locked_for", []):
            result.setdefault(role, []).append(perm["key"])
    return result


@router.get("/permission-keys")
async def get_permission_keys(_: CurrentUserPayload) -> list[str]:
    """Return the ordered list of permission column keys (local constant, always available)."""
    return PERMISSION_KEYS


@router.get("/role-permissions")
async def get_role_permissions(db: SessionDep, _: CurrentUserPayload, token: BearerToken) -> dict:
    """Return the effective permission matrix from identity (60 s cached); fallback = frozen JSONB.

    Passes through 4xx/5xx from identity as-is (e.g. 401 invalid token → 401 here).
    Only falls back to frozen JSONB on network/timeout failures.
    """
    try:
        return await authz_client.get_matrix(db, token)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)


@router.patch("/role-permissions")
async def update_role_permissions(
    body: dict, db: SessionDep, user: AdminDep, token: BearerToken
):
    """Proxy PATCH to identity authz hub (system_admin belt-and-braces guard kept).

    Wraps the legacy epms body {role:{key:bool}} as {"changes": body} before
    forwarding.  Passes through status + detail from identity (409 locked, 422
    unknown).  On connection failure returns 502.

    Write-through: once identity accepts the change (200), the same changes are
    also persisted into the local company_config.role_permissions JSONB via
    config_crud.update_role_permissions.  epms is the single write choke point
    (all writes go through this endpoint), so there is no race with another
    writer.  Identity goes first because it is the source of truth and does
    the real validation (locked cells, unknown role/permission keys); the local
    mirror write happens after and is best-effort — it exists to keep the
    token=None access_scope path (and the outage-fallback path) from serving a
    matrix that a revocation never reached.  A mirror-write failure must NOT
    fail the request (the mirror is a cache, not the truth) but is logged at
    ERROR level so drift is visible.
    """
    try:
        status_code, resp_body = await authz_client.forward(
            "PATCH", "/authz/matrix", token, json={"changes": body}
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Identity unreachable: {exc}")
    if status_code == 200:
        try:
            cfg = await config_crud.get_or_create(db)
            await config_crud.update_role_permissions(
                db, cfg, RolePermissionsUpdate(permissions=body), uuid.UUID(user["sub"])
            )
            await db.commit()
        except Exception:
            # Roll back so the session isn't left with an aborted transaction —
            # get_session() does one more commit() after this endpoint returns,
            # and a broken session there would turn this into a 500 even though
            # we intend to still return identity's 200.
            await db.rollback()
            logger.error(
                "role-permissions PATCH: local mirror write failed after identity "
                "accepted the change — mirror is now stale until the next successful write",
                exc_info=True,
            )
        authz_client.invalidate_cache()
    if status_code not in (200,):
        raise HTTPException(status_code=status_code, detail=resp_body.get("detail"))
    return resp_body


# ── Roles endpoints ──────────────────────────────────────────────────────────

@router.get("/roles", response_model=list[dict])
async def list_roles(db: SessionDep, _: CurrentUserPayload, token: BearerToken):
    """List all roles from identity defs; falls back to local list_all_roles.

    Falls back only on connection failure (httpx.RequestError) or a non-200 from
    identity.  Transform errors (KeyError on a malformed response) are not swallowed.
    """
    try:
        status_code, body = await authz_client.forward("GET", "/authz/defs", token)
    except httpx.RequestError:
        cfg = await config_crud.get_or_create(db)
        return config_crud.list_all_roles(cfg)
    if status_code != 200:
        cfg = await config_crud.get_or_create(db)
        return config_crud.list_all_roles(cfg)
    # Transform outside the try — BUILT_IN_ROLES imported at module level (finding 4)
    # Map identity role shape {code, label, sort, is_active} → legacy {code, name, description, is_active, is_builtin}
    return [
        {
            "code": r["code"],
            "name": r.get("label", r["code"]),
            "description": "",
            "is_active": r.get("is_active", True),
            "is_builtin": r["code"] in BUILT_IN_ROLES,
        }
        for r in body.get("roles", [])
    ]


# ── New passthrough endpoints ────────────────────────────────────────────────

@router.get("/me/permissions")
async def get_my_permissions(user: CurrentUserPayload, db: SessionDep, token: BearerToken):
    """Proxy GET /me/permissions from identity.

    Outage fallback (Spec §4): a connection failure (httpx.RequestError) falls
    back to the local mirror + the caller's own JWT, synthesizing
    {"permissions": mirror[role], "roles": [role]} — primary role only, same as
    pre-branch client behaviour (dropping additional-role visibility under-
    grants, never over-grants). 4xx/5xx from identity are real errors and must
    still propagate — only a transport-level failure falls back.
    """
    try:
        status_code, body = await authz_client.forward("GET", "/me/permissions", token)
    except httpx.RequestError:
        mirror = await authz_client.get_matrix(db, None)
        role = user.get("role", "")
        return {"permissions": mirror.get(role, {}), "roles": [role]}
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=body.get("detail"))
    return body


@router.get("/authz-defs")
async def get_authz_defs(_: CurrentUserPayload, token: BearerToken):
    """Proxy GET /authz/defs from identity (full defs: roles + permissions)."""
    try:
        status_code, body = await authz_client.forward("GET", "/authz/defs", token)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Identity unreachable: {exc}")
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=body.get("detail"))
    return body


@router.get("/user-roles")
async def get_user_roles(_: CurrentUserPayload, token: BearerToken):
    """Proxy GET /authz/user-roles from identity (every user's additional roles)."""
    try:
        status_code, body = await authz_client.forward("GET", "/authz/user-roles", token)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Identity unreachable: {exc}")
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=body.get("detail"))
    return body


@router.put("/users/{user_id}/roles", status_code=204)
async def put_user_roles(user_id: uuid.UUID, body: dict, _: AdminDep, token: BearerToken):
    """Proxy PUT /authz/users/{id}/roles to identity (system_admin only)."""
    try:
        status_code, resp_body = await authz_client.forward(
            "PUT", f"/authz/users/{user_id}/roles", token, json=body
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Identity unreachable: {exc}")
    if status_code not in (200, 204):
        raise HTTPException(status_code=status_code, detail=resp_body.get("detail"))
    return Response(status_code=204)
