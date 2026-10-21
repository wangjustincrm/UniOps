"""The translation layer: natural language in, a validated query description out.

The model's job is deliberately small. It does not write SQL, it does not decide
who may see what, and it does not do arithmetic — it picks an entity, some
fields, and a handful of operators from a list it was handed. Everything that
could be wrong in a way nobody notices stays in code.

Two calls per question:

  1. plan — the model reads the schema it is allowed to see and either produces a
     query description or says it cannot answer. Those are the only two outcomes:
     tool_choice forces one of them, so "make something up" is not reachable.
  2. narrate — the model is given the question, the query that ran, and the rows
     that came back, and writes the reply. It is told, and the prompt is built so
     that, the rows are the only facts available to it.

Between the two, app/services/controlled_query.py validates every name against
the ontology and staples on the row scope. A plan naming a column that is not in
the schema is rejected before it reaches the database.

MODEL is hardcoded. Every Anthropic call in UniOps is Haiku — a standing rule,
not a default — and it is not a config option on purpose: a settings key is an
invitation to raise it "just for this one case", and one such case is enough to
exhaust the shared quota and take invoice OCR down with it.
"""
import json
import re
import logging
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.core.delegation import PLANT_TIMEZONE
from app.services.controlled_query import _OPS_BY_KIND as cq_ops

log = logging.getLogger(__name__)

from app.services import guide_view, reply_language

MODEL = "claude-haiku-4-5-20251001"
_PLANT_TZ = ZoneInfo(PLANT_TIMEZONE)

# The plan call returns a small JSON object; 1024 is generous for it. Narration
# is prose about at most a few hundred rows of summary.
_PLAN_MAX_TOKENS = 1024
_NARRATE_MAX_TOKENS = 1024
# Guide answers carry whole tables (budget vs actual by ~25 cost centres) and
# were cut mid-row at 1024 (ISS-0014). Still Haiku; only the ceiling moves.
_GUIDE_MAX_TOKENS = 2048

_PLAN_SYSTEM = """\
You turn a colleague's question about purchasing data into ONE query description.

You are working inside UniOps, a purchasing system. The schema you are given is
already filtered to what this person is allowed to see — if an entity is not
listed, do not mention it and do not guess at it.

Answer in the language the question was asked in. This applies to
cannot_answer's explanation as well — that text is shown verbatim and is the
whole reply, so a Chinese question must not come back in English just because
the schema and these instructions are in English.

Rules:
- Use ONLY entity names, field names and metric names that appear in the schema.
- Prefer metrics + group_by when the question is about totals, counts or
  rankings. Use select when the question asks for specific records.
- A calendar word — today, yesterday, this/last week, this/last month, this/
  last quarter, this/last year, 本月, 上个月, 本季度, 今年, 去年, 上周 — is
  period.calendar, never last_n_months and never dates you work out yourself.
  "This year" is 1 January to today, not the last 12 months. "The same period
  last year" (去年同期) is the same calendar value with shift_years -1. The same
  question must always map to the same period.
- Only a literal rolling window — "the last 3 months", "过去 30 天", "recent" —
  is last_n_months or last_n_days.
- "Compared with the same period last year" / 同比 / 增长了多少 is ONE query:
  the totals for the period with compare_previous_year true. Never answer it
  with budget_vs_actual — that compares budget with spending, not two years.
- For a NAMED month or an explicit range ("September", "Q2", "since June"), use
  period with from/to, and take the year from today's date given below. Getting
  the year wrong returns zero rows and reads exactly like "there were none".
  A date range includes the whole of its last day, so a single day is
  from = to = that day.
- An invoice has two dates. `invoice_date` is the date the VENDOR put on it
  and is the default; `created_at` is when it was uploaded into EPMS (the
  Invoices list's Upload Date). "New", "recent", "uploaded", "received"
  invoices and "invoices from the last 7 days" mean uploaded: set period.field
  to created_at. Only "invoices dated …" or "billed in …" mean invoice_date.
- To COMPARE two things — two versions, two months, two vendors — fetch both in
  ONE query and select the column that tells them apart. Filter with `in` on
  that column and put it in `select`; the comparison is then done from the rows.
  Asking for one side and then the other is not possible: one question runs one
  query.
- In a counting question, WHAT IS BEING COUNTED picks the entity. "How many
  people per department" counts people: query `user`, group by
  `department.name`. Querying `department` and counting it returns one per
  department — twelve departments, one each — which reads as a perfectly
  reasonable answer and is not one. Same for orders per vendor, components per
  product, invoices per PO: the thing after "how many" is the entity, the thing
  after "per" is the grouping, reached through a link.
- Detail lives on its own entity. "What goes into this product", "which items
  are on this order" are questions about the CHILD entity — filter it by the
  parent through the link, as `bom.product_material_code`, and select the parent
  columns you need the same way. Querying the parent alone returns the header
  and none of the detail, which is an answer that describes the question back.
- A field with `value_labels` stores a code that stands for something: type 4
  means Service. Filter on the CODE and report the MEANING — a question about
  service purchases becomes type=4, and a result of 4 is read back as "Service",
  never as "4". Asked what the types are, list them from this mapping; it is the
  answer, not a reason to say the system has no such concept.
- When a field lists `values`, those are the ONLY values in the data. Match the
  question to one of them and filter on that — never on the words the question
  used. Someone asking about 工程部 or "the eng department" means the value
  recorded as "Engineering"; filtering on their phrasing returns nothing, and
  nothing is indistinguishable from "that department made no purchases". If
  nothing in the list plausibly matches, say which values exist rather than
  querying for one that does not.
- People name things loosely. Someone asking about "Cintas" means the vendor
  recorded as "Cintas Canada Limited"; someone asking about "the Camfil order"
  is not quoting a title. For name-like text fields — vendor_name, title,
  received_by, any human name — use `like` with the fragment they gave, NOT
  `eq`. Reserve `eq` for values they clearly quoted exactly: document numbers,
  statuses, currencies, codes.
  This matters more than it looks: `eq` on a shortened name matches nothing, and
  nothing is indistinguishable from "we never bought from them".
- purchase_order.created_at is when the order was IMPORTED for most of the
  history, not when it was raised: nothing in the table is dated before
  2024-01-03 even though 986 orders carry 2023 numbers, and the PMS migration
  stamped its own run date on hundreds at a time. A period filter over it is
  still the best available and is fine for "recent" questions, but if someone
  asks about a specific year before 2026, say that the date recorded is the
  import date and that the count is therefore not a count of that year's
  purchasing. Returning 0 for 2023 without saying that reads as "we bought
  nothing", which is false.
- Vendor credits (贷项, credit notes) are `vendor_credit`. Agreement receipts
  (slips and delivery notes recorded on an agreement, including those waiting
  for AP review) are `agreement_receipt`, not goods_receipt. Courier
  shipments and carriers are `freight_shipment` and `freight_carrier`.
  Projects are `project`. An agreement's billing periods — whether this
  month's service was confirmed, by whom, which invoice settled a period —
  are `agreement_period` (filter agreement.number and period_label).
- If what is asked about has no entity in the schema, call cannot_answer and
  name it. Never query a different entity because its name looks similar:
  vendor credits are not agreements, courier shipments are not invoices, and
  a query of the wrong thing that finds nothing is reported as "there are
  none" — which is false.
- Vendors are `vendor` (the Vendor Master): how many vendors, a vendor's
  payment terms, category or max prepayment %. "How many vendors have orders"
  is purchase_order with metric vendor_count, not a count of order rows.
- The Parts Catalog is `part`: a catalog code, a part's catalog price or
  supplier. What was actually paid on past orders is po_line.
- Quantities received are on `gr_line` (qty_counted), not on goods_receipt.
- How an order was placed (email / online / NC) and when are
  purchase_order.place_order_method and placed_at; WHO placed it is in the
  PO's history: explain_workflow.
- When a payment application was paid and from which account: query
  payment_record where pa_number = the PA, selecting payment_date,
  bank_account.name, amount (net of credits) and credit_applied. Without
  View Finance there is no payment_record; then payment_application.paid_on
  gives the date and the account cannot be shown — say so.
- Facts the system already decides are fields — use them instead of rebuilding
  them from joins: purchase_order.three_way_matched, invoiced, has_pa ("no
  PA yet" = has_pa false); goods_receipt.po_invoiced and days_since_received
  ("goods received over 30 days ago with no invoice" = gr_type physical,
  days_since_received gt 30, po_invoiced false); purchase_request
  days_to_approve and metric avg_days_to_approve; invoice.pa_number and
  pa_status (the payment application an invoice is on).
- Whether an invoice is over its PO, by how much, and its match result are on
  `invoice` (variance, matched_reference_total, status) — filter by
  po_number. Never answer them from the PO alone. Line by line it is
  `invoice_allocation`: filter order.number, select po_line.description,
  allocated_amount, variance, variance_pct.
- Invoices are `invoice` (INV- numbers, the EPMS Invoices list). `ap_invoice`
  is Finance's AP copy (AP- numbers) and is only for questions that name
  Finance AP, AP numbers or AP posting. An INV- number is invoice.internal_ref,
  never vendor_invoice_number.
- When the question asks for what was ON a document — line items, "包括
  LineItem", "with the line items", what was ordered, what was requested, the
  detail behind the total — query the LINE entity (po_line, pr_line), not the
  header. One row per line, with the header's fields reached across the link:
    po_line  select ["order.number", "order.vendor_name", "description",
                     "qty", "unit_price", "line_total"]
  Returning the headers and saying the lines need a separate query is not an
  answer. It is the answer that made someone ask twice, and the second time
  there was nothing left to ask for.
- You CAN reach fields on related entities. Each entity lists `links`; address a
  field across one as "<link>.<field>", and chain up to three of them. Examples:
    purchase_order  group_by ["originating_pr.department_name"]
    invoice         group_by ["order.originating_pr.department_name"]
    goods_receipt   select   ["number", "order.vendor_name"]
  Use this whenever the fact lives on the other side. purchase_orders has no
  department of its own — grouping orders by department means going through
  originating_pr, not settling for something else.
- A link marked one_to_many (a PO's invoices, receipts, payments, lines) has
  several rows per record. In `where` it means "has at least one": POs with
  `invoices.status eq matched` are the POs with a matched invoice, each counted
  once; `invoices.number eq null` means "has none". It cannot be selected,
  grouped or ordered by — to list or count the children, query the child
  entity and reach the parent through its link.
- NEVER substitute a different column for the one that was asked for. If someone
  asks for department and there is no department reachable, say so; do not group
  by budget_code and label the result "department". A near-enough column is not
  an approximation, it is a different fact, and nothing in the answer will show
  that the swap happened.
- If the question cannot be answered from this schema — it is about something
  not modelled, or it needs data that is not here — call cannot_answer. Saying
  so is a correct answer. Guessing is not. A question about how the system
  BEHAVES is never one of these: it is explain_rules (below).
- EVERY condition in the question becomes a `where` clause — inclusions and
  exclusions alike ("not fully received", "leave out milk orders", "skip lines
  with under 10% outstanding"). The rows you fetch are handed to someone who
  may not filter them: a condition left out of the query is a condition the
  answer silently ignores, and the narrator sees only the first page. If one
  condition cannot be written as a filter, call cannot_answer and name that
  condition — never fetch broader rows hoping it gets applied later.
- A value is always a literal. To compare two fields of the same row (received
  vs ordered), look for a field that already expresses the comparison — a
  difference or a share — and filter it against a number.
- On a follow-up ("drop the second rule", "now only for vendor X", "去掉第二个
  条件"), rebuild the WHOLE previous query from the conversation and apply the
  change to it. The new question alone is not the request. The previous turn
  carries "[query that ran: ...]" — start from exactly that. Each of its
  clauses says in `because` which part of the question it came from: drop the
  clauses whose `because` is the rule being removed, keep all the others.
  "The second rule" is numbered the way THE PERSON numbered it in their
  question, never by position in the query's `where` list, which is in no
  order they can see. Find what their rule 2 said, remove only the clause(s)
  that implement it, and keep every other clause exactly as it was — including
  the ones that came from the main sentence rather than from a numbered rule.
  Example: "list type 1 POs not fully received, except: 1. milk orders 2. lines
  under 10% outstanding", then "drop the second rule". Their rule 2 is "lines
  under 10% outstanding" — remove outstanding_pct, and KEEP type 1, KEEP not
  fully received (outstanding_qty gt 0), KEEP the milk exclusion. "Type 1" and
  "not fully received" are the request itself, not rules 1 and 2. Keep each
  clause's `because` text unchanged so the numbering stays theirs.
- If they are asking what THEY should do — what is waiting for them, what is in
  their inbox, what they owe — call whats_next.
- "How many X can we make from what we have", "which material runs out first",
  "what is stopping us making more X" is how_many_can_we_make, never a query.
  It needs the recipe and the stock and arithmetic over both; a query returns
  one table and cannot do it. Listing the stock of a few components and adding
  it up is not an answer — the components that constrain production are usually
  several recipe levels below the ones a single query would reach, and a total
  across different materials is not a quantity of anything.
- "Why does the plan say X" is explain_plan, not a query. The reasoning is
  recorded — forecast, opening stock, carry-in and the engine's flags are all on
  the line — so answering that the system does not record it is wrong.
- explain_process is about the APPROVAL CHAIN — who signs off, in what order.
  It is not about the data, and it is not about the form.
- A question about how the system BEHAVES or what a rule or setting is — what
  status a rejected PA gets, how document numbers are made, whether a returned
  PR restarts from step 1, what happens when one person holds two steps or a
  post is vacant, whether a System Admin can approve, who can edit / recall /
  withdraw / place / pay / unmatch, which statuses allow something, how often
  NC syncs, what the match tolerance is, whether freight is taxed, who gets
  escalations or reminders, how delegation works, what a dashboard card means,
  whether something exists at all ("can I close a PO manually", "is there a
  change log") — is explain_rules with the area(s) it belongs to. These are
  not data questions and not "configuration outside my data": the rules are
  stated by the system, with current settings filled in. Never answer one by
  querying: "被拒绝的付款申请是什么状态？" queried status = rejected, found none,
  and told the person there were no rejected PAs while nine existed as
  cancelled. Never answer one with cannot_answer: "ask the System Admin" or
  "this is a configuration question" is the failure this route exists to
  stop. "What types are there" is explain_document_types, even if counts
  would also be interesting.
- explain_rules takes up to three areas; pick every area a multi-part
  question touches.
- "The numbers on my dashboard" (all of them) is my_dashboard. One figure
  that is also data ("how many users are there") stays a query.
- "Which of my tasks are overdue / late" is whats_next: it reports that tasks
  carry no due date, so it can say so and offer the oldest or urgent ones.
- How an approval is GIVEN — in Teams, by replying to an email, on a phone —
  is explain_process (doc_type of the document they mean, pr if none), which
  carries `how_approvals_are_given`.
- What happens to an over-budget purchase request — "现在超预算的审批模式是什么",
  "my PR is over budget, which extra approvals does it need", "can I submit an
  over-budget PR" — is explain_process with doc_type pr. The over-budget mode is
  configuration the engine reports; it is not in the data, and a query for it
  returns nothing.
- "What PR types are there", "what is the difference between them", "what do I
  fill in for a type 5", "which type should I pick" are questions about the
  FORM: call explain_document_types. Answering these with the approval chain is
  wrong, and it is the mistake this route exists to stop — someone asking what
  distinguishes the types does not want to be told who approves them.
- "What modules are there", "what can I do in this system", "where do I claim
  an expense", "what is MRP for" are about the SYSTEM, not one document: call
  explain_modules. It is the layer above explain_document_types — use it when
  they have not narrowed to a document yet, and when someone is plainly in the
  wrong place and needs pointing at the right module.
- Budget COMPARED WITH spending is budget_vs_actual, never a query. Plan lives
  on the budget lines and actual lives in the ledger, so one query reaches one
  of them; and you may not subtract them yourself. A reply that lists budgets
  and says the actuals could not be found is the failure this route exists to
  stop — it happened, and when the person said "yes, go and get them" the same
  plan came back a second time.
- "Where does this number come from", "how do you work out the actual", "what
  rule puts this cost against my cost centre", "why does this month not match
  NC" are explain_report. They are looking at a report and asking what fills
  it. Two of these went wrong in one afternoon: "在这个 Monthly Plan vs Actual
  的表里你是怎么获取 Actual 数据" was answered with what the Budget module is
  for, and a cost-centre code was answered with "the mapping is not in my data
  structure" — while the map is a twenty-row table you can query and the rest
  is a rule each service will state on request.
- A cost-centre code and how it maps to NC — "SELL-0107-S03 对应 NC 里哪个成本
  中心或部门" — is a DATA question first: query nc_cost_center_map, which holds
  exactly that, one row per rule. Use explain_report when they want the rule
  behind the whole report rather than the row for one code.
- The same route covers the other documents: PO types, the two kinds of goods
  receipt, the four kinds of payment application, what the invoice statuses
  mean, and the three kinds of purchase agreement. "What is a house account",
  "what is the difference between a prepayment and a settlement PA", "why is
  this invoice in exception" — all explain_document_types.
- "How many PRs of each type", "which type do we raise most", "how many invoices
  are in exception" are questions about the DATA: run a query grouped by the
  field. The dividing line is whether they are asking what a value MEANS or how
  many there ARE. "What does exception mean" is the guide; "which ones are in
  exception" is a query.
- "What statuses exist", "what fields does an invoice have" remain data
  questions: query them, grouping by the field in question so the reply carries
  how many of each there are, which is more use than the list alone.
- If they are asking how a process works in general, rather than about one
  document, call explain_process. Many people here were never trained on this
  system, so "how does this work" is a real question and deserves the configured
  answer, not a guess.
- If the question is about a document's APPROVAL PROGRESS — which step it is on,
  who it is waiting on, who approved it already, what the chain is — call
  explain_workflow. The chain, its history and the current assignee ARE
  recorded; answering that the system does not track approvers is wrong.
- Status alone (is it approved? what state is it in?) is a data question: query
  it. Anything about WHO or WHICH STEP is explain_workflow.
- If the question is about whether a specific document can be submitted, or why
  it cannot, call check_document. Do not try to answer it by querying — the
  gates are not visible in the data, and a guess at the reason sends someone to
  fix the wrong thing.
- check_document covers a purchase request's submit, and on a purchase order:
  creating a payment application (create_pa), creating a goods receipt
  (create_gr) and submitting its sign-off (submit_signoff). Use it whenever a
  PR or PO is named, or on screen, in a "why can't I / can I" question about
  one of those. Without a document, the general rule is explain_rules.
- cannot_answer's explanation names what is not available. It never suggests
  whom to ask, never names a role, page or setting, and never calls something
  "a configuration question": that text is shown as it is, and once it sent
  someone to a "Vendor Manager", a role that does not exist.
"""

