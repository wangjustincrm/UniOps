"""One-off OAuth authorization — replaces the Playground, which gates Production apps.

Intuit forbids localhost/IP redirect URIs on production keys, so this uses an
existing UniOps HTTPS domain. The path does NOT need to exist: the authorization
code arrives as a query parameter, so the browser address bar shows it even if
the page 404s. Copy that whole URL back into step 2.

Step 1 — print the authorization URL, open it in a browser, log in as the QBO admin:
    python scripts/qbo_import/authorize.py --url

Step 2 — paste the full redirected URL (address bar) to exchange it for tokens:
    python scripts/qbo_import/authorize.py --callback "https://finance.canadaroyalmilk.com/qbo-callback?code=XAB...&state=uniops-qbo&realmId=1234567890"

The authorization code is single-use and expires in ~10 minutes, so do step 2
right after step 1. On success REALM_ID and REFRESH_TOKEN are written into
C:\\Project\\qbo_conn.env and nothing else needs doing.
"""
import argparse
import base64
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.qbo_import.client import ENV_PATH, TOKEN_URL, load_cfg  # noqa: E402

AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
SCOPE = "com.intuit.quickbooks.accounting"
STATE = "uniops-qbo"


def _persist(key: str, value: str, path: Path = ENV_PATH) -> None:
    text = path.read_text(encoding="utf-8-sig")
    new, n = re.subn(rf"(?m)^{key}=.*$", f"{key}={value}", text)
    assert n == 1, f"expected exactly 1 {key} line in {path}, found {n}"
    path.write_text(new, encoding="utf-8")


def build_url(cfg: dict) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": cfg["CLIENT_ID"],
        "response_type": "code",
        "scope": SCOPE,
        "redirect_uri": cfg["REDIRECT_URI"],
        "state": STATE,
    })


def exchange(cfg: dict, code: str, realm_id: str) -> None:
    basic = base64.b64encode(
        f"{cfg['CLIENT_ID']}:{cfg['CLIENT_SECRET']}".encode()
    ).decode()
    r = httpx.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": cfg["REDIRECT_URI"],
        },
        timeout=60.0,
    )
    if r.status_code != 200:
        raise SystemExit(
            f"token exchange failed ({r.status_code}): {r.text}\n"
            "invalid_grant usually means the code was already used or is older "
            "than ~10 minutes — re-run --url and try again straight away.\n"
            "invalid_client means REDIRECT_URI here does not byte-match the one "
            "registered on the app's Keys & OAuth tab (trailing slash counts)."
        )
    payload = r.json()
    _persist("REALM_ID", realm_id)
    _persist("REFRESH_TOKEN", payload["refresh_token"])
    print(f"OK — REALM_ID={realm_id} and REFRESH_TOKEN written to {ENV_PATH}")
    print(f"     access token valid {payload.get('expires_in')}s, "
          f"refresh token valid {payload.get('x_refresh_token_expires_in')}s")
    print("\nNext: python scripts/qbo_import/sample_extract.py")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", action="store_true", help="print the authorization URL")
    ap.add_argument("--callback", help="the full redirected URL from the address bar")
    args = ap.parse_args()

    cfg = load_cfg()
    if not cfg.get("REDIRECT_URI"):
        raise SystemExit(f"REDIRECT_URI is empty in {ENV_PATH}")

    if args.url:
        print("1. Open this URL in a browser, sign in as the QuickBooks admin, "
              "and pick the CRM company:\n")
        print(build_url(cfg))
        print("\n2. You will land on a page that probably 404s — that is fine. "
              "Copy the FULL url from the address bar and run:\n")
        print('   python scripts/qbo_import/authorize.py --callback "<that url>"')
        return 0

    if args.callback:
        qs = parse_qs(urlparse(args.callback).query)
        code = (qs.get("code") or [None])[0]
        realm_id = (qs.get("realmId") or [None])[0]
        if not code or not realm_id:
            raise SystemExit(
                f"could not find both code and realmId in that URL "
                f"(got code={bool(code)}, realmId={bool(realm_id)}). "
                "Paste the whole address-bar URL, quoted."
            )
        exchange(cfg, code, realm_id)
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
