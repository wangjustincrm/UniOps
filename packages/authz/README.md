# uniops-authz

Shared authorization gate for UniOps backend services.

## Why this exists

Phase 1 made identity the source of truth for the role x permission matrix,
but only the frontend obeyed it — backends still hardcode role tuples
(`require_roles("system_admin", "finance_manager", ...)`). This package makes
the matrix gate the backends too.

Every service shares **one physical database**, so this package reads
identity's `role_permissions` / `role_permission_locks` tables directly —
no HTTP call to identity-api, no auth token, no cache. That is deliberate:

- If the identity-api *service* is down, permission checks still work; only a
  database outage stops them, and a database outage stops everything anyway.
- It is what lets phase 1's `authz_client` apparatus (HTTP client + 60s cache
  + outage fallback + write-through mirror) retire once every service is
  wired to this package instead.

Should the services ever be split across separate databases, only the
internals of `core.py` need to change to an HTTP client — consumers import
`bind`, `effective_permissions`, `user_role_codes` and will not notice.

## Semantics (pinned by `tests/test_core.py`)

- **Effective matrix = granted UNION locked.** A row in
  `role_permission_locks` is a forced grant that the Access Control Matrix UI
  cannot clear, so it counts even without a matching `role_permissions` row.
- **Role union = `users.role` (primary) UNION `user_roles` (additional).**
  Consulting only `user_roles` silently loses primary-role holders.
- **Inactive roles are ignored.** An additional role via `user_roles` whose
  `role_defs.is_active` is false contributes no permissions.
- **Every known permission key resolves to `True`/`False`, never missing.**
  Keys come from `permission_defs`, so callers can `.get(key)` without
  worrying whether an ungranted key is `False` or absent.
- **`system_admin` short-circuits.** Every `require_roles()` helper being
  replaced starts with `if role == "system_admin": return payload` —
  `bind()`'s `require_permission` preserves that exactly, without a DB lookup.

## Usage

Each service injects its own DB-session and token-payload dependencies, so
this package cannot export a module-level `require_permission` — the
dependency names differ across services (epms `get_session` /
`get_token_payload`, finance `get_db` / `get_token_payload`, ...). Instead,
call `bind()` once per service to get back a `require_permission(key)`
factory bound to that service's own dependencies:

```python
# app/core/authz.py
from uniops_authz import bind
from app.core.deps import get_session, get_token_payload

require_permission = bind(get_session, get_token_payload)
```

```python
# app/api/purchase_orders.py
from app.core.authz import require_permission

@router.post("/", dependencies=[Depends(require_permission("epms.po.write"))])
async def create_po(...):
    ...
```

`effective_permissions(db, user_id, base_role)` and
`user_role_codes(db, user_id, base_role)` are also exported directly for
callers that need the full matrix rather than a single-key gate (e.g. the
`/me/permissions` endpoint the frontend reads).

## Running tests

Needs a Python environment with sqlalchemy, fastapi, asyncpg, psycopg2,
pytest, and pytest-asyncio — e.g. epms-api's or identity-api's venv. Install
the package into it first:

```bash
<python> -m pip install -e /c/Project/uniops/packages/authz
cd /c/Project/uniops/packages/authz
TEST_PG_PASSWORD=<password> <python> -m pytest tests -q
```

Tests build their own `authz_test` database with minimal raw-SQL shadow
tables (no ORM models — this package has none by design) mirroring the
columns identity's physical tables actually have.
