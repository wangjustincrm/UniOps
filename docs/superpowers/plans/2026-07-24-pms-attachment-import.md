# PMS Attachment Import (PR/PO/PA) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an independent **Import Attachments** button to the EPMS PMS Data Import panel that imports PR/PO/PA official-list SharePoint attachments into `pr/po/pa_attachments`.

**Architecture:** Extend the existing SharePoint importer. A new `extract_list_attachments` helper stages PR/PO/PA list-item attachments to `data/<kind>_attachments/`; a new `sync_doc_attachments(kind)` uploads them to the file server (service=epms) and inserts rows, idempotent by `(doc_id, filename)`. A new runner phase `"attachments"` wires extract→load without touching main data or the watermark. The panel gets a third button.

**Tech Stack:** FastAPI + async SQLAlchemy (raw `text()` for the importer, matching `attachments.py`), httpx, React + TS 5.9.3 (epms frontend), pytest against `epms_test`.

## Global Constraints

- Worktree `c:/Project/uniops-pms-attach`, branch `feature/pms-attachment-import` (base `main` @ e4b5a3e). Never touch `main`; commit per task.
- **Official lists only**: `Purchase Request`, `PO List`, `Payment Request`. The `*Backup` lists must never be enumerated.
- File server upload uses `app.services.attachment_helper.upload_to_file_server` with `doc_type` ∈ {`pr`,`po`,`pa`} (→ `service="epms"`), identical to the manual-upload endpoints.
- Number → document mapping: PR `PR_x0020_No`→`purchase_requests.number`; PO `Title`→`purchase_orders.number`; PA `Title`→`payment_applications.pa_number`.
- No DB migration (`pr/po/pa_attachments` already exist). `created_at/updated_at` come from `server_default=func.now()` — never set them in inserts.
- UI strings English only. Idempotent: re-running uploads nothing new.
- epms frontend typecheck: `npx tsc -p tsconfig.app.json --noEmit` (TS 5.9.3 — **no** `--ignoreDeprecations`).
- Backend tests: run from `epms-api/` against the local `epms_test` DB (per project test-DB env discipline); run the epms suite **serially**.

---

### Task 1: Extract PR/PO/PA list attachments

**Files:**
- Modify: `epms-api/scripts/import_pms/extract.py`
- Test: `epms-api/tests/test_pms_doc_attachment_extract.py` (create)

**Interfaces:**
- Produces: `DOC_ATTACHMENT_SPEC: dict[str, dict]` (single source of truth for both extract and load) and `extract_list_attachments(sp, spec: dict, since: str | None = None) -> int`.
- Each spec value has keys: `list_title, number_field, subdir, meta_name, att_table, fk_col, doc_table, number_col, doc_type`.
- Consumes: existing `_attachment_files(item)`, `DATA_DIR`, `sp.get_list_items(...)`, `sp.download_file(...)`.

- [ ] **Step 1: Write the failing test**

Create `epms-api/tests/test_pms_doc_attachment_extract.py`:

```python
"""extract_list_attachments stages PR/PO/PA list-item attachments; Backup lists excluded."""
import json

from scripts.import_pms import extract
from scripts.import_pms.extract import DOC_ATTACHMENT_SPEC, extract_list_attachments


class _FakeSP:
    def __init__(self, items):
        self._items = items
        self.requested_titles: list[str] = []

    def get_list_items(self, title, select=None, expand=None, since=None):
        self.requested_titles.append(title)
        # assert the caller asks for what the loader needs
        assert "Attachments" in (select or [])
        assert expand == ["AttachmentFiles"]
        return self._items

    def download_file(self, url):
        return b"%PDF-FAKE"


def test_no_spec_targets_a_backup_list():
    for spec in DOC_ATTACHMENT_SPEC.values():
        assert "Backup" not in spec["list_title"]


def test_stages_files_and_meta(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "DATA_DIR", tmp_path)
    spec = DOC_ATTACHMENT_SPEC["pr"]
    items = [
        {"ID": "7", spec["number_field"]: "PR-1", "Attachments": True,
         "AttachmentFiles": {"results": [
             {"FileName": "a.pdf", "ServerRelativeUrl": "/sites/pr2/x/a.pdf"}]}},
        {"ID": "8", spec["number_field"]: "PR-2", "Attachments": False,
         "AttachmentFiles": {"results": []}},
    ]
    sp = _FakeSP(items)

    n = extract_list_attachments(sp, spec)

    assert n == 1
    assert sp.requested_titles == ["Purchase Request"]
    staged = tmp_path / spec["subdir"] / "7" / "a.pdf"
    assert staged.read_bytes() == b"%PDF-FAKE"
    meta = json.loads((tmp_path / spec["meta_name"]).read_text(encoding="utf-8"))
    assert meta == [{
        "doc_number": "PR-1", "sp_item_id": "7", "file_name": "a.pdf",
        "content_type": "application/pdf", "size": len(b"%PDF-FAKE"),
    }]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pms_doc_attachment_extract.py -v`
