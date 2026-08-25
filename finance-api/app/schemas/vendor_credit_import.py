"""Schemas for the throwaway QBO vendor-credit import. Delete with its siblings
when QuickBooks is retired.

Every money field crosses the wire as a Decimal-serialised STRING, matching the
rest of this service. The frontend must put arithmetic through Number().
"""
import uuid

from pydantic import BaseModel, Field


class ImportCandidate(BaseModel):
    qbo_vendor_id: str
    qbo_display_name: str | None = None
    credit_count: int
    credit_total: str
    currencies: list[str]
    # A suggestion is a PRE-FILL, never a decision: run_import imports only the
    # vendors the caller explicitly listed in `mapping`. The measured data
    # contains a vendor whose obvious guess was wrong ("Julie Ju Ni" reads like
    # an employee reimbursement, but is a graphic-design supplier), which is
    # why nothing here is auto-applied.
    suggested_vendor_id: str | None = None
    suggested_vendor_name: str | None = None
    already_imported: int


class ImportDriftRow(BaseModel):
    """A credit whose QBO balance moved after we imported it. Reported only —
    neither side is altered."""
    source_ref: str
    credit_number: str
    vendor_name: str
    imported_total: str
    qbo_balance: str
    applied_amount: str


class ImportCutover(BaseModel):
    """The FULL RELOAD the import reads from; stamped on every imported row."""
    sync_run_id: str | None = None
    finished_at: str | None = None


class ImportCandidatesResponse(BaseModel):
    candidates: list[ImportCandidate]
    drift: list[ImportDriftRow]
    cutover: ImportCutover


class ImportRunRequest(BaseModel):
    # qbo_vendor_id -> EPMS business_partners.id. Deliberately carried in the
    # request rather than persisted: this tool is disposable and must leave no
    # schema behind.
    mapping: dict[str, uuid.UUID] = Field(default_factory=dict)
    # Defaults to a preview. A caller that forgets the flag gets no writes.
    dry_run: bool = True


class ImportedRow(BaseModel):
    qbo_id: str
    vendor_credit_number: str
    vendor_name: str
    amount: str
    currency: str


class ImportDuplicateRow(BaseModel):
    """Already in the ledger from a manual upload — importing would double the
    vendor's credit, so it is skipped and named."""
    qbo_id: str
    vendor_credit_number: str
    existing_credit_number: str


class ImportRunResponse(BaseModel):
    dry_run: bool
    imported: int
    skipped_unmapped: int
    skipped_existing: int
    skipped_duplicate: int
    total_amount: str
    rows: list[ImportedRow]
    duplicates: list[ImportDuplicateRow]
