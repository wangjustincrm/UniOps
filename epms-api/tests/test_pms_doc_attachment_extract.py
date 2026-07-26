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
