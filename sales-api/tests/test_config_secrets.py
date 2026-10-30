"""A secret in the wrong column is refused with a fixed message and never
echoed (2026-10-29, the dev2 Connections CSV): not in an error response, an
import report, the log or the audit trail. Also: Excel's scientific notation
in a code or user name. The values below are synthetic stand-ins of the
shapes the user's file had."""
import csv
import io

import sqlalchemy as sa

from app.schemas.common import EXCEL_MESSAGE, HOST_KEY_MESSAGE, SECRET_NAME_MESSAGE
from tests.test_operations_api import C, FAKE_KEY, OP, api, conn_body  # noqa: F401 — api is a fixture

TOKEN = "shpat_" + "0123456789abcdef0123456789abcdef"   # a Shopify access token's shape
PASSWORD = "Hunter2!Winter#2026"                    # a password's shape
PASSWORD_WITH_SPACE = "correct horse battery"      # passed the old ^\S+ \S+ pattern
SECRETS = (TOKEN, PASSWORD, PASSWORD_WITH_SPACE)


def _csv(rows):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue().encode()


def _no_secret(text: str) -> None:
    for s in SECRETS:
        assert s not in text, f"a rejected value was echoed: {s!r}"


def field_errors(r):
    return {e["field"]: e["message"] for e in r.json()["error"]["details"]["errors"]}


async def test_a_token_or_password_as_host_key_is_refused_and_never_echoed(api):  # noqa: F811
    for value in SECRETS:
        r = await api.post(C, json=conn_body(host_key=value), headers=OP)
        assert r.status_code == 422
        assert field_errors(r) == {"host_key": HOST_KEY_MESSAGE}
        _no_secret(r.text)
    c = (await api.post(C, json=conn_body(host_key=FAKE_KEY + " admin@box"), headers=OP)).json()
    assert c["host_key"] == FAKE_KEY  # a real key line is accepted, its comment dropped


async def test_a_key_line_must_carry_its_own_type(api):  # noqa: F811
    rsa_typed = FAKE_KEY.replace("ssh-ed25519", "ssh-rsa")      # base64 says ed25519
    not_base64 = "ssh-ed25519 not*base64"
    for value in (rsa_typed, not_base64, "ssh-dss AAAAB3NzaC1kc3M="):
        r = await api.post(C, json=conn_body(host_key=value), headers=OP)
        assert r.status_code == 422 and field_errors(r) == {"host_key": HOST_KEY_MESSAGE}


async def test_shopify_takes_no_host_key(api):  # noqa: F811
    from tests.test_operations_api import SHOP as shop, shopify_partner
    await shopify_partner(api)
    r = await api.post(C, json=conn_body(name="Shopify", host_key=FAKE_KEY, **shop), headers=OP)
    assert r.status_code == 422 and r.json()["error"]["details"]["field"] == "host_key"
    assert "must be empty for a SHOPIFY connection" in r.json()["error"]["message"]
    assert (await api.post(C, json=conn_body(name="Shopify", **shop), headers=OP)).status_code == 201


async def test_secret_name_and_excel_numbers_get_a_clear_message(api):  # noqa: F811
    r = await api.post(C, json=conn_body(secret_name="Radiant"), headers=OP)
    assert field_errors(r) == {"secret_name": SECRET_NAME_MESSAGE}
    r = await api.post(C, json=conn_body(username="1.22E+11"), headers=OP)
    assert field_errors(r) == {"username": EXCEL_MESSAGE}
    for code in ("1.22E+11", "6.28e10"):
        r = await api.post("/api/v1/config/partners", headers=OP, json={
            "code": code, "name": "X", "channel": "MANUAL", "settlement_mode": "INVOICE", "sending_mode": "MANUAL",
            "delay_minutes": 0, "invoice_method": "NONE", "dc_routing_required": False})
        assert field_errors(r) == {"code": EXCEL_MESSAGE}
    assert (await api.post(C, json=conn_body(username="122000000000"), headers=OP)).status_code == 201


async def test_the_users_three_rows_are_refused_per_field_and_nothing_leaks(api, db_engine):  # noqa: F811
    base = {k: "" for k in ("inbound_folder", "outbound_folder", "processed_folder", "error_folder",
                            "test_inbound_folder", "test_outbound_folder")}
    rows = [
        {"name": "Shopify", "kind": "SHOPIFY", "host": "example.myshopify.com", "port": "443", "username": "",
         **base, "after_read": "LEAVE", "stable_seconds": "60", "mode": "TEST", "secret_name": "Shopify",
         "host_key": TOKEN, "active": "true"},
        {"name": "TrueCommerce", "kind": "SFTP", "host": "sftp.example.com", "port": "22", "username": "1.22E+11",
         **base, "inbound_folder": "/in", "after_read": "LEAVE", "stable_seconds": "60", "mode": "TEST",
         "secret_name": "TrueCommerce", "host_key": PASSWORD, "active": "true"},
        {"name": "Radiant", "kind": "SFTP", "host": "sftp.example.org", "port": "22", "username": "radiant",
         **base, "inbound_folder": "/in", "after_read": "LEAVE", "stable_seconds": "60", "mode": "TEST",
         "secret_name": "Radiant", "host_key": PASSWORD_WITH_SPACE, "active": "true"},
    ]
    for dry_run in ("true", "false"):
        r = await api.post(f"{C}/import.csv?dry_run={dry_run}", files={"file": ("c.csv", _csv(rows), "text/csv")},
                           headers=OP)
        assert r.status_code == 200
        rep = r.json()
        assert not rep["ok"] and not rep["written"] and rep["rows"] == []
        _no_secret(r.text)
        got = {(e["row"], e["column"]): e["message"] for e in rep["errors"]}
        assert got == {
            (1, "secret_name"): SECRET_NAME_MESSAGE, (1, "host_key"): HOST_KEY_MESSAGE,
            (2, "username"): EXCEL_MESSAGE, (2, "secret_name"): SECRET_NAME_MESSAGE, (2, "host_key"): HOST_KEY_MESSAGE,
            (3, "secret_name"): SECRET_NAME_MESSAGE, (3, "host_key"): HOST_KEY_MESSAGE,
        }
    async with db_engine.connect() as c:
        for table in ("sales_log", "sales_audit", "sales_connections"):
            dump = str((await c.execute(sa.text(f"SELECT * FROM {table}"))).all())
            _no_secret(dump)
        assert (await c.execute(sa.text("SELECT count(*) FROM sales_connections"))).scalar() == 0
