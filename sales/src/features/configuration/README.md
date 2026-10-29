# features/configuration

Configuration: partners, items, DC routing, connections, settings, notifications — CrudPage screens (FR-OB-09). Built from `@/platform-ui` only (patterns and primitives); no direct `@uniops/shell` import.

- `partners/` (P1, 2026-10-29) — Configuration → Partners on `CrudPage`: list with search / sort, form from a zod schema mirroring the API's DTO (FR-OB-33 included; the server's refusals appear on their field), read-only view for readers without `sales.config`, the partner's audit history under the form, CSV export and import with a dry-run preview. Types in `partnersApi.ts` are hand-written mirrors of `sales-api/app/schemas/partners.py` until OpenAPI type generation is set up.
