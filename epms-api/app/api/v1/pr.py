"""Purchase Request endpoints."""
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from app.core.access_scope import build_scope
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep, require_roles
from app.crud import pr as pr_crud
from app.crud.current_step import enrich_current_step
from app.models.task import Task
from app.schemas.pr import (
    ApprovalEventResponse,
    BudgetCheckRequest,
    BudgetCheckResponse,
    PrActionRequest,
    PrCreate,
    PrListResponse,
    PrResponse,
    PrUpdate,
)
from app.schemas.reminder import ReminderResponse
from app.services import approval_client as approval_client
from app.services.approval_client import delegate_action
from app.services.budget_client import ensure_known_budget_code
from app.services.doc_preflight import field_checks_for
from app.services.manual_reminder import remind_document
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
    department_id: uuid.UUID | None = Query(default=None),
    is_prepaid: bool | None = Query(default=None),
    created_by: uuid.UUID | None = Query(default=None),
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
        department_id=department_id,
        is_prepaid=is_prepaid,
        created_by=created_by,   # narrows WITHIN the enforced scope — safe
        pr_ids_subq=scope["pr_subq"],
        search=search,
        page=page,
        page_size=page_size,
    )
    await enrich_current_step(db, "pr", items)
    return PrListResponse(items=items, total=total)


