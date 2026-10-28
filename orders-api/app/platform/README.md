# `app/platform` — back-end foundation

The base classes this service is built on (design `docs/superpowers/specs/2026-10-23-order-bridge-design.md` §13). Order Bridge uses them first; whether they move into a shared package is decided after go-live (§14.7).

## Rules

- **One-way dependency.** Nothing here imports business code (`app.*` outside `app.platform`). `tests/platform/test_one_way_dependency.py` walks the AST of every module and fails on such an import. It also has a positive test proving the checker catches one.
- **No business names.** No partner, order, document-type, table or switch names appear here. Table names, the task prefix, the document type and the outbound switch names are passed in by the business code. The same test checks for them.
- **Extraction** means moving the folder, adding a package manifest and changing the imports. No code inside the folder changes.

## Modules

| Module | What it gives |
| --- | --- |
| `errors` | `BusinessError(code, message, details)` (409), `InvalidInput` (422), `NotFound` (404); `install_error_handlers(app)` → `{"error": {code, message, details}}`, 500 with a correlation id |
| `actors` | `Actor(user_id, name)`, `SYSTEM_ACTOR`, `actor_from_token(payload)` |
| `entities` | mixins `Entity`, `CreationAudited`, `Audited`, `Versioned` (optimistic lock), `SoftDeletable` (available, unused in v1; `partial_unique`), `Deactivatable` |
| `values` | `Quantity.to_base/to_ea(UnitTable)` (raises `MissingConversion`, never defaults to 1), `Money` (quantised, refuses mixed currencies) |
| `query` | `QuerySpec`, `SortKey`, `Filter`, `FilterRule`, `Page`; `query_spec` FastAPI dependency (`?page&page_size≤200&sort=a,-b&search&filter=f:op:v&include_historical`) |
| `repository` | `Repository[T]`: `get(for_update)`, `list(spec, open_work=)`, `add`, `soft_delete`; whitelisted sort/filter keys, historical exclusion, stable paging. No `delete` |
| `uow` | `SqlAlchemyUnitOfWork` / `UnitOfWorkFactory(session_factory, handlers=, audit=)`: commit = in-transaction event handlers, then COMMIT; leaving without commit rolls back |
| `events` | `DomainEvent`, `StatusChanged`, `EventBuffer`, `HandlerRegistry` |
| `audit` | `AuditTrail(model).attach(session)`: fills created/updated by from the actor; writes one row per change of an `Audited` entity (`AuditRecordMixin` columns) |
| `state` | `StateMachine(table, name, field=, initial=, holds=)`: `can`, `pairs`, `transition` (raises `state.invalid_transition`, emits `StatusChanged`); hold states remember `<field>_before_hold` |
| `services` | `ReadOnlyService`, `CrudService` (create / update with `exclude_unset` and version / deactivate; hooks `validate_create`, `before_update`, `after_delete`; `export_csv`; `import_csv` validates every row and is then all-or-nothing, with a `dry_run` plan), `CommandService` + `Command` |
| `crud_router` | `crud_router(prefix, ServiceClass, uow=, require=)` — list, get, create, update, deactivate, export.csv, import.csv |
| `outbox` | `Outbox(model)`: `add`, `requeue`; `OutboxWorker(outbox, kinds, uow_factory)`; `RetryPolicy`, `ONCE`, `retry_while_not_accepted(n, backoff)`; `NotAccepted`, `AcceptedThenFailed`; `OutboxRecordMixin` |
| `registry` | `Registry[K, V]` for strategy lookup (parsers, builders, senders) |
| `gateways` | `SftpGateway`, `EmailGateway` protocols, `RemoteFile`, `ExternalEmail`; `FakeSftp`, `FakeEmail`; `GuardedGateway(inner, settings=, guarded={method: switch}, allow=, on_refused=)` — the only reader of the outbound switches |
| `x12` | `Segment`, `Envelope(EnvelopeParams)`, `read(raw)`, `pad_control`, `same_control`, `ControlSequence(sequence_name)` |
| `pipeline` | `Step`, `CycleContext`, `StepResult`, `isolated(unit, fn, uow_factory=, log=)` (rolls back, reports, never re-raises), `run_steps` |
| `jobs` | `ScheduledJob` (advisory lock, RUNNING row with heartbeat, stale closure, interval/pause read every tick), `RunRecordMixin`, `Background` (`spawn` / `drain`) |
| `settings_registry` | `Setting(key, default, parse, validate)`, `SettingsRegistry(model)` with a short-lived cache; `SettingRecordMixin` |
| `tasks` | `TaskService(table=, prefix=, document_type=, assigned_role=, assignee=)`: `raise_problem` (one open task per problem key, in a savepoint), `resolve_problem`; `shared_tasks_table()` on its own MetaData |
| `container` | `gateway(settings, real=, fake=, guarded=)` — fake in the test environment, real behind the guard elsewhere |

## Retry policy per outbox kind (product owner, 2026-10-27)

- **File puts and API writes** use `ONCE`. A failure marks the row FAILED and calls `on_failed`; only a person retries, through `requeue`. On a manual retry the effect gets `ctx.manual=True`, so it can read the remote state first and write only what is missing.
- **External email** uses `retry_while_not_accepted(n)`. It is retried only on `NotAccepted`. On `AcceptedThenFailed` the row is DONE and the email is never resent.

## Third-party dependencies

SQLAlchemy (asyncio), asyncpg, pydantic, FastAPI. The real gateways will add asyncssh (SFTP), httpx (HTTP APIs) and aiosmtplib (email) when they are built in later phases.

## Tests

`tests/platform/` runs only in a throwaway container against a database whose name ends in `_test`, using its own `pt_` tables. Never run it with the host `.env`.
