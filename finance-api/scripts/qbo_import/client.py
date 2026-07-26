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
import logging
import os
import re
import time
from pathlib import Path

import httpx

# Credentials live outside the repo. Host scripts (import/authorize on Windows)
# default to C:\Project\qbo_conn.env; the containerized finance-api overrides this
# with QBO_CONN_ENV pointing at a path mounted into the container (Linux), since
# the Windows default is unreachable there.
ENV_PATH = Path(os.environ.get("QBO_CONN_ENV", r"C:\Project\qbo_conn.env"))

# All errors (with Intuit's intuit_tid transaction id) are written here so a run
# can be handed to Intuit support for troubleshooting. Console still shows them too.
LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "qbo" / "qbo_import.log"


def _get_logger() -> logging.Logger:
    log = logging.getLogger("qbo_import")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(fh)
    return log


logger = _get_logger()

# Intuit's OpenID discovery documents. The OAuth endpoints (token exchange,
# authorization, revocation) are read from here at runtime rather than hardcoded,
# so a future endpoint change on Intuit's side does not silently break the flow.
# The literals below are only a fallback if the discovery fetch fails.
DISCOVERY_URLS = {
    "production": "https://developer.api.intuit.com/.well-known/openid_configuration",
    "sandbox": "https://developer.api.intuit.com/.well-known/openid_sandbox_configuration",
}
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
REVOKE_URL = "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"
AUTH_ENDPOINT = "https://appcenter.intuit.com/connect/oauth2"
BASE_URLS = {
    "production": "https://quickbooks.api.intuit.com",
    "sandbox": "https://sandbox-quickbooks.api.intuit.com",
}

# QBO caps a single query response at 1000 rows regardless of what MAXRESULTS asks for.
PAGE_SIZE = 1000

_discovery_cache: dict[str, dict] = {}


def discover(env: str) -> dict:
    """Fetch (and cache) Intuit's OpenID discovery document for `env`.

    Returns a dict with at least authorization_endpoint / token_endpoint /
    revocation_endpoint. Falls back to the hardcoded literals on any failure so
    a discovery outage never blocks a migration run.
    """
    if env in _discovery_cache:
        return _discovery_cache[env]
    doc = {
        "authorization_endpoint": AUTH_ENDPOINT,
        "token_endpoint": TOKEN_URL,
        "revocation_endpoint": REVOKE_URL,
    }
    try:
        r = httpx.get(DISCOVERY_URLS[env], timeout=30.0)
        if r.status_code == 200:
            fetched = r.json()
            doc = {k: fetched.get(k, doc[k]) for k in doc}
    except Exception as exc:  # noqa: BLE001 — fall back to literals, never abort
        print(f"  [discovery] fetch failed ({exc}); using built-in endpoints")
    _discovery_cache[env] = doc
    return doc


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
        self.env = self.cfg.get("QBO_ENV", "production")
        self.base_url = BASE_URLS[self.env]
        self.token_url = discover(self.env)["token_endpoint"]
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
            self.token_url,
            headers={
                "Authorization": f"Basic {basic}",
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "refresh_token", "refresh_token": self.cfg["REFRESH_TOKEN"]},
        )
        if r.status_code != 200:
            tid = r.headers.get("intuit_tid", "?")
            logger.error("token refresh failed %s (tid=%s): %s",
                         r.status_code, tid, r.text[:500])
            raise SystemExit(
                f"token refresh failed ({r.status_code}, tid={tid}): {r.text}\n"
                "If this says invalid_grant the refresh token expired or was "
                "superseded — re-run authorize.py to get a new one."
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
            # intuit_tid is Intuit's per-request transaction id — the first thing
            # their support asks for. Capture it on every non-200 response.
            tid = r.headers.get("intuit_tid", "?")
            if r.status_code == 401 and attempt == 0:
                logger.info("401 on %s (tid=%s) — refreshing access token", path, tid)
                self.refresh()  # access token aged out mid-run
                continue
            if r.status_code == 429 or r.status_code >= 500:
                logger.warning("%s on %s (tid=%s), retry in %.0fs",
                               r.status_code, path, tid, delay)
                print(f"  [retry] {r.status_code} on {path} (tid={tid}), sleeping {delay:.0f}s")
                time.sleep(delay)
                delay *= 2
                continue
            msg = f"GET {path} -> {r.status_code} (tid={tid}): {r.text[:500]}"
            logger.error(msg)
            raise RuntimeError(msg)
        msg = f"GET {path} failed after {_retries} attempts"
        logger.error(msg)
        raise RuntimeError(msg)

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
