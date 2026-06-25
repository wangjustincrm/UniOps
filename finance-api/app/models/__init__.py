"""Model package. Importing AdminAuditLog registers it with Base.metadata
(shared admin_audit_log table — no finance-api migration owns it)."""
from app.models.admin_audit_log import AdminAuditLog  # noqa: F401

__all__ = ["AdminAuditLog"]
