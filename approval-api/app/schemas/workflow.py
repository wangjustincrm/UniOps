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
    # Set when this step does not always run: the stored definition has no such
    # flag, because _should_skip_step decides per document at runtime. Anything
    # describing the process to a person needs it, or every step reads as
    # mandatory. False means it always runs.
    conditional: bool = False
    condition: str | None = None


class WorkflowDef(BaseModel):
    doc_type: str
    steps: list[WorkflowNodeDef]


class AllWorkflowDefs(BaseModel):
    pr: WorkflowDef
    po: WorkflowDef
    pa: WorkflowDef