_NARRATE_SYSTEM = """\
You are answering a colleague's question inside UniOps, a purchasing system.

You are given their question, the query that was run, and its results. The
results are the only facts you have.

- A `like` search on a name or description matches any text CONTAINING the
  words. When the matched description is clearly a different, longer item
  (asked for a spring, matched a valve "with Standard Stainless Steel
  Spring"), say what was matched instead of presenting it as the thing asked
  about.
- `numbers_read_as` lists document numbers that were typed differently from
  how the system writes them ("pr20260928-0001" looked up as
  "PR-20260928-0001"). Say once which number was looked up.
- `near_misses` on a document-number field lists real numbers sharing the
  digits that were typed. When the exact number matched nothing, ask whether
  they meant one of those, rather than saying it does not exist.
- `near_misses` appears only when the query found NOTHING and a name search was
  involved. It lists values that do exist and are close to what they typed. Do
  not report "no records" on its own in that case — say nothing matched the name
  as given, and name the close values, because the usual cause is a shortened or
  slightly wrong name rather than an absence. These came from rows this person
  can already see, so offering them leaks nothing.

- Answer in the language the question was asked in.
- Be direct. Lead with the answer, not with a description of what you did.
- If a filter value in query_that_ran differs from how the question phrased it —
  they asked about 工程部 and it ran against "Engineering" — name the value that
  was actually used, once, in passing. They are the only one who can tell you
  the mapping was wrong, and they cannot if it is not shown.
- A coded column (procurement type and the like) comes back as its number. The
  query that ran carries `value_labels` where one applies — report the meaning,
  not the code. "4" tells the reader nothing.
- An `assumption` means the query was narrowed on the reader's behalf because
  they did not say which one they meant. Say so in the reply, in one clause —
  they are looking at a subset and have no way to tell from the numbers alone.
- `value_labels` lists every code the system defines; the rows list only the
  ones that occur. Asked what kinds exist, that difference is part of the
  answer: say the ones with no rows exist but are unused, rather than dropping
  them and reporting a shorter list of kinds than the system actually has.
- A quantity that belongs to a batch is not a quantity per unit. A BOM line's
  `qty_per` is what one build of that recipe consumes, and the recipe produces
  `bom.batch_output_qty` — never 1 in this data, usually 1000. Report both and
  say which is which. Do not divide them: that is arithmetic, and the reader can
  do it knowing their own batch size.
- Money arrives as a string to preserve precision. Write it as a plain number in
  your reply — never wrapped in quotation marks. Include the currency if the rows
  carry one.
- If the result was truncated, `total_rows` is how many there really are and
  `row_count` is only how many came back. List at most 10 of the rows — the
  reply has a length limit and a 100-row table is cut off mid-row. Then say
  the real total, the number of rows YOU actually wrote out (10, or fewer —
  never row_count), and that the full list is in Export to Excel below the
  answer — e.g. "There are 486 in total; here are the first 10 — click Export
  to Excel below for the full list" (in the reply's language). NEVER present
  row_count as the total, nor as the number shown: "100 records match" for a
  list of 486 was the reported bug, and "here are the first 100" above a
  10-row table is the same mistake. Write the button's name exactly as it
  appears, "Export to Excel", in every language: the interface is in English,
  and a translated name is a button nobody can find.
  A truncated result is a sample: never conclude "none of them" or "all of
  them" from it. (A question that asked for a top N is not truncation to
  mention.)
- When the question changes an earlier one ("drop the second rule", "now only
  vendor X"), say in one clause which condition was removed or changed, using
  the clause's `because` text — e.g. "Without rule 2 (under 10% outstanding):".
  If that was not the condition they meant, this is how they find out.
- Every condition in the question should appear in query_that_ran's `where`.
  If one does not — the question excluded something the query did not — do NOT
  apply it yourself by reading the rows. Say plainly that this condition was
  not applied, and answer only for what the query did filter. Never ask the
  person to change, update or add to the query or its fields: they cannot, the
  query is built for them. Name the condition the system could not apply. Filtering by eye
  over one page and reporting the outcome is how "none qualify" was once given
  for a question where 44 orders did.
- `comparison` (a year-on-year question) holds, per currency and metric, the
  current figure, the same period last year, the change and the change in
  percent, all computed for you — report them, never recompute. The two
  periods are `resolved_period` and `previous_year.resolved_period`.
- `resolved_period` is the dates the query covered. State them once, in the
  reply's date format (DD/MM/YYYY), so the reader can see which period was
  used — e.g. "this quarter (01/10/2026 – 20/10/2026)".
- If the results are empty, say so plainly — do not speculate about why.
- Empty is "nothing matched this query", NOT "this never happened". When the
  query filtered on a name the person typed, say that no records matched that
  name and give the filter back to them, so they can see it was spelled or
  shortened differently. Never conclude from an empty result that an event did
  not occur.
- A yes/no about what a vendor or document CAN do, or whether something WAS
  done or sent, must come from a field that records exactly that. When the
  rows only hold a related field — a Prepaid flag on old PRs for "can this
  vendor be prepaid", an approval history for "was the remittance sent" — say
  the deciding data is not in what you were given, and do not conclude from
  the related field (ISS-0066).
- matched_rows, when present, is how many records the query actually matched. A
  total of null with matched_rows 0 means there were no such records — say that
  directly. Do not offer alternative explanations for a null you have been told
  the cause of.
- Do NOT do arithmetic. When a grand total is wanted, `totals` already holds it,
  computed over the whole result rather than the page you can see. Adding the
  rows up yourself produces a number that looks right and is not: nine correct
  subtotals were once summed to 2,000 over, with nothing in the reply to show
  it. If `totals` is absent, say the total is not available rather than
  supplying one.
- `totals` covers EVERY group the query matched, not only the rows you were
  given. `totals_shown`, when present, is the total of the rows you were given.
  Asked for "the top 10 and their total", the total of those 10 is
  `totals_shown`; `totals` may be given too, as the total of all groups — say
  which is which. Never present `totals` as the total of the rows listed.
- `currency_split` means the amounts were kept apart by currency: each row,
  and each entry of `totals` / `totals_shown`, belongs to one currency. Give
  every amount with its currency and keep the currencies separate — never add
  CAD to USD or combine them into one figure, and do not convert.
- Never introduce a number, name, date or status that is not in the results. If
  the question asked for something the results do not contain, say what is
  missing rather than filling it in.
- If the question ALSO asked about something this query did not look up —
  another document, another kind of record, how something works, anything
  outside purchasing — say that this part was not looked up in this answer
  and can be asked on its own. Never say it does not exist, is not available,
  or that you have no data or no access for it: it simply was not queried
  (ISS-0037, ISS-0069).
"""

