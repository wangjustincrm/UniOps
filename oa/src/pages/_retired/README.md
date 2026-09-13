# Retired pages

Code for features that have been withdrawn from the product but deliberately
kept in the tree, so they can be switched back on rather than rewritten.

Nothing in here is referenced by `src/app/routes.tsx` — that is what makes it
retired. It still compiles (tsc covers `src/**`), and the server-side code it
talks to is still under test, so it does not rot silently.

`scripts/check-routes.mjs` skips this directory: the navigate() targets in here
point at routes that no longer exist, which is the correct state for retired
code and would otherwise be reported as a broken link forever.

## pa/ — Direct Payment Applications

OA's own PA, raised against an OCR'd vendor invoice rather than a purchase
order. Product decision 2026-08-07, confirmed 2026-09-11: hidden from the UI,
creation refused at the API (`DIRECT_PA_RETIRED` in
`expense-api/app/api/v1/pa.py`, which returns 410).

Reading, approving and paying existing records still work server-side, and
`GET /pa/by-po/{po_id}` is still served for EPMS's document chain tree — the
retirement is of OA's own entry point, not of the PA tables.

To restore: set `DIRECT_PA_RETIRED = False`, move this directory back to
`src/pages/pa`, and re-add the four `/pa` routes and the sidebar entry.

## invoices/ — the OA invoice list and detail

These existed to feed the Direct PA: upload a vendor invoice, OCR it, confirm
the extracted fields, raise a payment against it. With creation refused there
is nothing downstream for an OA invoice to become, so the pages go with it.

The SERVER side stays, and must: EPMS reads `/api/v1/invoices/all`,
`/api/v1/invoice-attachments` and `/api/v1/ocr/*` from expense-api. Those
endpoints are shared infrastructure, not OA's to retire.

To restore: move this directory back to `src/pages/invoices` and re-add the two
`/invoices` routes and the sidebar entry.
