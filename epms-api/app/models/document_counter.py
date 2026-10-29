"""The highest sequence ever issued per document-number prefix.

Numbers taken from MAX(existing) + 1 come back after the newest document is
deleted: INV-2026-0653 and INV-2026-0618 were each issued to two (0618: three)
different invoices on 2026-10-07/08/13, and Finance — which keeps the number
as the AP row's reference — then showed a deleted invoice's payable under the
live invoice's number. A prefix listed here never goes backwards
(crud._numbering.next_number_never_reused).
"""
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentCounter(Base):
    __tablename__ = "document_counters"

    prefix: Mapped[str] = mapped_column(String(60), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False)
