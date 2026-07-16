"""This service's authz gate, bound to its own DB/token dependencies.

Import require_permission FROM HERE — the package needs this service's own
get_db / get_token_payload dependencies wired in; it deliberately does not
expose a module-level require_permission because those dependency names
differ across services.
"""
from uniops_authz import bind

from app.core.deps import get_token_payload
from app.db.base import get_db

require_permission = bind(get_db, get_token_payload)
