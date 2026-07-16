"""Shared authorization gate for UniOps backend services.

Every service shares one physical database, so this reads identity's
role_permissions / role_permission_locks directly — no HTTP, no token, no
cache. If identity-api the *service* is down, this still works; only a DB
outage stops it, and that stops everything anyway.

Should the services ever be split across databases, swap the internals of
core.py for an HTTP client — consumers import only these two names and will
not notice.
"""
from uniops_authz.core import (  # noqa: F401
    bind,
    effective_permissions,
    role_matrix,
    user_role_codes,
)
