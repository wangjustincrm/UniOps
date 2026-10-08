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
) -> dict:
    """
    Call POST /approval/v1/approvals/{doc_type}/{doc_id}/action on the Approval Engine.
    Returns the ActionResult dict.  Raises ValueError on 422/409, RuntimeError on other errors.
    """
    url = f"{settings.APPROVAL_ENGINE_URL}/approvals/{doc_type}/{doc_id}/action"
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json={"action": action, "comment": comment},
                headers={"Authorization": f"Bearer {bearer_token}"},
            )
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