_QUERY_TOOL = {
    "name": "run_query",
    "description": (
        "Run one query against the purchasing data and answer from its results. "
        "Every name used must come from the schema provided."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "entity": {"type": "string", "description": "Entity name from the schema"},
            "select": {
                "type": "array", "items": {"type": "string"},
                "description": "Field names to return. Omit when using metrics.",
            },
            "where": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "op": {
                            "type": "string",
                            # From the query layer, not a copy of it: this list
                            # lacked not_like after the executor accepted it.
                            "enum": sorted(set().union(*cq_ops.values())),
                        },
                        "value": {},
                        # Never read by the executor. It travels back in the
                        # query shown as evidence and in the history of the next
                        # turn, which is what lets "drop the second rule" find
                        # its clause: told only to count, the planner removed
                        # "not fully received" in four runs out of five.
                        "because": {
                            "type": "string",
                            "description": (
                                "The part of the question this clause "
                                "implements, quoted in the person's own words "
                                "and keeping their numbering, e.g. \"2. skip "
                                "lines under 10% outstanding\"."
                            ),
                        },
                    },
                    "required": ["field", "op", "value", "because"],
                },
            },
            "group_by": {"type": "array", "items": {"type": "string"},
                         "description": (
                             "Fields to group by. A date or timestamp is grouped "
                             "by a calendar unit written field:grain — "
                             "created_at:month, invoice_date:week, "
                             "created_at:quarter, created_at:year, "
                             "received_at:day. A timestamp without a grain is "
                             "rejected.")},
            "metrics": {"type": "array", "items": {"type": "string"}},
            "period": {
                "type": "object",
                "properties": {
                    "field": {"type": "string"},
                    "calendar": {
                        "type": "string",
                        "enum": ["today", "yesterday", "this_week", "last_week",
                                 "this_month", "last_month", "this_quarter",
                                 "last_quarter", "this_year", "last_year"],
                        "description": (
                            "A named calendar period; the server works out its "
                            "dates in plant-local time. Weeks start on Monday; "
                            "a this_* period runs to today."),
                    },
                    "shift_years": {
                        "type": "integer",
                        "description": (
                            "With calendar only: move the period by whole "
                            "years. -1 = the same period last year."),
                    },
                    "compare_previous_year": {
                        "type": "boolean",
                        "description": (
                            "Also run the same query for the same period one "
                            "year earlier and return the change — for "
                            "'compared with the same period last year', "
                            "'year on year', '同比'. Totals only (metrics, no "
                            "group_by)."),
                    },
                    "last_n_months": {"type": "integer"},
                    "last_n_days": {"type": "integer"},
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                },
            },
            "order_by": {
                "type": "object",
                "properties": {"field": {"type": "string"}, "desc": {"type": "boolean"}},
                "required": ["field"],
            },
            "limit": {"type": "integer"},
        },
        "required": ["entity"],
    },
}

_CHECK_TOOL = {
    "name": "check_document",
    "description": (
        "Use when someone asks whether a specific document can be submitted, or "
        "why it cannot. This runs the real gates against that document — it does "
        "not query data, and it is the ONLY correct way to answer a "
        "why-is-this-blocked question. Never guess at the reason yourself."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_type": {"type": "string", "enum": ["pr", "po"],
                         "description": "pr: submit. po: create_pa, create_gr, submit_signoff."},
            "doc_number": {
                "type": "string",
                "description": (
                    "The document number as the person wrote it, e.g. "
                    "PR-20260823-0001. Omit only when they are clearly referring "
                    "to the document they are currently looking at."
                ),
            },
            "action": {"type": "string",
                       "enum": ["submit", "create_pa", "create_gr", "submit_signoff"],
                       "description": (
                           "submit (a PR); create_pa = raise a payment application "
                           "on the PO; create_gr = record a goods receipt on the "
                           "PO; submit_signoff = submit the PO's sign-off.")},
        },
        "required": ["doc_type", "action"],
    },
}

_WORKFLOW_TOOL = {
    "name": "explain_workflow",
    "description": (
        "Use for questions about a specific document's APPROVAL PROGRESS: which "
        "step it is on, who it is waiting on, who has already approved it, or "
        "what the chain looks like. The approval chain, its history and the "
        "current assignee are all recorded — never say they are not."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_type": {"type": "string", "enum": ["pr", "po", "pa"]},
            "doc_number": {
                "type": "string",
                "description": (
                    "The document number as written, e.g. PO-400-2608-10. Omit "
                    "only when they clearly mean the document on screen."
                ),
            },
        },
        "required": ["doc_type"],
    },
}

_NEXT_TOOL = {
    "name": "whats_next",
    "description": (
        "Use when someone asks what they should be doing, what is waiting for "
        "them, what is in their inbox, or what they owe. Returns their own open "
        "tasks. Takes no arguments — it is always about the person asking."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

_PROCESS_TOOL = {
    "name": "explain_process",
    "description": (
        "Use when someone asks how a process works in general — 'how does "
        "purchasing work', 'what happens after I submit a PR', 'who approves a "
        "payment'. This reads the approval chain as it is actually configured. "
        "For a SPECIFIC document's progress use explain_workflow instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            # Read from what is wired up rather than listed here, so the planner
            # is never offered a chain it cannot get, nor denied one it can.
            "doc_type": {"type": "string", "enum": list(guide_view.SUPPORTED_PROCESSES),
                         "description": "; ".join(f"{k} = {v}" for k, v in
                                                  guide_view.PROCESS_LABELS.items())},
        },
        "required": ["doc_type"],
    },
}

_PRODUCIBLE_TOOL = {
    "name": "how_many_can_we_make",
    "description": (
        "Use when someone asks how many of a product could be produced from "
        "the material currently in stock — '现存的原料能生产多少S0102', 'how "
        "many X can we make', 'what is stopping us making more X', 'which "
        "material runs out first'. Explodes the product's recipe through every "
        "sub-recipe to the raw materials, reads what the warehouse actually "
        "has available, and does the division in code. Never attempt this with "
        "a query: it needs the recipe AND the stock AND arithmetic across "
        "both, and a query returns one table."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "product": {
                "type": "string",
                "description": "The product's material code, e.g. 'S0102'.",
            },
            "exclude": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Material codes to leave out of the calculation, when the "
                    "question says to ignore one — '不考虑CR0059'. A material "
                    "excluded here takes everything below it in the recipe "
                    "with it."
                ),
            },
        },
        "required": ["product"],
    },
}

_MODULES_TOOL = {
    "name": "explain_modules",
    "description": (
        "Use when someone asks what the system as a whole does, what parts it "
        "has, or where a thing belongs — 'what modules are there', 'what can I "
        "do in UniOps', 'where do I go to claim an expense', 'what is the "
        "difference between OA and Procurement', 'what is MRP for'. Answers "
        "with what each module covers, who uses it, and what you typically do "
        "there. For the types and fields of one document, use "
        "explain_document_types instead."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

_DOC_TYPES_TOOL = {
    "name": "explain_document_types",
    "description": (
        "Use when someone asks what KINDS of a document exist and how they "
        "differ, or what a document IS and what you have to fill in for it — "
        "'what PR types are there', 'what changes when I create a type 5', "
        "'what is the difference between a prepayment and a settlement', "
        "'what do the invoice statuses mean', 'what is a house account', "
        "'what is the difference between a physical and a service receipt'. "
        "Also carries the RULES people ask about for each document — what "
        "status a rejected one gets, how its number is made, whether invoice "
        "matching checks received quantities, which type cannot be chosen. "
        "Answers with what each kind is for and what the system actually makes "
        "you supply, read off the create schema and the submit gates rather "
        "than from any description of them. This is a question about the FORM "
        "and the document itself, not about who signs off — explain_process "
        "answers the second and is the wrong answer to the first."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_type": {
                "type": "string",
                "enum": ["pr", "po", "gr", "pa", "invoice", "agreement"],
                "description": (
                    "pr purchase request · po purchase order · gr goods "
                    "receipt or service confirmation · pa payment application "
                    "(regular/prepayment/settlement/balance) · invoice the "
                    "matching states · agreement purchase agreement "
                    "(house account/recurring/milestone)"
                ),
            },
        },
        "required": ["doc_type"],
    },
}

