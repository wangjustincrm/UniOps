"""Schemas for the PO sign-off flow (doc_type "posign")."""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SignoffSubmitRequest(BaseModel):
    # The buyer's case for the purchase: why buy this, why this quantity. Kept
    # out of the PDF on purpose — that document goes to the vendor.
    justification: str = Field(min_length=1, max_length=4000)


class SignoffSignRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class SignoffCommentRequest(BaseModel):
    comment: str = Field(min_length=1, max_length=4000)


class SignoffStepState(BaseModel):
    id: str
    role: str
    label: str
    # "initials" | "signature" | None — where this step lands on the PDF.
    sig_slot: str | None = None
    # Whoever currently holds the post, and whether they can actually sign.
    holder_count: int
    holders_without_signature: list[str] = []
    # Filled once the step has been signed (from the snapshot, not live).
    signed_by_name: str | None = None
    signed_at: datetime | None = None


class SignoffThreadEntry(BaseModel):
    """One entry of the append-only justification thread.

    Sourced from approval_events rows scoped to document_type "posign": the
    submitter's justification (submit), a signer's objection (return), a
    signature note (approve) and free-standing additions (note).
    """
    action: str
    actor_name: str | None = None
    actor_role: str
    comment: str | None = None
    at: datetime


class SignoffState(BaseModel):
    status: str
    step_idx: int
    submitted_by: uuid.UUID | None = None
    submitted_by_name: str | None = None
    submitted_at: datetime | None = None
    steps: list[SignoffStepState] = []
    thread: list[SignoffThreadEntry] = []
    # What the current user may do right now.
    can_submit: bool = False
    can_sign: bool = False
    can_note: bool = False
    # Why submit is unavailable — shown verbatim, so each entry is a sentence.
    blockers: list[str] = []
