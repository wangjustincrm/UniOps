"""Service base classes (design §13.5): template methods with hooks. The
application services built on them live in app/services/.

Transactions: one per operation. Services work inside the caller's unit of
work and never commit — they flush at most. Only the outermost caller commits:
the HTTP endpoint (exactly once, at the end) or a job's unit (`isolated`).
A service calling another service shares the caller's unit of work.

Authorization: each service declares its keys (`read_permission`,
`write_permission`, `CommandService.permission`, or `@requires` on a method)
and checks them itself at the start of the operation, before anything is
written (app/platform/authorization.py). `system_allowed` lets the system
actor through; otherwise it is refused like a person without the key.
"""
from __future__ import annotations

import csv
import io
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.platform.actors import Actor
from app.platform.audit import jsonable
from app.platform.authorization import authorize
from app.platform.entities import Deactivatable, Versioned
from app.platform.errors import BusinessError, InvalidInput
from app.platform.query import Page, QuerySpec
from app.platform.repository import Repository
from app.platform.state import StateMachine

T = TypeVar("T")
R = TypeVar("R", bound=BaseModel)
C = TypeVar("C", bound=BaseModel)
U = TypeVar("U", bound=BaseModel)

EXPORT_PAGE = 200
# An import is validated in memory as a whole; bigger files are refused.
MAX_IMPORT_BYTES = 2 * 1024 * 1024
# Cells a spreadsheet would run as a formula (CSV injection). On export such a
# cell gets a leading apostrophe; on import a leading apostrophe before one of
# them is removed again, so an export round-trips.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


class ReadOnlyService(Generic[T, R]):
    repo_cls: ClassVar[type[Repository]]
    read_schema: ClassVar[type[BaseModel]]
    read_permission: ClassVar[str]
    # The system actor (jobs) may use this service's operations.
    system_allowed: ClassVar[bool] = False

    def __init__(self, uow) -> None:
        self.uow = uow
        self.repo = self.repo_cls(uow)

    async def _authorize(self, key: str) -> None:
        await authorize(self.uow, key, system=self.system_allowed)

    def to_read(self, entity: T) -> R:
        return self.read_schema.model_validate(entity, from_attributes=True)

    async def get(self, id: int) -> R:
        await self._authorize(self.read_permission)
        return self.to_read(await self.repo.get(id))

    async def list(self, spec: QuerySpec) -> Page[R]:
        await self._authorize(self.read_permission)
        page = await self.repo.list(spec)
        return Page[Any](items=[self.to_read(e) for e in page.items], total=page.total,
                         groups=page.groups, groups_truncated=page.groups_truncated)

    META_VERSION: ClassVar[int] = 1

    async def meta(self) -> dict[str, Any]:
        """The list's fields for the client (design §13.19): name, label, type,
        enum values, what each allows (filter / sort / group) and its
        operators — from the same declaration the list query uses."""
        await self._authorize(self.read_permission)
        repo = self.repo_cls
        return {"version": self.META_VERSION, "fields": repo.field_meta(self.read_schema),
                "searchable": bool(repo.search_fields), "default_sort": list(repo.default_sort),
                "views": [p.as_dict() for p in repo.presets]}


@dataclass
class RowIssue:
    row: int  # 1-based data row (the header is row 0)
    column: str | None
    code: str
    message: str


@dataclass
class RowPlan:
    row: int
    key: str
    action: str  # create | update | unchanged
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


@dataclass
class ImportReport:
    ok: bool
    dry_run: bool
    written: bool
    errors: list[RowIssue] = field(default_factory=list)
    rows: list[RowPlan] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out = {"create": 0, "update": 0, "unchanged": 0}
        for r in self.rows:
            out[r.action] += 1
        return out


