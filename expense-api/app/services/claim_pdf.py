"""Generate a claim's PDF and attach it — at approval, and on Regenerate PDF.

One path for every OA claim type, so the PDF filed at approval and the one a
user regenerates later cannot disagree: EXP / MIL / TRV / CFM render through
pdf_claim.py, TRA through its own form in pdf_tra.py.

The attachment is named <claim_number>.pdf and REPLACES any earlier one of
that name (row and file-api blob) — never piles up.
"""
import asyncio
import logging
import uuid

from sqlalchemy import select

from app.models.expense import ExpenseAttachment, ExpenseClaim
from app.services import attachment_helper
from app.services.pdf_claim import build_claim_pdf, cfm_answers
from app.services.pdf_tra import build_travel_application_pdf

log = logging.getLogger(__name__)

#: Statuses at which a claim has an approved document to print. TRA is the
#: exception: its form doubles as the application, printable at any status
#: (that was the TRA endpoint's behaviour before this module existed).
PDF_STATUSES = {"approved", "paid"}


def pdf_filename(claim: ExpenseClaim) -> str:
    return f"{claim.claim_number}.pdf"


async def approval_trail(db, claim: ExpenseClaim) -> list[dict]:
    """The approval record printed on the PDF, in order:

    "Submitted" (the last submission — the comment typed there is often the
    only explanation of the claim), then one row per configured workflow step
    with who approved it, when, and their comment.

    Only events from the LAST submission count: a claim that was returned and
    resubmitted went through the chain again, and an approval from the first
    round did not approve what was finally paid. A configured step with no
    approval in that round was skipped by the engine (e.g. the approver was the
    claimant) and says so rather than being silently dropped.

    Source is the shared approval_events table — the same one the claim's
    Approval Timeline and Approval Status read (expenses._shared_approval_events).
    """
    from app.api.v1.expenses import _action_key, _get_workflow_defs, _shared_approval_events, _workflow_key

    events = await _shared_approval_events(db, claim.id)
    submits = [e for e in events if e.action == "submit"]
    last_submit = submits[-1] if submits else None
    current = [e for e in events if last_submit is None or e.created_at >= last_submit.created_at]

    rows: list[dict] = []
    if last_submit is not None:
        rows.append({"step": "Submitted", "name": last_submit.actor_name or claim.employee_name,
                     "at": last_submit.created_at, "comment": last_submit.comment})

    approved_by_step: dict[int, object] = {}
    for e in current:
        if e.action == "approve":
            approved_by_step[e.step_idx] = e     # ascending → the last one wins

    wf = await _get_workflow_defs(db)
    steps = wf.get(_action_key(claim.claim_type)) or wf.get(_workflow_key(claim.claim_type)) or []
    for i, step in enumerate(steps):
        label = step.get("label") or step.get("role") or f"Step {i + 1}"
        ev = approved_by_step.pop(i, None)
        if ev is not None:
            rows.append({"step": label, "name": ev.actor_name, "at": ev.created_at, "comment": ev.comment})
        else:
            rows.append({"step": label, "name": None, "at": None, "comment": "Skipped — no approval required"})
    # Approvals at a step the current workflow no longer has (the chain was
    # edited after this claim went through) still happened; print them.
    for idx in sorted(approved_by_step):
        ev = approved_by_step[idx]
        rows.append({"step": f"Step {idx + 1}", "name": ev.actor_name, "at": ev.created_at, "comment": ev.comment})
    return rows


async def _tra_approvals(db, claim: ExpenseClaim) -> list[dict]:
    """pdf_tra's signature rows: approve events by step_idx, actor names resolved."""
    from app.api.v1.expenses import _shared_approval_events

    return [{
        "step_idx": e.step_idx,
        "actor_name": e.actor_name or "",
        "acted_date": e.created_at.date().isoformat() if e.created_at else None,
    } for e in await _shared_approval_events(db, claim.id) if e.action == "approve"]


async def _company_name(db) -> str | None:
    from app.models.company_config_mirror import EpmsCompanyConfig
    cfg = (await db.execute(select(EpmsCompanyConfig).limit(1))).scalar_one_or_none()
    return getattr(cfg, "name", None) if cfg else None


async def _cfm_form(db, claim: ExpenseClaim) -> tuple[str | None, list[tuple[str, str]]]:
    """(form name, [(label, answer)]) for a custom-form claim, in field_schema
    order. Answers whose field is gone from the schema are kept, under their key."""
    from app.crud.custom_form import get_by_code

    code = claim.claim_type.split("_", 1)[1] if "_" in claim.claim_type else claim.claim_type[3:]
    form = await get_by_code(db, code) if code else None
    answers, _ = cfm_answers(claim.notes)
    out: list[tuple[str, str]] = []
    for f in (form.field_schema if form else []) or []:
        name = f.get("name")
        if not name:
            continue
        val = answers.pop(name, None)
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val)
        elif isinstance(val, bool):
            val = "Yes" if val else "No"
        out.append((f.get("label") or name, "" if val is None else str(val)))
    for k, v in answers.items():
        out.append((k, "" if v is None else str(v)))
    return (form.name if form else None), out


async def render(db, claim: ExpenseClaim) -> bytes:
    if claim.claim_type == "TRA":
        approvals = await _tra_approvals(db, claim)
        return await asyncio.get_running_loop().run_in_executor(
            None, lambda: build_travel_application_pdf(claim, approvals=approvals))

    approvals = await approval_trail(db, claim)
    company_name = await _company_name(db)
    form_name, cfm_fields = None, None
    if claim.claim_type.upper().startswith("CFM"):
        form_name, cfm_fields = await _cfm_form(db, claim)
    tra_number = None
    if claim.claim_type == "TRV" and claim.travel_application_id:
        tra_number = (await db.execute(
            select(ExpenseClaim.claim_number).where(ExpenseClaim.id == claim.travel_application_id)
        )).scalar_one_or_none()
    return await asyncio.get_running_loop().run_in_executor(None, lambda: build_claim_pdf(
        claim, approvals, company_name=company_name, form_name=form_name,
        cfm_fields=cfm_fields, travel_application_number=tra_number))


async def attach_pdf(db, claim: ExpenseClaim, token: str) -> dict:
    """Render, upload, and replace any earlier <claim_number>.pdf. Commits.

    Upload happens before anything is deleted, so a file-api failure leaves the
    previous PDF in place. Raises RuntimeError when file-api refuses.
    """
    data = await render(db, claim)
    filename = pdf_filename(claim)
    storage_key = await attachment_helper.upload_to_file_server(
        data, filename, "application/pdf",
        claim.claim_type.lower().split("_")[0],   # same doc_type as a user upload
        claim.id, token)

    # Only the generated document is swept — matched by exact name.
    superseded = list((await db.execute(
        select(ExpenseAttachment).where(ExpenseAttachment.claim_id == claim.id,
                                        ExpenseAttachment.file_name == filename)
    )).scalars().all())
    for old in superseded:
        if old.file_id and old.file_id != str(storage_key):
            try:
                await attachment_helper.delete_from_file_server(uuid.UUID(old.file_id), token)
            except Exception:
                # An orphaned blob is better than a failed regeneration.
                log.warning("file-api delete failed for %s; continuing", old.file_id)
        await db.delete(old)

    db.add(ExpenseAttachment(claim_id=claim.id, file_id=str(storage_key), file_name=filename,
                             file_size_bytes=len(data), mime_type="application/pdf"))
    await db.commit()
    return {"file_name": filename, "file_id": str(storage_key), "replaced": len(superseded)}
