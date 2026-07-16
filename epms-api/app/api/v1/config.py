"""Company / Workflow Config endpoints."""
import logging
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from uniops_authz import effective_permissions, role_matrix, user_role_codes

from app.core.config import settings
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep, require_permission
from app.crud import config as config_crud
from app.services import approval_client
from app.schemas.config import ConfigResponse, ConfigUpdate
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


async def _forward_identity(
    method: str, path: str, token: str | None, json: dict | None = None
) -> tuple[int, dict]:
    """Pass a caller request through to identity using the caller's own Bearer
    token. This is Phase 2's replacement for the deleted app.core.authz_client
    module's `forward()` — kept local to config.py since these are the only
    callers. Still used for the authz endpoints that remain identity's domain
    (write validation + audit columns, or catalog listings): PATCH
    /role-permissions, GET /locked-permissions, /roles, /authz-defs,
    /user-roles, PUT /users/{id}/roles. The matrix READS
    (GET /role-permissions, GET /me/permissions) migrated to a direct DB read
    below — see uniops_authz.role_matrix — because identity and epms share
    one physical database.

    Raises on connection failure (e.g. RuntimeError / httpx.ConnectError);
    callers should catch and return 502.
    """
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.request(
            method,
            f"{settings.IDENTITY_API_URL}/identity/v1{path}",
            headers=headers,
            json=json,
        )
        body = r.json() if r.content else {}
        return r.status_code, body


async def _full_response(db, user_payload: dict) -> ConfigResponse:
    cfg = await config_crud.get_or_create(db)
    return ConfigResponse.model_validate(cfg)


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
        status_code, body = await _forward_identity("GET", "/authz/defs", token)
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
async def get_role_permissions(db: SessionDep, _: CurrentUserPayload) -> dict:
    """Return the effective permission matrix, read directly from identity's
    authz tables (same physical DB — no HTTP, no token, no dependency on the
    identity *service* being up).

    Shape unchanged: {role: {key: bool}} — the booking frontend still reads
    this exact endpoint/shape. Delegates to the shared uniops_authz package
    (role_matrix) so this query lives in exactly one place.
    """
    return await role_matrix(db)


@router.patch("/role-permissions")
async def update_role_permissions(
    body: dict, db: SessionDep, user: AdminDep, token: BearerToken
):
    """Proxy PATCH to identity authz hub (system_admin belt-and-braces guard kept).

    Wraps the legacy epms body {role:{key:bool}} as {"changes": body} before
    forwarding.  Passes through status + detail from identity (409 locked, 422
    unknown).  On connection failure returns 502.

    Writes still go through identity — it owns lock-cell validation and the
    audit columns (updated_by/updated_at). Only the READ side of this module
    became a direct DB read this phase.

    Phase 1's write-through mirror into company_config.role_permissions is
    retired: nothing reads that JSONB anymore (this endpoint and
    access_scope/deps now read identity's tables directly), so there is
    nothing left to keep warm. The column itself is left alone — untouched,
    just unread.
    """
    try:
        status_code, resp_body = await _forward_identity(
            "PATCH", "/authz/matrix", token, json={"changes": body}
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Identity unreachable: {exc}")
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
        status_code, body = await _forward_identity("GET", "/authz/defs", token)
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
async def get_my_permissions(user: CurrentUserPayload, db: SessionDep):
    """Effective permissions across all of the caller's roles (base + any
    additional roles from identity's user_roles), read directly — same
    physical DB as identity, no HTTP, so this never depends on the identity
    *service* being up (only a DB outage can stop it, and that stops
    everything anyway).
    """
    uid = uuid.UUID(user["sub"])
    role = user.get("role", "")
    perms = await effective_permissions(db, uid, role)
    codes = await user_role_codes(db, uid, role)
    additional = sorted(c for c in codes if c != role)
    roles = ([role] if role else []) + additional
    return {"permissions": perms, "roles": roles}


@router.get("/authz-defs")
async def get_authz_defs(_: CurrentUserPayload, token: BearerToken):
    """Proxy GET /authz/defs from identity (full defs: roles + permissions)."""
    try:
        status_code, body = await _forward_identity("GET", "/authz/defs", token)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Identity unreachable: {exc}")
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=body.get("detail"))
    return body


@router.get("/user-roles")
async def get_user_roles(_: CurrentUserPayload, token: BearerToken):
    """Proxy GET /authz/user-roles from identity (every user's additional roles)."""
    try:
        status_code, body = await _forward_identity("GET", "/authz/user-roles", token)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Identity unreachable: {exc}")
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=body.get("detail"))
    return body


@router.put("/users/{user_id}/roles", status_code=204)
async def put_user_roles(user_id: uuid.UUID, body: dict, _: AdminDep, token: BearerToken):
    """Proxy PUT /authz/users/{id}/roles to identity (system_admin only)."""
    try:
        status_code, resp_body = await _forward_identity(
            "PUT", f"/authz/users/{user_id}/roles", token, json=body
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Identity unreachable: {exc}")
    if status_code not in (200, 204):
        raise HTTPException(status_code=status_code, detail=resp_body.get("detail"))
    return Response(status_code=204)


# ── Approval routing passthrough (Phase 3) ──────────────────────────────────
#
# approval-api is server-to-server only (Caddyfile: "Browser-facing APIs
# (approval/identity are server-to-server, no subdomain)") — there is no
# public hostname/CORS wiring for the browser to reach it directly. epms-api
# is the gateway, exactly as Phase 1 did for identity via authz_client. No
# authz logic is duplicated here: approval-api's own /routing handlers gate
# PUT to system_admin and own all 422 validation. This is a write-capable
# admin surface (not a read cache), so a connection failure must surface as
# a 502 — no fallback to stale data.

@router.get("/approval-routing")
async def get_approval_routing(_: CurrentUserPayload, token: BearerToken):
    """Proxy GET /approval/v1/routing from the Approval Engine."""
    try:
        status_code, body = await approval_client.forward("GET", "/routing", token)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Approval Engine unreachable: {exc}")
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=body.get("detail"))
    return body


@router.put("/approval-routing")
async def put_approval_routing(body: dict, _: CurrentUserPayload, token: BearerToken):
    """Proxy PUT /approval/v1/routing to the Approval Engine.

    approval-api enforces system_admin on this write itself (403) and owns
    all validation (422 on unknown/inactive dept_id, bad gm_or_opm, unknown
    backup role) — both pass through untouched.
    """
    try:
        status_code, resp_body = await approval_client.forward("PUT", "/routing", token, json=body)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Approval Engine unreachable: {exc}")
    if status_code != 200:
        raise HTTPException(status_code=status_code, detail=resp_body.get("detail"))
    return resp_body
