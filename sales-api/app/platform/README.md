# `app/platform` — back-end foundation

The base classes this service is built on (design `docs/superpowers/specs/2026-10-23-order-bridge-design.md` §13). The Sales module uses them first; whether they move into a shared package is decided after go-live (§14.7).

## Layers of this service

```
app/api           HTTP endpoints — thin; a write endpoint commits exactly once, at the end
app/services      application services (use cases); declare and check their permissions
app/schemas       DTOs (pydantic): shape only — types, lengths, required fields
app/domain        business rules and the status machines
app/models        SQLAlchemy models (sales_* tables)
app/integrations  outside systems: SFTP, external email, X12, the shared EPMS tasks table
app/platform      this folder: generic base classes, no business or integration knowledge
app/core, app/db  settings, auth dependencies, engine/session
```

One-way: `platform` imports nothing else of `app`; `integrations` may import `platform`, never `domain`, `services` or `api`. `tests/platform/test_one_way_dependency.py` enforces both, each with a positive half.

## Transaction contract (one transaction per operation)

- Only the **outermost caller** commits: the HTTP endpoint (exactly once, at the end; `get_uow` never commits by itself) or a job's unit (`pipeline.isolated` — one unit per connection, document or order, FR-OB-50).
- Services, `CommandService.execute` and the step functions a job runs **never commit**; they flush at most. A service that calls another passes its unit of work along.
- The single exception is the outbox worker: it commits the claim (IN_FLIGHT) and the outcome in two transactions of its own, for at-most-once.
- `tests/platform/test_transactions_and_authorization.py` proves that an operation failing in its second service leaves no row, audit row or outbox row, that a command alone commits nothing, and (statically) that every write endpoint commits exactly once.

## Authorization at the service

Each service declares its keys — `read_permission` / `write_permission` on CRUD services, `permission` on a `CommandService`, `@requires(key)` on any other method — and checks them itself before it reads or writes anything (`authorization.authorize`), so a job or another service calling it is gated the same way. The system actor passes only where `system_allowed = True` / `@requires(key, system=True)` says so. Routers enforce nothing of their own. The unit of work factory carries the checker (`permissions=`); the business code wires the UniOps role matrix there.

## Rules

- **One-way dependency.** Nothing here imports business code (`app.*` outside `app.platform`). `tests/platform/test_one_way_dependency.py` walks the AST of every module and fails on such an import. It also has a positive test proving the checker catches one.
- **No business names.** No partner, order, document-type, table or switch names appear here. Table names, the task prefix, the document type and the outbound switch names are passed in by the business code. The same test checks for them.
- **Extraction** means moving the folder, adding a package manifest and changing the imports. No code inside the folder changes.

## Modules

