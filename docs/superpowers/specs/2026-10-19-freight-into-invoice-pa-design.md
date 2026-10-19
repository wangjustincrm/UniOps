# Freight: courier invoices and Freight PAs move back into the Invoice and PA pages

- Date: 2026-10-19
- Status: draft for review
- Supersedes the page layout in `2026-10-19-freight-payment-design.md` (the data model there stays)

## Why

The first Freight release (`1d87198d`) gave Freight its own **Invoices** and **Create PA** tabs. The data was
never separate — a courier invoice is an ordinary `invoices` row (`match_route='freight'`) and a Freight PA is
an ordinary `payment_applications` row (`source='freight'`) — but the *pages* were, which gives us:

- two places to upload an invoice, and two places to start a PA;
- courier invoices sitting in the Invoice page's **Unmatched** tab with an **Assign match** button the API
  refuses (409 "handle it on the Freight page");
- an invoice detail page that, for a courier invoice, only says "open it in Freight".

The intended model is: **the Invoice page is where every invoice arrives and is dispatched; the PA page is
where every PA is raised.** Freight keeps only what is genuinely its own — shipments and carriers.

## Target layout

| Page | Before | After |
|---|---|---|
| Freight | Shipments · Invoices · Create PA · Carriers | **Shipments · Carriers** |
| Invoices → Upload | vendor invoice / credit note | + **Courier invoice (Freight)** |
| Invoices → list | courier invoices unmarked, wrong action | **Freight** badge, route filter, correct action |
| Invoices → detail | "open it in Freight →" | **shipment matching panel in place** |
| PA → New | PO only (agreement from its own page) | + **Freight payment** |

## A. Invoice page

1. **Upload.** The upload modal gets a type choice at the top: *Vendor invoice / credit note* (today's form,
   unchanged) or *Courier invoice (Freight)*. The courier choice swaps the body for today's courier upload form
   (carrier, AI read with the `freight_invoice` mode, per-shipment lines, duplicate check), moved out of
   `pages/freight/UploadInvoiceDialog.tsx` into a shared component. The choice is shown only to
   `epms.freight.manage` holders, and the **Upload** button appears for them even without `invoice_upload`.
   After upload the user lands on `/invoices/:id`.
2. **List.**
   - *All Invoices*: a **Freight** badge on courier rows and a **Route** filter (All / PO / Agreement / Freight).
   - *Unmatched*: a courier row's action becomes **Match shipments**, which opens its detail page, instead of
     **Assign match** (which the API refuses).
   - *Exceptions*: courier invoices are excluded (the exception actions refuse them).
3. **Detail.** `/invoices/:id` for a courier invoice renders the shipment-matching panel (today's
   `FreightInvoicePage` body: lines, auto-match, cost centre per line, finalize / reopen / delete) where the
   PO three-way match normally sits. **Create PA** goes to `/pa/new?source=freight&invoice=<id>`.
4. `/freight/invoices/:id` stays as a redirect to `/invoices/:id`, so open tabs and old links keep working.

## B. PA page

1. `/pa/new?source=freight` renders Freight PA creation (today's `CreatePaTab` body: pick matched courier
   invoices, cost-centre split, create draft), the same way `?agreement_id=` renders the agreement route.
   `&invoice=<id>` pre-ticks one invoice.
2. The PA list gets a **New Freight PA** button beside **New PA** for `epms.freight.manage` holders (two
   buttons rather than a menu). Both open the one static `/pa/new` tab, so each re-points it first. (Agreement
   PAs are still started from the agreement, as today.)
3. Freight PA detail: the cost-allocation table links each invoice to `/invoices/:id`.

## C. Freight page

Tabs **Shipments** and **Carriers** only. The shipment row's invoice link goes to `/invoices/:id`. Subtitle:
"Register courier shipments and maintain carriers — courier invoices are uploaded and matched on the Invoices
page, and paid from the PA page."

## D. Backend (epms-api)

1. **Visibility.** A restricted caller sees courier invoices — in the list, on the detail page and in its
   document chain — when they hold `epms.freight.manage`. Unrestricted roles (AP, finance, admin) already see
   every invoice. Nothing is keyed on role codes: who else may open them (e.g. the Freight PA approvers) is
   granted in the Access Control Matrix (user decision, 2026-10-19). **Tests:** an admission test for a
   restricted manager, and a denial test for a restricted user without the key.
2. ~~Exceptions list excludes courier invoices~~ — not needed: a courier invoice is never in `exception`.
3. `GET /freight/invoices/{id}` admits every invoice reader read-only (the panel now sits on the invoice
   page, which AP and finance open without the freight key); every write stays on `epms.freight.manage`.
4. `_refuse_freight` message: "This is a courier invoice — it is matched to shipments on its invoice page".
5. No endpoint is removed; the new places call the existing `/freight/*` endpoints. **No migration.**

## E. Unchanged

Permissions (`register` / `manage` / `admin`), the Freight PA approval chain (`pa_frt`), the finance due list and
NC export, the OCR modes, the data model.

## F. Docs that change with it

- `docs/prd/EPMS.md` §3.21 (page layout), FRT-009, the "PA source" paragraph; version bump.
- Assistant guide layer: `epms-api/app/knowledge/modules.yaml`, `invoice_types.yaml`, `pa_types.yaml` — where
  a courier invoice is uploaded and where a Freight PA is raised.

## G. Verification

- epms-api: the full suite against the `1d87198d` baseline, with the same failure set; new visibility tests.
- epms tsc against the baseline error count (`--ignoreDeprecations 6.0`, file count checked).
- Headless browser walk-through on a private stack: Invoices → Upload → Courier → match → finalize →
  Create PA → `/pa/new?source=freight` → create → submit; the Freight page shows only two tabs; the old
  `/freight/invoices/:id` redirects.
