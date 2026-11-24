"""Application services (design §13.5): one class per use case group, built on
the platform bases (CrudService, CommandService, ReadOnlyService) or plain
classes with `@requires` methods. Each declares the sales.* permission every
operation needs and checks it itself; none commits — the endpoint or job
that opened the unit of work does, once."""

from app.platform.services import ReadOnlyService  # noqa: E402

from app.platform.clock import business_timezone

# People read Sales' times in Toronto (the 940's "today", the schedules, the PDFs): so do its exports (user 2026-11-18).
ReadOnlyService.export_timezone = business_timezone()
