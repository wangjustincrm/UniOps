"""This service's authz gate, bound to its own DB/token dependencies.

Import require_permission FROM HERE (app.core.deps also re-exports it, for
existing call sites) — the package needs this service's own get_session /
get_current_user_payload dependencies wired in; it deliberately does not
expose a module-level require_permission because those dependency names
differ across services.
"""
from uniops_authz import bind

from app.core.deps import get_current_user_payload, get_session

require_permission = bind(get_session, get_current_user_payload)
