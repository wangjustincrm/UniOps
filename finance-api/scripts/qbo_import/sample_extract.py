"""P0 — pull a small real-data sample of every AP entity, plus row counts.

Purpose is to define the qbo_* mirror columns from ACTUAL payloads rather than
from the Intuit docs. Docs mark fields optional that never appear, and required
fields that arrive as empty strings; the CAD-specific TxnTaxDetail shape is only
visible on real rows. Nothing is written to the database here.

Outputs to finance-api/data/qbo_samples/:
    _summary.json        entity -> row count + observed top-level key frequency
    <Entity>.json        first N raw rows, verbatim

Usage:
    python scripts/qbo_import/sample_extract.py
    python scripts/qbo_import/sample_extract.py --limit 50 --entities Bill,Vendor
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.qbo_import.client import QboClient  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "qbo_samples"

# AP scope: vendor bills, the payments that settle them, and the masters they
# reference. Invoice/Payment (the AR pair) are deliberately excluded — confirm
# whether QBO is also used for customer invoicing before adding them.
ENTITIES = [
    "Vendor",
    "Bill",
    "BillPayment",
    "VendorCredit",
    "Account",
    "Purchase",
    "Attachable",
]


def count_of(client: QboClient, entity: str) -> int | None:
    try:
        return client.query(f"SELECT COUNT(*) FROM {entity}").get("totalCount")
    except Exception as exc:  # noqa: BLE001 — a missing entity must not abort the sweep
        print(f"  ! COUNT({entity}) failed: {exc}")
        return None


def sample_of(client: QboClient, entity: str, limit: int) -> list[dict]:
    rows = []
    for row in client.query_all(entity, page_size=min(limit, 1000)):
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20, help="rows per entity (default 20)")
    ap.add_argument("--entities", help="comma-separated subset of the default list")
    args = ap.parse_args()

    entities = args.entities.split(",") if args.entities else ENTITIES

    client = QboClient()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    info = client.get("companyinfo/" + client.realm_id)["CompanyInfo"]
    print(f"Connected: {info.get('CompanyName')} "
          f"(realm {client.realm_id}, country {info.get('Country')}, "
          f"FY starts {info.get('FiscalYearStartMonth')})")
    (OUT_DIR / "CompanyInfo.json").write_text(
        json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")

    summary = {"company": info.get("CompanyName"), "realm_id": client.realm_id, "entities": {}}
    for entity in entities:
        print(f"\n{entity}")
        total = count_of(client, entity)
        print(f"  count: {total}")
        rows = sample_of(client, entity, args.limit)
        print(f"  sampled: {len(rows)}")

        # Key frequency exposes which documented fields actually show up.
        keys = Counter(k for row in rows for k in row)
        summary["entities"][entity] = {
            "total": total,
            "sampled": len(rows),
            "keys": {k: f"{n}/{len(rows)}" for k, n in keys.most_common()},
        }
        (OUT_DIR / f"{entity}.json").write_text(
            json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    (OUT_DIR / "_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
