import pytest
from sqlalchemy import select
from app.models.qbo import QboAttachment, QboAttachmentLink
from scripts.qbo_import.attachments import load_attachments

ATTACHABLES = [
    {"Id": "900", "FileName": "Invoice_1.pdf", "ContentType": "application/pdf",
     "Size": 16416, "TempDownloadUri": "https://example.test/doc/900",
     "MetaData": {"LastUpdatedTime": "2019-05-02T10:00:00-07:00"},
     "AttachableRef": [
        {"EntityRef": {"value": "48751", "type": "Bill"}},
        {"EntityRef": {"value": "49832", "type": "Bill"}},
     ]},
]


class FakeDownloader:
    def __init__(self, blob): self.blob = blob; self.urls = []
    def get_bytes(self, url): self.urls.append(url); return self.blob


@pytest.mark.asyncio
async def test_load_attachments_metadata_links_and_bytes(db_session):
    dl = FakeDownloader(b"%PDF-1.4 fake")
    n = await load_attachments(db_session, ATTACHABLES, downloader=dl)
    assert n["inserted"] == 1
    a = (await db_session.execute(select(QboAttachment))).scalar_one()
    assert a.file_name == "Invoice_1.pdf"
    assert a.content_type == "application/pdf"
    assert a.content == b"%PDF-1.4 fake"
    assert dl.urls == ["https://example.test/doc/900"]
    links = (await db_session.execute(
        select(QboAttachmentLink).order_by(QboAttachmentLink.txn_id))).scalars().all()
    assert [(l.txn_id, l.txn_type) for l in links] == [("48751", "Bill"), ("49832", "Bill")]
