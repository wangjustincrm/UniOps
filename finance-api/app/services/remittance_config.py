"""Resolve remittance sender settings from company_config.

Server parameters are shared with the rest of the system (outbound po_smtp_*
preferred, internal smtp_* as fallback). The From address is always the
remittance-specific one — never the shared smtp_from — with optional
credential overrides for servers that reject a mismatched From.
"""
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class RemittanceSettings:
    enabled: bool
    from_email: str
    from_name: str
    cc_email: str | None
    smtp_host: str
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    smtp_use_tls: bool


async def load(db: AsyncSession) -> RemittanceSettings | None:
    """None when remittance is switched off or not configured well enough to
    send (no from address, or no SMTP host anywhere)."""
    row = (await db.execute(sa.text(
        "SELECT remittance_config, po_smtp_host, po_smtp_port, po_smtp_user,"
        " po_smtp_password, po_smtp_use_tls, smtp_host, smtp_port, smtp_user,"
        " smtp_password, smtp_use_tls FROM company_config LIMIT 1"
    ))).mappings().first()
    if row is None:
        return None

    cfg = row["remittance_config"] or {}
    if not cfg.get("enabled"):
        return None
    from_email = (cfg.get("from_email") or "").strip()
    if not from_email:
        return None

    use_po = bool(row["po_smtp_host"])
    host = (row["po_smtp_host"] if use_po else row["smtp_host"]) or ""
    if not host:
        return None
    port = (row["po_smtp_port"] if use_po else row["smtp_port"]) or 587
    user = row["po_smtp_user"] if use_po else row["smtp_user"]
    password = row["po_smtp_password"] if use_po else row["smtp_password"]
    use_tls = row["po_smtp_use_tls"] if use_po else row["smtp_use_tls"]

    return RemittanceSettings(
        enabled=True,
        from_email=from_email,
        from_name=(cfg.get("from_name") or "").strip(),
        cc_email=(cfg.get("cc_email") or "").strip() or None,
        smtp_host=host, smtp_port=int(port),
        smtp_user=cfg.get("smtp_user") or user,
        smtp_password=cfg.get("smtp_password") or password,
        smtp_use_tls=bool(use_tls),
    )
