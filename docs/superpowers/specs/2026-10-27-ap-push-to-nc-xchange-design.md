# AP → NC push via XChange, plus FX rate module — design

Status: **decisions complete, ready to implement** (2026-10-27). Replaces the Excel "Export to NC" handoff (FIN-AP-05).
Evidence: test-environment experiments on NC65 test (app 10.10.95.38, DB 10.10.95.39, refreshed
from production 2026-10-26), field-by-field comparison against clerk-entered bills, and the
2026-10-21 mapping study (524 matched AP ↔ NC bills).

## 1. Goal

Today an AP clerk downloads an Excel workbook from Finance → Accounts Payable and imports it into
NC by hand. Instead, UniOps sends each AP invoice to NC directly. NC creates a **saved, not yet
approved** payable bill (应付单). The AP clerk reviews it in NC, adjusts it if needed, approves it,
then clicks "制单" to turn the temporary voucher into a formal one — exactly as now.

Non-goals: UniOps does not approve bills in NC, does not create vouchers, and never writes to the
NC database.

## 2. Decisions already made

| # | Decision | Who / when |
|---|---|---|
| D1 | Use NC's own **XChange (外部交换平台)** HTTP interface, so NC's business logic assigns PKs, bill numbers, tally rows and balances. Direct DB writes are rejected. | User, 2026-10-22 |
| D2 | **One AP = one NC bill.** The clerk's habit of combining several invoices into one bill existed only because of manual entry. | User, 2026-10-22 |
| D3 | Bills arrive in **saved state** (billstatus −1); the clerk approves in NC. | User, 2026-10-22 |
| D4 | Summary (摘要) rule, see §4.4. PA number is **not** used, except for Direct invoices. | User, 2026-10-27 |
| D5 | Exchange rate = **Bank of Canada daily average rate on the invoice issue date**; on a weekend or holiday, the **previous business day**. | User, 2026-10-27 (the clerk confirmed the invoice-date practice) |
| D6 | Build an **FX rate module** in UniOps: fetch rates daily and keep a history table that the whole system reads. | User, 2026-10-27 |
| D7 | **Manual trigger only:** a "Push to NC" action replaces "Export to NC". There is no automatic push on posting. | User, 2026-10-27 |
| D8 | Production NC app server = **10.10.95.66** (XChangeServlet answers there; reachable from UniOps). | User, 2026-10-27 |
| D9 | Bill maker = **the user who clicks Push to NC**, mapped to their NC user code (§6.1). | User, 2026-10-27 |
| D10 | NC PKs (e.g. the `def5` cost centre) are read from the **existing NC read-only connection** (`finance-api` nc_sync). | User, 2026-10-27 |
| D11 | An invoice with **several tax rates is split into one body row per rate**, so each row has a single rate (§4.3). | User, 2026-10-27 |
| D12 | The **Excel export is removed**; there is no fallback during the parallel run. | User, 2026-10-27 |
| D13 | The AP list column "NC Export" becomes **"NC AP Doc"**: the NC payable bill number for this AP, so you can see whether NC already has it (§6.2). | User, 2026-10-27 |

## 3. What the test environment proved

| Check | Result |
|---|---|
| XChange endpoint | `POST http://<nc-app>/service/XChangeServlet?account=0815&groupcode=0`. 0815 is the business-centre code ("Canada Royal Milk ERP System"); 0 is the group code. |
| F1 plugin | `nc.bs.arap.plugin.ArapExpPfxxPlugin` is registered for bill type F1. |
| Sender registration | The sender must exist in 外部系统信息设置 (`xx_exsystem`), otherwise NC returns −31102. `uniops` was registered on test; production needs the NC admin to add it through the UI. |
| Saved state | billstatus −1, approvestatus 3, effectstatus 0. NC assigns the bill number. It writes AP_PAYABLEBILL/ITEM, ARAP_TERMITEM and the three ARAP_TALLY tables, and computes the due date from the payment term. |
| Origin marker | NC sets `src_syscode = 17` on every XChange bill, so system-pushed bills can be told apart from manual ones. |
| Amounts | Match the clerk's bill exactly when we send them (722.64 CAD at 13% HST; 680.00 USD → 967.10 CAD at 1.4222). |
| Idempotency | `replace="Y"` with the same external bill id **replaces** a saved bill: the old bill gets dr=1 and the new one gets a new bill number. NC records external id → PK in `xx_idcontrast`. |
| After approval | Resending the same id is **refused**: −32000 "单据已经审核，不允许重复导入单据". Approved bills cannot be overwritten. |
| Approval → voucher | The clerk approved a test bill: NC created the temporary voucher with explanation `Received Invoice` + supplier code + invoice no + **item summary**, the same as the clerk's own bills. |

