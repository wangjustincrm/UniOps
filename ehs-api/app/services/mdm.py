"""Talking to mdm-api about the plant area tree.

Safety reads locations straight from the mirror — it is the same physical
table and a read does not need a network hop — but writes go through the
service that owns the table. The HSE Manager maintains the content, so the
screen is here; the write path stays where the data lives, which is what keeps
the other consumers of this tree (maintenance, VMS, MRP) honest.

The caller's own token is forwarded rather than a service credential, so
mdm-api applies `mdm.location.write` to the actual person. Gating it a second
time here would be two gates to keep in step, and this codebase has watched
that pair drift before.
"""
from __future__ import annotations

import uuid
from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import settings

_TIMEOUT = 15.0


async def _call(method: str, path: str, token: str, payload: dict | None = None) -> Any:
    url = f"{settings.MDM_API_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(
                method, url, json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="The master-data service is not reachable, so the area could "
                   "not be saved.",
        ) from exc

    if not resp.is_success:
        # Passed through rather than flattened: a 403 here means the person
        # lacks `mdm.location.write`, and telling them "could not save" would
        # send them looking for a fault that is not there.
        detail = "The master-data service rejected the change."
        try:
            body = resp.json()
            detail = body.get("detail") or detail
        except ValueError:
            pass
        raise HTTPException(status_code=resp.status_code, detail=detail)

    return resp.json()


async def create_location(payload: dict, token: str) -> dict:
    return await _call("POST", "/locations", token, payload)


async def update_location(location_id: uuid.UUID, payload: dict, token: str) -> dict:
    return await _call("PATCH", f"/locations/{location_id}", token, payload)


async def set_location_active(location_id: uuid.UUID, active: bool, token: str) -> dict:
    verb = "restore" if active else "retire"
    return await _call("POST", f"/locations/{location_id}/{verb}", token)
