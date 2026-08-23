"""Resolve remittance sender settings from company_config.

Server parameters are shared with the rest of the system (outbound po_smtp_*
preferred, internal smtp_* as fallback). The From address is always the
remittance-specific one — never the shared smtp_from — with optional
credential overrides for servers that reject a mismatched From.
"""
import logging
import re
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.remittance import normalize_recipients

logger = logging.getLogger(__name__)

# Light sanity guard, not a full validator: logo_data_url is admin-uploaded
# (system_admin-gated, same trust tier as the rest of remittance_config.template
# per remittance_template.py's docstring) but is interpolated RAW into
# `<img src="...">` with no escaping (see render()'s logo_html) — a value
# containing a `"` could close the attribute and inject markup/JS into every
# remittance email. Only surface values that look like an image data URI or an
# http(s) URL; anything else (including one crafted with a stray quote) is
# dropped to None rather than rendered.
_LOGO_URL_RE = re.compile(r"^(data:image/[a-zA-Z0-9.+-]+;|https?://)", re.IGNORECASE)


def _safe_logo_url(value: str | None) -> str | None:
    if isinstance(value, str) and _LOGO_URL_RE.match(value) and '"' not in value:
        return value
    return None


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
    template: dict = field(default_factory=dict)
    logo_data_url: str | None = None


def _cc_email(cfg: dict) -> str | None:
    """The standing CC on every remittance email, as an RFC 5322 address-list.

    A malformed CC does NOT fail the send the way a malformed payee address
    does — To and Cc are parsed as separate headers, so the payee is still
    accepted and aiosmtplib only raises when EVERY recipient is refused. The
    finance copy would simply never arrive, while the log recorded a
    successful send. Dropping an unusable value (loudly) beats shipping a
    header that silently swallows it.
    """
    normalized, ok = normalize_recipients((cfg.get("cc_email") or "").strip())
    if not normalized:
        return None
    if not ok:
        logger.error("remittance cc_email is not a usable address list (%r) — "
                     "sending without a CC", normalized)
        return None
    return normalized


async def load(db: AsyncSession) -> RemittanceSettings | None:
    """None when remittance is switched off or not configured well enough to
    send (no from address, or no SMTP host anywhere)."""
    row = (await db.execute(sa.text(
        "SELECT remittance_config, logo_data_url, po_smtp_host, po_smtp_port,"
        " po_smtp_user, po_smtp_password, po_smtp_use_tls, smtp_host, smtp_port,"
        " smtp_user, smtp_password, smtp_use_tls FROM company_config LIMIT 1"
    ))).mappings().first()
    if row is None:
        return None

    cfg = row["remittance_config"] or {}
    if not cfg.get("enabled"):
        return None
    from_email = (cfg.get("from_email") or "").strip()
    if not from_email:
        return None

    # Per-field fallback from the outbound po_smtp_* profile to the internal
    # smtp_* profile — matches epms-api/app/api/v1/po.py. Each field falls
    # back independently (`is not None`, not truthiness) so an admin can
    # override just the host/user/password while still inheriting the
    # internal profile's port and TLS mode, and so an explicit
    # po_smtp_use_tls=False is honoured rather than falling through.
    host = row["po_smtp_host"] if row["po_smtp_host"] is not None else row["smtp_host"]
    host = host or ""
    if not host:
        return None
    port = row["po_smtp_port"] if row["po_smtp_port"] is not None else row["smtp_port"]
    port = port or 587
    user = row["po_smtp_user"] if row["po_smtp_user"] is not None else row["smtp_user"]
    password = row["po_smtp_password"] if row["po_smtp_password"] is not None else row["smtp_password"]
    use_tls = row["po_smtp_use_tls"] if row["po_smtp_use_tls"] is not None else row["smtp_use_tls"]

    return RemittanceSettings(
        enabled=True,
        from_email=from_email,
        from_name=(cfg.get("from_name") or "").strip(),
        cc_email=_cc_email(cfg),
        smtp_host=host, smtp_port=int(port),
        smtp_user=cfg.get("smtp_user") or user,
        smtp_password=cfg.get("smtp_password") or password,
        smtp_use_tls=bool(use_tls),
        template=cfg.get("template") or {},
        logo_data_url=_safe_logo_url(row["logo_data_url"]),
    )
