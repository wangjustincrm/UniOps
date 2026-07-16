"""User management endpoints (system_admin only)."""
import csv
import io
import uuid
from datetime import datetime, timezone
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, HTTPException, Query, UploadFile, File, status
from fastapi.responses import Response
from sqlalchemy import func, or_, select

from app.core.config import INITIAL_PASSWORD
from app.core.deps import CurrentUserPayload, SessionDep, require_roles
from app.core.security import hash_password
from app.crud import department as dept_crud
from app.crud import user as user_crud
from app.models.department import Department
from app.models.user import User
from app.schemas.user import (
    VALID_ROLES,
    VALID_NOTIFICATION_CHANNELS,
    UserAdminResponse,
    UserBriefListResponse,
    UserBriefResponse,
    UserCreate,
    UserListResponse,
    UserUpdate,
    ErpImportRequest,
    ErpImportResponse,
    ErpImportCreated,
    ErpImportError,
)
from app.services.mdm_client import MdmClient

router = APIRouter(prefix="/users", tags=["users"])

AdminDep = Annotated[dict, Depends(require_roles("system_admin"))]

_CSV_HEADERS = ["email", "full_name", "role", "department_code", "is_active", "teams_account"]
_CSV_IMPORT_HEADERS = _CSV_HEADERS + ["password"]  # password optional on import

# gm/opm/vendor_manager/finance_manager/procurement_manager are company-unique
# singleton POSTS (identity enforces one holder each — migration
# 0003_post_role_singleton + the cross-table 409 in PUT /authz/users/{id}/roles).
# finance_bp is deliberately excluded: it's a job FUNCTION many people hold,
# so multiple holders are expected and fine.
_POST_ROLES = frozenset({"gm", "opm", "vendor_manager", "finance_manager", "procurement_manager"})


async def _post_conflict(db, role: str, exclude_user_id: uuid.UUID | None = None) -> str | None:
    """Return the id (str) of another user already holding `role` as a
    singleton post — as PRIMARY role (users.role) OR ADDITIONAL role
    (identity's user_roles, same physical DB) — excluding exclude_user_id so
    re-saving the current holder is idempotent. Mirrors identity-api's
    authz.py::_post_conflict (same invariant, same physical DB): the
    partial unique index on user_roles (migration 0003_post_role_singleton)
    only guards ADDITIONAL-role assignment; a post set as a PRIMARY role via
    this admin endpoint is otherwise invisible to that invariant.

    Returns None (no conflict) for non-post roles like finance_bp, which may
    have any number of holders.
    """
    if role not in _POST_ROLES:
        return None
    params: dict = {"role": role}
    primary_sql = "SELECT id::text FROM users WHERE role = :role"
    additional_sql = "SELECT user_id::text FROM user_roles WHERE role_code = :role"
    if exclude_user_id is not None:
        params["uid"] = str(exclude_user_id)
        primary_sql += " AND id != :uid"
        additional_sql += " AND user_id != :uid"
    row = (await db.execute(sa.text(primary_sql), params)).first()
    if row is not None:
        return row[0]
    row = (await db.execute(sa.text(additional_sql), params)).first()
    return row[0] if row is not None else None


async def _with_dept_names(db: SessionDep, users: list[User]) -> list[UserAdminResponse]:
    """Attach department_name to each user in one extra query."""
    dept_ids = {u.department_id for u in users if u.department_id}
    dept_map: dict[uuid.UUID, str] = {}
    if dept_ids:
        result = await db.execute(
            select(Department.id, Department.name).where(Department.id.in_(dept_ids))
        )
        dept_map = {row.id: row.name for row in result}

    items = []
    for u in users:
        r = UserAdminResponse.model_validate(u)
        r.department_name = dept_map.get(u.department_id) if u.department_id else None
        items.append(r)
    return items


@router.get("", response_model=UserListResponse)
async def list_users(
    db: SessionDep,
    _: AdminDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    search: str | None = None,
    role: str | None = None,
    is_active: bool | None = None,
):
    """List users with optional search, role filter, and pagination."""
    q = select(User).order_by(User.full_name)
    if search:
        q = q.where(
            or_(User.full_name.ilike(f"%{search}%"), User.email.ilike(f"%{search}%"))
        )
    if role:
        q = q.where(User.role == role)
    if is_active is not None:
        q = q.where(User.is_active == is_active)

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    q = q.offset((page - 1) * page_size).limit(page_size)
    users = list((await db.execute(q)).scalars().all())
    items = await _with_dept_names(db, users)
    return UserListResponse(items=items, total=total)