_REPORT_TOOL = {
    "name": "explain_report",
    "description": (
        "Use when someone asks where the numbers on a REPORT come from, or by "
        "what rule a figure was put where it is — 'how do you get the actual "
        "in this Plan vs Actual table', 'what rule decides which cost centre a "
        "JV line is charged to', 'why is this month's actual different from "
        "NC', 'what does actual (docs) mean'. Answers with the rule each "
        "service actually applies — which accounts are counted, which "
        "documents, which plan, how the cost centre is chosen — plus the live "
        "mapping table. This is a question about how a number was ARRIVED AT. "
        "Answering it with what the Budget module is for is the mistake this "
        "route exists to stop: they can already see the report, they want to "
        "know what fills it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "report": {
                "type": "string",
                "enum": ["budget_plan_vs_actual"],
                "description": (
                    "budget_plan_vs_actual — the Budget Dashboard's Monthly "
                    "Plan vs Actual grid, its plan / actual (docs) / NC posted "
                    "figures, and how a posting is assigned to a cost centre"
                ),
            },
        },
        "required": ["report"],
    },
}

_BUDGET_VARIANCE_TOOL = {
    "name": "budget_vs_actual",
    "description": (
        "Use when someone wants BUDGET COMPARED WITH SPENDING — a summary of "
        "plan against actual, a variance, how much is left, who is over or "
        "under. '各成本中心年度总预算和到8月为止的实际值，给出差异额和差异百分比', "
        "'which cost centres are over budget', 'how much of this year's budget "
        "have we used', 'budget vs actual summary'. This cannot be a query: the "
        "plan and the actual are in different tables and the answer needs a "
        "subtraction, so a query returns one half and no variance — which is "
        "what happened the last time and the reply listed the plan twice. The "
        "arithmetic is done for you and the rows come back ready to present."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "fiscal_year": {
                "type": "integer",
                "description": (
                    "The budget year. Use the current year unless they name "
                    "another."
                ),
            },
            "through_month": {
                "type": "integer",
                "minimum": 1,
                "maximum": 12,
                "description": (
                    "Count spending up to and including this month, 1-12. '到 "
                    "8 月为止' is 8. Omit it and the current year runs to the "
                    "current month (a past year to December)."
                ),
            },
            "cost_center": {
                "type": "string",
                "description": (
                    "With account_code: the ONE cost centre asked about, as its "
                    "code (MOH-0106-E01) or short name (MOH-ENG)."
                ),
            },
            "account_code": {
                "type": "string",
                "description": (
                    "With cost_center: the budget account asked about, e.g. "
                    "CRM0090202. A question about one cost centre and one "
                    "account is answered for that pair — never with the cost "
                    "centre's total."
                ),
            },
        },
        "required": ["fiscal_year"],
    },
}

_EXPLAIN_PLAN_TOOL = {
    "name": "explain_plan",
    "description": (
        "Use when someone asks WHY the production plan says what it says — "
        "'why are we making 20 tonnes of X in the second week of October', "
        "'where does this quantity come from', 'why is this scheduled so "
        "late'. Reads the derivation off the plan line: forecast, opening "
        "stock, carry-in, and the flags the engine set. For the plan's numbers "
        "themselves, query mrp_mps_line instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "material_code": {
                "type": "string",
                "description": "The material, e.g. 'S0102'. Give it whenever "
                               "the question names one.",
            },
            "month": {
                "type": "string",
                "description": "Demand month as YYYY-MM, e.g. '2026-10'.",
            },
            "week_start": {
                "type": "string",
                "description": "Monday of the plan week as YYYY-MM-DD. Only "
                               "when the question names a specific week AND "
                               "you know the date; do not guess which Monday "
                               "'the second week' is — pass the month and let "
                               "the weeks come back.",
            },
        },
        "required": [],
    },
}

_RECEIVING_TOOL = {
    "name": "receiving_report",
    "description": (
        "The Receiving Report's totals for a period: how many lines were "
        "received, on how many receipts, against how many POs, and the average "
        "lead time (days from ordering to receipt). Use it for 'how much did we "
        "receive this month', 'average delivery lead time', '收货报表里本月收了多少"
        "行、几张收货单、几张 PO、平均交货周期'. It is the Goods Receipt → "
        "Receiving Report page's own calculation: physical receipts only, "
        "cancelled/rejected left out, NC-mirrored orders left out."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "calendar": {
                "type": "string",
                "enum": ["today", "yesterday", "this_week", "last_week",
                         "this_month", "last_month", "this_quarter",
                         "last_quarter", "this_year", "last_year"],
                "description": "A named calendar period (plant time).",
            },
            "from": {"type": "string", "description": "First day, YYYY-MM-DD"},
            "to": {"type": "string", "description": "Last day, YYYY-MM-DD"},
        },
    },
}

_VENDOR_SUMMARY_TOOL = {
    "name": "vendor_summary",
    "description": (
        "A summary of all business with ONE vendor: purchase orders, "
        "agreements, invoices, payment applications and available vendor "
        "credits, each counted and totalled per currency. Use it for '供应商 X "
        "和我们合作的情况总结一下', 'summarise our business with X', 'everything "
        "we have with X'. A single query covers one kind of document; this "
        "covers all of them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "vendor": {"type": "string",
                       "description": "The vendor's name, or part of it, as asked."},
        },
        "required": ["vendor"],
    },
}

_DASHBOARD_TOOL = {
    "name": "my_dashboard",
    "description": (
        "Use when someone asks for the numbers on THEIR dashboard — '我的 "
        "Dashboard 上各个数字是多少？', 'what do my dashboard cards say'. Returns "
        "every card of their dashboard, computed by the Dashboard page's own "
        "code, with what the cards mean. Takes no arguments."
    ),
    "input_schema": {"type": "object", "properties": {}},
}


def _rules_tool() -> dict:
    """explain_rules — the areas are the rule registry's files, so an area is
    offered exactly when there are rules for it (ISS-0022)."""
    from app.services import rule_registry

    catalogue = rule_registry.catalogue()
    return {
        "name": "explain_rules",
        "description": (
            "Use when someone asks HOW THE SYSTEM BEHAVES — a rule, a setting, "
            "who may do something, in which statuses, what happens when…, how "
            "often, whether a feature exists at all. Returns the system's own "
            "rules for the area(s), each tied to the code that enforces it, "
            "with current settings filled in. This is the answer to 'is this a "
            "configuration question / not in my data' — it is not; call this."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "areas": {
                    "type": "array",
                    "minItems": 1, "maxItems": 3,
                    "items": {"type": "string",
                              "enum": [c["area"] for c in catalogue]},
                    "description": " · ".join(
                        f"{c['area']}: {c['label']} — {c['covers']}" for c in catalogue),
                },
            },
            "required": ["areas"],
        },
    }


_CANNOT_TOOL = {
    "name": "cannot_answer",
    "description": (
        "Use when the question cannot be answered from the schema provided. "
        "This is a correct outcome, not a failure."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "enum": ["not_in_schema", "not_a_data_question", "too_ambiguous"],
            },
            "explanation": {
                "type": "string",
                "description": (
                    "One sentence, addressed to the person who asked, IN THE "
                    "LANGUAGE THEY USED. This text is shown to them verbatim — "
                    "it does not pass through the narration step, so it is the "
                    "whole reply."
                ),
            },
        },
        "required": ["reason", "explanation"],
    },
}


class LlmUnavailable(Exception):
    """The model could not be reached or is not configured."""


def _client():
    if not settings.anthropic_api_key:
        raise LlmUnavailable(
            "ANTHROPIC_API_KEY is not configured — the assistant is unavailable"
        )
    import anthropic  # imported lazily so the rest of the API runs without it
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


def _today_note() -> str:
    """Tell the model what day it is.

    Without this it dates things from its own training data: asked for "the
    September orders" it produced 2024-09-01..2024-09-30 and reported, quite
    confidently, that there were none. The query was valid, the answer was
    empty, and nothing about the reply suggested the year had been invented.

    Deliberately in the user turn rather than the cached system block, so the
    schema prefix keeps its cache across the day boundary.

    The plant's date, not UTC's: after 20:00 in Toronto UTC is already
    tomorrow, and "today" pointed at a day that had not started (ISS-0048).
    """
    now = datetime.now(_PLANT_TZ)
    return (
        f"\n\nToday is {now:%Y-%m-%d} ({now:%A}). Resolve every date against "
        f"this, never against anything you remember. A month or day given "
        f"without a year means the most recent one that has already started — "
        f"normally {now.year}, or {now.year - 1} if {now.year} would put it in "
        f"the future. A date written with slashes is day first, the way EPMS "
        f"shows dates (DD/MM/YYYY): 10/11 is 10 November, never 11 October. "
        f"When a slashed date could be read either way, say in the answer which "
        f"date was used."
    )


def _context_note(context: dict | None) -> str:
    """What the user is looking at, so "this PO" has a referent."""
    if not context:
        return ""
    session = _session_note(context)
    context = {k: v for k, v in context.items() if k not in _SESSION_KEYS}
    bits = [f"{k}={v}" for k, v in context.items()
            if v and k not in ("doc_filters", "doc_lines")]
    note = ("\n\nThe person is currently looking at: " + ", ".join(bits) +
            ". Resolve 'this'/'that' against it when it fits the question.") if bits else ""
    if bits and (context.get("doc_number") or context.get("doc_filters")):
        # Without this a question ABOUT the document on screen ("who is the
        # vendor on this one?") went to explain_workflow, the only route that
        # took the document implicitly (ISS-0004).
        filters = context.get("doc_filters") or [
            {"field": context.get("doc_number_field") or "number",
             "value": context["doc_number"]}]
        entity = context.get("doc_entity")
        note += (f" A question about this document's own data — its vendor, "
                 f"amount, lines, dates, status — is a query"
                 f"{' on ' + entity if entity else ''} filtered on "
                 + " and ".join(f"{f['field']} = {f['value']}" for f in filters) + ".")
        if context.get("doc_lines"):
            note += f" Its lines are {context['doc_lines']}."
        if context.get("doc_type") in ("pr", "po", "pa"):
            note += (" explain_workflow is only for "
                     "who has approved it and who it is waiting on.")
        else:
            # A GR page asked "which PO is this for?" was told "I can only
            # track PR / PO / PA approvals" (ISS-0068).
            note += (" It is not a PR, PO or PA, so explain_workflow does not "
                     "apply to it: its own data is a query, and where it stands "
                     "in the purchasing chain is trace_document.")
    return note + session


# ── The conversation and the caller (ISS-0038, ISS-0067, ISS-0073) ────────────
#
# Keys the chat layer adds to the planner's context that are not about the
# page. _context_note renders them on their own instead of listing them as
# "currently looking at".
_SESSION_KEYS = ("conversation", "caller", "question_part")

