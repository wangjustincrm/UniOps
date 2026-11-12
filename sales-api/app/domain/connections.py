"""Rules of the connection configuration (design §3.1, §4.3)."""
from __future__ import annotations

from typing import Any

from app.platform.errors import InvalidInput

# An SFTP connection needs these (an empty one is useless); its folders by what it may do.
SFTP_REQUIRED = ("host", "username", "secret_name")


def _same_folder(a: Any, b: Any) -> bool:
    return "/" + str(a or "").strip().strip("/") == "/" + str(b or "").strip().strip("/")


def check_connection(values: dict[str, Any]) -> None:
    if values.get("kind") == "SFTP":
        for field in SFTP_REQUIRED:
            if not values.get(field):
                raise InvalidInput("connection.field_required", f"{field} is required for an SFTP connection",
                                   {"field": field})
        # Reading has its folder, its archive (after-read Move) and its error folder; writing has one folder.
        if values.get("can_read", True) and not values.get("inbound_folder"):
            raise InvalidInput("connection.field_required", "a read folder is required to read this connection",
                               {"field": "inbound_folder"})
        if values.get("can_write") and not values.get("outbound_folder"):
            raise InvalidInput("connection.field_required", "a write folder is required to write to this connection",
                               {"field": "outbound_folder"})
        for field in ("inbound_folder", "processed_folder", "error_folder"):
            if values.get(field) and values.get("outbound_folder") and _same_folder(values[field], values["outbound_folder"]):
                raise InvalidInput("connection.folder_is_write_folder",
                                   "the read, archive and error folders must not be the write folder: Sales would read "
                                   "its own files back, or the partner would read what Sales moved there", {"field": field})
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
        raise InvalidInput("connection.field_required", "an archive folder is required when after-read is Move",
                           {"field": "processed_folder"})
