"""SQLAlchemy declarative base. Every table of this service is prefixed
`obr_` and lives in the shared database's public schema."""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