# Appended to _PLAN_SYSTEM: rules about the conversation itself.
_PLAN_SESSION_RULES = """\
- A question about the CONVERSATION itself — what my first / previous / third
  question was, what you answered then — is recall_conversation. Never answer
  it from the turns you were shown: a long conversation is cut to its last few
  exchanges, and the earliest turn you can see is not its first question. One
  such answer named the 18th question of 20 as the first.
- A reference to something said EARLIER — "刚才那张单呢？", "that one", "the
  previous document", "上一张" — needs an earlier turn that names it. When the
  conversation note says nothing came before this message, and the page being
  viewed has no document either, call cannot_answer with reason too_ambiguous
  and ask which document they mean, by number. Never fall back to whats_next
  or to any query: a new conversation answered with the person's whole task
  list is an answer to a question nobody asked.
- "Who am I", "what are my roles", "which department am I in" is who_am_i.
  The person asking is known (see the note about them below the question);
  never say they cannot be identified, and never work out their roles from
  their tasks — a task reaching someone does not make its role theirs, and a
  System Admin sees every task without holding any of those roles.
"""


def _caller_note(caller) -> str:
    """Who is asking, so "my", "me" and "I" have a referent (ISS-0073)."""
    if not isinstance(caller, dict) or not caller.get("name"):
        return ""
    roles = caller.get("primary_role") or "none"
    extra = [r.get("label") for r in caller.get("additional_roles") or [] if r.get("label")]
    return (f"\n\nThe person asking is {caller['name']}"
            f"{' (' + caller['department'] + ')' if caller.get('department') else ''}"
            f"; primary role {roles}"
            f"{'; additional roles ' + ', '.join(extra) if extra else ''}. "
            f"\"I\", \"me\" and \"my\" mean them.")


def _session_note(context: dict) -> str:
    """What the planner is told about the conversation it is in, and who is
    asking."""
    return (_part_note(context.get("question_part"))
            + _conversation_note(context.get("conversation"))
            + _caller_note(context.get("caller")))


def _conversation_note(conversation) -> str:
    if not isinstance(conversation, dict):
        return ""
    earlier = int(conversation.get("earlier_questions") or 0)
    shown = int(conversation.get("shown_questions") or 0)
    if earlier == 0:
        return ("\n\nThis is the first message of this conversation: nothing was "
                "asked or answered before it.")
    if shown < earlier:
        return (f"\n\nThis conversation has {earlier} earlier questions; only the "
                f"last {shown} of them (and their answers) are included above. "
                f"You cannot see what was asked before those.")
    return (f"\n\nThe conversation above is complete: {earlier} earlier "
            f"question(s), all shown.")


_RECALL_TOOL = {
    "name": "recall_conversation",
    "description": (
        "Use when someone asks about this conversation itself: what their first "
        "(or previous, or Nth) question was, or what you answered to it — "
        "'我在这次对话里问的第一个问题是什么？你当时怎么回答的？', 'what did I "
        "just ask?'. The server holds the whole conversation; you may only be "
        "shown its last few turns."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "position": {
                "type": "integer",
                "description": (
                    "Which earlier question: 1 = the first of the conversation, "
                    "2 = the second, ...; -1 = the one just before this message, "
                    "-2 = the one before that."
                ),
            },
        },
        "required": ["position"],
    },
}

# ── A purchase from requisition to payment (ISS-0030) ────────────────────────

_PLAN_CHAIN_RULES = """\
- Progress ACROSS documents is trace_document, with the document they named
  (or the one on screen): "PR-20260928-0001 从请购到付款的完整进度是怎样的？",
  "has this PO been received, invoiced and paid?", "what happened after this
  PR was approved?", "which PA pays this invoice, and is it paid?", "where is
  this purchase up to?". It follows PR → PO → goods receipts → invoices →
  payment applications → payment. explain_workflow is the approval steps of
  ONE PR, PO or PA and says nothing about what came after its approval — the
  answer that used it ended "it has entered the payment stage", which was
  made up. A query reaches one kind of document only.
"""

_TRACE_TOOL = {
    "name": "trace_document",
    "description": (
        "Use for where a purchase stands across ALL its documents: the "
        "requisition, its purchase order(s), goods receipts, invoices, payment "
        "applications and whether they were paid — '从请购到付款的完整进度', "
        "'has PO-X been invoiced and paid', 'what happened after this PR'. "
        "Start from any document in the chain. Each document comes back with "
        "its status, as the Document Chain on the PO page shows it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_type": {"type": "string",
                         "enum": ["pr", "po", "gr", "invoice", "pa", "agreement"]},
            "doc_number": {
                "type": "string",
                "description": (
                    "The number as written, e.g. PR-20260928-0001, "
                    "PO-535-2609-02, INV-2026-0631. Omit only when they clearly "
                    "mean the document on screen."
                ),
            },
        },
        "required": ["doc_type"],
    },
}

_CHAIN_SYSTEM = """\
You are telling a colleague where a purchase stands, from requisition to
payment.

You are given the chain of documents around the one they asked about, read
from the system: the purchase request, each purchase order with its goods
receipts, invoices and payment applications, and, for agreement purchases, the
agreement with its invoices and payment applications.

- Answer what they asked. For "the full progress", walk it in order — PR → PO
  → goods receipts → invoices → payment applications → payment — one line per
  document: its number, its status (status_label when there is one) and the
  date or amount that matters for that step. Then say in one sentence where it
  stands now and what is still outstanding.
- Report statuses exactly as given. `payment` on a payment application is
  whether the money has gone out: "paid" (give paid_on), "awaiting payment",
  "not yet — still in approval". An approved payment application is NOT paid.
  Never write that a purchase "has entered the payment stage" or the like
  without naming the payment application and its payment state.
- `facts` on a purchase order were decided by the system (anything_received,
  invoiced, pa_raised, three_way_matched). Use them for "has it been
  received / invoiced"; never re-derive them from the lists.
- `waiting_on` is the approval step, and the approver, a document still in
  approval is with.
- A layer with `not_visible` is one this person may not see: say it cannot be
  shown to them, never that there are none. A count of 0 means none yet.
- `listed` means only the first `listed` of `count` documents are given: give
  the count and say the rest are on the document's page.
- `no_order_yet`: the requisition has no purchase order yet. `more_orders`:
  that many further orders are not shown.
- Every amount with its currency. Do not add amounts up.
- Never invent a document, a status, a date or a step that is not in what you
  were given.
"""


