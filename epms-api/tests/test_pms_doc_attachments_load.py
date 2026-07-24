"""sync_doc_attachments: maps SP number→EPMS doc, uploads, idempotent by (doc_id, filename)."""
import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete as sa_delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import hash_password
from app.models.pr import PurchaseRequest
from app.models.pr_attachment import PrAttachment
from app.models.user import User
from scripts.import_pms import attachments
from scripts.import_pms.attachments import sync_doc_attachments
from tests.conftest import _TEST_DB_URL

PR_NUM = "PR-ATT-1"
SYS_EMAIL = "migration@epms.local"


async def _seed(test_engine):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        # system user the importer signs its file-server token as
        if not (await db.execute(select(User).where(User.email == SYS_EMAIL))).scalar_one_or_none():
            db.add(User(id=uuid.uuid4(), email=SYS_EMAIL, hashed_password=hash_password("x"),
                        full_name="Migration", role="system_admin", is_active=True))
        owner = User(id=uuid.uuid4(), email=f"att-{uuid.uuid4().hex[:8]}@example.com",
                     hashed_password=hash_password("x"), full_name="Owner",
                     role="requester", is_active=True)
        db.add(owner)
        await db.flush()
        pr = PurchaseRequest(id=uuid.uuid4(), number=PR_NUM, title="t", type=2,
                             status="approved", currency="CAD", amount=Decimal("1"),
                             created_by=owner.id)
        db.add(pr)
        await db.commit()
        return owner.id, pr.id


async def _cleanup(test_engine, owner_id, pr_id):
    sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as db:
        await db.execute(sa_delete(PrAttachment).where(PrAttachment.pr_id == pr_id))
        await db.execute(sa_delete(PurchaseRequest).where(PurchaseRequest.number == PR_NUM))
        await db.execute(sa_delete(User).where(User.id == owner_id))
        await db.commit()


def _stage(tmp_path, monkeypatch, doc_number):
    """Write a pr_attachments.json + one staged file under a temp DATA_DIR."""
    monkeypatch.setattr(attachments, "DATA_DIR", tmp_path)
    spec = attachments.DOC_ATTACHMENT_SPEC["pr"]
    meta = [{"doc_number": doc_number, "sp_item_id": "7", "file_name": "a.pdf",
             "content_type": "application/pdf", "size": 9}]
    (tmp_path / spec["meta_name"]).write_text(json.dumps(meta), encoding="utf-8")
    fdir = tmp_path / spec["subdir"] / "7"
    fdir.mkdir(parents=True)
    (fdir / "a.pdf").write_bytes(b"%PDF-FAKE")


async def test_dry_run_counts_without_writing(test_engine, tmp_path, monkeypatch):
    owner_id, pr_id = await _seed(test_engine)
    _stage(tmp_path, monkeypatch, PR_NUM)
    try:
        rep = await sync_doc_attachments("pr", dry_run=True, db_url=_TEST_DB_URL)
        assert rep.uploaded == 1 and rep.skipped_existing == 0
        rep.to_doc_dict()["no_doc"]  # key exists
        sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with sf() as db:
            rows = (await db.execute(select(PrAttachment).where(PrAttachment.pr_id == pr_id))).all()
            assert rows == []  # dry-run wrote nothing
    finally:
        await _cleanup(test_engine, owner_id, pr_id)


async def test_commit_uploads_then_idempotent(test_engine, tmp_path, monkeypatch):
    owner_id, pr_id = await _seed(test_engine)
    _stage(tmp_path, monkeypatch, PR_NUM)
    fake_key = uuid.uuid4()

    async def _fake_upload(data, filename, content_type, doc_type, doc_id, token):
        assert doc_type == "pr" and doc_id == pr_id
        return fake_key
    monkeypatch.setattr(attachments, "upload_to_file_server", _fake_upload)
    try:
        rep1 = await sync_doc_attachments("pr", dry_run=False, db_url=_TEST_DB_URL)
        assert rep1.uploaded == 1
        sf = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with sf() as db:
            att = (await db.execute(select(PrAttachment).where(PrAttachment.pr_id == pr_id))).scalar_one()
            assert att.filename == "a.pdf" and att.storage_key == fake_key
            assert att.file_size == 9 and att.content_type == "application/pdf"
        # second run is a no-op
        rep2 = await sync_doc_attachments("pr", dry_run=False, db_url=_TEST_DB_URL)
        assert rep2.uploaded == 0 and rep2.skipped_existing == 1
    finally:
        await _cleanup(test_engine, owner_id, pr_id)


async def test_unmatched_number_counts_no_doc(test_engine, tmp_path, monkeypatch):
    owner_id, pr_id = await _seed(test_engine)
    _stage(tmp_path, monkeypatch, "PR-DOES-NOT-EXIST")
    try:
        rep = await sync_doc_attachments("pr", dry_run=True, db_url=_TEST_DB_URL)
        assert rep.uploaded == 0
        assert rep.to_doc_dict()["no_doc"] == 1
    finally:
        await _cleanup(test_engine, owner_id, pr_id)
