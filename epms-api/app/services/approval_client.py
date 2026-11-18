"""HTTP client — EPMS → Approval Engine delegation."""
import logging
import time

import httpx
from app.core.config import settings

_log = logging.getLogger(__name__)


async def delegate_action(
    doc_type: str,
    doc_id: str,
    action: str,
    comment: str | None,
    bearer_token: str,
    expected_step: int | None = None,
) -> dict:
    """
    Call POST /approval/v1/approvals/{doc_type}/{doc_id}/action on the Approval Engine.
    Returns the ActionResult dict.  Raises ValueError on 422/409, RuntimeError on other errors.
    """
    url = f"{settings.APPROVAL_ENGINE_URL}/approvals/{doc_type}/{doc_id}/action"
    # DB clock before the call: an engine event written after this instant is
    # this call's (see _outcome_after_timeout).
    sent_at = await _db_now()
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=ACTION_TIMEOUT_S) as client:
            resp = await client.post(
                url,
                json={"action": action, "comment": comment, "expected_step": expected_step},
                headers={"Authorization": f"Bearer {bearer_token}"},
            )
    except httpx.TimeoutException:
        # The engine may well have done it and only been slow to answer —
        # 2026-11-17 an agreement submit committed (status, event, approver
        # task) but its reply never came, and the page reported "saved as a
        # draft but not submitted" for a document that was in approval. The
        # engine shares this database and records every action as an
        # approval_events row, so look there instead of guessing.
        outcome = await _outcome_after_timeout(doc_type, doc_id, action, sent_at)
        dur_ms = int((time.monotonic() - t0) * 1000)
        if outcome is not None:
            _log.warning(
                "outbound | service=approval-api | doc_type=%s | doc_id=%s | action=%s | status=TIMEOUT_BUT_APPLIED"
                " | new_status=%s | dur=%dms", doc_type, doc_id, action, outcome["new_status"], dur_ms)
            return outcome
        _log.error(
            "outbound | service=approval-api | doc_type=%s | doc_id=%s | action=%s | status=TIMEOUT | dur=%dms",
            doc_type, doc_id, action, dur_ms)
        raise RuntimeError(
            "The approval engine did not answer in time, and the action has not been "
            "recorded yet. It may still go through — refresh the page in a moment "
            "before trying again.")
    except httpx.ConnectError:
        dur_ms = int((time.monotonic() - t0) * 1000)
        _log.error(
            "outbound | service=approval-api | doc_type=%s | doc_id=%s | action=%s | status=CONN_ERROR | dur=%dms"
            " — Is approval-api running on %s? Run: ./check-health.sh",
            doc_type, doc_id, action, dur_ms, settings.APPROVAL_ENGINE_URL,
        )
        raise RuntimeError(
            f"Approval Engine unreachable at {settings.APPROVAL_ENGINE_URL}. "
            "Is approval-api running? Run: ./check-health.sh"
        )

    dur_ms = int((time.monotonic() - t0) * 1000)
    _log.info(
        "outbound | service=approval-api | doc_type=%s | doc_id=%s | action=%s | status=%d | dur=%dms",
        doc_type, doc_id, action, resp.status_code, dur_ms,
    )

    if resp.status_code in (409, 422):
        detail = resp.json().get("detail", "Conflict" if resp.status_code == 409 else "Validation error")
        raise ValueError(detail)
    if resp.status_code == 404:
        detail = resp.json().get("detail", "Not found")
        raise LookupError(detail)
    if not resp.is_success:
        _log.error(
            "outbound | service=approval-api | doc_type=%s | doc_id=%s | action=%s | status=%d | body=%s",
            doc_type, doc_id, action, resp.status_code, resp.text[:200],
        )
        raise RuntimeError(f"Approval Engine error {resp.status_code}: {resp.text[:200]}")

    return resp.json()


# How long to wait for the engine's answer, and then how long to keep looking
# in the database for the action having landed anyway.
ACTION_TIMEOUT_S = 10.0
SETTLE_WAIT_S = 10.0
SETTLE_POLL_S = 0.5


def _session_factory():
    # Indirection so tests can point the probe at the test database.
    from app.db.session import AsyncSessionLocal
    return AsyncSessionLocal


async def _db_now():
    from sqlalchemy import text
    try:
        async with _session_factory()() as s:
            return (await s.execute(text("SELECT clock_timestamp()"))).scalar_one()
    except Exception:  # never let the probe itself block the action
        _log.warning("approval_client: could not read the DB clock", exc_info=True)
        return None


def _status_model(doc_type: str):
    from app.models.agreement import PurchaseAgreement
    from app.models.pa import PaymentApplication
    from app.models.po import PurchaseOrder
    from app.models.pr import PurchaseRequest
    return {"pr": PurchaseRequest, "po": PurchaseOrder, "pa": PaymentApplication,
            "agr": PurchaseAgreement}.get(doc_type)