Expected: FAIL — `ImportError: cannot import name 'DOC_ATTACHMENT_SPEC'`.

- [ ] **Step 3: Add the spec + helper to `extract.py`**

After the existing `extract_invoice_attachments` function (leave it untouched), add:

```python
# ── PR / PO / PA document attachments (official lists only — no *Backup) ──────
DOC_ATTACHMENT_SPEC: dict[str, dict] = {
    "pr": {
        "list_title": "Purchase Request", "number_field": "PR_x0020_No",
        "subdir": "pr_attachments", "meta_name": "pr_attachments.json",
        "att_table": "pr_attachments", "fk_col": "pr_id",
        "doc_table": "purchase_requests", "number_col": "number", "doc_type": "pr",
    },
    "po": {
        "list_title": "PO List", "number_field": "Title",
        "subdir": "po_attachments", "meta_name": "po_attachments.json",
        "att_table": "po_attachments", "fk_col": "po_id",
        "doc_table": "purchase_orders", "number_col": "number", "doc_type": "po",
    },
    "pa": {
        "list_title": "Payment Request", "number_field": "Title",
        "subdir": "pa_attachments", "meta_name": "pa_attachments.json",
        "att_table": "pa_attachments", "fk_col": "pa_id",
        "doc_table": "payment_applications", "number_col": "pa_number", "doc_type": "pa",
    },
}


def extract_list_attachments(sp: SharePointClient, spec: dict, since: str | None = None) -> int:
    """Download a document list's item attachments → data/<subdir>/<sp_item_id>/<file>
    and write a metadata index keyed by document number. Returns files staged."""
    nf = spec["number_field"]
    dest_root = DATA_DIR / spec["subdir"]
    dest_root.mkdir(parents=True, exist_ok=True)
    items = sp.get_list_items(
        spec["list_title"], select=["ID", nf, "Attachments"],
        expand=["AttachmentFiles"], since=since,
    )
    meta: list[dict] = []
    n = 0
    for it in items:
        if not it.get("Attachments"):
            continue
        number = it.get(nf)
        if not number:
            continue
        sp_id = str(it.get("ID"))
        for f in _attachment_files(it):
            name = f.get("FileName")
            url = f.get("ServerRelativeUrl")
            if not name or not url:
                continue
            try:
                data = sp.download_file(url)
            except Exception as e:  # noqa: BLE001
                print(f"    !! attachment {sp_id}/{name}: {e}")
                continue
            dest = dest_root / sp_id
            dest.mkdir(exist_ok=True)
            (dest / name).write_bytes(data)
            meta.append({
                "doc_number": str(number),
                "sp_item_id": sp_id,
                "file_name": name,
                "content_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
                "size": len(data),
            })
            n += 1
    (DATA_DIR / spec["meta_name"]).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  {spec['subdir']}: staged {n} file(s) across "
          f"{len({m['sp_item_id'] for m in meta})} doc(s)")
    return n
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pms_doc_attachment_extract.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add epms-api/scripts/import_pms/extract.py epms-api/tests/test_pms_doc_attachment_extract.py
git commit -m "feat(pms-import): stage PR/PO/PA list attachments (extract)"
```

---

### Task 2: Load staged attachments into pr/po/pa_attachments