@router.get("/export")
async def export_users(db: SessionDep, _: AdminDep):
    """Export all users as CSV."""
    result = await db.execute(select(User).order_by(User.full_name))
    users = list(result.scalars().all())

    dept_result = await db.execute(select(Department.id, Department.code))
    dept_code_map: dict[uuid.UUID, str] = {row.id: row.code for row in dept_result}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_CSV_IMPORT_HEADERS)
    for u in users:
        writer.writerow([
            u.email,
            u.full_name,
            u.role,
            dept_code_map.get(u.department_id, "") if u.department_id else "",
            "true" if u.is_active else "false",
            u.teams_account or "",
            "",  # password blank on export
        ])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="users.csv"'},
    )


# ── Public directory ─────────────────────────────────────────────────────────
# Open to ANY authenticated UniOps user. Returns only non-sensitive fields:
# id / full_name / email / department_id / department_name. Used by VMS Host
# search (PRD §6.2) and any other module that needs a "who is this person"
# lookup without the admin-only payload.
#
# IMPORTANT: these routes MUST be defined before `/{user_id}` to avoid being
# shadowed by the path-parameter route below.

@router.get("/directory", response_model=UserBriefListResponse)
async def list_users_directory(
    db: SessionDep,
    _: CurrentUserPayload,
    search: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List active users with optional name/email search.

    Limited to `is_active=True` (inactive users should not appear in pickers).
    """
    q = select(User).where(User.is_active.is_(True)).order_by(User.full_name)
    if search:
        q = q.where(
            or_(User.full_name.ilike(f"%{search}%"), User.email.ilike(f"%{search}%"))
        )

    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    q = q.offset((page - 1) * page_size).limit(page_size)
    users = list((await db.execute(q)).scalars().all())

    # Resolve department names in one extra query.
    dept_ids = {u.department_id for u in users if u.department_id}
    dept_name_map: dict[uuid.UUID, str] = {}
    if dept_ids:
        dept_result = await db.execute(
            select(Department.id, Department.name).where(Department.id.in_(dept_ids))
        )
        dept_name_map = {row.id: row.name for row in dept_result}

    items = []
    for u in users:
        r = UserBriefResponse.model_validate(u)
        r.department_name = dept_name_map.get(u.department_id) if u.department_id else None
        items.append(r)

    return UserBriefListResponse(items=items, total=total)


@router.get("/directory/{user_id}", response_model=UserBriefResponse)
async def get_user_directory(
    user_id: uuid.UUID,
    db: SessionDep,
    _: CurrentUserPayload,
):
    """Single brief lookup by user id. Returns 404 if user does not exist
    or is inactive (we hide inactive users to match the list endpoint)."""
    user = await user_crud.get_by_id(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    r = UserBriefResponse.model_validate(user)
    if user.department_id:
        dept_result = await db.execute(
            select(Department.name).where(Department.id == user.department_id)
        )
        r.department_name = dept_result.scalar_one_or_none()
    return r


@router.post("/import")
async def import_users(
    db: SessionDep,
    _: AdminDep,
    file: UploadFile = File(...),
):
    """Import users from CSV. Creates new users, updates existing by email."""
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")  # handle Excel BOM
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))

    dept_result = await db.execute(select(Department.id, Department.code))
    dept_code_map: dict[str, uuid.UUID] = {row.code: row.id for row in dept_result}

    created, updated = 0, 0
    errors: list[str] = []
    # Track singleton posts claimed by an earlier row IN THIS SAME FILE, keyed
    # by role -> the email that claimed it. The per-row DB check below only
    # sees a prior row once it has been flushed; this in-loop set is a second,
    # explicit guard so two rows in one CSV can never both claim e.g. `gm`
    # regardless of flush timing.
    claimed_posts: dict[str, str] = {}

    for i, row in enumerate(reader, start=2):
        try:
            email = (row.get("email") or "").strip().lower()
            if not email:
                errors.append(f"Row {i}: email is required")
                continue

            full_name = (row.get("full_name") or "").strip()
            role = (row.get("role") or "requester").strip()
            dept_code = (row.get("department_code") or "").strip()
            is_active_raw = (row.get("is_active") or "true").strip().lower()
            is_active = is_active_raw in ("true", "1", "yes")
            teams_account = (row.get("teams_account") or "").strip() or None
            password = (row.get("password") or "").strip() or None

            if role not in VALID_ROLES:
                errors.append(f"Row {i} ({email}): invalid role '{role}'")
                continue

            dept_id = dept_code_map.get(dept_code) if dept_code else None
            if dept_code and dept_id is None:
                errors.append(f"Row {i} ({email}): department code '{dept_code}' not found")
                continue

            existing = await user_crud.get_by_email(db, email)

            if role in _POST_ROLES:
                claimed_by = claimed_posts.get(role)
                if claimed_by is not None and claimed_by != email:
                    errors.append(
                        f"Row {i} ({email}): '{role}' is a singleton post already "
                        f"claimed by {claimed_by} earlier in this file"
                    )
                    continue
                conflict = await _post_conflict(
                    db, role, exclude_user_id=existing.id if existing else None
                )
                if conflict is not None:
                    errors.append(
                        f"Row {i} ({email}): '{role}' is a singleton post already "
                        f"held by user {conflict}"
                    )
                    continue
                claimed_posts[role] = email

            if existing:
                if full_name:
                    existing.full_name = full_name
                existing.role = role
                existing.is_active = is_active
                existing.department_id = dept_id
                existing.teams_account = teams_account
                if password:
                    existing.hashed_password = hash_password(password)
                    existing.must_change_password = True
                    existing.password_changed_at = datetime.now(timezone.utc)
                await db.flush()
                updated += 1
            else:
                if not full_name:
                    errors.append(f"Row {i} ({email}): full_name required for new users")
                    continue
                auto_pwd = password or INITIAL_PASSWORD
                body = UserCreate(
                    email=email,
                    full_name=full_name,
                    role=role,
                    department_id=dept_id,
                    is_active=is_active,
                    password=auto_pwd,
                    teams_account=teams_account,
                )
                await user_crud.create_admin(db, body)
                created += 1
        except Exception as exc:
            errors.append(f"Row {i}: {exc}")

    return {"created": created, "updated": updated, "errors": errors}


@router.post("", response_model=UserAdminResponse, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, db: SessionDep, _: AdminDep):
    if body.role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid role. Must be one of: {sorted(VALID_ROLES)}",
        )
    conflict = await _post_conflict(db, body.role)
    if conflict is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"'{body.role}' is a singleton post already held by user {conflict}",
        )
    erp_code = (body.erp_person_code or "").strip()
    if not erp_code:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ERP Code is required",
        )
    body.erp_person_code = erp_code
    if await user_crud.get_by_erp_person_code(db, erp_code):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"ERP Code already in use: {erp_code}")
    if await user_crud.get_by_email(db, body.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = await user_crud.create_admin(db, body)
    items = await _with_dept_names(db, [user])
    return items[0]


@router.get("/{user_id}", response_model=UserAdminResponse)
async def get_user(user_id: uuid.UUID, db: SessionDep, _: AdminDep):
    user = await user_crud.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    items = await _with_dept_names(db, [user])
    return items[0]


@router.patch("/{user_id}", response_model=UserAdminResponse)
async def update_user(user_id: uuid.UUID, body: UserUpdate, db: SessionDep, _: AdminDep):
    user = await user_crud.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if body.role is not None:
        if body.role not in VALID_ROLES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid role. Must be one of: {sorted(VALID_ROLES)}",
            )
        conflict = await _post_conflict(db, body.role, exclude_user_id=user.id)
        if conflict is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"'{body.role}' is a singleton post already held by user {conflict}",
            )
        user.role = body.role

    if body.department_id is not None:
        if await dept_crud.get_by_id(db, body.department_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Department not found")
        user.department_id = body.department_id

    # supervisor_id: omitting leaves unchanged; sending null explicitly clears it.
    # Uses model_fields_set to distinguish "omitted" from "sent as null".
    if "supervisor_id" in body.model_fields_set:
        user.supervisor_id = body.supervisor_id

    # ERP Code is freely editable for any user; only uniqueness is enforced.
    if body.erp_person_code is not None:
        new_code = body.erp_person_code.strip()
        if new_code and new_code != user.erp_person_code:
            existing = await user_crud.get_by_erp_person_code(db, new_code)
            if existing and existing.id != user.id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"ERP Code already in use: {new_code}",
                )
        user.erp_person_code = new_code or None

    if body.full_name is not None:
        user.full_name = body.full_name
    if body.email is not None:
        user.email = body.email.lower()
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password is not None:
        user.hashed_password = hash_password(body.password)
        user.must_change_password = True
        user.password_changed_at = datetime.now(timezone.utc)
    if body.teams_account is not None:
        user.teams_account = body.teams_account or None
    if body.notification_channel is not None:
        if body.notification_channel not in VALID_NOTIFICATION_CHANNELS:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid notification_channel. Must be one of: {sorted(VALID_NOTIFICATION_CHANNELS)}",
            )
        user.notification_channel = body.notification_channel

    await db.flush()
    await db.refresh(user)
    items = await _with_dept_names(db, [user])
    return items[0]


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: uuid.UUID, db: SessionDep, _: AdminDep):
    user = await user_crud.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.is_active = False
    await db.flush()


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return authorization.split(None, 1)[1]


@router.post("/import-from-erp", response_model=ErpImportResponse)
async def import_users_from_erp(
    body: ErpImportRequest,
    db: SessionDep,
    _: AdminDep,
    authorization: str | None = Header(default=None),
):
    bearer = _extract_bearer(authorization)
    created: list[ErpImportCreated] = []
    errors: list[ErpImportError] = []
    # Same in-loop guard as the CSV importer: this endpoint only ever creates
    # new users (an existing email is always an error below), so there is no
    # "current holder" to exclude — but two items in the SAME request can
    # still both claim a singleton post before either is committed.
    claimed_posts: dict[str, str] = {}

    async with MdmClient(bearer_token=bearer) as mdm:
        for item in body.items:
            try:
                person = await mdm.get_person(item.erp_person_code)
            except Exception as e:
                errors.append(ErpImportError(erp_person_code=item.erp_person_code, reason=f"mdm error: {e}"))
                continue
            if not person:
                errors.append(ErpImportError(erp_person_code=item.erp_person_code, reason="ERP person not found in mdm-api mirror"))
                continue

            role = item.role or "requester"
            if role not in VALID_ROLES:
                errors.append(ErpImportError(erp_person_code=item.erp_person_code, reason=f"invalid role: {role}"))
                continue

            dept_id = item.department_id
            if dept_id is None and person.get("department_code"):
                dept = await dept_crud.get_by_code(db, person["department_code"])
                if dept:
                    dept_id = dept.id

            if await user_crud.get_by_email(db, item.email):
                errors.append(ErpImportError(erp_person_code=item.erp_person_code, reason=f"email already exists: {item.email}"))
                continue
            existing_erp = await user_crud.get_by_erp_person_code(db, item.erp_person_code)
            if existing_erp:
                errors.append(ErpImportError(erp_person_code=item.erp_person_code, reason="already imported"))
                continue

            if role in _POST_ROLES:
                claimed_by = claimed_posts.get(role)
                if claimed_by is not None and claimed_by != item.erp_person_code:
                    errors.append(ErpImportError(
                        erp_person_code=item.erp_person_code,
                        reason=f"'{role}' is a singleton post already claimed by "
                               f"{claimed_by} earlier in this import",
                    ))
                    continue
                conflict = await _post_conflict(db, role)
                if conflict is not None:
                    errors.append(ErpImportError(
                        erp_person_code=item.erp_person_code,
                        reason=f"'{role}' is a singleton post already held by user {conflict}",
                    ))
                    continue
                claimed_posts[role] = item.erp_person_code

            temp_pw = INITIAL_PASSWORD
            user = User(
                email=item.email.strip().lower(),
                hashed_password=hash_password(temp_pw),
                full_name=person.get("person_name") or item.email,
                role=role,
                department_id=dept_id,
                is_active=item.is_active,
                must_change_password=True,
                erp_person_code=item.erp_person_code,
                erp_imported=True,
                password_changed_at=datetime.now(timezone.utc),
            )
            db.add(user)
            try:
                await db.flush()
            except Exception as e:
                await db.rollback()
                errors.append(ErpImportError(erp_person_code=item.erp_person_code, reason=f"db error: {e}"))
                continue
            created.append(ErpImportCreated(
                email=user.email, full_name=user.full_name, temp_password=temp_pw,
            ))

        await db.commit()

    return ErpImportResponse(created=created, errors=errors)