class CrudService(ReadOnlyService[T, R], Generic[T, R, C, U]):
    create_schema: ClassVar[type[BaseModel]]
    update_schema: ClassVar[type[BaseModel]]
    write_permission: ClassVar[str]
    # CSV: the columns exported and accepted, and the natural key that decides
    # create vs update on import (e.g. "code").
    csv_columns: ClassVar[tuple[str, ...]] = ()
    # One column, or several for a composite natural key (e.g. partner + buyer part).
    csv_key: ClassVar[str | tuple[str, ...] | None] = None
    # Database unique constraints / indexes whose violation is a refusal, not
    # a bug: constraint name → (error code, field). The service's own check
    # gives the message first; this catches what races past it.
    unique_constraints: ClassVar[Mapping[str, tuple[str, str]]] = {}

    @property
    def model(self) -> type:
        return self.repo.model

    # ── hooks ────────────────────────────────────────────────────────────────
    async def validate_create(self, data: C) -> None:
        """Raise BusinessError to refuse."""

    async def before_update(self, entity: T, changes: dict[str, Any]) -> None:
        """`changes` holds only the fields the client sent; raise to refuse."""

    async def after_delete(self, entity: T) -> None:
        """Only for SoftDeletable entities (none in v1)."""

    def build(self, values: dict[str, Any]) -> T:
        """The new entity from validated values (create and CSV import). Override
        for values that are not plain columns (e.g. an item's units)."""
        return self.model(**values)

    async def apply_changes(self, entity: T, changes: dict[str, Any]) -> None:
        """Apply validated changes (update and CSV import); see `build`."""
        for k, v in changes.items():
            setattr(entity, k, v)

    @property
    def _key_columns(self) -> tuple[str, ...]:
        if not self.csv_key:
            return ()
        return (self.csv_key,) if isinstance(self.csv_key, str) else tuple(self.csv_key)

    # ── writes ───────────────────────────────────────────────────────────────
    def _reason(self, operation: str) -> None:
        """The audit rows of a CRUD write carry the operation as their reason,
        unless the caller already gave one."""
        if not self.uow.session.info.get("audit_reason"):
            self.uow.set_reason(operation)

    async def create(self, data: C) -> R:
        await self._authorize(self.write_permission)
        self._reason("create")
        await self.validate_create(data)
        entity = self.build(data.model_dump())
        try:
            async with self.uow.session.begin_nested():
                await self.repo.add(entity)
        except IntegrityError as e:
            raise self._unique_refusal(e) or e
        return self.to_read(entity)

    async def update(self, id: int, data: U, version: int | None = None) -> R:
        await self._authorize(self.write_permission)
        self._reason("update")
        entity = await self.repo.get(id, for_update=True)
        if isinstance(entity, Versioned):
            if version is None:
                raise InvalidInput("entity.version_required", "send the version you edited")
            if entity.version != version:
                raise BusinessError(
                    "entity.stale", "this record was changed by someone else; reload it",
                    {"current_version": entity.version, "sent_version": version},
                )
        # exclude_unset: a field sent as null is cleared, an absent field is untouched.
        changes = data.model_dump(exclude_unset=True)
        await self.before_update(entity, changes)
        await self.apply_changes(entity, changes)
        try:
            await self.uow.session.flush()
        except StaleDataError as e:
            raise BusinessError("entity.stale", "this record was changed by someone else; reload it") from e
        return self.to_read(entity)

    async def deactivate(self, id: int) -> R:
        await self._authorize(self.write_permission)
        self._reason("deactivate")
        entity = await self.repo.get(id, for_update=True)
        if not isinstance(entity, Deactivatable):
            raise TypeError(f"{type(entity).__name__} is not Deactivatable")
        entity.active = False
        await self.uow.session.flush()
        return self.to_read(entity)

    # ── CSV ──────────────────────────────────────────────────────────────────
    async def export_csv(self, spec: QuerySpec) -> bytes:
        await self._authorize(self.read_permission)
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        columns = self._csv_export_columns()
        w.writerow(columns)
        page_no = 1
        while True:
            page = await self.repo.list(spec.model_copy(update={"page": page_no, "page_size": EXPORT_PAGE}))
            for e in page.items:
                w.writerow([_csv_cell(self.csv_value(e, c)) for c in columns])
            if page_no * EXPORT_PAGE >= page.total:
                break
            page_no += 1
        return out.getvalue().encode("utf-8")

    async def import_csv(self, file: bytes, *, dry_run: bool) -> ImportReport:
        """Validates every row first. Any error: the full list, nothing
        written. Otherwise all rows are written in this unit of work (the
        caller commits once). `dry_run` returns the per-row plan only."""
        if not self.csv_key or not self.csv_columns:
            raise TypeError(f"{type(self).__name__} declares no CSV columns/key")
        await self._authorize(self.write_permission)
        report = ImportReport(ok=False, dry_run=dry_run, written=False)
        if len(file) > MAX_IMPORT_BYTES:
            report.errors.append(RowIssue(0, None, "csv.too_large",
                                          f"the file is larger than {MAX_IMPORT_BYTES // 1024} KB"))
            return report
        try:
            text = file.decode("utf-8-sig")
        except UnicodeDecodeError:
            report.errors.append(RowIssue(0, None, "csv.encoding", "the file is not UTF-8"))
            return report
        reader = csv.DictReader(io.StringIO(text))
        header = reader.fieldnames or []
        versioned = issubclass(self.model, Versioned)
        unknown = [h for h in header if h not in self._csv_export_columns()]
        for h in unknown:
            report.errors.append(RowIssue(0, h, "csv.unknown_column", f"unknown column {h!r}"))
        keys = self._key_columns
        for kc in keys:
            if kc not in header:
                report.errors.append(RowIssue(0, kc, "csv.missing_key", f"column {kc!r} is required"))
        if len(set(header)) != len(header):
            report.errors.append(RowIssue(0, None, "csv.duplicate_column", "a column appears twice"))
        if report.errors:
            return report

        # Keys match after trimming and case-folding ("abc " is "ABC"). Writing
        # locks the rows, so nothing changes between this read and the flush.
        q = select(self.model) if dry_run else select(self.model).with_for_update()
        existing: dict[str, Any] = {}
        ambiguous: set[str] = set()
        for e in (await self.uow.session.execute(q)).scalars():
            k = tuple(_norm_key(getattr(e, kc)) for kc in keys)
            if k in existing:
                ambiguous.add(k)
            existing[k] = e
        seen: set[str] = set()
        planned: list[tuple[RowPlan, Any, dict[str, Any]]] = []

        for i, raw in enumerate(reader, start=1):
            if None in raw:  # more cells than header columns
                report.errors.append(RowIssue(i, None, "csv.extra_cells", "the row has more cells than the header"))
                continue
            values = {k: _csv_in(v) for k, v in raw.items()}
            sent_version = values.pop("version", None)
            empty = [kc for kc in keys if values.get(kc) is None or not str(values[kc]).strip()]
            if empty:
                report.errors.append(RowIssue(i, empty[0], "csv.missing_key", "the key is empty"))
                continue
            for kc in keys:
                values[kc] = str(values[kc]).strip()
            key = " / ".join(values[kc] for kc in keys)
            nkey = tuple(_norm_key(values[kc]) for kc in keys)
            if nkey in seen:
                report.errors.append(RowIssue(i, keys[0], "csv.duplicate_key", f"{key!r} appears twice"))
                continue
            seen.add(nkey)
            if nkey in ambiguous:
                report.errors.append(RowIssue(i, keys[0], "csv.ambiguous_key",
                                              f"{key!r} matches more than one existing record"))
                continue
            current = existing.get(nkey)
            if current is not None and versioned and sent_version is not None:
                if str(current.version) != str(sent_version).strip():
                    report.errors.append(RowIssue(
                        i, "version", "entity.stale",
                        f"{key!r} was changed since this file was exported (version {current.version}, "
                        f"file has {sent_version}); export again"))
                    continue
            if current is not None:
                for kc in keys:
                    values[kc] = getattr(current, kc)  # keep the stored spelling
            try:
                if current is None:
                    data = self.create_schema.model_validate(values)
                    await self.validate_create(data)
                    changes = data.model_dump()
                    planned.append((RowPlan(i, key, "create", None, jsonable(changes)), None, changes))
                else:
                    # The key identifies the row; it is never an update field.
                    data = self.update_schema.model_validate(
                        {k: v for k, v in values.items() if k not in keys})
                    sent = data.model_dump(exclude_unset=True)
                    changes = {k: v for k, v in sent.items() if not self._same(current, k, v)}
                    if changes:
                        await self.before_update(current, changes)
                    before = {k: jsonable(getattr(current, k)) for k in changes}
                    action = "update" if changes else "unchanged"
                    planned.append((RowPlan(i, key, action, before, jsonable(changes)), current, changes))
            except ValidationError as e:
                for err in e.errors():
                    col = ".".join(str(p) for p in err["loc"]) or None
                    report.errors.append(RowIssue(i, col, "csv.invalid", err["msg"]))
            except BusinessError as e:
                report.errors.append(RowIssue(i, e.details.get("field"), e.code, e.message))

        report.rows = [p for p, _, _ in planned]
        if report.errors:
            report.rows = []
            return report
        report.ok = True
        if dry_run:
            return report
        self._reason("import.csv")

        # Row by row in savepoints, so a database constraint (another unique
        # column, a foreign key) is reported against its row. Any such error:
        # nothing is kept — the caller only commits an ok report.
        for plan, entity, changes in planned:
            if plan.action == "unchanged":
                continue
            try:
                async with self.uow.session.begin_nested():
                    if plan.action == "create":
                        self.uow.session.add(self.build(changes))
                    else:
                        await self.apply_changes(entity, changes)
                    await self.uow.session.flush()
            except DBAPIError as e:  # constraint, value too long, … (not a lost connection)
                if e.connection_invalidated:
                    raise
                refusal = self._unique_refusal(e)
                if refusal is not None:
                    report.errors.append(RowIssue(plan.row, refusal.details.get("field"), refusal.code, refusal.message))
                    continue
                detail = str(getattr(e.orig, "args", [e])[0]).splitlines()[0]
                report.errors.append(RowIssue(plan.row, None, "csv.rejected_by_database", detail))
            except StaleDataError:
                report.errors.append(RowIssue(plan.row, "version", "entity.stale",
                                              f"{plan.key!r} was changed by someone else; export again"))
        if report.errors:
            report.ok = False
            report.rows = []
            return report
        report.written = True
        return report

    def csv_value(self, entity: T, column: str) -> Any:
        """A column's export value (override for values that are not plain columns)."""
        return getattr(entity, column)

    def _same(self, entity: T, key: str, value: Any) -> bool:
        """Whether an imported value equals the stored one (override for values
        that are not plain columns)."""
        return getattr(entity, key) == value

    def _unique_refusal(self, e: DBAPIError) -> BusinessError | None:
        hit = self.unique_constraints.get(_constraint_name(e) or "")
        if hit is None:
            return None
        code, field = hit
        return BusinessError(code, f"a record with this {field} already exists", {"field": field})

    def _csv_export_columns(self) -> tuple[str, ...]:
        """The declared columns, plus `version` for a Versioned model so an
        edited export is refused when the record changed meanwhile."""
        if issubclass(self.model, Versioned) and "version" not in self.csv_columns:
            return (*self.csv_columns, "version")
        return self.csv_columns


