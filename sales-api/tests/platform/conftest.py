"""Fixtures for the platform tests: their own tables on their own MetaData
(`pt_` prefix), created in the `_test` database after the alembic run. They
stand in for the business tables, which the platform never names."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Numeric, String
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.platform.audit import AuditRecordMixin, AuditTrail
from app.platform.entities import Audited, CreationAudited, Deactivatable, Entity, SoftDeletable, Versioned
from app.platform.jobs import RunRecordMixin
from app.platform.outbox import OutboxRecordMixin
from app.platform.query import FilterRule
from app.platform.repository import Repository, parse_bool
from app.platform.services import CrudService
from app.platform.settings_registry import SettingRecordMixin
from app.integrations.epms_tasks import shared_tasks_table
from app.platform.uow import UnitOfWorkFactory
from tests.conftest import SYNC_URL


class PTBase(DeclarativeBase):
    pass


class Widget(Audited, Versioned, Deactivatable, PTBase):
    __tablename__ = "pt_widgets"
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(100), nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    historical: Mapped[bool] = mapped_column(default=False)


class Note(Entity, SoftDeletable, PTBase):
    __tablename__ = "pt_notes"
    __table_args__ = (SoftDeletable.partial_unique("pt_notes_title_live", "title"),)
    title: Mapped[str] = mapped_column(String(50))


class AuditRow(AuditRecordMixin, PTBase):
    __tablename__ = "pt_audit"


class OutboxRow(OutboxRecordMixin, PTBase):
    __tablename__ = "pt_outbox"


class RunRow(RunRecordMixin, PTBase):
    __tablename__ = "pt_runs"


class SettingRow(Audited, SettingRecordMixin, PTBase):
    __tablename__ = "pt_settings"


class Thing(Entity, PTBase):
    """A stateful aggregate for command / state machine tests."""
    __tablename__ = "pt_things"
    status: Mapped[str] = mapped_column(String(30))
    status_before_hold: Mapped[str | None] = mapped_column(String(30), nullable=True)


class Doc(CreationAudited, PTBase):
    """A document-like row: creation-audited only, with a status machine."""
    __tablename__ = "pt_docs"
    name: Mapped[str] = mapped_column(String(50))
    file_status: Mapped[str] = mapped_column(String(30))


TASKS = shared_tasks_table(PTBase.metadata)
SEQUENCE = sa.Sequence("pt_control_seq", start=100, metadata=PTBase.metadata)


class WidgetRepository(Repository[Widget]):
    model = Widget
    search_fields = ("code", "name")
    sortable = {"code": Widget.code, "name": Widget.name, "price": Widget.price}
    filterable = {
        "active": FilterRule(Widget.active, cast=parse_bool),
        "code": FilterRule(Widget.code, ops=frozenset({"eq", "in", "contains"})),
        "price": FilterRule(Widget.price, ops=frozenset({"range"}), cast=Decimal),
    }
    default_sort = ("code",)
    historical_column = "historical"


class WidgetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    email: str | None
    price: Decimal
    active: bool
    version: int


class WidgetCreate(BaseModel):
    code: str
    name: str
    email: str | None = None
    price: Decimal = Decimal("0")


class WidgetUpdate(BaseModel):
    code: str | None = None
    name: str | None = None
    email: str | None = None
    price: Decimal | None = None
    active: bool | None = None


class WidgetService(CrudService[Widget, WidgetRead, WidgetCreate, WidgetUpdate]):  # an application service
    repo_cls = WidgetRepository
    read_schema = WidgetRead
    create_schema = WidgetCreate
    update_schema = WidgetUpdate
    read_permission = "pt.read"
    write_permission = "pt.config"
    csv_columns = ("code", "name", "email", "price", "active")
    csv_key = "code"

    async def validate_create(self, data):
        from app.platform.errors import BusinessError
        if data.name.lower() == "forbidden":
            raise BusinessError("widget.name_forbidden", "that name is not allowed", {"field": "name"})


@pytest.fixture(scope="session")
def platform_tables(migrated_db):
    eng = sa.create_engine(SYNC_URL)
    PTBase.metadata.drop_all(eng)
    PTBase.metadata.create_all(eng)
    eng.dispose()
    yield
    eng = sa.create_engine(SYNC_URL)
    PTBase.metadata.drop_all(eng)
    eng.dispose()


@pytest_asyncio.fixture
async def clean(platform_tables, db_engine):
    async with db_engine.begin() as conn:
        for t in reversed(PTBase.metadata.sorted_tables):
            await conn.execute(t.delete())
    yield


@pytest.fixture
def session_factory(db_engine, clean):
    return async_sessionmaker(db_engine, expire_on_commit=False)


# The permission checker the services consult: grants per actor (the real one
# is the UniOps role matrix, wired in app/core/deps.py).
GRANTS: dict[uuid.UUID, set[str]] = {}
PT_KEYS = {"pt.read", "pt.config", "pt.operate"}


async def fake_permissions(session, actor, key: str) -> bool:
    return key in GRANTS.get(actor.user_id, set())


def actor_with(*keys: str, name: str = "Someone"):
    from app.platform.actors import Actor
    a = Actor(uuid.uuid4(), name, role="pt_role")
    GRANTS[a.user_id] = set(keys)
    return a


@pytest.fixture
def uow_factory(session_factory):
    return UnitOfWorkFactory(session_factory, audit=AuditTrail(AuditRow), permissions=fake_permissions)


@pytest.fixture
def person():
    """Holds every pt.* key."""
    return actor_with(*PT_KEYS, name="Pat Person")


@pytest.fixture
def stranger():
    """Authenticated, holds no key."""
    return actor_with(name="Sam Stranger")
