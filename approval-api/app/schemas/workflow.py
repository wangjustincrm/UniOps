from pydantic import BaseModel


class WorkflowNodeDef(BaseModel):
    id: str
    role: str
    label: str


class WorkflowDef(BaseModel):
    doc_type: str
    steps: list[WorkflowNodeDef]


class AllWorkflowDefs(BaseModel):
    pr: WorkflowDef
    po: WorkflowDef
    pa: WorkflowDef
