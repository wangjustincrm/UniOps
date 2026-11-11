"""Rules of the connection configuration (design §3.1, §4.3)."""
from __future__ import annotations

from typing import Any

from app.platform.errors import InvalidInput

# An SFTP connection the intake can use needs these (an empty one is useless).
SFTP_REQUIRED = ("host", "username", "inbound_folder", "secret_name")


def _same_folder(a: Any, b: Any) -> bool:
    return "/" + str(a or "").strip().strip("/") == "/" + str(b or "").strip().strip("/")


def check_connection(values: dict[str, Any]) -> None:
    # A Test connection writes only into its test outbound folder: set to the real
    # folder, a Test run would deliver to the partner (the write guard refuses it too).
    if str(values.get("test_outbound_folder") or "").strip("/ ") and _same_folder(
            values.get("test_outbound_folder"), values.get("outbound_folder")):
        raise InvalidInput("connection.test_folder_is_real",
                           "the test outbound folder must not be the real outbound folder: leave it empty when the "
                           "partner has no test folder (nothing is then sent in Test mode)", {"field": "test_outbound_folder"})
    if values.get("kind") == "SFTP":
        for field in SFTP_REQUIRED:
            if not values.get(field):
                raise InvalidInput("connection.field_required", f"{field} is required for an SFTP connection",
                                   {"field": field})
    # A host key is pinned for SFTP only; for SHOPIFY the field must stay
    # empty (a token put there by mistake must never be stored). The message
    # never repeats the value.
    if values.get("kind") == "SHOPIFY" and values.get("host_key"):
        raise InvalidInput("connection.host_key_not_for_shopify",
                           "host_key must be empty for a SHOPIFY connection. Passwords and tokens never go in "
                           "the CSV; put them in the server environment under secret_name.",
                           {"field": "host_key"})
    # A Shopify channel names its store, API version and partner (option 1,
    # 2026-10-29); an SFTP channel takes its partner from each file.
    if values.get("kind") == "SHOPIFY":
        host = str(values.get("host") or "").strip().lower()
        if not host.endswith(".myshopify.com"):
            raise InvalidInput("connection.shopify_host", "host must be the store's <name>.myshopify.com address",
                               {"field": "host"})
        for field in ("api_version", "partner_code"):
            if not values.get(field):
                raise InvalidInput("connection.field_required", f"{field} is required for a SHOPIFY connection",
                                   {"field": field})
    elif values.get("partner_code"):
        raise InvalidInput("connection.partner_shopify_only",
                           "partner_code is for SHOPIFY connections only; an SFTP channel's partner comes from each file",
                           {"field": "partner_code"})
    # After-read Move needs somewhere to move to (FR-OB-74).
    if values.get("after_read") == "MOVE" and not values.get("processed_folder"):
        raise InvalidInput("connection.field_required", "a processed folder is required when after-read is MOVE",
                           {"field": "processed_folder"})