async def _outcome_after_timeout(doc_type: str, doc_id: str, action: str, sent_at):
    """The engine's ActionResult-shaped outcome if the timed-out action was in
    fact applied, else None.

    Applied = the engine committed an approval_events row for this document and
    action after the request was sent (the engine writes action.lower(), with
    created_at = its transaction start, so it can only be later than sent_at).
    The engine locks the document row for the whole action, so a still-running
    action shows up here as soon as it commits — hence the short poll.
    """
    import asyncio
    import uuid as _uuid

    from sqlalchemy import select

    from app.models.approval import ApprovalEvent

    if sent_at is None:
        return None
    doc_uuid = _uuid.UUID(str(doc_id))
    deadline = time.monotonic() + SETTLE_WAIT_S
    while True:
        try:
            async with _session_factory()() as s:
                hit = (await s.execute(
                    select(ApprovalEvent.id).where(
                        ApprovalEvent.document_type == doc_type,
                        ApprovalEvent.document_id == doc_uuid,
                        ApprovalEvent.action == action.lower(),
                        ApprovalEvent.created_at >= sent_at,
                    ).limit(1)
                )).scalar_one_or_none()
                if hit is not None:
                    new_status = None
                    step = None
                    Model = _status_model(doc_type)
                    if Model is not None:
                        row = (await s.execute(
                            select(Model.status, Model.approval_step_idx).where(Model.id == doc_uuid)
                        )).first()
                        if row is not None:
                            new_status, step = row
                    return {"doc_type": doc_type, "doc_id": str(doc_uuid),
                            "new_status": new_status, "approval_step_idx": step,
                            "recovered_after_timeout": True}
        except Exception:
            _log.warning("approval_client: timeout probe failed", exc_info=True)
            return None
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(SETTLE_POLL_S)


async def resync_document(doc_type: str, doc_id: str, bearer_token: str) -> dict:
    """Call POST /routing/resync-document on the Approval Engine to realign this
    document to current routing (used after an admin changes its Requester).
    Raises RuntimeError on unreachable / error so the caller can surface a warning."""
    url = f"{settings.APPROVAL_ENGINE_URL}/routing/resync-document"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json={"doc_type": doc_type, "doc_id": doc_id},
                                 headers={"Authorization": f"Bearer {bearer_token}"})
    if not resp.is_success:
        raise RuntimeError(f"resync-document {resp.status_code}: {resp.text[:200]}")
    return resp.json()


async def resync_over_budget(bearer_token: str) -> dict:
    """Call POST /routing/resync-over-budget on the Approval Engine after a Budget
    Config save: a loosened over-budget mode waives the pre-approval steps that
    in-flight over-budget PRs are still waiting on, so the Finance Manager's
    task moves on instead of stranding (2026-10-16, ISS-0006). Raises
    RuntimeError on unreachable / error; the config save is already committed.
    """
    url = f"{settings.APPROVAL_ENGINE_URL}/routing/resync-over-budget"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers={"Authorization": f"Bearer {bearer_token}"})
    if not resp.is_success:
        raise RuntimeError(f"resync-over-budget {resp.status_code}: {resp.text[:200]}")
    return resp.json()


async def realign_workflows(bearer_token: str, *, new_defs: dict | None = None) -> dict:
    """Call POST /routing/realign-workflows on the Approval Engine.

    Without `new_defs`: after an Approval Workflows save, carry every in-flight
    document over to the chains as saved (the engine keeps the chains they were
    aligned to). With `new_defs`: a preview of saving those — nothing changes.
    Returns {"changed_workflows", "realigned": [...], "errors": [...]}. Raises
    RuntimeError on unreachable / error; a save is already committed by then.
    """
    url = f"{settings.APPROVAL_ENGINE_URL}/routing/realign-workflows"
    body = {"dry_run": True, "new_defs": new_defs} if new_defs is not None else {}
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=body,
                                 headers={"Authorization": f"Bearer {bearer_token}"})
    if not resp.is_success:
        raise RuntimeError(f"realign-workflows {resp.status_code}: {resp.text[:200]}")
    return resp.json()


async def resync_inflight(bearer_token: str) -> dict:
    """Call POST /routing/resync-inflight on the Approval Engine: re-point every
    in-flight document at whoever the CURRENT config resolves to.

    Used after an admin edits a user's role / department / active state — approve
    tasks for department-scoped roles are pinned to a specific person at creation
    time, so without this the previous holder keeps them (prod incident
    2026-08-18: 33 PA/PR tasks stranded on a swapped-out Department Manager).
    Raises RuntimeError on unreachable / error so the caller can surface a
    warning; the user edit itself has already been committed.
    """
    url = f"{settings.APPROVAL_ENGINE_URL}/routing/resync-inflight"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers={"Authorization": f"Bearer {bearer_token}"})
    if not resp.is_success:
        raise RuntimeError(f"resync-inflight {resp.status_code}: {resp.text[:200]}")
    return resp.json()


