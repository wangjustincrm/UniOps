# NC-Imported PO Buyer Details — Manual UI Checklist

**The UI on this branch was never functionally verified by an agent**, and deliberately so:
`docker-compose.dev.yml` bind-mounts `./epms-api` and `./epms` relative to whatever directory
compose was invoked from, and the containers running during development belonged to other
worktrees (`uniops_epms_api` ← `uniops-mrp-phase0`, `uniops_epms_frontend` ← `uniops`). Pointing
a browser at `localhost:5173` would have exercised a different branch's code — a false signal —
and re-pointing the stack would have disrupted another branch's environment.

Run this after deploying a build made from this branch.

1. Log in as a user holding `erp_pa_officer` (or `system_admin`). Open an NC-imported PO with
   `status = 'issued'` — the **Edit Details** button must be visible.
2. Open an NC PO with `status = 'closed'` or `'nc_milk'` — the button must be **absent**.
3. Log in as a plain `requester`, open the same issued NC PO — the button must be **absent**.
4. In Edit Details: Vendor, Currency, Title, Type and Budget Code, and every line quantity and
   price, must render as text with no input. Only Supplier Item ID, Sample and the header fields
   accept input.
5. Fill in Incoterms, Buyer Notes, one Supplier Item ID and one Sample, then Save. It should return
   to the detail page with the values shown, and the attachment list should carry a fresh
   `<PO number>.pdf`.
6. Open that PDF: it must show the Sample column, an Incoterms line, and the Buyer Notes block,
   with **Incoterms above Buyer Notes**, and **no `[NC …]` marker anywhere**.
7. On an NC PO that has both a buyer-entered note and NC sync notes: the detail page must show
   **Buyer Notes** and **NC Sync Notes** as two separately-labelled rows with the right content in
   each, and the PDF must carry neither NC marker.
8. **Find a non-CAD NC PO that carries tax.** Open Edit Details, change only Incoterms, save, and
   confirm the tax amount and total are unchanged. (This is the regression the final review caught:
   the page used to zero the tax on any non-CAD save.)
9. Type `Bag <2 kg> & more` into Buyer Notes on a test PO, save, and confirm the PDF renders it
   intact across multiple lines. (Free text now goes through XML escaping; before the fix the
   angle-bracketed part vanished silently and `&` produced a 500.)

## Before any of this works

In Portal Admin → Access Control, confirm **Edit Imported (NC) POs** (`epms.po.edit_imported`) is
ticked for **ERP PA Officer**. The identity-api migration `0006_po_edit_imported` seeds it, but
verify rather than assume.
