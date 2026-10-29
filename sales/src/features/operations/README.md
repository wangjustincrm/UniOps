# features/operations

The module's own records: the document register (every file read or written, with its original bytes — FR-OB-05 / -70..76) and the scheduler's runs with the log (FR-OB-07, FR-OB-53). Read with `sales.read`; "Run now" needs `sales.file.retry`. Built from `@/platform-ui` only.

- `DocumentsPage` (P2) — register with search and filters; a document's details and its original bytes. Reparse / resend / ignore arrive with the phases that parse and send.
- `RunsPage` (P2) — runs with their pass report per connection (FR-OB-76), Run now, and the log searchable by text, level and run.
