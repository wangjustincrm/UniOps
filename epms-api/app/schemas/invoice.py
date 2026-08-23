"""Pydantic schemas for Invoice."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class InvoiceLineItem(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(default=Decimal("1"), ge=0)
    unit: str | None = Field(default=None, max_length=50)
    # unit_price / line_total may be negative, mirroring PO lines: vendor invoices
    # carry the same discount / rebate / credit lines (header amount stays > 0).
    unit_price: Decimal = Field(default=Decimal("0"))
    line_total: Decimal = Field(default=Decimal("0"))
    # 非PO费用标记(shipping/packaging 等):不参与 PO 匹配,金额照付(随发票头)
    non_po_fee: bool = False
    non_po_note: str | None = Field(default=None, max_length=500)


class InvoiceCreate(BaseModel):
    vendor_id: uuid.UUID
    vendor_invoice_number: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(gt=0)
    tax_amount: Decimal = Field(default=Decimal("0"), ge=0)
    currency: str = Field(default="CAD", max_length=10)
    invoice_date: date
    due_date: date
    line_items: list[InvoiceLineItem] = Field(default_factory=list)
    file_name: str | None = Field(default=None, max_length=255)
    file_size: str | None = Field(default=None, max_length=50)
    notes: str | None = None
    # Optional pre-link to a PO at upload time
    po_id: uuid.UUID | None = None


class InvoiceUpdate(BaseModel):
    vendor_invoice_number: str | None = Field(default=None, min_length=1, max_length=100)
    amount: Decimal | None = Field(default=None, gt=0)
    tax_amount: Decimal | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, max_length=10)
    invoice_date: date | None = None
    due_date: date | None = None
    notes: str | None = None
    line_items: list[InvoiceLineItem] | None = None
    # Explicit GR selection for re-match; omit field entirely to keep existing GRs
    gr_ids: list[uuid.UUID] | None = None


class AllocationInput(BaseModel):
    invoice_line_id: uuid.UUID
    po_id: uuid.UUID
    po_line_id: uuid.UUID | None = None
    allocated_amount: Decimal = Field(ge=0)   # pre-tax
    allocated_tax: Decimal = Field(default=Decimal("0"), ge=0)
    note: str | None = None


class AllocationResponse(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_line_id: uuid.UUID
    po_id: uuid.UUID
    po_line_id: uuid.UUID | None
    allocated_amount: Decimal
    allocated_tax: Decimal
    allocated_total: Decimal
    variance: Decimal | None
    variance_pct: Decimal | None
    note: str | None
    # Display helpers resolved at read time (not stored on the allocation row).
    po_number: str | None = None
    po_line_description: str | None = None
    model_config = {"from_attributes": True}


class NonPoLineInput(BaseModel):
    line_id: uuid.UUID
    note: str | None = Field(default=None, max_length=500)


class InvoiceMatchRequest(BaseModel):
    # New multi-PO path: when provided, takes priority.
    allocations: list[AllocationInput] | None = None
    # 非PO费用行:排除出匹配,但其 line_total 计入平账(照付)
    non_po_lines: list[NonPoLineInput] | None = None
    # Legacy single-PO path (existing tests / PATCH re-match / pre-rework UI).
    po_id: uuid.UUID | None = None
    gr_id: uuid.UUID | None = None
    gr_ids: list[uuid.UUID] | None = None
    po_line_ids: list[uuid.UUID] | None = None
    # When there are NO PO allocations (fee-only invoice, e.g. standalone freight),
    # the PO this invoice is associated with for traceability. Ignored when
    # allocations are present. The fees are paid in full via the AP header.
    reference_po_id: uuid.UUID | None = None
    # Agreement route: takes priority over every PO field when set. The invoice
    # is linked to the agreement for traceability and paid in full from the AP
    # header — there is no line reference to measure a variance against.
    agreement_id: uuid.UUID | None = None
    # Task 6: matching to a house_account agreement is now pure linkage — no
    # evidence field belongs on this request. Mounting receipts to an invoice
    # and declaring "no evidence, here's why" are separate acts that live on
    # the invoice detail page (Task 7/8), not bundled into /match. The only
    # place that still enforces "evidence or an explicit reason" is the PA
    # gate (api/v1/pa.py) at payment time — this schema used to carry
    # receipt_ids / receipt_variance_reason / legacy_settlement_reason for
    # that purpose; all three are gone.
    # milestone 协议必填 —— 人工指定这张票付的是哪个阶段。recurring 默认由 FIFO
    # 自动认领,不读这个字段;但显式传入时是人工指定期次的逃生舱(whole-branch
    # review 补齐 spec §4.3 step 5):FIFO 认不到期次(超容差 / 无候选行)会永久
    # 卡在 match_review 无解,这里让人工越过容差直接指定哪一行。house_account
    # 没有排期行,同样不读。
    schedule_id: uuid.UUID | None = None


class InvoiceReceiptsRequest(BaseModel):
    # 全量覆盖语义:这个列表就是这张发票最终持有的凭证集合。没列出的会被释放。
    receipt_ids: list[uuid.UUID]
    variance_reason: str | None = None


class AssignBillingPeriodRequest(BaseModel):
    """POST /invoices/{id}/billing-period — the after-the-fact half of the
    manual period assignment /match already accepts."""
    schedule_id: uuid.UUID


class SettleWithoutReceiptRequest(BaseModel):
    reason: str = Field(min_length=1)

    # Fix-round 1 (Important #2): `Field(min_length=1)` alone lets "   " (all
    # whitespace) through — crud/invoice.py's settle_without_receipt then
    # .strip()s it down to "", leaving legacy_settlement=True permanently
    # stamped on the invoice with an empty, invisible reason
    # (InvoiceDetailPage.tsx renders it with `{reason && ...}`, so a blank
    # string shows nothing — an irreversible audit flag with no explanation
    # anyone can see). Strip THEN check non-empty, at the schema boundary,
    # so this can't be re-introduced by a caller who never learns about the
    # crud-layer .strip().
    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("A reason is required")
        return v


class InvoiceExceptionRequest(BaseModel):
    resolution: str = Field(min_length=1, max_length=30)  # accepted | credit_note_requested
    note: str | None = None


class AssignMatchRequest(BaseModel):
    user_id: uuid.UUID


class DeclineMatchRequest(BaseModel):
    note: str


class MatchReviewRequest(BaseModel):
    action: Literal["approve", "reject"]
    note: str | None = None


class ClaimedReceipt(BaseModel):
    """协议凭证的只读投影,随发票读权限(view_invoice 矩阵)返回。

    存在的理由:GET /invoices/{id}/agreements/{aid}/receipts 的门禁是
    _require_invoice_match_access(AP / 上传人 / 持 match 任务者),PA 审批人
    全部 403;而且它只在 candidates_for_vendor 里找,已关闭的协议直接 404。
    PA 差异面板的读者正是那批审批人,所以数据必须走发票自己的读权限下发。

    total_amount 可空且**必须保持可空** —— delivery / service 两类凭证本就
    没有金额(ag09 起放开),折成 0 会让前端把它算进汇总,凭空造出差异。
    """
    id: uuid.UUID
    receipt_ref: str | None = None
    receipt_date: date
    receipt_type: str
    total_amount: Decimal | None = None
    vendor_name: str | None = None


class InvoiceResponse(BaseModel):
    id: uuid.UUID
    internal_ref: str
    vendor_invoice_number: str
    vendor_id: uuid.UUID
    vendor_name: str
    amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    currency: str
    invoice_date: date
    due_date: date
    status: str
    line_items: list[InvoiceLineItem] = Field(default_factory=list)
    file_name: str | None
    file_size: str | None
    notes: str | None
    uploaded_by: uuid.UUID
    uploaded_by_name: str | None
    po_id: uuid.UUID | None
    po_number: str | None
    gr_id: uuid.UUID | None
    gr_number: str | None
    gr_ids: list | None = None
    # Agreement receipts this invoice claims (house_account route, Task 5).
    # Same JSONB-array-on-the-model shape as gr_ids above — was missing from
    # this response model even though Invoice.receipt_ids exists on the ORM
    # object (models/invoice.py), which silently dropped it on every
    # /invoices and /invoices/{id} response. Needed by useChainAttachments'
    # receipt lineage branch (epms/src/hooks/useChainAttachments.ts).
    receipt_ids: list | None = None
    # Task 5: read-only projection of the receipts receipt_ids above points at
    # (id, ref, date, type, amount, vendor_name) — see ClaimedReceipt's own
    # docstring for why this has to ride on the invoice's own read permission
    # instead of the narrower dedicated receipts route.
    claimed_receipts: list[ClaimedReceipt] | None = None
    # Task 11 fix-round 1 (Important): free-text explanation for why the
    # claimed receipts' total doesn't line up with the invoice total
    # (MatchPanel.tsx submits it, crud/invoice.py:444 stores it,
    # models/invoice.py:78 keeps it separate from legacy_settlement_reason
    # on purpose). Was write-only end to end — nothing in epms/src ever read
    # it back — the same silent-drop bug as receipt_ids above, one field over.
    receipt_variance_reason: str | None = None
    # Which scheduled billing period this invoice claims (recurring route).
    # Same silent-drop as receipt_ids above: the column has existed since
    # Phase 1B and nothing ever sent it to the client, so the invoice page had
    # no way to tell an invoice that claimed a period from one that never did
    # — the state that makes it unpayable, and the only cue the "assign a
    # billing period" action can key off.
    schedule_id: uuid.UUID | None = None
    matched_at: datetime | None
    matched_by: uuid.UUID | None
    matched_by_name: str | None
    po_total: Decimal | None
    gr_value: Decimal | None
    variance: Decimal | None
    variance_pct: Decimal | None
    matched_po_line_ids: list | None = None
    matched_reference_total: Decimal | None = None
    exception_reason: str | None
    exception_resolved_at: datetime | None
    exception_resolved_by: uuid.UUID | None
    exception_resolved_by_name: str | None
    exception_resolution: str | None
    created_at: datetime
    updated_at: datetime
    uploaded_at: datetime | None = None
    allocations: list[AllocationResponse] = Field(default_factory=list)
    match_assignee_id: uuid.UUID | None = None
    match_assignee_name: str | None = None
    agreement_id: uuid.UUID | None = None
    agreement_number: str | None = None
    # house_account | recurring | milestone — snapshot written alongside
    # agreement_number at match time (Task 8 fix round 1, Important 1). Lets
    # the frontend gate the Receipt Evidence panel / its copy on the ACTUAL
    # agreement type for every caller who can read this invoice, without a
    # second request to a more narrowly (epms.agreement.read) gated route
    # that 403s for roles who can legitimately match/reconcile an invoice but
    # can't read the agreement detail page.
    agreement_type: str | None = None
    match_route: str | None = None
    match_route_auto: bool = False
    legacy_settlement: bool = False
    legacy_settlement_reason: str | None = None

    @model_validator(mode='after')
    def _set_uploaded_at(self) -> 'InvoiceResponse':
        if self.uploaded_at is None:
            self.uploaded_at = self.created_at
        return self

    model_config = {"from_attributes": True}


class InvoiceListResponse(BaseModel):
    items: list[InvoiceResponse]
    total: int


class UnmatchRequest(BaseModel):
    """Reversing a match is a financial action, so the reason is mandatory and
    is what the audit row carries — a blank one would leave a trail that
    records the change without recording why."""
    reason: str = Field(max_length=1000)

    @field_validator("reason")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("A reason is required when unmatching")
        return v.strip()


# ── Document chain (Invoice List due-date drawer) ─────────────────────────────

class ChainStepRef(BaseModel):
    """One document a chain step points at. `number` is the human-readable
    document number; it is nullable only because the underlying columns are."""
    doc_type: str   # po | agreement | gr | pa
    id: str
    number: str | None


class ChainStep(BaseModel):
    key: str        # match_po | link_gr | create_pa | payment
    state: str      # done | pending | blocked | not_applicable | restricted
    detail: str | None = None
    refs: list[ChainStepRef] = []


class InvoiceChainResponse(BaseModel):
    invoice_id: uuid.UUID
    internal_ref: str
    status: str
    due_date: date
    steps: list[ChainStep]
