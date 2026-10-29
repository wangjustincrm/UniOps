"""Rules of the connection configuration (design §3.1, §4.3)."""
from __future__ import annotations

from typing import Any

from app.platform.errors import InvalidInput

# An SFTP connection the intake can use needs these (an empty one is useless).
SFTP_REQUIRED = ("host", "username", "inbound_folder", "secret_name")


def check_connection(values: dict[str, Any]) -> None:
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
    # After-read Move needs somewhere to move to (FR-OB-74).
    if values.get("after_read") == "MOVE" and not values.get("processed_folder"):
        raise InvalidInput("connection.field_required", "a processed folder is required when after-read is MOVE",
                           {"field": "processed_folder"})
