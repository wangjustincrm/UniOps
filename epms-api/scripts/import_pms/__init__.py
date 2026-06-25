"""PMS → EPMS migration tool.

Imports legacy Purchase Request (PR), Purchase Order (PO), Payment Request (PA),
their line items, and invoices from the SharePoint-based PMS
(https://canadaroyalmilk.sharepoint.com/sites/pr2) into the EPMS database.

Two phases:
  * extract  — pull every SharePoint list into local JSON staging (read-only)
  * load     — transform staged JSON and bulk-insert into EPMS Postgres

See README.md for usage. Run as:  python -m scripts.import_pms --help
"""