async def forward(method: str, path: str, token: str | None, json=None) -> tuple[int, dict]:
    """Pass a caller request through to the Approval Engine using the caller's own Bearer token.

    Used by the /config/approval-routing proxy (Phase 3): approval-api has no
    browser-facing subdomain (see Caddyfile — it's server-to-server only), so
    epms-api is the gateway, mirroring how authz_client.forward gateways
    identity. No authz logic is re-implemented here — approval-api's own
    /routing handlers gate PUT to system_admin and own all 422 validation;
    this just passes status + body through unchanged.

    Raises on connection failure (e.g. httpx.ConnectError); callers should
    catch and return 502 — this is a write-capable admin surface, so an
    outage must surface as an error, never a stale-data fallback.
    """
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.request(
            method,
            f"{settings.APPROVAL_ENGINE_URL}{path}",
            headers=headers,
            json=json,
        )
        body = r.json() if r.content else {}
        return r.status_code, body


async def get_workflow_steps(doc_type: str, doc_id: str, bearer_token: str) -> list[dict]:
    """
    Call GET /approval/v1/approvals/{doc_type}/{doc_id}/workflow-steps on the Approval Engine.
    Returns a list of {id, role, label} dicts for the document's effective workflow.
    Raises LookupError on 404, RuntimeError on other errors.
    """
    url = f"{settings.APPROVAL_ENGINE_URL}/approvals/{doc_type}/{doc_id}/workflow-steps"
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {bearer_token}"})
    except httpx.ConnectError:
        dur_ms = int((time.monotonic() - t0) * 1000)
        _log.error(
            "outbound | service=approval-api | doc_type=%s | doc_id=%s | action=workflow-steps | status=CONN_ERROR | dur=%dms"
            " — Is approval-api running on %s? Run: ./check-health.sh",
            doc_type, doc_id, dur_ms, settings.APPROVAL_ENGINE_URL,
        )
        raise RuntimeError(
            f"Approval Engine unreachable at {settings.APPROVAL_ENGINE_URL}. "
            "Is approval-api running? Run: ./check-health.sh"
        )

    dur_ms = int((time.monotonic() - t0) * 1000)
    _log.info(
        "outbound | service=approval-api | doc_type=%s | doc_id=%s | action=workflow-steps | status=%d | dur=%dms",
        doc_type, doc_id, resp.status_code, dur_ms,
    )

    if resp.status_code == 404:
        raise LookupError(resp.json().get("detail", "Not found"))
    if not resp.is_success:
        _log.error(
            "outbound | service=approval-api | doc_type=%s | doc_id=%s | action=workflow-steps | status=%d | body=%s",
            doc_type, doc_id, resp.status_code, resp.text[:200],
        )
        raise RuntimeError(f"Approval Engine error {resp.status_code}: {resp.text[:200]}")

    return resp.json()


async def get_preflight(doc_type: str, doc_id: str, action: str,
                        bearer_token: str) -> dict:
    """GET /approval/v1/approvals/{doc_type}/{doc_id}/preflight on the engine.

    Returns the engine's own verdict — state machine, budget mode, approver
    routing — for the caller to merge with the field-level checks epms-api owns.
    Raises LookupError on 404, RuntimeError on anything else, matching the other
    helpers here so callers handle one shape.
    """
    url = (f"{settings.APPROVAL_ENGINE_URL}/approvals/{doc_type}/{doc_id}"
           f"/preflight?action={action}")
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {bearer_token}"})
    except httpx.ConnectError:
        dur_ms = int((time.monotonic() - t0) * 1000)
        _log.error(
            "outbound | service=approval-api | doc_type=%s | doc_id=%s | action=preflight |"
            " status=CONN_ERROR | dur=%dms — Is approval-api running on %s? Run: ./check-health.sh",
            doc_type, doc_id, dur_ms, settings.APPROVAL_ENGINE_URL,
        )
        raise RuntimeError(
            f"Approval Engine unreachable at {settings.APPROVAL_ENGINE_URL}. "
            "Is approval-api running? Run: ./check-health.sh"
        )

    dur_ms = int((time.monotonic() - t0) * 1000)
    _log.info(
        "outbound | service=approval-api | doc_type=%s | doc_id=%s | action=preflight |"
        " status=%d | dur=%dms", doc_type, doc_id, resp.status_code, dur_ms,
    )
    if resp.status_code == 404:
        raise LookupError(f"{doc_type.upper()} {doc_id} not found in approval engine")
    if resp.status_code >= 400:
        raise RuntimeError(f"Approval Engine returned {resp.status_code}: {resp.text[:200]}")
    return resp.json()
