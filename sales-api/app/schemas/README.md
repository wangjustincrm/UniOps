# `app/schemas` — DTOs

Request and response models (pydantic) — `<Thing>Create`, `<Thing>Update`, `<Thing>Read`. Shape validation only: types, lengths (matching the column widths), required fields, enums. A rule that needs another record, a setting or a status (e.g. "invoice email required when the method is EMAIL", FR-OB-33) is a business rule and goes in `app/domain` or the service's `validate_create` / `before_update`, never in a validator here.

First resource (P1, 2026-10-29): `partners.py`. The closed sets come from `app/domain/config.py` (the CHECK constraints use the same tuples; a test pins the DTOs' Literal types to them).
