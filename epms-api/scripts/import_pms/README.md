# PMS → EPMS migration (`scripts/import_pms`)

One-time, re-runnable importer that moves the legacy SharePoint PMS
(`https://canadaroyalmilk.sharepoint.com/sites/pr2`) into EPMS: Purchase
Requests (PR), Purchase Orders (PO), Payment Requests (PA), their line items,
and invoices — preserving legacy document numbers, dates, statuses and
relationships.

## Prerequisites

- Run from the `epms-api` directory using the project venv
  (`.venv/Scripts/python.exe` on Windows).
- SharePoint credentials in the environment:

  ```
  SP_USER=appuser@canadaroyalmilk.ca
  SP_PASSWORD=********
  # optional: SP_TENANT=canadaroyalmilk.ca  SP_SITE=https://canadaroyalmilk.sharepoint.com/sites/pr2
  ```

- EPMS DB connection comes from `app.core.config.settings` (i.e. `.env`), or is
  overridden with `--env <name>` (reads `.env.<name>`) or `--db-url`.

## Auth note

This tenant has **legacy SAML cookie auth disabled** (the
`/_forms/default.aspx?wa=wsignin1.0` path returns `accessremoved`). The client
uses **OAuth ROPC** with the Microsoft Office first-party client id, which is
pre-consented for SharePoint and works with username/password. See
[`sharepoint.py`](sharepoint.py).

## Usage

```bash
# 1) Extract every list to ./data/*.json  (read-only; safe)
python -m scripts.import_pms --extract

# 2) Dry-run the load  (read-only on EPMS: builds, resolves, validates,
#    prints a report, writes NOTHING — safe to point at production)
python -m scripts.import_pms --load

# 3) Commit for real  (atomic single transaction, idempotent)
python -m scripts.import_pms --load --commit --yes
```

Useful flags: `--only pr,po,invoice,pa` to scope a load · `--env production` /
`--db-url ...` to target a specific DB · `--batch-size N`.

## How it maps (summary)

| Source | Target | Notes |
|---|---|---|
| `Purchase Request` (+Backup) keyed by `PR No` | `purchase_requests` | number preserved; `PRType`→`type`, `GLCode`→`budget_code`, `ProjectNo`→`project_code`; `OP` ignored |
| `PR Item` (Title==PR No) | `pr_line_items` | SP item id mapped → new UUID |
| `PO List` (+Backup), Title==PO No | `purchase_orders` | vendor required; `pr_id` linked via `PR.PONo`; type inherited from PR |
| `PO Item` (Title==PO No) | `po_line_items` | `PRITEMID`→`pr_line_id`; `ReceivedQTY`→`received_qty` |
| `Payment Request` (+Backup), Title==PA No | `payment_applications` | `PONO`→`po_id`/vendor; amounts split; `invoice_ids` from items |
| `Payment Item` (Title==PA No) | `pa_line_items` | `POITEMID`→`po_line_id` |
| `INVOICE` | `invoices` | vendor/PO/amount derived from referencing items; `internal_ref="INV-<spid>"` |

Crosswalks live in [`mappings.py`](mappings.py): **PR type**, **cost center**
(image.png, keyed by `Department+CostCenter`), **Applier→EPMS user** (user.txt),
plus status/currency maps.

### Decisions baked in
- **Users**: `Applier` resolved via the user.txt crosswalk to existing EPMS
  users; unmatched → a `migration@epms.local` system user.
- **Vendors & departments**: matched to existing EPMS records by name;
  unmatched PO/PA suppliers are auto-created (`category=general`, code `PMS-NNNN`)
  and flagged in the report.
- **Invoices**: migrated; `received_qty` copied onto PO lines; no synthetic
  Goods Receipt documents (`PA.gr_ids=[]`).
- **PAs without a resolvable PO** are skipped (PO is a required FK) and counted.

## Verification

After a dry-run, review the printed report (inserts, idempotent skips, unmatched
vendors/appliers/cost-centers). Then commit and re-check counts against
`data/_overview.json`. Re-running `--load --commit` is a no-op (all skipped).
