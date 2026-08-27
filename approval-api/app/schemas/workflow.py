from pydantic import BaseModel


class WorkflowNodeDef(BaseModel):
    id: str
    role: str
    label: str
    # PO sign-off only: where this step's signature is drawn on the PO PDF —
    # "initials" (between the two signature blocks) or "signature" (our side's
    # block). None means the step is still signed, but does not appear on the
    # vendor-facing document; the PDF has exactly those two places.
    sig_slot: str | None = None


class WorkflowDef(BaseModel):
    doc_type: str
    steps: list[WorkflowNodeDef]


class AllWorkflowDefs(BaseModel):
    pr: WorkflowDef
    po: WorkflowDef
    pa: WorkflowDef
