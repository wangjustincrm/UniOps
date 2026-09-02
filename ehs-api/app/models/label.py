"""Workplace labels for chemical containers, and the gate before one is printed.

WHMIS requires a workplace label on any container a product is decanted into.
The information is on the supplier's data sheet; the work is transcribing it,
and transcribing it wrongly is a violation rather than a typo — a container
labelled with the wrong pictogram is worse than one labelled with none, because
somebody will believe it.

So the model is built around one rule: **nothing prints until a person has
approved it.** `status` moves draft → approved → printed, and the PDF endpoint
refuses anything that is not approved. Extraction from the data sheet fills the
draft in; it never publishes.

`source` records where each draft came from — the chemical record, an extraction
from the SDS, or typed by hand. Three years from now, "how did this label come
to say that" has an answer.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class ChemicalLabel(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "ehs_chemical_labels"

    chemical_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_chemicals.id", ondelete="CASCADE"),
        nullable=False, index=True)
    # The revision the label was drawn from, so a superseded sheet's label can
    # be found and replaced rather than quietly outliving it.
    sds_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_sds.id", ondelete="SET NULL"), nullable=True)

    product_identifier: Mapped[str] = mapped_column(String(250), nullable=False)
    supplier: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signal_word: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # [{code, text}] — H-statements. Codes are kept beside the wording because
    # the code is what a responder looks up and the wording is what is read.
    hazard_statements: Mapped[list] = mapped_column(JSONB, nullable=False,
                                                    default=list, server_default="[]")
    precautionary_statements: Mapped[list] = mapped_column(JSONB, nullable=False,
                                                           default=list, server_default="[]")
    # Pictogram keys from the ghs_hazard_class vocabulary.
    pictograms: Mapped[list] = mapped_column(JSONB, nullable=False,
                                             default=list, server_default="[]")
    first_aid: Mapped[str | None] = mapped_column(Text, nullable=True)
    ppe_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # chemical_record | sds_extraction | manual
    source: Mapped[str] = mapped_column(String(20), nullable=False,
                                        default="manual", server_default="manual")
    # What the extraction returned, verbatim, including its own confidence per
    # field. Kept so a reviewer can see what was suggested against what was
    # approved — and so a bad extraction is diagnosable rather than deniable.
    extraction: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # draft | approved | printed | superseded
    status: Mapped[str] = mapped_column(String(12), nullable=False,
                                        default="draft", server_default="draft", index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    print_count: Mapped[int] = mapped_column(Integer, nullable=False,
                                             default=0, server_default="0")
    last_printed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