def _csv_cell(v: Any) -> str:
    if v is None:
        return ""
    v = jsonable(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):  # a jsonb column: JSON text, so the import can read it back
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    s = str(v)
    if s.startswith(_FORMULA_START) and not _is_number(s):
        return "'" + s
    return s


def _csv_in(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if v.startswith("'") and v[1:].startswith(_FORMULA_START):
        return v[1:]
    return v


def _is_number(s: str) -> bool:
    try:
        Decimal(s)
    except ArithmeticError:
        return False
    return s.strip() == s


def _constraint_name(e: DBAPIError) -> str | None:
    """The violated constraint's name from the driver error (asyncpg keeps it
    on the cause, psycopg on `diag`)."""
    orig = getattr(e, "orig", None)
    for x in (orig, getattr(orig, "__cause__", None), getattr(orig, "diag", None)):
        name = getattr(x, "constraint_name", None)
        if name:
            return name
    return None


def _norm_key(v: Any) -> str:
    return str(v).strip().casefold()


@dataclass(kw_only=True)
class Command:
    aggregate_id: int
    reason: str | None = None


class CommandService(Generic[T]):
    """A state change on one aggregate. Aggregates are never edited by a
    generic PUT: each change is a command that checks its permission, locks
    the row, re-checks its rule, transitions through the machine and flushes
    (the audit rows are written then). It does not commit: the caller that
    opened the unit of work does, once.

    Subclasses set `repo_cls`, `machine` and `permission` and implement
    `target`; `check` and `apply` are hooks. Runs inside an entered unit of
    work."""

    repo_cls: ClassVar[type[Repository]]
    machine: ClassVar[StateMachine]
    permission: ClassVar[str]
    system_allowed: ClassVar[bool] = False

    def __init__(self, uow) -> None:
        self.uow = uow
        self.repo = self.repo_cls(uow)

    def target(self, agg: T, cmd: Command) -> Any:
        raise NotImplementedError

    def check(self, agg: T, cmd: Command) -> None:
        """Re-check the rule on the locked row; raise BusinessError to refuse."""

    async def apply(self, agg: T, cmd: Command) -> None:
        """Further changes and events after the transition."""

    @property
    def actor(self) -> Actor:
        return self.uow.actor

    async def execute(self, cmd: Command) -> T:
        await authorize(self.uow, self.permission, system=self.system_allowed)
        agg = await self.repo.get(cmd.aggregate_id, for_update=True)  # SELECT … FOR UPDATE
        self.check(agg, cmd)
        self.uow.set_reason(cmd.reason or type(cmd).__name__)
        self.machine.transition(
            agg, self.target(agg, cmd), actor=self.actor, reason=cmd.reason, events=self.uow.events,
        )
        await self.apply(agg, cmd)
        await self.uow.session.flush()
        return agg


def changed_columns(entity: Any) -> list[str]:
    """Names of column attributes with pending changes (useful in hooks)."""
    state = inspect(entity)
    return [a.key for a in state.mapper.column_attrs if state.attrs[a.key].history.has_changes()]
