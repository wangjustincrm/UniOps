"""SharePoint Online REST client (read-only).

Authentication: legacy SAML cookie auth is DISABLED on this tenant (the
``/_forms/default.aspx?wa=wsignin1.0`` endpoint returns ``accessremoved``), so we
use OAuth 2.0 Resource Owner Password Credentials (ROPC) against Azure AD with
the Microsoft Office first-party client id (pre-consented for SharePoint), then
call the SharePoint REST API with a bearer token.

Credentials come from the environment, never hard-coded:
    SP_USER       e.g. appuser@canadaroyalmilk.ca
    SP_PASSWORD
    SP_TENANT     default "canadaroyalmilk.ca"
    SP_SITE       default "https://canadaroyalmilk.sharepoint.com/sites/pr2"
"""
from __future__ import annotations

import os

import httpx

# Microsoft Office — a first-party public client that is pre-authorized for
# SharePoint and supports the ROPC grant. (Verified working on this tenant.)
_OFFICE_CLIENT_ID = "d3590ed6-52b3-4102-aeff-aad2292ab01c"

_DEFAULT_TENANT = "canadaroyalmilk.ca"
_DEFAULT_SITE = "https://canadaroyalmilk.sharepoint.com/sites/pr2"


class SharePointError(RuntimeError):
    pass


class SharePointClient:
    """Minimal read-only SharePoint REST client."""

    def __init__(
        self,
        user: str | None = None,
        password: str | None = None,
        tenant: str | None = None,
        site: str | None = None,
    ) -> None:
        self.user = user or os.environ.get("SP_USER", "")
        self.password = password or os.environ.get("SP_PASSWORD", "")
        self.tenant = tenant or os.environ.get("SP_TENANT", _DEFAULT_TENANT)
        self.site = (site or os.environ.get("SP_SITE", _DEFAULT_SITE)).rstrip("/")
        # Resource = scheme://host (no path)
        self.resource = self.site.split("/sites/")[0]
        if not self.user or not self.password:
            raise SharePointError(
                "SharePoint credentials missing — set SP_USER and SP_PASSWORD "
                "in the environment."
            )
        self._token: str | None = None

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _acquire_token(self) -> str:
        url = f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token"
        data = {
            "client_id": _OFFICE_CLIENT_ID,
            "scope": f"{self.resource}/.default",
            "username": self.user,
            "password": self.password,
            "grant_type": "password",
        }
        r = httpx.post(url, data=data, timeout=30.0)
        if r.status_code != 200:
            try:
                err = r.json()
                detail = f"{err.get('error')}: {err.get('error_description', '')[:200]}"
            except Exception:
                detail = r.text[:200]
            raise SharePointError(f"ROPC token request failed ({r.status_code}): {detail}")
        return r.json()["access_token"]

    @property
    def token(self) -> str:
        if self._token is None:
            self._token = self._acquire_token()
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json;odata=nometadata",
        }

    # ── Lists ─────────────────────────────────────────────────────────────────

    def get_list_items(
        self,
        list_title: str,
        select: list[str] | None = None,
        page_size: int = 5000,
        since: str | None = None,
        expand: list[str] | None = None,
    ) -> list[dict]:
        """Return all items of a list, following odata.nextLink paging.

        ``since`` (ISO-8601 UTC, e.g. '2026-06-01T00:00:00Z') restricts to rows
        whose ``Modified`` is at or after that instant — used for incremental sync.
        ``expand`` adds OData $expand (e.g. ['AttachmentFiles']).
        """
        params = f"$top={page_size}"
        if select:
            params += "&$select=" + ",".join(select)
        if expand:
            params += "&$expand=" + ",".join(expand)
        if since:
            params += f"&$filter=Modified ge datetime'{since}'"
        url = (
            f"{self.site}/_api/web/lists/getbytitle('{list_title}')/items?{params}"
        )
        out: list[dict] = []
        with httpx.Client(timeout=180.0) as client:
            while url:
                r = client.get(url, headers=self._headers())
                if r.status_code == 401:  # token expired mid-run — refresh once
                    self._token = None
                    r = client.get(url, headers=self._headers())
                if r.status_code != 200:
                    raise SharePointError(
                        f"GET items '{list_title}' failed ({r.status_code}): {r.text[:200]}"
                    )
                body = r.json()
                out.extend(body.get("value", []))
                url = body.get("odata.nextLink") or body.get("@odata.nextLink")
        return out

    def download_file(self, server_relative_url: str) -> bytes:
        """Download a file (e.g. a list-item attachment) by its server-relative URL.

        Must call the API on the site web that owns the file (not the root web),
        and use GetFileByServerRelativePath(decodedurl=…) which tolerates spaces,
        parentheses and non-ASCII names. Single quotes are doubled per OData."""
        from urllib.parse import quote
        decoded = server_relative_url.replace("'", "''")
        path = quote(
            f"{self.site}/_api/web/GetFileByServerRelativePath(decodedurl='{decoded}')/$value",
            safe="/:?='()$,&",
        )
        url = path
        with httpx.Client(timeout=120.0) as client:
            r = client.get(url, headers={"Authorization": f"Bearer {self.token}"})
            if r.status_code == 401:
                self._token = None
                r = client.get(url, headers={"Authorization": f"Bearer {self.token}"})
            if r.status_code != 200:
                raise SharePointError(
                    f"download '{server_relative_url}' failed ({r.status_code})"
                )
            return r.content

    def list_overview(self) -> list[dict]:
        """Return non-hidden generic lists with item counts (for reconciliation)."""
        url = (
            f"{self.site}/_api/web/lists"
            "?$select=Title,ItemCount,Hidden,BaseTemplate&$top=500"
        )
        r = httpx.get(url, headers=self._headers(), timeout=60.0)
        if r.status_code != 200:
            raise SharePointError(f"GET lists failed ({r.status_code}): {r.text[:200]}")
        return [
            {"title": x["Title"], "count": x["ItemCount"]}
            for x in r.json()["value"]
            if not x["Hidden"] and x["BaseTemplate"] == 100
        ]
