"""Phase A — extract every SharePoint list into local JSON staging (read-only)."""
from __future__ import annotations

import json
import mimetypes
from pathlib import Path

from .mappings import EXTRACT_SPEC
from .sharepoint import SharePointClient

DATA_DIR = Path(__file__).resolve().parent / "data"
ATT_DIR = DATA_DIR / "invoice_attachments"


def _attachment_files(item: dict) -> list[dict]:
    """Normalize the expanded AttachmentFiles payload (nometadata vs verbose)."""
    af = item.get("AttachmentFiles")
    if isinstance(af, dict):
        af = af.get("results", [])
    return af or []


def extract_invoice_attachments(sp: SharePointClient, since: str | None = None) -> int:
    """Download INVOICE list-item attachments → data/invoice_attachments/<spid>/<file>
    and write a metadata index. Returns number of files staged."""
    ATT_DIR.mkdir(parents=True, exist_ok=True)
    items = sp.get_list_items(
        "INVOICE", select=["ID", "Attachments"], expand=["AttachmentFiles"], since=since
    )
    meta: list[dict] = []
    n = 0
    for it in items:
        if not it.get("Attachments"):
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
            dest = ATT_DIR / sp_id
            dest.mkdir(exist_ok=True)
            (dest / name).write_bytes(data)
            meta.append({
                "sp_invoice_id": int(sp_id),
                "file_name": name,
                "content_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
                "size": len(data),
            })
            n += 1
    (DATA_DIR / "invoice_attachments.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"  Invoice attachments staged: {n} file(s) across {len({m['sp_invoice_id'] for m in meta})} invoice(s)")
    return n


def extract(only: set[str] | None = None, since: str | None = None,
            skip_attachments: bool = False) -> None:
    """Pull SharePoint lists into data/*.json.

    ``since`` (ISO UTC) → incremental: only rows Modified at/after it.

    ``ALWAYS_FULL`` lists ignore ``since`` and are always pulled whole:
      * vendorlist — a master with no Modified-based change tracking need.
      * po_item / pa_item — the ONLY place the invoice↔PO link lives
        (``InvoiceID`` on the line row). An incremental invoice usually points
        at a PO that did NOT change, so its line rows would be missing under a
        Modified filter and the invoice would resolve to no PO / no vendor
        ("PMS Unknown Vendor"). Full-pulling them keeps the linkage complete."""
    DATA_DIR.mkdir(exist_ok=True)
    sp = SharePointClient()

    mode = f"incremental since {since}" if since else "full"
    print(f"Connected to {sp.site} as {sp.user}  [{mode}]\n")
    overview = {o["title"]: o["count"] for o in sp.list_overview()}

    ALWAYS_FULL = {"vendorlist.json", "po_item.json", "pa_item.json"}
    summary: dict[str, int] = {}
    for list_title, (filename, fields) in EXTRACT_SPEC.items():
        if only and filename.split(".")[0] not in only:
            continue
        list_since = None if filename in ALWAYS_FULL else since
        expected = overview.get(list_title, "?")
        print(f"  Fetching {list_title!r} (server count {expected}) ...", flush=True)
        items = sp.get_list_items(list_title, select=fields, since=list_since)
        out_path = DATA_DIR / filename
        out_path.write_text(json.dumps(items, ensure_ascii=False, default=str), encoding="utf-8")
        summary[filename] = len(items)
        print(f"    → {len(items)} rows → {out_path.name}")

    # Invoice attachments (the actual uploaded invoice files)
    if not skip_attachments and (not only or "invoice" in only):
        print("  Fetching INVOICE attachments ...", flush=True)
        summary["invoice_attachments"] = extract_invoice_attachments(sp, since)

    (DATA_DIR / "_overview.json").write_text(
        json.dumps({"server_counts": overview, "extracted": summary}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("\nExtract complete. Staged files in", DATA_DIR)


def load_staging(filename: str) -> list[dict]:
    path = DATA_DIR / filename
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))
