"""Purchase Request endpoints."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.core.access_scope import build_scope
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep, require_roles
from app.crud import pr as pr_crud
from app.models.task import Task
from app.schemas.pr import ApprovalEventResponse, PrActionRequest, PrCreate, PrListResponse, PrResponse, PrUpdate
from app.services.approval_client import delegate_action
from app.services.notification import fire_and_forget_notify

router = APIRouter(prefix="/pr", tags=["purchase-requests"])

# Any authenticated user can create PRs; no write-role restriction.
# Action permissions are checked per-action in the handler.


@router.get("", response_model=PrListResponse)
async def list_prs(
    db: SessionDep,
    user: CurrentUserPayload,
    status: str | None = Query(default=None),
    pr_type: int | None = Query(default=None),
    cost_center_id: uuid.UUID | None = Query(default=None),
    mine: bool = False,
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    # §1.3 PL-001–006: server-side scope enforcement — cannot be bypassed by client params.
    # build_scope encodes per-role visibility AND multi-role expansion via Role Management
    # (a dept_manager who is also the configured procurement_manager gets unrestricted scope).
    scope = await build_scope(db, user)
    # Access Control Matrix gate — any role without view_pr returns an empty list.
    if not scope["perms"].get("view_pr", False):
        return PrListResponse(items=[], total=0)
    items, total = await pr_crud.get_all(
        db,
        status=status,
        pr_type=pr_type,
        cost_center_id=cost_center_id,
        pr_ids_subq=scope["pr_subq"],
        search=search,
        page=page,
        page_size=page_size,
    )
    return PrListResponse(items=items, total=total)


@router.post("", response_model=PrResponse, status_code=status.HTTP_201_CREATED)
async def create_pr(
    body: PrCreate, db: SessionDep, user: CurrentUserPayload, token: BearerToken,
):
    return await pr_crud.create(
        db, body, created_by=uuid.UUID(user["sub"]), bearer_token=token,
    )


@router.get("/{pr_id}", response_model=PrResponse)
async def get_pr(pr_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    from app.core.access_scope import is_pr_visible
    pr = await pr_crud.get_by_id(db, pr_id)
    if pr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PR not found")
    scope = await build_scope(db, user)
    if not await is_pr_visible(db, pr_id, scope):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PR not found")
    return pr


@router.patch("/{pr_id}", response_model=PrResponse)
async def update_pr(
    pr_id: uuid.UUID, body: PrUpdate, db: SessionDep,
    user: CurrentUserPayload, token: BearerToken,
):
    pr = await pr_crud.get_by_id(db, pr_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="PR not found")
    if pr.status not in ("draft", "returned"):
        raise HTTPException(status_code=409, detail=f"Cannot edit PR in status '{pr.status}'")
    if str(pr.created_by) != user["sub"] and user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="Not the PR owner")
    return await pr_crud.update(db, pr, body, bearer_token=token)


async def _generate_pr_pdf_background(pr_id: uuid.UUID, pr_number: str, token: str) -> None:
    """Generate and attach the approved-PR PDF using a fresh DB session.

    Uses a fresh session independent of the request session lifecycle.
    Uploads the PDF to the file server (storage_key) per PRD §3.4.
    """
    import asyncio
    import logging
    from app.services.pdf_pr import generate_pr_pdf
    from app.models.pr import PurchaseRequest
    from app.models.pr_attachment import PrAttachment
    from app.models.config import CompanyConfig
    from app.services.attachment_helper import upload_to_file_server
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import select as sa_select

    log = logging.getLogger(__name__)
    try:
        async with AsyncSessionLocal() as fresh_db:
            pr_row = (await fresh_db.execute(
                sa_select(PurchaseRequest).where(PurchaseRequest.id == pr_id)
            )).scalar_one_or_none()
            if pr_row is None:
                return
            cfg_r = await fresh_db.execute(sa_select(CompanyConfig).limit(1))
            cfg = cfg_r.scalar_one_or_none()
            company_name = cfg.name if cfg else "EPMS"

            existing = (await fresh_db.execute(
                sa_select(PrAttachment).where(
                    PrAttachment.pr_id == pr_id,
                    PrAttachment.filename == f"{pr_number}.pdf",
                )
            )).scalar_one_or_none()
            if existing:
                return

            loop = asyncio.get_event_loop()
            pdf_bytes = await loop.run_in_executor(
                None, generate_pr_pdf, pr_row, company_name,
                cfg.pdf_templates if cfg else None,
                cfg.logo_data_url if cfg else None,
            )
            storage_key = await upload_to_file_server(
                pdf_bytes, f"{pr_number}.pdf", "application/pdf", "pr", pr_id, token,
            )
            fresh_db.add(PrAttachment(
                pr_id=pr_id,
                filename=f"{pr_number}.pdf",
                content_type="application/pdf",
                file_size=len(pdf_bytes),
                storage_key=storage_key,
            ))
            await fresh_db.commit()
            log.info("PR PDF uploaded to file server for %s (key=%s)", pr_number, storage_key)
    except Exception:
        log.warning("PR PDF generation failed for %s", pr_id, exc_info=True)


@router.post("/{pr_id}/action", response_model=PrResponse)
async def pr_action(
    pr_id: uuid.UUID,
    body: PrActionRequest,
    db: SessionDep,
    user: CurrentUserPayload,
    token: BearerToken,
):
    pr = await pr_crud.get_by_id(db, pr_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="PR not found")
    try:
        await delegate_action("pr", str(pr_id), body.action, body.comment, token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # Re-read updated PR for response + PDF side-effects
    await db.refresh(pr)
    if pr.status == "approved":
        import asyncio
        asyncio.create_task(_generate_pr_pdf_background(pr_id, pr.number, token))

    # Fire notifications for newly opened tasks
    new_tasks_result = await db.execute(
        select(Task).where(
            Task.document_type == "pr",
            Task.document_id == pr_id,
            Task.is_completed.is_(False),
        )
    )
    for task in new_tasks_result.scalars().all():
        fire_and_forget_notify(task, db)
    return pr


@router.get("/{pr_id}/events", response_model=list[ApprovalEventResponse])
async def pr_approval_events(pr_id: uuid.UUID, db: SessionDep, _: CurrentUserPayload):
    pr = await pr_crud.get_by_id(db, pr_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="PR not found")
    return await pr_crud.get_approval_events(db, pr_id)
