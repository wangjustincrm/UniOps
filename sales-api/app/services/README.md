# `app/services` — application layer

Use cases live here (STEP 1, 2026-10-29). Each service:

- works in the caller's unit of work (`self.uow`) and **never commits** — it flushes at most; the HTTP endpoint (once, at the end) or the job's unit (`isolated`) commits;
- **declares its permission** — `read_permission` / `write_permission` (CRUD), `permission` (command), or `@requires("sales.…")` on a method — and checks it before reading or writing anything; `system=True` / `system_allowed = True` only where the scheduler runs it;
- takes and returns DTOs from `app/schemas`; business rules come from `app/domain` or live in the service, never in a schema;
- calls outside systems only through `app/integrations` (and external writes only through the outbox).

Empty until P0 brings the first configuration services.
