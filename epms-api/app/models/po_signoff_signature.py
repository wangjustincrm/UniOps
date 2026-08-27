"""Signature snapshots taken during PO sign-off.

Why a snapshot and not a join to users.signature_image: the PO PDF is
regenerated on demand (there is a Regenerate button, and every attachment
refresh re-renders it). Reading the signer's *current* profile signature at
render time would mean that changing your signature — or leaving the company —
silently restamps every PO you ever signed with an image you never put on it.
What was signed has to stay what is shown, so the image, the name and the
timestamp are copied in at the moment of signing.

Rows are wiped when a returned sign-off is resubmitted (crud/po_signoff.py):
a signature only ever certifies the round it was given in.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKey


class PoSignoffSignature(UUIDPrimaryKey, Base):
    __tablename__ = "po_signoff_signatures"
    __table_args__ = (
        UniqueConstraint("po_id", "step_idx", name="uq_po_signoff_signatures_po_step"),
    )

    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    step_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    # Workflow role that signed this step, e.g. procurement_manager / opm.
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    # Where this signature is drawn on the PDF: "initials" | "signature", or
    # NULL for a configured step that carries no slot (it still has to be
    # signed, it just does not appear on the vendor-facing document).
    sig_slot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    signed_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
    )
    # Copied from users.full_name at signing time, for the same reason the
    # image is copied: a later rename must not rewrite a signed document.
    signer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    signature_image: Mapped[str] = mapped_column(Text, nullable=False)
    signed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    po: Mapped["PurchaseOrder"] = relationship(  # noqa: F821
        "PurchaseOrder", back_populates="signoff_signatures",
    )