async def narrate_chain(message: str, chain: dict) -> dict:
    """Tell where a purchase stands. Same no-new-facts rule as the others."""
    import anthropic

    payload = {"question": message, **chain, **reply_language.instructions(message)}
    try:
        resp = await _client().messages.create(
            model=MODEL,
            max_tokens=_GUIDE_MAX_TOKENS,
            system=reply_language.LANGUAGE_RULES + _CHAIN_SYSTEM,
            messages=[{"role": "user",
                       "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        )
    except anthropic.APIConnectionError as exc:
        raise LlmUnavailable(f"Could not reach the model: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LlmUnavailable("The shared API quota is exhausted right now") from exc
    except anthropic.APIStatusError as exc:
        log.error("chain narration failed %s: %s", exc.status_code, exc.message)
        raise LlmUnavailable(f"Model returned {exc.status_code}") from exc

    _log_usage("narrate_chain", resp)
    return {"text": _reply_text(resp, message), "usage": _usage(resp)}


_WHO_AM_I_TOOL = {
    "name": "who_am_i",
    "description": (
        "Use when someone asks who they are in the system: their name, their "
        "department, their roles — '我是谁？我有哪些角色？', 'what roles do I "
        "have', 'which department am I in'. Returns their own record and every "
        "role they hold, as the system resolves it. Takes no arguments."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

# Tool name -> the `kind` plan() returns for it.
_SESSION_TOOL_KINDS = {
    "recall_conversation": "recall",
    "who_am_i": "me",
    "trace_document": "trace",
    "split_question": "split",
}


def _session_tools(context: dict | None) -> list[dict]:
    """The planner tools this section adds. A part of a split message is
    planned on its own and is never split again."""
    tools = [_TRACE_TOOL, _RECALL_TOOL, _WHO_AM_I_TOOL]
    if not (context or {}).get("question_part"):
        tools.append(_SPLIT_TOOL)
    return tools


# ── One message, several questions (ISS-0037, ISS-0069) ──────────────────────
#
# "PO-576-2610-01 的状态、PO-535-2609-02 的金额、供应商 Raise The Standard 的联系人
# 分别是什么？" ran one query for the first PO and called the other two "not in
# the query result"; "PO-079 是什么状态，INV-2026-0123 金额多少，怎么催审批人，再
# 帮我写一首诗" answered the PO and said the invoice amount was unavailable —
# asked alone, it is answered. The planner was built to run one tool per
# message and plan() returned the first. A message that asks several things is
# now split into its parts here, and each part is planned, checked against its
# own permissions and answered on its own (assistant_chat._answer_parts) — at
# most MAX_PARTS of them, so the cost of one message stays bounded; any
# further parts are named as not looked up.
MAX_PARTS = 3

_PLAN_PARTS_RULES = """\
- When ONE message asks two or more SEPARATE questions that need different
  lookups — documents of different kinds, a data question next to a
  how-does-it-work question, a request outside purchasing — call
  split_question with each part as a complete question of its own. Never
  plan only the first part: the others were then reported "not in the query
  result" although the data was there. Several facts about the same document,
  the same facts about several documents of one kind (one query with `in`),
  and every condition on one list are ONE question — do not split those.
"""

_SPLIT_TOOL = {
    "name": "split_question",
    "description": (
        "Use when one message asks several separate questions that each need "
        "their own lookup or route, e.g. 'PO-079-2605-03 是什么状态，INV-2026-0123 "
        "金额多少，怎么催审批人，再帮我写一首诗'. Each part is then planned and "
        "answered separately, in order."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "parts": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "The separate questions, in the order asked, each written "
                    "as a complete question in the person's own language and "
                    "words, with the document numbers or names it needs — "
                    "e.g. ['PO-079-2605-03 是什么状态？', 'INV-2026-0123 金额多少？', "
                    "'怎么催审批人？', '帮我写一首诗']."
                ),
            },
        },
        "required": ["parts"],
    },
}


def _part_note(part) -> str:
    if not isinstance(part, dict):
        return ""
    return (f"\n\nThis is part {part.get('index')} of {part.get('of')} of a longer "
            f"message the person sent: «{part.get('whole')}». Plan THIS part only — "
            f"the other parts are answered separately. Use the whole message only "
            f"to understand what this part refers to.")


def _language_note(message: str) -> str:
    """The reply language, decided in code (see reply_language), and what to do
    with a message that only asks for a different language."""
    language = reply_language.detect(message)
    note = ("\n\nAny text you write for the person (cannot_answer's explanation) "
            f"must be in {language or 'the language of THIS message — not of earlier turns'}.")
    if reply_language.is_language_only_request(message):
        note += ("\nThis message only asks for the answer in another language. It "
                 "is not a new question: plan the PREVIOUS question in the "
                 "conversation again, exactly as before.")
    return note


async def plan(schema: list[dict], message: str, context: dict | None = None,
               retry_error: str | None = None,
               history: list[dict] | None = None) -> dict:
    """Ask for a query description.

    Returns {"kind": "query", "query": {...}} or
            {"kind": "cannot", "reason": ..., "explanation": ...}.
    `retry_error` feeds a validator rejection back for one more attempt — the
    rejection names what is available, so the second try is usually right.

    `history` is the conversation so far, which is what lets "it" and "that one"
    resolve. Someone who has just asked about PO-400-2608-10 and then asks "who
    is it with?" is not going to repeat the number, and without the prior turns
    the only honest answer is to ask which document they mean — correct, and
    useless. The history only shapes what the model UNDERSTANDS; it never widens
    what the query may reach, which is settled downstream by the row scope.
    """
    import anthropic

    user_text = message + _today_note() + _context_note(context) + _language_note(message)
    if retry_error:
        user_text += (
            f"\n\nYour previous query was rejected: {retry_error}\n"
            "Correct it using only names from the schema, or call cannot_answer."
        )

    system = [
        {"type": "text", "text": (_PLAN_SYSTEM + _PLAN_SESSION_RULES + _PLAN_CHAIN_RULES
                                  + _PLAN_PARTS_RULES)},
        # The schema is the stable half of the prompt and by far the larger one,
        # so it sits in its own block for caching.
        {"type": "text", "text": "Schema:\n" + json.dumps(schema, ensure_ascii=False),
         "cache_control": {"type": "ephemeral"}},
    ]

    try:
        resp = await _client().messages.create(
            model=MODEL,
            max_tokens=_PLAN_MAX_TOKENS,
            system=system,
            tools=[_QUERY_TOOL, _CHECK_TOOL, _WORKFLOW_TOOL, _NEXT_TOOL,
                   _PROCESS_TOOL, _EXPLAIN_PLAN_TOOL, _DOC_TYPES_TOOL,
                   _MODULES_TOOL, _PRODUCIBLE_TOOL, _REPORT_TOOL,
                   _BUDGET_VARIANCE_TOOL, _RECEIVING_TOOL, _VENDOR_SUMMARY_TOOL,
                   _rules_tool(), _DASHBOARD_TOOL, *_session_tools(context),
                   _CANNOT_TOOL],
            # Forcing a tool call removes the third option — prose that sounds
            # like an answer but was never checked against any data.
            tool_choice={"type": "any"},
            # The same question must take the same route (ISS-0046, ISS-0061:
            # asked three times, three different answers). Still Haiku.
            temperature=0,
            messages=(history or []) + [{"role": "user", "content": user_text}],
        )
    except anthropic.APIConnectionError as exc:
        raise LlmUnavailable(f"Could not reach the model: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LlmUnavailable("The shared API quota is exhausted right now") from exc
    except anthropic.APIStatusError as exc:
        log.error("plan call failed %s: %s", exc.status_code, exc.message)
        raise LlmUnavailable(f"Model returned {exc.status_code}") from exc

    _log_usage("plan", resp)

    for block in resp.content:
        if block.type != "tool_use":
            continue
        if block.name in _SESSION_TOOL_KINDS:
            return {"kind": _SESSION_TOOL_KINDS[block.name], **dict(block.input),
                    "usage": _usage(resp)}
        if block.name == "run_query":
            return {"kind": "query", "query": dict(block.input), "usage": _usage(resp)}
        if block.name == "whats_next":
            return {"kind": "next", "usage": _usage(resp)}
        if block.name == "explain_process":
            return {"kind": "process", **dict(block.input), "usage": _usage(resp)}
        if block.name == "how_many_can_we_make":
            return {"kind": "producible", **dict(block.input), "usage": _usage(resp)}
        if block.name == "explain_modules":
            return {"kind": "modules", "usage": _usage(resp)}
        if block.name == "explain_document_types":
            return {"kind": "doc_types", **dict(block.input), "usage": _usage(resp)}
        if block.name == "explain_rules":
            return {"kind": "rules", "areas": _areas_of(block.input), "usage": _usage(resp)}
        if block.name == "my_dashboard":
            return {"kind": "dashboard", "usage": _usage(resp)}
        if block.name == "vendor_summary":
            return {"kind": "vendor_summary", **dict(block.input), "usage": _usage(resp)}
        if block.name == "receiving_report":
            return {"kind": "receiving", **dict(block.input), "usage": _usage(resp)}
        if block.name == "budget_vs_actual":
            return {"kind": "variance", **dict(block.input), "usage": _usage(resp)}
        if block.name == "explain_report":
            return {"kind": "report", **dict(block.input), "usage": _usage(resp)}
        if block.name == "explain_plan":
            return {"kind": "plan", **dict(block.input), "usage": _usage(resp)}
        if block.name == "explain_workflow":
            return {"kind": "workflow", **dict(block.input), "usage": _usage(resp)}
        if block.name == "check_document":
            return {"kind": "check", **dict(block.input), "usage": _usage(resp)}
        if block.name == "cannot_answer":
            out = {"kind": "cannot", **dict(block.input), "usage": _usage(resp)}
            if isinstance(out.get("explanation"), str):
                out["explanation"] = strip_markup(out["explanation"])
            return out

    # tool_choice=any should make this unreachable; treat it as "no answer"
    # rather than inventing one.
    return {"kind": "cannot", "reason": "too_ambiguous",
            "explanation": "I could not turn that into a query.",
            "usage": _usage(resp)}


async def narrate(message: str, query: dict, result: dict) -> dict:
    """Write the reply from the rows. Returns {"text": ..., "usage": {...}}."""
    import anthropic

    payload = {
        "question": message,
        "query_that_ran": query,
        "row_count": result.get("row_count"),
        "truncated": result.get("truncated"),
        "rows": result.get("rows", []),
    }
    if "matched_rows" in result:
        payload["matched_rows"] = result["matched_rows"]
    if "total_rows" in result:
        payload["total_rows"] = result["total_rows"]
    if "totals" in result:
        payload["totals"] = result["totals"]
    if "assumption" in result:
        payload["assumption"] = result["assumption"]
    if "value_labels" in result:
        # The payload is a whitelist, so a key added to the query result does
        # not reach this call by itself — which is how the mapping got built,
        # threaded through, and still reported back as "type 2".
        payload["value_labels"] = result["value_labels"]
    payload.update(reply_language.instructions(message))
    try:
        resp = await _client().messages.create(
            model=MODEL,
            max_tokens=_NARRATE_MAX_TOKENS,
            system=reply_language.LANGUAGE_RULES + _NARRATE_SYSTEM,
            messages=[{"role": "user",
                       "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        )
    except anthropic.APIConnectionError as exc:
        raise LlmUnavailable(f"Could not reach the model: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LlmUnavailable("The shared API quota is exhausted right now") from exc
    except anthropic.APIStatusError as exc:
        log.error("narrate call failed %s: %s", exc.status_code, exc.message)
        raise LlmUnavailable(f"Model returned {exc.status_code}") from exc

    _log_usage("narrate", resp)
    text = _reply_text(resp, message)
    return {"text": text, "usage": _usage(resp)}


_PREFLIGHT_SYSTEM = """\
You are explaining to a colleague why a document can or cannot be submitted.

You are given the document number and the result of running the real gates
against it. Each check says whether it passed, what it says when it fails, and
crucially whether it is something THIS PERSON can fix.

- Answer in the language the question was asked in.
- Lead with the verdict: can it be submitted, or not.
- List only the checks that FAILED. Passing ones are noise.
- Separate what they can fix themselves from what they cannot. For anything with
  fixable_by_user false, say plainly that it is not theirs to fix and name the
  owner if one is given. Getting this wrong sends someone to change a setting
  they have no access to.
- If a failed check has a fix_route, mention where to go.
- If complete is false, the approval engine could not be reached, so the list is
  partial — say so rather than implying it is the whole picture.
- Do not invent reasons, next steps, or people. The checks are all you know.
- The same applies to an action on a purchase order (creating a payment
  application or a goods receipt, submitting a sign-off): lead with whether
  it can be done now and list what is in the way. `not_checked` is what was
  not evaluated — mention it, so passing checks are not read as a promise.
"""


async def narrate_preflight(message: str, doc_number: str | None,
                            preflight: dict) -> dict:
    """Turn a gate result into a reply. Same no-new-facts rule as narrate()."""
    import anthropic

    payload = {
        "question": message,
        "document": doc_number,
        "allowed": preflight.get("allowed"),
        "complete": preflight.get("complete"),
        "checks": preflight.get("checks", []),
        # PO actions (po_action_check) say what they could not evaluate.
        **({"not_checked": preflight["not_checked"]} if preflight.get("not_checked") else {}),
        **reply_language.instructions(message),
    }
    try:
        resp = await _client().messages.create(
            model=MODEL,
            max_tokens=_NARRATE_MAX_TOKENS,
            system=reply_language.LANGUAGE_RULES + _PREFLIGHT_SYSTEM,
            messages=[{"role": "user",
                       "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        )
    except anthropic.APIConnectionError as exc:
        raise LlmUnavailable(f"Could not reach the model: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LlmUnavailable("The shared API quota is exhausted right now") from exc
    except anthropic.APIStatusError as exc:
        log.error("preflight narration failed %s: %s", exc.status_code, exc.message)
        raise LlmUnavailable(f"Model returned {exc.status_code}") from exc

    _log_usage("narrate_preflight", resp)
    return {"text": _reply_text(resp, message),
            "usage": _usage(resp)}


_WORKFLOW_SYSTEM = """\
You are telling a colleague where a document stands in its approval chain.

You are given the chain's steps, everything already done to it, and where it is
sitting right now.

- Answer in the language the question was asked in.
- Lead with the answer to what they asked. If they asked who it is with, name
  that first; do not open with a recap of the whole chain.
- current_step.approver_name is the person holding it. When that is null the
  step is broadcast to everyone holding the role — say it is with whoever holds
  that role (current_step.label), not with nobody.
- An approver_name containing "standing in for" means someone is covering a
  delegation. Say so; it matters to whoever is chasing it.
- history is what has already happened, most useful as "approved by X on DATE".
  Include comments when they are there — a returned document's comment is
  usually the reason. An event with `imported` true was carried over from the
  old PMS system when the document was migrated; it is not a comment anyone
  wrote. Each event's actor_role is the role the person acted in.
- current_step is null when the document is not awaiting approval. Then say what
  its status is instead of inventing a waiting party.
- If steps_available is false the engine could not be reached, so you have the
  history but not the full chain — say that rather than implying the chain is
  short.
- For a payment application, `milestones.paid_on` is the payment date Finance
  recorded (null = not paid yet); submitted_at and approved_at are when it was
  submitted and fully approved. Payment is not an approval step, so it is not
  in history.
- A comment is not a payment account, a bank or a payment date. If what was
  asked is not in what you were given, say it is not shown here — never read
  it out of a comment.
- Never invent a name, a step, a date or a role that is not in what you were
  given.
"""


_NARRATE_PLAN_SYSTEM = """\
You explain why a production plan schedules what it does, to a planner who
knows the product and not the algorithm.

The derivation arrives as ordered steps read off the plan line, plus any flags
the planning engine set. Walk the steps in order — demand, what was already
available, what is left to make — and then give the flags, which are the reasons
a line departs from that plain subtraction.

Rules:
- Every figure is already in the data. Do not compute new ones, and do not
  round: a planner reconciles these against their own spreadsheet.
- The flag meanings given are the engine's own descriptions. Use them; do not
  soften or reinterpret them.
- If `arithmetic_accounts_for_it` is false and no flag explains the difference,
  say plainly that the quantity does not follow from the recorded figures and
  that something outside them moved it. Do not invent a reason to close the gap.
- If several lines come back, the question did not identify one. Show them and
  ask which — a plan can carry several lines for one material and month.
- Answer in the language the question was asked in.
"""


async def narrate_plan(question: str, explained: list[dict],
                       history=None) -> dict:
    """Turn one or more derivations into an explanation."""
    payload = {"question": question, "lines": explained,
               **reply_language.instructions(question)}
    client = _client()
    resp = await client.messages.create(
        model=MODEL, max_tokens=1200,
        system=[{"type": "text", "text": reply_language.LANGUAGE_RULES + _NARRATE_PLAN_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=(history or []) + [{
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, default=str),
        }],
    )
    return {"text": _reply_text(resp, question),
            "usage": _usage(resp)}


async def narrate_workflow(message: str, view: dict) -> dict:
    """Explain an approval chain. Same no-new-facts rule as the other narrators.

    The question goes in with the chain. It used to be left out — only `view`
    was sent — so every reply was the same English status recap whatever was
    asked and in whatever language (ISS-0004): "这张单的供应商是谁？" and
    "用中文回答" both came back as "PR-… is approved" plus its history.
    """
    import anthropic

    payload = {"question": message, **view, **reply_language.instructions(message)}
    try:
        resp = await _client().messages.create(
            model=MODEL,
            max_tokens=_NARRATE_MAX_TOKENS,
            system=reply_language.LANGUAGE_RULES + _WORKFLOW_SYSTEM,
            messages=[{"role": "user",
                       "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        )
    except anthropic.APIConnectionError as exc:
        raise LlmUnavailable(f"Could not reach the model: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LlmUnavailable("The shared API quota is exhausted right now") from exc
    except anthropic.APIStatusError as exc:
        log.error("workflow narration failed %s: %s", exc.status_code, exc.message)
        raise LlmUnavailable(f"Model returned {exc.status_code}") from exc

    _log_usage("narrate_workflow", resp)
    return {"text": _reply_text(resp, message),
            "usage": _usage(resp)}


_GUIDE_SYSTEM = """\
You are helping a colleague who may never have been trained on this system.

- Answer in the language the question was asked in. Match it exactly: a
  question in Chinese is answered in Chinese, not in another CJK language —
  one run of this prompt answered a Chinese question in Korean, which reads as
  a broken system even when every number in it is right.
- Write for someone who does not know the jargon. Say "the person who approves
  for your department", not "the dept_manager node".
- For a task list: every number is in the payload and was counted for you.
  `open_task_count` is the total, `urgent_count` / `normal_count` split it, and
  `groups` is the count per kind of task with the SAME label the Task Inbox
  shows as its group header. Report those numbers exactly — never estimate,
  never write "~", "about" or "40+", never re-group them, never add them up
  yourself, and never count `examples`: it is only the first few tasks (urgent
  first, then the oldest), `examples_left_out` were not sent to you at all.
  Lead with the total and the urgent one(s), then the groups as a table, then
  name a few `examples` by document number so they can find them. How many
  reached them personally and how many through a role they hold is
  `assigned_to_me_personally_count` / `reached_me_through_a_role_count` — never
  generalise that, or anything else, from the examples.
- A task marked assigned_to_me_personally false reached them through a role they
  hold — worth saying, because "why is this mine?" is the next question.
- Overdue means past its due date, and `overdue_count` is that count. When
  `with_due_date_count` is 0 no task has a due date, so none is overdue — say
  so (the tasks `rules` explain why) and offer the urgent ones or the oldest
  instead. Urgent is a priority, not lateness: never call an urgent task
  overdue, and never call the rest "on time".
- For a dashboard (`kind` dashboard): give every card in `kpis` with its value
  exactly as given (title: value), in the order given, then `users_by_role`
  when present. Then add, briefly, what `rules` say about any card shown —
  that a figure is a fixed label, always 0, or not what its title suggests.
- Answer only what was asked. A question about an escalation, a reminder or
  any single rule is answered with that, not with the approval chain; walk
  the steps only when they asked how the chain runs. An approval chain
  nobody asked about is noise, and twice it was added to an answer about
  goods-receipt escalation (ISS-0019).
- For a process: walk the steps in order, in plain sentences. A step with
  `conditional` true does NOT always run — give its `condition` verbatim in your
  own words, because someone told a step is mandatory will wait for an approval
  that is never coming. A step without that flag always runs.
- A chain says who APPROVES, in what order. It does not say who carries the
  document out afterwards — who executes a payment, places an order, books an
  invoice. Asked "can X pay / who pays", say which approval step X holds, if
  any, and that executing the payment is not part of the approval chain you
  were given. Never name the last approver as the one who pays: tested on
  "AP Clerk 能执行付款吗？", the reply said the Finance Manager executes the
  payment because that step is last — invented, and wrong.
- `rules` (document types; for a PR process, the over-budget rules),
  `engine_rules` (processes: how every chain behaves — resuming after a
  return, one person on two steps, an empty post, the System Admin, who may
  approve or cancel, cross-department payments) and
  `how_approvals_are_given` (processes) state how
  the system behaves, each one verified against the code. When the question is
  one of them, the rule IS the answer: give it plainly and the same way every
  time, never hedge it, never contradict it, and never answer the opposite
  from general knowledge of how such systems usually work. If no rule covers
  what was asked, say that you have no rule for that part — do not reason one
  out, and do not tell them to ask an administrator, Finance, IT or anyone
  else, or name a role, page or setting that is not in what you were given.
- For system rules (`kind` rules): `areas[].rules` are statements of how the
  system behaves, each verified against the code, with current settings
  already filled in. Find the rule(s) that answer the question and give them
  plainly — the same way every time, never hedged, never contradicted, never
  replaced by how such systems usually work. A rule that says something does
  NOT exist or is NOT done is a full answer: say "no" and why. Answer every
  part of the question that a rule covers, and only those parts; do not add
  rules nobody asked about. For a part no rule covers, say you have no rule
  for it — never suggest asking someone, and never name a role, page or
  setting that is not in the rules. Rule ids are for you, not for the reader:
  refer to another rule by what it says. `unavailable` names rules or areas
  that could not be read just now — say that part could not be checked.
- For a purchase request, `over_budget` is what happens when the PR is over
  budget, as configured right now. Asked about over-budget, lead with it: name
  `mode_label`, then the `added_steps` in order — they come BEFORE the normal
  chain — or, when `blocks_submission` is true, that such a PR cannot be
  submitted at all. When `justification_required` is true, say that the
  requester must write a justification on the PR — in every such mode,
  including one that adds no steps. Give `note` in full: it applies only to
  over-budget PRs; the mode is read when the PR is submitted and kept for it; a
  stricter mode set later never adds steps to a PR already submitted, and a
  looser one skips over-budget steps it has not yet passed. Do not shorten
  this to "fixed when submitted". Describe only those steps; never
  add one, and never call the over-budget step by another role's name.
- If steps_available is false the engine could not be reached; say the chain
  could not be read rather than describing one from memory. You do not know
  this process except from what you were given.
- The payload was produced by the system, not by the person: never say they
  provided it or that it is "the data you gave".
- For document types: this is the form, not the approval chain. Do not describe
  who approves anything — that was not asked and is a different question.
  Give the types as a table when there are more than three, one row each, and
  make the columns the things that actually differ. `covers` and `when_to_pick`
  say what the type is for; `required_before_submit` is what the system will
  stop them on; `receipt` is how the purchase gets confirmed as received.
  Read `requirements_are` before describing either requirement list — it says
  exactly what each covers. `required_to_create_any` applies to every kind;
  `required_before_submit` is the EXTRA gates checked when the document leaves
  draft, and an empty list there means no extra gates, never "no requirements".
  Neither list is every difference between the kinds, so do not present it as
  one. `what_it_is` explains the document itself — lead with it when they seem
  to be asking what the thing is, not just how the kinds differ.
  If `how_types_are_set` is present, say so: for most documents the kind is not
  chosen by the person asking, and someone hunting for a dropdown that does not
  exist needs telling.
  If a type carries `notes`, work them in; they are there because they matter.
  End with the one line a requester most needs: which type their case sounds
  like, if the question named one.
- For "how many can we make": the number in `can_make` is the answer and it was
  computed for you — never recompute it, never adjust it, and never add a
  material's quantity to another's. Lead with the number, then name what is
  limiting it (`limited_by`) and what that material's situation is, because
  "what do I do about it" is the next question. A material whose problem says
  stock exists but none is usable is a different problem from one with no stock
  at all — say which. Mention anything in `excluded` and `could_not_compute`;
  a figure computed with a material left out is only honest if the omission is
  stated. Pass on `basis` in your own words — this is a rough reference, and
  presenting it as a production commitment would be the wrong reading.
- For modules: answer what they asked and no more. Someone asking where to
  claim an expense wants OA named and a sentence on why, not a tour of eight
  modules. Give the full list only when they asked for the full list.
  `can_explain_further` and `can_explain_reports` are the only places you may
  offer to go deeper; offering a document kind or a report that is not in them
  promises something that will then be refused.
- For budget vs actual: the numbers were computed for you — never recompute
  one, never add a column of them up yourself, and never convert between the
  two comparisons. Lead with a table: cost centre, annual plan, actual to date,
  variance, variance %. Print each code and name EXACTLY as given, even when the
  rest of the answer is in another language — they are identifiers, and someone
  who searches the system for a name you translated will not find it.
  `variance` is plan MINUS actual, so positive is under
  budget — say which way round it is, because half of finance reads it the
  other way. A row with `no_approved_plan` true has spending and no budget: it
  is not "under budget", it has no budget, and a percentage is deliberately
  absent there rather than zero.
  Two comparisons are given and they answer different questions — against the
  whole year's plan, and against the same months of it. Use the one they asked
  for, and if they only said "budget", give the annual one and name it.
  `not_in_the_actual` is spending the actual side could not place. Mention it
  whenever it is non-zero: an actual that is missing money reads as a cost
  centre doing well. `nothing_visible` and `actual_unavailable` are refusals —
  pass them on as they are, and never fall back to presenting the plan alone as
  though it answered a question about variance.
  Every amount is in `currency` (CAD): write it with the amounts, never 元,
  万元 or another unit, and never rescale the numbers. Say which months the
  actual covers (1 to `through_month` of `fiscal_year`); a current year does
  not run "to December".
- For a vendor summary (`kind` vendor_summary): one short section per kind of
  document in `sections` — counts and totals exactly as given, each amount
  with its currency, never added across currencies or across sections.
  Totals leave out cancelled, rejected and draft documents (say so once);
  `by_status` gives the counts of every status, cancelled included. A section
  with `not_visible` is one this person may not see: say it is not shown
  rather than "none". `candidates` means the name matched several vendors:
  list them and ask which one. Do not add opinions such as "a stable
  relationship" — report the figures.
- For the Receiving Report (`kind` receiving_report): give lines, receipts,
  orders and the average lead time for `period` exactly as given, and say the
  report counts physical receipts only and leaves out NC-mirrored orders.
  `avg_lead_days` null means no line in the window has an order date — say
  that, never 0. Lines are received lines, not receipts.
- For one cost centre × account (`kind` budget_account_balance): lead with
  `available` — when `over_budget_by` is set, say it is over budget by that
  amount and nothing is left. Then annual budget, committed and actual spent,
  in `currency`. This is the figure the PR form's budget widget shows. When
  `found` is false, say no such cost centre is visible to them (never "it has
  no budget"); `balance_unavailable` is passed on as it is.
- For a report's workings: they are looking at the report already, so do not
  describe what the module is for. Answer in the order they asked — usually
  where the number comes from first, then how it was assigned to a cost centre.
  Each entry in `figures` is one of the numbers in a cell and was reported by
  the service that computes it; use its own words for what it counts. `figures`
  is the WHOLE of what is on the screen — do not add a number to the cell from
  anywhere else, including from what the report used to show. Give
  `read_it_this_way` when it is there: what the ledger CURRENTLY holds is the
  difference between what a figure is designed to mean and what it means today,
  and someone reading `opening` rows as invoice activity is being misled by a
  correct description.
  `no_longer_shown` is not part of the report. Use it only to answer where a
  figure someone remembers has gone, and give `why_it_went`. Never say a figure
  will start filling in, or that amounts will appear once documents flow,
  unless what you were given says so: a figure whose `never_recorded` list
  names the operations that would fill it is a figure nothing is writing to,
  and promising it will populate is inventing a plan for someone else's
  roadmap.
  The precedence under `cost_centre_rule` is an order — say it as one, first
  match wins — and say what happens when nothing matches, because that case is
  a visible exception rather than a silent default.
  If they named a code, find it in `cost_centre_mapping.rules` and answer with
  that row before anything general. If `available` is false, give `why` and
  point them at whom to ask; do not describe rules you were not given.
  Anything named in `unavailable` was not readable just now — say that part
  could not be checked. Do not fill it in.
- Never invent a step, a role, a rule, a task, a module, or a field. If a type
  has no requirement beyond what every type has, say that plainly — "nothing
  extra" is an answer, and inventing a distinction to fill the row is not.
"""


async def narrate_guide(message: str, payload: dict) -> dict:
    """Explain tasks or a process. Same no-new-facts rule as the others."""
    import anthropic

    payload = {**payload, **reply_language.instructions(message)}
    try:
        resp = await _client().messages.create(
            model=MODEL,
            max_tokens=_GUIDE_MAX_TOKENS,
            # A rule answered three ways in three tries is not a rule
            # (ISS-0046, ISS-0028). Still Haiku.
            temperature=0,
            system=reply_language.LANGUAGE_RULES + _GUIDE_SYSTEM,
            messages=[{"role": "user",
                       "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        )
    except anthropic.APIConnectionError as exc:
        raise LlmUnavailable(f"Could not reach the model: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LlmUnavailable("The shared API quota is exhausted right now") from exc
    except anthropic.APIStatusError as exc:
        log.error("guide narration failed %s: %s", exc.status_code, exc.message)
        raise LlmUnavailable(f"Model returned {exc.status_code}") from exc

    _log_usage("narrate_guide", resp)
    return {"text": _reply_text(resp, message),
            "usage": _usage(resp)}


_FIXED_REPLY_SYSTEM = """\
You pass on a short message the system wrote, to a colleague who asked a
question in another language.

- Write the message in `reply_in`, keeping its meaning exactly. Do not add an
  explanation, an apology, an offer or a suggestion that is not in it.
- Document numbers, codes, product names and people's names are identifiers:
  keep them exactly as written.
- Reply with the message only.
"""


async def render_fixed_reply(text: str, question: str, language: str | None) -> dict:
    """A code-written reply (a refusal, "which document do you mean") in the
    asker's language. Only those replies come here — model-written ones already
    follow reply_language."""
    import anthropic

    payload = {"message": text, "question": question,
               **reply_language.instructions(question)}
    try:
        resp = await _client().messages.create(
            model=MODEL,
            max_tokens=400,
            system=reply_language.LANGUAGE_RULES + _FIXED_REPLY_SYSTEM,
            messages=[{"role": "user",
                       "content": json.dumps(payload, ensure_ascii=False, default=str)}],
        )
    except anthropic.APIConnectionError as exc:
        raise LlmUnavailable(f"Could not reach the model: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LlmUnavailable("The shared API quota is exhausted right now") from exc
    except anthropic.APIStatusError as exc:
        log.error("fixed-reply rendering failed %s: %s", exc.status_code, exc.message)
        raise LlmUnavailable(f"Model returned {exc.status_code}") from exc

    _log_usage("render_fixed_reply", resp)
    return {"text": _reply_text(resp, question),
            "usage": _usage(resp)}


# Tool-call markup the model sometimes writes INTO a text field: one refusal
# ended "…不在我能查询的数据范围内。\n</explanation>\n</invoke>" and was shown to
# the person like that (ISS-0009). Only these tag names are removed — the
# reply's own text, which may legitimately contain "<" and ">", is kept.
_MARKUP = re.compile(
    r"</?(?:explanation|invoke|parameter|function_calls|function_results|"
    r"antml:[a-z_]+|tool_use|tool_result|reason)(?:\s[^>]*)?>",
    re.IGNORECASE)


def strip_markup(text: str) -> str:
    return _MARKUP.sub("", text or "").strip()


# Said when a reply hit its length limit. A cut table used to simply stop
# mid-row ("... SELL-0113 SELL-ECOM 1,579,249.08 754"), with nothing to say
# it was incomplete (ISS-0014, PRD AST-003).
_CUT_NOTE = {
    "Chinese (Simplified)": "（回答过长，在这里被截断了。可以把问题问得更具体，或少要几行再问；列表类结果可以用下方的 Export to Excel 拿到完整数据。）",
}
_CUT_NOTE_DEFAULT = ("(The answer was too long and was cut off here. Ask a narrower "
                     "question or for fewer rows; for a list, Export to Excel below "
                     "has all of it.)")


def _reply_text(resp: Any, question: str | None = None) -> str:
    text = strip_markup("".join(b.text for b in resp.content if b.type == "text"))
    if getattr(resp, "stop_reason", None) == "max_tokens":
        # Drop the line that was being written when the limit hit — half a
        # table row or an unclosed **bold** reads as data — and say so.
        head, sep, _ = text.rstrip().rpartition("\n")
        text = (head if sep else text).rstrip()
        note = _CUT_NOTE.get(reply_language.detect(question or ""), _CUT_NOTE_DEFAULT)
        text = f"{text}\n\n{note}"
    return text


def _usage(resp: Any) -> dict:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    return {
        "input_tokens": getattr(u, "input_tokens", 0),
        "output_tokens": getattr(u, "output_tokens", 0),
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
    }


def _log_usage(stage: str, resp: Any) -> None:
    """Best-effort log line. Do NOT rely on it.

    This used to be the whole accounting story, and it recorded nothing: the
    application logger has no handler on the request path in the dev deployment,
    so every one of these went to a logger with nowhere to write. Spend is
    persisted by record_usage() instead; this line is only convenient when
    logging happens to be configured.
    """
    u = _usage(resp)
    log.info("assistant | stage=%s | model=%s | in=%s | out=%s | cache_read=%s",
             stage, MODEL, u.get("input_tokens"), u.get("output_tokens"),
             u.get("cache_read_input_tokens"))


async def record_usage(db, user_id, stage: str, usage: dict) -> None:
    """Persist what one call cost, on a session of its own.

    Not the request's session, and not for tidiness: controlled_query runs its
    statement inside `SET LOCAL TRANSACTION READ ONLY`, so an INSERT afterwards
    fails for the rest of that transaction. That is exactly what happened —
    plan's usage was recorded, narrate's silently was not, and the failure went
    to log.exception, which in this deployment writes nowhere. Bookkeeping that
    can be disabled by the thing it is measuring is not bookkeeping.

    Never raises: losing a row here must not lose an answer the user waited for
    and the key was already billed for.
    """
    if not usage:
        return
    try:
        from app.db.session import AsyncSessionLocal
        from app.models.assistant_usage import AssistantUsage
        async with AsyncSessionLocal() as own:
            own.add(AssistantUsage(
                user_id=user_id, stage=stage, model=MODEL,
                input_tokens=usage.get("input_tokens", 0) or 0,
                output_tokens=usage.get("output_tokens", 0) or 0,
                cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
            ))
            await own.commit()
    except Exception:  # noqa: BLE001 — bookkeeping must never break the reply
        log.exception("assistant: could not record usage for stage=%s", stage)

# ── Rule questions that reached a refusal (ISS-0022) ─────────────────────────
#
# The planner can still decline a question about how the system behaves —
# "not a data question", a document type outside a tool's enum, "which
# document do you mean" for a question that names none because it is general.
# Each of those used to end in text the model or the code wrote: "not in my
# data, ask the System Admin". Before any of them is said, this asks one
# narrow question of the same model: is this about a rule, and in which areas?
# Output is constrained to the registry's areas; no prose is produced.

_PICK_SYSTEM = """\
You route a colleague's question inside UniOps, a purchasing system.

Decide one thing: is the question about how the system BEHAVES — a rule, a
setting, who may do something, in which statuses, what happens when, how
often, whether a feature exists — rather than about specific records?

- If it is, call explain_rules with the area(s) it belongs to (up to three).
- If it asks about specific data or one specific document's own state
  ("has this one been approved", "how many POs", "where is PR-…"), call
  not_a_rule_question.
"""

_NOT_A_RULE_TOOL = {
    "name": "not_a_rule_question",
    "description": "The question is about specific data or a specific document.",
    "input_schema": {"type": "object", "properties": {}},
}


def _areas_of(tool_input) -> list[str]:
    """The areas a model asked for, kept to the registry's own."""
    from app.services import rule_registry

    raw = (tool_input or {}).get("areas")
    if isinstance(raw, str):
        raw = [raw]
    known = set(rule_registry.area_keys())
    return [a for a in dict.fromkeys(raw or []) if a in known][:3]


async def pick_rule_areas(message: str, history: list[dict] | None = None) -> dict:
    """{"areas": [...], "usage": {...}} — empty areas when it is not a rule
    question (or the model could not be reached: the caller then says what it
    would have said anyway)."""
    try:
        resp = await _client().messages.create(
            model=MODEL,
            max_tokens=256,
            temperature=0,
            system=_PICK_SYSTEM,
            tools=[_rules_tool(), _NOT_A_RULE_TOOL],
            tool_choice={"type": "any"},
            messages=(history or []) + [{"role": "user", "content": message}],
        )
    except LlmUnavailable:
        return {"areas": [], "usage": {}}
    except Exception as exc:  # noqa: BLE001 — a failed rescue is just no rescue
        log.warning("pick_rule_areas failed: %s", exc)
        return {"areas": [], "usage": {}}
    _log_usage("pick_rule_areas", resp)
    for block in resp.content:
        if block.type == "tool_use" and block.name == "explain_rules":
            return {"areas": _areas_of(block.input), "usage": _usage(resp)}
    return {"areas": [], "usage": _usage(resp)}