| Module | What it gives |
| --- | --- |
| `errors` | `BusinessError(code, message, details)` (409), `InvalidInput` (422), `NotFound` (404); `install_error_handlers(app)` → `{"error": {code, message, details}}`; a request the schemas refuse → `request.invalid` (422) with `details.errors` = `[{field, message}]` and `details.field`; 500 with a correlation id |
| `actors` | `Actor(user_id, name, role)`, `SYSTEM_ACTOR`, `actor_from_token(payload)` |
| `authorization` | `authorize(uow, key, system=)`, `@requires(key, system=)`, `PermissionDenied` (403), `PermissionChecker` |
| `entities` | mixins `Entity`, `CreationAudited`, `Audited`, `Versioned` (optimistic lock), `SoftDeletable` (available, unused in v1; `partial_unique`), `Deactivatable` |
| `values` | `Quantity.to_base/to_ea(UnitTable)` (raises `MissingConversion` / `FractionalQuantity`, never defaults to 1), `Money` (full precision; `rounded`, `line_total`, `total`; refuses mixed currencies and floats) |
| `query` | `QuerySpec`, `SortKey`, `Filter`, `FilterRule`, `Page`; `query_spec` FastAPI dependency (`?page&page_size≤200&sort=a,-b&search&filter=f:op:v&include_historical`) |
| `repository` | `Repository[T]`: `get(for_update)`, `list(spec, open_work=)`, `add`, `soft_delete`; whitelisted sort/filter keys, historical exclusion, stable paging. No `delete` |
| `uow` | `SqlAlchemyUnitOfWork` / `UnitOfWorkFactory(session_factory, handlers=, audit=, permissions=)`: commit = in-transaction event handlers, then COMMIT; leaving without commit rolls back |
| `events` | `DomainEvent`, `StatusChanged`, `EventBuffer`, `HandlerRegistry` |
| `audit` | `AuditTrail(model).attach(session)`: fills created/updated by from the actor; writes one row per change of an `Audited` entity (`AuditRecordMixin` columns) |
| `state` | `StateMachine(table, name, field=, initial=, holds=)`: `can`, `pairs`, `transition` (raises `state.invalid_transition`, emits `StatusChanged`); hold states remember `<field>_before_hold` |
| `services` | `ReadOnlyService`, `CrudService` (create / update with `exclude_unset` and version / deactivate; hooks `validate_create`, `before_update`, `after_delete`; `export_csv`; `import_csv` validates every row and is then all-or-nothing, with a `dry_run` plan; the key column is never an update field, a jsonb cell exports as JSON; a CRUD write's audit rows carry the operation — `create`, `update`, `deactivate`, `import.csv` — as their reason unless the caller set one), `CommandService` + `Command` (checks `permission`, locks, transitions, flushes — never commits). All check their declared keys first |
| `crud_router` | `crud_router(prefix, ServiceClass, uow=)` — list, get, create, update, deactivate, export.csv, import.csv; no permission check of its own; each write endpoint commits once. `read_router(prefix, ReadOnlyServiceClass, uow=)` — list, get |
| `outbox` | `Outbox(model)`: `add`, `requeue`; `OutboxWorker(outbox, kinds, uow_factory)`; `RetryPolicy`, `ONCE`, `retry_while_not_accepted(n, backoff)`; `NotAccepted`, `AcceptedThenFailed`; `OutboxRecordMixin` |
| `registry` | `Registry[K, V]` for strategy lookup (parsers, builders, senders) |
| `guard` | `GuardedGateway(inner, settings=, guarded={method: switch}, reads=, allow=, summarise=, on_refused=)` — an allow-list, the only reader of the outbound switches; `Suppressed`, `Refused`. It knows no integration: each integration module supplies its own write/read lists |
| `pipeline` | `Step`, `CycleContext`, `StepResult`, `isolated(unit, fn, uow_factory=, log=)` (a job's unit boundary: commits the unit, or rolls it back and reports; never re-raises), `run_steps` |
| `jobs` | `ScheduledJob` (advisory lock, RUNNING row with heartbeat, stale closure, interval/pause read every tick; `tick` = `claim` + `execute`, so a "Run now" claims in the request and runs in the background; an optional `scope` makes single flight per (job, scope); hooks `due` — when an unforced claim runs — and `on_closed`), `RunRecordMixin` (with `scope`), `Background` (`spawn` / `drain`) |
| `settings_registry` | `Setting(key, default, parse, validate)`, `SettingsRegistry(model)` with a short-lived cache; `SettingRecordMixin` |
| `container` | `gateway(settings, real=, fake=, guarded=)` — fake in the test environment, real behind the guard elsewhere |

## Retry policy per outbox kind (product owner, 2026-10-27)

- **File puts and API writes** use `ONCE`. A failure marks the row FAILED and calls `on_failed`; only a person retries, through `requeue`. On a manual retry the effect gets `ctx.manual=True`, so it can read the remote state first and write only what is missing.
- **External email** uses `retry_while_not_accepted(n)`. It is retried only on `NotAccepted`. On `AcceptedThenFailed` the row is DONE and the email is never resent.

Moved out to `app/integrations/` (STEP 1, 2026-10-29): `sftp` and `email` (protocols, fakes, guarded/read lists, summaries), `x12`, `epms_tasks` (the shared Task Inbox table and `TaskService`).

## Third-party dependencies

SQLAlchemy (asyncio), asyncpg, pydantic, FastAPI. The real gateways will add asyncssh (SFTP), httpx (HTTP APIs) and aiosmtplib (email) when they are built in later phases.

## Tests

`tests/platform/` runs only in a throwaway container against a database whose name ends in `_test`, using its own `pt_` tables. Never run it with the host `.env`.