### Field traps found

| Field | Trap | Handling |
|---|---|---|
| `pk_busitype` (AP01) | The translator cannot resolve it. | Omit it; NC defaults it the same as clerk bills (AP01). |
| `scomment` (summary) | XChange caps it at 20 characters, while the DB column holds 250 and the voucher explanation uses it. | Raise `maxLength` 20 → 250 for `scomment` in `/home/nchome/pfxx/billdefine/arap/F1.xml` and `billdefine/F1.xml` (head and body). This is a config file, takes effect without a restart, and is done on test. **Required on production.** |
| `def5` (actual cost centre) | Free-defined fields are not translated; a code is stored literally ("E04"). | Send the NC **PK** of `resa_costcenter`. |
| Body `pk_fiorg` | A code is translated to the *version* PK (wrong). The metadata marks the field "deprecated". | Omit it. |
| `rate` / `local_*` | **NC does not look up a rate**: its rate table was last maintained in 2020-09, and a missing rate means 1.0. | Send the rate and every local-currency amount (§5). |

### Found in the end-to-end run (production AP data → NC test server, 2026-10-27)

13 real invoices of every route were planned from production (read-only) and pushed to the NC test
server; 10 were created, 3 were refused by UniOps for data reasons. Four rules came out of it:

| Finding | Rule |
|---|---|
| An **import** bill (buysellflag 4) may carry no tax in XChange: "组织本币无税金额不等于价税合计". Clerks key 73 such rows a year by hand (a US supplier charging HST). | A foreign supplier that charges Canadian tax is booked as **domestic (2)**; untaxed foreign bills stay 4. **To confirm with finance.** |
| **CNY is quoted inversely** in NC (`bd_currinfo.convmode = 1`: 1 CAD = x CNY), and NC re-derives local amounts with zero tolerance: "本币变动超过最大折算误差". | Rate sent = 1 / BoC rate (8 dp); local = amount ÷ rate. USD / EUR / GBP multiply. |
| 11 of 642 tax lines carry the tax but a **0 taxable base**. | The base is the pre-tax amount less the other lines' bases (two such lines → refused). |
| An EPMS PO **imported from NC** (raw material / packaging, `purchase_orders.source = 'nc'`) gets its payable in NC from the purchase invoice (D1). | Refused: an F1 on top would book it twice. |

A row's tax rate is snapped to the statutory rate when rounding puts it within 0.1 (3.90 on 29.98 =
13.01 % → 13); the explicit amounts are unchanged.

## 4. Field mapping (one AP → one F1 bill)

### 4.1 Header

