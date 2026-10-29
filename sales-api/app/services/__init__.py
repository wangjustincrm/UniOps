"""Application services (design §13.5): one class per use case group, built on
the platform bases (CrudService, CommandService, ReadOnlyService) or plain
classes with `@requires` methods. Each declares the sales.* permission every
operation needs and checks it itself; none commits — the endpoint or job
that opened the unit of work does, once."""
