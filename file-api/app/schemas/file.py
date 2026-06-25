import uuid
from pydantic import BaseModel


class FileMetaResponse(BaseModel):
    id: uuid.UUID
    original_filename: str
    content_type: str
    file_size: int
    service: str
    doc_type: str
    doc_id: uuid.UUID
    created_at: str
    download_url: str

    model_config = {"from_attributes": True}
