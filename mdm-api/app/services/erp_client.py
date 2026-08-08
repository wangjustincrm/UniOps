"""HTTP client for the NC ERP master-data HTTP interface."""
from __future__ import annotations
from datetime import datetime
from typing import Any
import httpx
from app.core.config import settings


class ErpError(Exception):
    def __init__(self, code: int | None, message: str):
        self.code = code
        super().__init__(f"ERP error code={code}: {message}")


_PATHS = {
    "material":   "/firmusData/touch_mdm_mes/material/getMaterialInfo",
    "supplier":   "/firmusData/touch_mdm_mes/supplier/getSupplierInfo",
    "person":     "/firmusData/touch_mdm_mes/person/getPersonInfo",
    "unit_tranf": "/firmusData/touch_mdm_mes/unitTranf/getUnitTranfInfo",
}


def _fmt_ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


class ErpClient:
    def __init__(self, http: httpx.AsyncClient | None = None):
        self._http = http
        self._owns_http = http is None

    async def __aenter__(self) -> "ErpClient":
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=settings.erp_base_url,
                timeout=settings.erp_timeout_seconds,
            )
        return self

    async def __aexit__(self, *exc):
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _post(self, path: str, ts: datetime) -> list[dict[str, Any]]:
        assert self._http is not None, "ErpClient used outside context manager"
        try:
            resp = await self._http.post(path, json={"ts": _fmt_ts(ts)})
        except httpx.HTTPError as e:
            raise ErpError(None, f"network error: {e}") from e
        if resp.status_code >= 500:
            raise ErpError(resp.status_code, f"HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as e:
            raise ErpError(None, f"invalid JSON: {e}") from e
        code = body.get("code")
        if code != 200:
            raise ErpError(code, body.get("message", "unknown ERP error"))
        return list(body.get("data") or [])

    async def fetch_materials(self, ts: datetime) -> list[dict[str, Any]]:
        return await self._post(_PATHS["material"], ts)

    async def fetch_suppliers(self, ts: datetime) -> list[dict[str, Any]]:
        return await self._post(_PATHS["supplier"], ts)

    async def fetch_persons(self, ts: datetime) -> list[dict[str, Any]]:
        return await self._post(_PATHS["person"], ts)

    async def fetch_unit_tranf(self, ts: datetime) -> list[dict[str, Any]]:
        return await self._post(_PATHS["unit_tranf"], ts)