| NC field | Value |
|---|---|
| pk_group / pk_org / pk_fiorg / sett_org | `0` / `01010104` (codes) |
| pk_billtype / pk_tradetype / billclass | `F1` / `F1-Cxx-017` / `yf` |
| billdate, billyear, billperiod | Push time (the clerk's bills also carry entry time, not invoice date) |
| billmaker / creator | NC user code of the person who clicked Push (D9, §6.1) |
| pk_currtype | AP currency code |
| money / local_money | AP total / total × rate |
| billstatus / isinit / syscode / src_syscode | −1 / N / 1 / 1 (NC overrides src_syscode to 17) |
| rececountryid / taxcountryid | `CA` |

### 4.2 Body (one row; courier invoices one row per cost centre, as in today's export)

| NC field | Value | Rule source (2026-10-21 study, hit rate) |
|---|---|---|
| supplier, ordercubasdoc | `vendors.erp_id` | 96% |
| invoiceno | Vendor invoice number, verbatim | 96% |
| money_cr, money_bal, occupationmny | AP total (credit note negative) | 93% / 99% |
| notax_cr, local_tax_cr, caltaxmny, def1 | AP amount / tax (× rate for local) | — |
| taxcodeid / taxrate | HST_ON, HST_NS → `001`@13; GST → `005`@5; no tax → `006`@0 | 99% (today's export sends 001@13 for zero tax — **wrong**) |
| buysellflag | Supplier country CA, **or the invoice carries Canadian tax** → `2`, else `4` | 100% on country (today's export uses currency — **wrong**); the taxed-foreign case is forced by XChange (§3) |
| pk_subjcode (收支项目) | PR budget code → Agreement → Direct PA; leaf only; courier `CRM0150102` | 82% |
| pk_deptid | PR department (Direct: PA department) | 92% |
| costcenter | NC parent CC code (ENG / QA / SC …) | — |
| def5 | NC actual CC **PK** (E04 / H01 / Q01 / P0x / S02) | today's export leaves it blank |
| subjcode (account) | MOH → **510102** by default; fixed list → 510101; GA → 6602; SELL → 660101; RD → 5301 | 88% (today's code 21%: it maps MOH → 510101) |
| pk_currtype / rate | AP currency / FX module (§5) | — |
| pk_payterm | `FH01` | — |
| direction / objtype / rowno | −1 / 1 / 0.. | — |
| scomment, def16 | Summary (§4.4); scomment ≤ 250, def16 ≤ 101 | User D4 |

### 4.3 Multi-rate split (D11)

`ap_invoice_tax_lines` holds one line per tax code, each with its own `taxable_base` and
`tax_amount`. The push emits one body row per tax line: notax = taxable_base, tax = tax_amount,
money = their sum, and that line's tax code and rate. All rows share the invoice's dimensions,
invoice number and summary. If the rows do not add up to the AP total (for example an untaxed
portion the tax lines don't cover), the difference becomes one extra row at `006`@0. If the
difference is negative, the push is refused with a reason. A courier invoice splits by
(cost centre, rate).

### 4.4 Summary rule (D4)

| Invoice | Summary |
|---|---|
| One PO | `PO No` + space + `PO Title` |
| Several POs | PO numbers only, joined by ` / ` |
| Agreement | `AGR No` + space + `AGR Title` |
| EPMS Direct | `PA No` + space + `PA Title` (the only use of PA) |
| Courier (freight) | `Freight ` + invoice no; plus the PO number when the freight line links to one |

Production coverage (735 non-void APs, 2026-10-27): one PO 584 · agreement 81 · several POs 11 ·
direct 21 · freight 8 · none 30 (all draft/unmatched, so not pushable). The longest single-source
summary is 137 characters; 11 exceed def16's 101 and are truncated there.

## 5. FX rate module

- **Storage:** reuse the existing `finance-api` table `exchange_rates` (from_currency, to_currency,
  rate, effective_date; unique per pair+date). It has 0 rows in production today and nothing writes
  it automatically. `crud/bank.py rate_on()` already returns "the latest rate on or before a date",
  which is D5's weekend rule.
- **Source:** Bank of Canada Valet API, `FX{CCY}CAD` daily average
  (`https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json`), reachable from our servers.
  Back-test: 17 of 27 matched USD bills equal the BoC rate on the invoice date. The others differ by
  1–2 days (a different date was read or used) or show a clerk typo.
- **Job:** a daily fetch after BoC publishes (~16:30 ET), via `app.core.background.spawn`, fetching
  the last 10 business days each run (idempotent upsert), plus a one-off backfill from 2025-01-01.
  Currencies: every non-CAD currency seen in AP plus a configurable list (USD, EUR, GBP, CNY …).
- **UI:** Finance → Exchange Rates: history by currency and date, a manual override with a required
  note (kept as `source=manual`), and the last fetch status.
- **Use:** AP → NC push takes `rate_on(currency, invoice_date)`. If there is no rate, the push is
  refused with a reason; it never falls back to 1.0.

## 6. Push flow

1. Eligibility is the same as today's export: AP status not draft/void and it has an accrual
   posting. Courier invoices need every line assigned to a cost centre. Direct invoices need a live
   Direct PA. Plus: the vendor has an `erp_id` and a rate exists.
2. Build the XML and POST it to XChange with bill id `UNIOPS-<AP number>` and `replace="Y"`.
3. Parse `sendresult`:
   - success → store `nc_bill_pk` (`<content>`), look up and store the `billno`, and set `nc_push_status=pushed`;
   - failure → `nc_push_status=failed` plus NC's message, shown on the AP row.
4. Re-push (after a fix in UniOps) reuses the same id, so NC replaces the saved bill. Once the clerk
   has approved it, NC refuses (−32000) and UniOps shows "already approved in NC — change it in NC".
5. Every request and response is logged (`nc_push_log`) for audit.

**Trigger (D7):** manual only. "Push to NC" replaces "Export to NC" on Finance → Accounts Payable:
the same multi-select and the same roles. The Excel export, its template and its batch download
are removed (D12). Nothing is pushed automatically.

### 6.1 Bill maker mapping (D9)

No new table. Every UniOps user already carries **`users.erp_person_code`** (ERP ID; 85 of 88
active users have it), which is the NC person-file code. The push resolves it through the NC
read-only connection: `bd_psndoc.code = erp_person_code` → `sm_user.pk_psndoc`, enabled users
only (`enablestate = 2`). It then sends `sm_user.user_code` as billmaker/creator; XChange translates
that code, as proven on test with `Raghav`.

If the user who clicks Push has no ERP ID, no NC person or no enabled NC user, the push is refused
("Your ERP ID does not resolve to an NC user"). If one person has several enabled NC users, it is
also refused and the candidates are listed, so nothing is guessed.

Checked against production on 2026-10-27, AP clerks:

| UniOps user | ERP ID | NC user code |
|---|---|---|
| Raghav Monga | 000940 | Raghav |
| KARLOVIC CINDY | 000076 | KARLOVIC CINDY |
| LIUYUHONG | 000529 | LIUYUHONG |
| Even Sun | 000584 | SUNQI |
| Meghan Northey | 001004 | Meghan |
| Alex Ji | 111002 | none (no NC person with this code) → would be refused |

### 6.2 "NC AP Doc" column on the AP list (D13)

The column replaces "NC Export" on Finance → Accounts Payable and shows the NC bill number with
its NC state:

| Source | When |
|---|---|
| Push result | The AP was pushed by UniOps: `nc_bill_no` / `nc_bill_pk` were stored from the XChange response; a re-push overwrites them. |
| NC AP mirror (`nc_ap_bills` / `nc_ap_lines`) | Every other AP, including the clerks' historical manual entries: the same match `ap_reconciliation` already uses (normalised invoice number + `vendors.erp_id` = supplier code), **including saved/unapproved bills**. Numbers compare ignoring case, spaces **and hyphens** (user, 2026-10-28: "IN-22-1014797" vs NC "IN-221014797"; 1 of 258 dev1 APs); a match that needed the hyphens dropped is flagged (`hyphen_match`, with NC's raw number) in the column and in the push refusal. `invoice_no_norm` itself is unchanged — duplicate-invoice reviews are keyed on it. |

- The state badge comes from the mirror: Saved (billstatus −1), Approved (1/1), Deleted (dr=1 —
  the stored number is shown struck through, so a bill deleted in NC is visible).
- One AP matching several NC bills (the clerk's old combined bills) shows every number.
- The number links to nothing (NC has no web URL); hovering shows bill date, amount and maker.
- The "Hide exported" filter becomes **"Hide in NC"** (AP has an NC AP Doc).
- Push is refused for an AP that already shows a non-deleted NC AP Doc it did not create (a manual
  entry), so nothing is booked twice. Re-push of our own saved bill is allowed (§6, step 4).

### 6.3 Invoice-date guard

The rate and the NC bill both depend on the issue date, and some issue dates were read with day and
month swapped (8 of 735 non-void APs on 2026-10-27 have an issue date after the entry date or after
the due date, e.g. AP-20260924-0001 issued "2026-12-07", due 2026-11-08). The push refuses such an
AP with "Check the invoice date" until it is corrected.

## 7. Security

- The XChange servlet has **no authentication**: anyone who can reach the NC app port and knows a
  registered sender code can create bills. Before production, restrict the NC app server so only
  the UniOps app server (10.10.50.65) can reach `/service/XChangeServlet`, either by firewall or with
  pfxx `<addresses effective="true">`.
- UniOps needs no NC database credentials for writing. The existing read-only mirror stays as it is.

## 8. Rollout checklist (production NC)

1. NC admin registers the sender `uniops` in 外部系统信息设置.
2. Raise scomment `maxLength` to 250 in both pfxx F1 billdefine files (back them up first).
3. Make sure every AP clerk's ERP ID resolves to an enabled NC user (§6.1; Alex Ji currently does not).
4. Restrict network access to XChangeServlet (§7).
5. Clean up vendor `erp_id` mismatches before go-live (Thermo Fisher 0000391 vs NC 0000153, plus
   Daltco, China Telecom, Nalco, JCI, NSF; four vendors with an empty erp_id).
6. Backfill FX history.

## 9. Open items

All six resolved on 2026-10-27 → D7–D12. None remain.

## 10. PRD impact

FINANCE.md: FIN-AP-05 is rewritten (manual push instead of export; the Excel export is removed); a new requirement covers Exchange
Rates; FIN-OI-26 (server-side refusal of re-export) is superseded by NC's own refusal of approved
bills.