async def _check_owner(db, owner_id: uuid.UUID | None) -> None:
    """422 unless the named service owner is a real, active user.

    The owner is who the completion-date sweep and the invoice-matched nudge
    will ask to confirm the receipt (app/crud/pr_owner.py). A deactivated or
    nonexistent id would pass the FK on the way in and then silently swallow
    every one of those reminders months later, when nobody is watching this
    form any more — so it is refused here, at the only moment a human can fix
    it. None is always fine: it means the requester.
    """
    if owner_id is None:
        return
    from app.models.user import User
    row = (await db.execute(
        select(User.is_active).where(User.id == owner_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=422, detail="Owner not found")
    if not row:
        raise HTTPException(status_code=422, detail="Owner is not an active user")


async def _owner_display_name(db, pr, requester_name: str | None) -> str | None:
    """The service owner's name for the PDF's Service Owner row.

    Resolves through crud.pr_owner, so a NULL owner_id prints the requester
    rather than a dash. When the owner IS the requester — the common case —
    `requester_name` is already in hand and no second query is made; that name
    comes from approval_signatories, which is what the rest of the PDF uses,
    so the two rows cannot disagree about spelling.
    """
    from app.crud.pr_owner import owner_id_of
    from app.models.user import User

    owner_id = owner_id_of(pr)
    if owner_id == pr.created_by:
        return requester_name
    return (await db.execute(
        select(User.full_name).where(User.id == owner_id)
    )).scalar_one_or_none()


@router.post("", response_model=PrResponse, status_code=status.HTTP_201_CREATED)
async def create_pr(
    body: PrCreate, db: SessionDep, user: CurrentUserPayload, token: BearerToken,
):
    await _check_owner(db, body.owner_id)
    await ensure_known_budget_code(token, body.budget_code)
    return await pr_crud.create(
        db, body, created_by=uuid.UUID(user["sub"]), bearer_token=token,
    )


@router.post("/budget-check", response_model=BudgetCheckResponse)
async def budget_check(
    body: BudgetCheckRequest, db: SessionDep, user: CurrentUserPayload, token: BearerToken,
):
    """Report whether the given amount is over budget for its account.

    Reuses the exact computation the create path runs (crud.pr.compute_budget_check),
    so the frontend can stop double-computing over_budget.
    """
    # A caller naming a cost centre and a non-zero amount but no budget_code is
    # asking something compute_budget_check cannot answer: it opens with
    # `if not budget_code: return False, None`, so the reply is a flat "not over
    # budget" indistinguishable from a real one. The Create form sent exactly
    # that body for as long as this endpoint existed — its query key listed the
    # account, its body left it out — which left the over-budget banner, the
    # justification requirement and the hard_block gate dead on that page while
    # create() went on persisting the real verdict from the real budget_code.
    # It surfaced nowhere, because a PR within budget and a PR never checked
    # look identical. Log it: the caller is malformed, not the data.
    if not body.budget_code and body.cost_center_id is not None and body.amount > 0:
        logging.getLogger(__name__).warning(
            "budget-check called with no budget_code (cost_center=%s amount=%s) — "
            "answering not-over-budget without checking anything",
            body.cost_center_id, body.amount,
        )
    over_budget, available = await pr_crud.compute_budget_check(
        body.budget_code, body.cost_center_id, body.amount, bearer_token=token,
    )
    return BudgetCheckResponse(
        over_budget=over_budget,
        available=(str(available) if available is not None else None),
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
        # "Requester", not "owner": since PRs carry an owner_id of their own,
        # the old "Not the PR owner" wording named the wrong person. Editing a
        # draft belongs to whoever raised it; the service owner's part starts at
        # receipt. (Nothing reads this string but a human.)
        raise HTTPException(
            status_code=403, detail="Only the requester who raised this PR can edit it")
    await _check_owner(db, body.owner_id)
    # Partial payload: None means "not sent", so an edit that never touched the
    # budget account isn't asked to re-prove it.
    if body.budget_code is not None:
        await ensure_known_budget_code(token, body.budget_code)
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
    from app.services import budget_client
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

            from app.crud.signatories import approval_signatories
            requester_name, approvals = await approval_signatories(
                fresh_db, "pr", pr_id, pr_row.created_by
            )
            owner_name = await _owner_display_name(fresh_db, pr_row, requester_name)

            budget_account_name = await budget_client.get_account_name(
                token, pr_row.budget_code
            )

            loop = asyncio.get_event_loop()
            pdf_bytes = await loop.run_in_executor(
                None, generate_pr_pdf, pr_row, company_name,
                cfg.pdf_templates if cfg else None,
                cfg.logo_data_url if cfg else None,
                requester_name, approvals, budget_account_name, owner_name,
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
    # Field-level gates (vendor, budget account, service completion date) run
    # here, before delegation: epms-api owns PR field rules, whereas approval-api
    # owns the state machine and never validates document fields. Why each gate
    # exists is documented next to it in services/doc_preflight.
    #
    # They live there rather than inline so the preflight endpoint reports
    # exactly what this path enforces. Submission still stops at the first
    # failure — that is right here; preflight is the one that collects them all.
    if body.action.lower() == "submit":
        for check in field_checks_for("pr", pr):
            if not check.passed:
                raise HTTPException(status_code=409, detail=check.message)
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
        from app.core.background import spawn
        spawn(_generate_pr_pdf_background(pr_id, pr.number, token), name=f"pr_pdf:{pr.number}")

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


@router.post("/{pr_id}/remind", response_model=ReminderResponse)
async def remind_pr_approver(pr_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    """Nudge whoever holds this PR's open approval task.

    Backs the "Send reminder" link under the current step of the Approval
    Timeline. Any user who can open the PR may nudge — the 24h cooldown in
    app/services/manual_reminder.py is what protects the approver's inbox, not a
    role gate, so the link never 403s on someone who can see it.
    """
    pr = await pr_crud.get_by_id(db, pr_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="PR not found")
    return await remind_document(
        db, document_type="pr", document_id=pr_id, actor_id=uuid.UUID(user["sub"]),
    )


@router.get("/{pr_id}/events", response_model=list[ApprovalEventResponse])
async def pr_approval_events(pr_id: uuid.UUID, db: SessionDep, _: CurrentUserPayload):
    pr = await pr_crud.get_by_id(db, pr_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="PR not found")
    return await pr_crud.get_approval_events(db, pr_id)


@router.get("/{pr_id}/workflow-steps")
async def pr_workflow_steps(pr_id: uuid.UUID, user: CurrentUserPayload, token: BearerToken):
    try:
        return await approval_client.get_workflow_steps("pr", str(pr_id), token)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
