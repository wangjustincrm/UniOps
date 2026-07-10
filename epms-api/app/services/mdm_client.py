"""Thin httpx client used by epms-api to read ERP mirror data from mdm-api."""
from __future__ import annotations
from typing import Any
import httpx
from app.core.config import settings


class MdmError(Exception):
    def __init__(self, status: int, detail: str):
        self.status = status
        super().__init__(f"mdm-api error {status}: {detail}")


class MdmClient:
    def __init__(self, bearer_token: str):
        self._bearer = bearer_token
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "MdmClient":
        self._http = httpx.AsyncClient(
            base_url=f"{settings.MDM_API_URL}/mdm/v1",
            timeout=settings.MDM_API_TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {self._bearer}"},
        )
        return self

    async def __aexit__(self, *exc):
        if self._http:
            await self._http.aclose()

    async def _get(self, path: str) -> Any:
        assert self._http is not None
        try:
            resp = await self._http.get(path)
        except httpx.HTTPError as e:
            raise MdmError(0, f"network: {e}") from e
        if resp.status_code == 404:
            return None
        if not resp.is_success:
            raise MdmError(resp.status_code, resp.text[:200])
        return resp.json()

    async def _send(self, method: str, path: str, payload: dict) -> dict:
        assert self._http is not None
        try:
            resp = await self._http.request(method, path, json=payload)
        except httpx.HTTPError as e:
            raise MdmError(0, f"network: {e}") from e
        if not resp.is_success:
            detail = resp.text[:300]
            try:
                detail = resp.json().get("detail", detail)
            except Exception:
                pass
            raise MdmError(resp.status_code, detail)
        return resp.json()

    async def get_person(self, code: str) -> dict | None:
        return await self._get(f"/erp/persons/{code}")

    async def get_supplier(self, code: str) -> dict | None:
        return await self._get(f"/erp/suppliers/{code}")

    async def get_tax_codes(self) -> list | None:
        """B2 税码主数据(激活集)。"""
        return await self._get("/tax/codes")

    # ── business_partners (mdm owns the table; epms forwards supplier writes) ──────
    async def find_partner_by_code(self, code: str) -> dict | None:
        """Exact-code lookup via the partners list (search matches name OR code)."""
        res = await self._get(f"/partners?search={code}&active_only=false&page_size=200")
        if not res:
            return None
        for p in res.get("items", []):
            if p.get("code") == code:
                return p
        return None

    async def create_partner(self, payload: dict) -> dict:
        return await self._send("POST", "/partners", payload)

    async def update_partner(self, partner_id: str, payload: dict) -> dict:
        return await self._send("PATCH", f"/partners/{partner_id}", payload)