**Files:**
- Modify: `epms-api/scripts/import_pms/attachments.py`
- Test: `epms-api/tests/test_pms_doc_attachments_load.py` (create)

**Interfaces:**
- Consumes: `DOC_ATTACHMENT_SPEC` from `scripts.import_pms.extract`; `upload_to_file_server` (imported at module level so tests can patch `attachments.upload_to_file_server`); existing `AttachReport`, `DATA_DIR`, `SYSTEM_USER_EMAIL`, `create_access_token`.
- Produces: `sync_doc_attachments(kind: str, dry_run: bool = True, db_url: str | None = None) -> AttachReport` and `AttachReport.to_doc_dict() -> dict` (same numbers as `to_dict` but exposes the not-found count under key `no_doc`).

- [ ] **Step 1: Write the failing test**

Create `epms-api/tests/test_pms_doc_attachments_load.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pms_doc_attachments_load.py -v`
Expected: FAIL — `ImportError: cannot import name 'sync_doc_attachments'`.

- [ ] **Step 3: Implement in `attachments.py`**

At the top of `attachments.py`, add the module-level import (so it is patchable):

```python
from app.services.attachment_helper import upload_to_file_server
from .extract import DOC_ATTACHMENT_SPEC
```

Add a `to_doc_dict` method to `AttachReport` (leave `to_dict` unchanged so the invoice flow's `no_invoice` output is byte-identical):

```python
    def to_doc_dict(self) -> dict:
        """Same numbers as to_dict(), but exposes the 'target not found' count
        under the neutral key `no_doc` (used by the PR/PO/PA attachment flow)."""
        return {
            "uploaded": self.uploaded,
            "skipped_existing": self.skipped_existing,
            "no_doc": self.no_invoice,
            "failed": self.failed,
            "samples_failed": self.samples_failed[:15],
        }
```

Add the loader (mirrors `sync_invoice_attachments`; `no_invoice` doubles as the generic "document not found" counter):

```python
async def sync_doc_attachments(kind: str, dry_run: bool = True,
                               db_url: str | None = None) -> AttachReport:
    """Upload staged PR/PO/PA list attachments into <kind>_attachments.

    Maps SharePoint document number → EPMS doc id, uploads the file to the file
    server (service='epms', doc_type=kind), inserts the attachment row. Idempotent
    by (doc_id, filename). Writes only on a committed (non-dry-run) load."""
    spec = DOC_ATTACHMENT_SPEC[kind]
    rep = AttachReport()
    meta_file = DATA_DIR / spec["meta_name"]
    if not meta_file.exists():
        return rep
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    if not meta:
        return rep

    att_table, fk = spec["att_table"], spec["fk_col"]
    doc_table, num_col = spec["doc_table"], spec["number_col"]
    subdir, doc_type = spec["subdir"], spec["doc_type"]

    engine = create_async_engine(db_url or settings.DATABASE_URL, echo=False)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sf() as db:
            rows = (await db.execute(text(f"select {num_col}, id from {doc_table}"))).all()
            id_by_number = {str(r[0]): r[1] for r in rows}
            sysid = (await db.execute(text(
                "select id from users where email=:e"), {"e": SYSTEM_USER_EMAIL})).scalar()

            existing = set()
            for did, fn in (await db.execute(text(
                f"select {fk}, filename from {att_table}"))).all():
                existing.add((did, fn))

            token = None if dry_run else create_access_token(subject=str(sysid), role="system_admin")

            for m in meta:
                doc_id = id_by_number.get(str(m["doc_number"]))
                if not doc_id:
                    rep.no_invoice += 1  # generic: target document not found
                    continue
                if (doc_id, m["file_name"]) in existing:
                    rep.skipped_existing += 1
                    continue
                if dry_run:
                    rep.uploaded += 1  # would upload
                    continue
                fpath = DATA_DIR / subdir / str(m["sp_item_id"]) / m["file_name"]
                if not fpath.exists():
                    rep.failed += 1
                    rep.samples_failed.append(f"missing file {fpath.name}")
                    continue
                try:
                    data = fpath.read_bytes()
                    storage_key = await upload_to_file_server(
                        data, m["file_name"], m["content_type"], doc_type, doc_id, token,
                    )
                    await db.execute(text(
                        f"insert into {att_table} "
                        f"(id, {fk}, filename, content_type, file_size, storage_key) "
                        f"values (:id,:did,:fn,:ct,:sz,:sk)"
                    ), {
                        "id": uuid.uuid4(), "did": doc_id, "fn": m["file_name"],
                        "ct": m["content_type"], "sz": m["size"], "sk": storage_key,
                    })
                    existing.add((doc_id, m["file_name"]))
                    rep.uploaded += 1
                except Exception as e:  # noqa: BLE001
                    rep.failed += 1
                    rep.samples_failed.append(f"{m['file_name']}: {type(e).__name__}")
            if not dry_run:
                await db.commit()
    finally:
        await engine.dispose()
    return rep
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pms_doc_attachments_load.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add epms-api/scripts/import_pms/attachments.py epms-api/tests/test_pms_doc_attachments_load.py
git commit -m "feat(pms-import): load PR/PO/PA attachments into <kind>_attachments"
```

---

### Task 3: Runner phase + API phase literal

**Files:**
- Modify: `epms-api/app/services/pms_import_runner.py`
- Modify: `epms-api/app/api/v1/pms_import.py:26` (RunRequest.phase Literal)
- Test: `epms-api/tests/test_pms_attachments_phase.py` (create)

**Interfaces:**
- Consumes: `extract_list_attachments`, `DOC_ATTACHMENT_SPEC` (extract), `sync_doc_attachments`, `SharePointClient`.
- Produces: runner `_execute` handles `phase == "attachments"`, writing `run.report = {"dry_run": bool, "doc_attachments": {"pr": {...}, "po": {...}, "pa": {...}}}`; `start_run` accepts `"attachments"`; `RunRequest.phase` allows `"attachments"`.

- [ ] **Step 1: Write the failing test**

Create `epms-api/tests/test_pms_attachments_phase.py`:

```python
"""The attachments phase must be accepted by the API schema and the runner guard."""
from app.api.v1.pms_import import RunRequest
from app.services import pms_import_runner as runner


def test_run_request_allows_attachments():
    assert RunRequest(phase="attachments").phase == "attachments"


def test_run_request_rejects_bad_phase():
    import pydantic, pytest
    with pytest.raises(pydantic.ValidationError):
        RunRequest(phase="nonsense")


def test_start_run_accepts_attachments_phase(monkeypatch):
    # Don't actually launch the background coroutine or persist to disk.
    def _no_task(coro):
        coro.close()
        return None
    monkeypatch.setattr(runner.asyncio, "create_task", _no_task)
    monkeypatch.setattr(runner, "_persist", lambda: None)
    monkeypatch.setattr(runner, "_ensure_loaded", lambda: [])
    runner._current = None
    try:
        out = runner.start_run("attachments", dry_run=True, triggered_by="tester")
        assert out["phase"] == "attachments" and out["status"] == "running"
    finally:
        runner._current = None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pms_attachments_phase.py -v`
Expected: FAIL — `RunRequest(phase="attachments")` raises ValidationError; `start_run` raises ValueError.

- [ ] **Step 3a: Widen the API Literal**

In `epms-api/app/api/v1/pms_import.py`, change the `RunRequest.phase` field (currently line 26):

```python
    phase: Literal["full", "incremental", "attachments"] = "incremental"
```

- [ ] **Step 3b: Accept the phase in `start_run`**

In `pms_import_runner.py`, change the guard in `start_run`:

```python
    if phase not in ("full", "incremental", "attachments"):
        raise ValueError("phase must be 'full', 'incremental' or 'attachments'")
```

- [ ] **Step 3c: Add the attachments branch to `_execute`**

In `pms_import_runner.py`, inside `_execute`, immediately **after** the SharePoint-credential setup block (the four `os.environ.setdefault(...)` lines) and **before** the `run_started = state.now_iso()` line, insert:

```python
        # Attachments-only phase: import PR/PO/PA list attachments, nothing else.
        if run.phase == "attachments":
            from scripts.import_pms.extract import DOC_ATTACHMENT_SPEC, extract_list_attachments
            from scripts.import_pms.sharepoint import SharePointClient
            from scripts.import_pms.attachments import sync_doc_attachments

            run.step = "extracting PR/PO/PA attachments from SharePoint"
            _persist()

            def _extract_all() -> None:
                sp = SharePointClient()
                for spec in DOC_ATTACHMENT_SPEC.values():
                    extract_list_attachments(sp, spec, since=None)  # full, idempotent

            await asyncio.to_thread(_extract_all)

            run.step = "loading attachments into EPMS" + (" (dry-run)" if run.dry_run else "")
            _persist()
            doc_att: dict = {}
            for kind in ("pr", "po", "pa"):
                rep = await sync_doc_attachments(kind, dry_run=run.dry_run)
                doc_att[kind] = rep.to_doc_dict()
            run.report = {"dry_run": run.dry_run, "doc_attachments": doc_att}
            run.status = "success"
            run.step = "done"
            logger.info("PMS attachment import %s: success", run.id)
            return  # `finally` still stamps finished_at, persists, clears _current
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pms_attachments_phase.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/services/pms_import_runner.py epms-api/app/api/v1/pms_import.py epms-api/tests/test_pms_attachments_phase.py
git commit -m "feat(pms-import): attachments-only runner phase + API"
```

---

### Task 4: Frontend — Import Attachments button + report block

**Files:**
- Modify: `epms/src/services/pmsImport.ts`
- Modify: `epms/src/pages/admin/PmsImportPanel.tsx`

**Interfaces:**
- Consumes: runner report shape `{ dry_run, doc_attachments: { pr, po, pa: {uploaded, skipped_existing, no_doc, failed} } }`.
- Produces: `PmsPhase` includes `'attachments'`; panel has an **Import Attachments** button and a "Document attachments" report block.

- [ ] **Step 1: Extend the service types (`pmsImport.ts`)**

Change the phase type and add the doc-attachments shape:

```typescript
export type PmsPhase = 'full' | 'incremental' | 'attachments'
```

Add inside the `PmsReport` interface (after the existing `attachments?` block):

```typescript
  doc_attachments?: {
    pr?: { uploaded: number; skipped_existing: number; no_doc: number; failed: number }
    po?: { uploaded: number; skipped_existing: number; no_doc: number; failed: number }
    pa?: { uploaded: number; skipped_existing: number; no_doc: number; failed: number }
  }
```

`pmsImportService.run` already takes `(phase: PmsPhase, dry_run)` — widening `PmsPhase` is enough.

- [ ] **Step 2: Add a phase label helper + Import Attachments button (`PmsImportPanel.tsx`)**

Add `Paperclip` to the lucide import on line 2:

```typescript
import { Database, Download, RefreshCw, Paperclip, AlertTriangle, CheckCircle2, Loader2, ChevronDown } from 'lucide-react'
```

Add a label helper near `fmt` (top of file):

```typescript
const phaseLabel = (p: PmsRun['phase']) =>
  p === 'full' ? 'Full import' : p === 'attachments' ? 'Import Attachments' : 'Incremental sync'
```

Change the `start` signature to accept the third phase:

```typescript
  const start = async (phase: 'full' | 'incremental' | 'attachments') => {
```

Add the button after the Incremental sync `<Button>` (inside the same `flex flex-wrap gap-3` div, before the `{!config?.last_sync && …}` hint):

```tsx
          <Button variant="secondary" onClick={() => start('attachments')} disabled={running || busy || !!noSp}>
            <Paperclip className="h-4 w-4 mr-1.5" /> Import Attachments
          </Button>
```

- [ ] **Step 3: Render the doc-attachments block + use the label helper**

In `ReportView`, after the existing invoice-`attachments` block (the `{r.attachments && (...) }` expression), add:

```tsx
      {r.doc_attachments && (
        <div className="rounded-lg border border-neutral-200 p-3">
          <div className="text-xs font-semibold text-neutral-500 uppercase mb-1.5">Document attachments</div>
          <div className="flex flex-col gap-0.5 text-sm text-neutral-600">
            {(['pr', 'po', 'pa'] as const).map((k) => {
              const a = r.doc_attachments![k]
              if (!a) return null
              return (
                <span key={k} className="tabular-nums">
                  <b className="uppercase">{k}</b> — {a.uploaded} uploaded, {a.skipped_existing} existing
                  {a.no_doc ? `, ${a.no_doc} no-doc` : ''}{a.failed ? `, ${a.failed} failed` : ''}
                </span>
              )
            })}
          </div>
        </div>
      )}
```

Replace the two current/history phase labels that read `current.phase === 'full' ? 'Full import' : 'Incremental sync'` (in the Current-run block) and `run.phase === 'full' ? 'Full import' : 'Incremental'` (in the history row) with `phaseLabel(current.phase)` and `phaseLabel(run.phase)` respectively.

- [ ] **Step 4: Typecheck**

Run (from `epms/`): `npx tsc -p tsconfig.app.json --noEmit`
Expected: no **new** errors referencing `pmsImport.ts` or `PmsImportPanel.tsx` (epms has ~69 pre-existing TS errors — confirm none of the new ones are in these two files).

- [ ] **Step 5: Commit**

```bash
git add epms/src/services/pmsImport.ts epms/src/pages/admin/PmsImportPanel.tsx
git commit -m "feat(pms-import): Import Attachments button + doc-attachment report"
```

---

### Task 5: Manual verification (dry-run) + full suite

**Files:** none (verification only).

- [ ] **Step 1: Backend suite (serial)** — from `epms-api/`, against `epms_test`:

Run: `python -m pytest tests/test_pms_doc_attachment_extract.py tests/test_pms_doc_attachments_load.py tests/test_pms_attachments_phase.py -v`
Expected: all PASS. (Do not run concurrently with another epms suite — shared test DB.)

- [ ] **Step 2: Dry-run in the app** — bring up the dev stack, open EPMS → Admin Panel → PMS Data Import. With **Dry-run** checked, click **Import Attachments**. Expected: a run appears, status → Success, report shows a "Document attachments" block with pr/po/pa counts and nothing written (verify `select count(*) from pr_attachments` unchanged).

- [ ] **Step 3: Committed run (dev only)** — uncheck Dry-run, click **Import Attachments**. Expected: `uploaded` counts > 0 on first run; a second click shows all `existing` (idempotent). Spot-check one PR/PO/PA detail page shows the imported attachment and it downloads.

- [ ] **Step 4:** Update memory file `project_pms_epms_migration.md` with the new attachment-import capability and branch, then stop for release-time convergence (merge to main + 15-image build happens at the single release point, per multi-session discipline).

---

## Self-Review

**Spec coverage:**
- Extract of 3 official lists (Backup excluded) → Task 1 (spec asserts no Backup title). ✓
- Load into pr/po/pa_attachments via file server (service=epms, doc_type) idempotent by (doc_id, filename) → Task 2. ✓
- Number→doc mapping (PR `PR_x0020_No`, PO/PA `Title`) → Task 1 spec + Task 2 loader. ✓
- Independent "attachments" runner phase, no watermark/main-data → Task 3 (`return` before main path; no `set_last_sync`). ✓
- API Literal + `admin_panel` gate (unchanged) → Task 3. ✓
- Frontend button + report block + phase labels → Task 4. ✓
- Full+idempotent, dry-run reuse → Tasks 2/3/4 + verified in Task 5. ✓
- No DB migration; `created_at/updated_at` via server_default → Task 2 (insert omits them). ✓
- AttachReport unchanged output for invoices (`to_dict` intact; `to_doc_dict` additive) → Task 2. ✓

**Placeholder scan:** none — every code step shows full code. ✓

**Type consistency:** `DOC_ATTACHMENT_SPEC` keys are consumed identically in extract (`list_title/number_field/subdir/meta_name`) and load (`att_table/fk_col/doc_table/number_col/doc_type/subdir/meta_name`). Report key `no_doc` produced by `to_doc_dict()` (Task 2) matches the frontend `doc_attachments[k].no_doc` (Task 4). `sync_doc_attachments(kind, dry_run, db_url)` signature matches runner calls (Task 3). `phaseLabel` accepts `PmsRun['phase']` which now includes `'attachments'`. ✓
