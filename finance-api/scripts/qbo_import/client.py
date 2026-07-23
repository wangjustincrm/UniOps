"""QuickBooks Online Accounting API v3 client — OAuth refresh + paged query.

Credentials live OUTSIDE the repo at C:\\Project\\qbo_conn.env (same convention
as nc65_conn.env). The refresh token rotates every ~24-26h, so every refresh
writes the new value back to that file — losing a rotated token means going
back to the OAuth Playground.

Throttling (per Intuit): 500 requests/min per realmId, max 10 concurrent.
This client is deliberately serial with backoff; a one-off migration has no
reason to race the limit.
"""
import base64
import re
import time
from pathlib import Path

import httpx

ENV_PATH = Path(r"C:\Project\qbo_conn.env")

TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
BASE_URLS = {
    "production": "https://quickbooks.api.intuit.com",
    "sandbox": "https://sandbox-quickbooks.api.intuit.com",
}

# QBO caps a single query response at 1000 rows regardless of what MAXRESULTS asks for.
PAGE_SIZE = 1000


def load_cfg(path: Path = ENV_PATH) -> dict:
    """Parse the KEY=VALUE env file (comment- and quote-tolerant, like _nc_cfg)."""
    cfg = {}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = re.sub(r"\s+#.*$", "", v).strip().strip('"').strip("'")
    return cfg


def _persist_refresh_token(token: str, path: Path = ENV_PATH) -> None:
    """Rewrite the REFRESH_TOKEN line in place, preserving comments/ordering."""
    text = path.read_text(encoding="utf-8-sig")
    new, n = re.subn(r"(?m)^REFRESH_TOKEN=.*$", f"REFRESH_TOKEN={token}", text)
    assert n == 1, f"expected exactly 1 REFRESH_TOKEN line in {path}, found {n}"
    path.write_text(new, encoding="utf-8")


class QboClient:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or load_cfg()
        for key in ("CLIENT_ID", "CLIENT_SECRET", "REALM_ID", "REFRESH_TOKEN"):
            if not self.cfg.get(key):
                raise SystemExit(
                    f"{key} is empty in {ENV_PATH}. "
                    "REALM_ID and REFRESH_TOKEN come from the OAuth 2.0 Playground."
                )
        self.realm_id = self.cfg["REALM_ID"]
        self.minor_version = self.cfg.get("MINOR_VERSION", "75")
        self.base_url = BASE_URLS[self.cfg.get("QBO_ENV", "production")]
        self._access_token: str | None = None
        self._http = httpx.Client(timeout=60.0)

    # ---------- auth ----------

    def refresh(self) -> str:
        """Exchange the refresh token for a fresh access token (valid 1h).

        Intuit rotates the refresh token on this call, so the response value is
        persisted immediately — before any data request can fail and abort.
        """
        basic = base64.b64encode(
            f"{self.cfg['CLIENT_ID']}:{self.cfg['CLIENT_SECRET']}".encode()
        ).decode()
        r = self._http.post(
            TOKEN_URL,
            headers={
                "Authorization": f"Basic {basic}",
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "refresh_token", "refresh_token": self.cfg["REFRESH_TOKEN"]},
        )
        if r.status_code != 200:
            raise SystemExit(
                f"token refresh failed ({r.status_code}): {r.text}\n"
                "If this says invalid_grant the refresh token expired or was "
                "superseded — re-run the OAuth Playground to get a new one."
            )
        payload = r.json()
        self._access_token = payload["access_token"]
        new_refresh = payload.get("refresh_token")
        if new_refresh and new_refresh != self.cfg["REFRESH_TOKEN"]:
            self.cfg["REFRESH_TOKEN"] = new_refresh
            _persist_refresh_token(new_refresh)
            print(f"  [auth] refresh token rotated, persisted to {ENV_PATH}")
        return self._access_token

    def _token(self) -> str:
        return self._access_token or self.refresh()

    # ---------- requests ----------

    def get(self, path: str, params: dict | None = None, _retries: int = 5) -> dict:
        """GET {base}/v3/company/{realm}/{path} with 401-refresh and 429/5xx backoff."""
        url = f"{self.base_url}/v3/company/{self.realm_id}/{path.lstrip('/')}"
        params = {**(params or {}), "minorversion": self.minor_version}
        delay = 2.0
        for attempt in range(_retries):
            r = self._http.get(
                url,
                params=params,
                headers={
                    "Authorization": f"Bearer {self._token()}",
                    "Accept": "application/json",
                },
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code == 401 and attempt == 0:
                self.refresh()  # access token aged out mid-run
                continue
            if r.status_code == 429 or r.status_code >= 500:
                print(f"  [retry] {r.status_code} on {path}, sleeping {delay:.0f}s")
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"GET {path} -> {r.status_code}: {r.text[:500]}")
        raise RuntimeError(f"GET {path} failed after {_retries} attempts")

    def query(self, statement: str) -> dict:
        """Run one QBO SQL-ish query statement. Returns the raw QueryResponse dict."""
        return self.get("query", {"query": statement})["QueryResponse"]

    def query_all(self, entity: str, where: str = "", page_size: int = PAGE_SIZE):
        """Yield every row of `entity`, paging with STARTPOSITION/MAXRESULTS.

        ORDER BY Id is required for a stable page window — without it QBO may
        reshuffle between pages and silently drop or duplicate rows.
        """
        start = 1
        while True:
            stmt = (
                f"SELECT * FROM {entity}"
                f"{' WHERE ' + where if where else ''}"
                f" ORDER BY Id STARTPOSITION {start} MAXRESULTS {page_size}"
            )
            rows = self.query(stmt).get(entity, [])
            if not rows:
                return
            yield from rows
            if len(rows) < page_size:
                return
            start += page_size

    def report(self, name: str, params: dict | None = None) -> dict:
        """Run a report, e.g. report('TrialBalance', {'start_date': ..., 'end_date': ...})."""
        return self.get(f"reports/{name}", params)
