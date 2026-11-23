"""Auditing (≈ ABP's Volo.Abp.Auditing, user 2026-11-23): the audit log of each request and background scope, its
contributors, its store and the switches. See scope.py for the shape and the patterns."""
from app.platform.auditing.contributors import AuditLogContributor, add_contributor, contributors
from app.platform.auditing.decorators import audited, disable_auditing
from app.platform.auditing.middleware import AuditingMiddleware, RequestLineContributor
from app.platform.auditing.scope import AuditLogAction, AuditLogInfo, AuditScope, begin_scope, current_scope, should_save
from app.platform.auditing.store import AuditingStore, InMemoryAuditingStore, NullAuditingStore, current_store, use_store

add_contributor(RequestLineContributor())  # built in: the stdout line of a request

__all__ = ["AuditLogAction", "AuditLogContributor", "AuditLogInfo", "AuditScope", "AuditingMiddleware", "AuditingStore",
           "InMemoryAuditingStore", "NullAuditingStore", "add_contributor", "audited", "begin_scope", "contributors",
           "current_scope", "current_store", "disable_auditing", "should_save", "use_store"]
