# `app/services` — application layer

Use cases live here (STEP 1, 2026-10-29). Each service:

- works in the caller's unit of work (`self.uow`) and **never commits** — it flushes at most; the HTTP endpoint (once, at the end) or the job's unit (`isolated`) commits;
- **declares its permission** — `read_permission` / `write_permission` (CRUD), `permission` (command), or `@requires("sales.…")` on a method — and checks it before reading or writing anything; `system=True` / `system_allowed = True` only where the scheduler runs it;
- takes and returns DTOs from `app/schemas`; business rules come from `app/domain` or live in the service, never in a schema;
- calls outside systems only through `app/integrations` (and external writes only through the outbox).

First services (P1, 2026-10-29): `partners.py` (`PartnerService` on the platform's `CrudService`; read `sales.read`, write `sales.config`) and `audit.py` (a record's history from `sales_audit`, read with the record's own read permission).
