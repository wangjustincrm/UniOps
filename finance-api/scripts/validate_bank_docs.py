"""Run the whole bank-reconciliation pipeline against real documents on disk.

Not a test — the real statements and payment files carry live vendor names and
amounts and stay out of the repo. This is what you point at a folder of them,
here and when the Chase / Bank of China / ICBC samples arrive.

Reads the statement PDF (page by page, Haiku), parses every payment file
(deterministic, layout mode), pulls the ledger side straight from NC, and runs
the matching ladder over all three. Prints what cleared, what did not, and why.

    docker run --rm --network host \
      -v /srv/uniops/bankDoc:/d:ro -v <worktree>/finance-api:/app:ro \
      -v <scratch>:/w \
      -e DATABASE_URL=... -e JWT_SECRET_KEY=x -e ANTHROPIC_API_KEY=... \
      -e NC_HOST=... -e NC_PORT=... -e NC_SERVICE=... -e NC_USER=... -e NC_PASSWORD=... \
      finance-api-test:bankrecon python /app/scripts/validate_bank_docs.py

Measured 2026-09-22 on RBC CAD July 2026: 37/37 statement lines and 260/260
ledger lines cleared, 237/237 advice lines linked, one finding naming the
statement line whose payment file was not in the folder.

The statement read is cached to /w so re-runs cost no AI quota; delete
stmt_verified.json to force a fresh read.
"""
import glob, json, os, sys
from datetime import date
from decimal import Decimal
sys.path.insert(0, "/app")

from app.services.bank_advice_parse import parse_advice, split_bill_payments
from app.services import bank_matching as M
from app.services.bank_book import classify_contra

# ── 1. statement (cached after the first real AI read) ─────────────────────────
CACHE = "/w/stmt_verified.json"
if os.path.exists(CACHE):
    payload = json.load(open(CACHE))
else:
    from app.services.bank_statement_parse import extract_payload
    payload = extract_payload(open("/d/RBC CAD 2026.7STATEMENT.pdf", "rb").read())
    json.dump(payload, open(CACHE, "w"))
from app.services.bank_statement_parse import payload_to_statement
st = payload_to_statement(payload)
print(f"statement: {len(st.lines)} lines, verified={st.verified}, "
      f"{len(st.debits)} debits {st.total_debits:,.2f} / {len(st.credits)} credits {st.total_credits:,.2f}")
for e in st.verify_errors: print("   !", e)

bank = [M.BankTxn(id=f"B{ln.seq}", txn_date=ln.txn_date, description=ln.description,
                  amount=ln.amount) for ln in st.lines]

# ── 2. advices ─────────────────────────────────────────────────────────────────
advices = []
for f in sorted(glob.glob("/d/PaymentFile/*.pdf")):
    for a in split_bill_payments(parse_advice(open(f, "rb").read())):
        advices.append(M.AdviceView(
            id=os.path.basename(f) + (f"#{a.confirmation_number}" if a.confirmation_number else ""),
            kind=a.kind, advice_date=a.advice_date, total=a.total, tie_ok=a.tie_ok,
            confirmation_number=a.confirmation_number, client_number=a.client_number,
            lines=[M.AdviceLineView(id=f"{os.path.basename(f)}:{ln.seq}",
                                    payee_name=ln.payee_name, amount=ln.amount)
                   for ln in a.lines]))
print(f"advices:   {len(advices)}  total {sum(a.total for a in advices):,.2f}")

# ── 3. NC ledger, RBC CAD, July — the real book side ───────────────────────────
import oracledb
oracledb.defaults.fetch_decimals = True
con = oracledb.connect(user=os.environ["NC_USER"], password=os.environ["NC_PASSWORD"],
                       dsn=oracledb.makedsn(os.environ["NC_HOST"],
                                            int(os.environ.get("NC_PORT", "1521")),
                                            service_name=os.environ["NC_SERVICE"]))
cur = con.cursor()
BP, BOOK = "0001Z010000000001N98", "1001A1100000003CGCBX"
cur.execute(f"""
 select d.pk_voucher, d.detailindex, v.num, v.prepareddate,
        d.explanation, d.localdebitamount, d.localcreditamount
   from NCSC.GL_DETAIL d
   join NCSC.GL_VOUCHER v on v.pk_voucher = d.pk_voucher
   left join NCSC.GL_FREEVALUE f on f.freevalueid = d.assid
   left join NCSC.BD_BANKACCSUB b on rtrim(b.pk_bankaccsub) = substr(f.typevalue1,21,20)
                                 and substr(f.typevalue1,1,20) = :p
  where d.pk_accountingbook = :b and d.accountcode = '100201'
    and b.code = '1033760' and v.year='2026' and v.period='07'
    and (v.discardflag is null or v.discardflag <> 'Y')
  order by v.prepareddate, v.num, d.detailindex""", p=BP, b=BOOK)
rows = list(cur)
# contra accounts per voucher
pks = list({r[0] for r in rows})
contra = {}
for i in range(0, len(pks), 500):
    chunk = pks[i:i+500]
    binds = {f"p{j}": v for j, v in enumerate(chunk)}
    cur.execute(f"""select pk_voucher, accountcode from NCSC.GL_DETAIL
                     where pk_voucher in ({','.join(':'+k for k in binds)})
                       and accountcode not like '1002%'""", **binds)
    for pk, acct in cur:
        contra.setdefault(pk, set()).add((acct or "").strip())
con.close()

books = []
for pk, idx, num, pdate, expl, dr, cr in rows:
    d = date.fromisoformat(pdate[:10])
    books.append(M.BookLineView(
        id=f"{pk}:{int(idx)}", voucher_date=d, summary=expl,
        amount=Decimal(str(dr or 0)) - Decimal(str(cr or 0)),
        contra_kind=classify_contra(sorted(contra.get(pk, []))),
        jv_number=f"JV-202607-{int(num or 0):04d}"))
tot = sum(b.amount for b in books)
print(f"book:      {len(books)} lines, net {tot:,.2f}  "
      f"(dr {sum(b.amount for b in books if b.amount>0):,.2f} / "
      f"cr {-sum(b.amount for b in books if b.amount<0):,.2f})")
kinds = {}
for b in books: kinds[b.contra_kind] = kinds.get(b.contra_kind, 0) + 1
print(f"           contra kinds: {kinds}")

# ── 4. the ladder ──────────────────────────────────────────────────────────────
p = M.plan(bank, advices, books)
cleared_bank = {b for g in p.groups for b in g.bank_ids}
cleared_book = {b for g in p.groups for b in g.book_ids}
by_method = {}
for g in p.groups: by_method[g.method] = by_method.get(g.method, 0) + 1
print(f"\nLADDER")
print(f"  groups        {len(p.groups)}   by rung: {by_method}")
print(f"  bank lines    {len(cleared_bank)}/{len(bank)} cleared")
print(f"  book lines    {len(cleared_book)}/{len(books)} cleared")
print(f"  advice lines  {len(p.advice_line_links)} linked to a ledger line (of 237)")
print(f"  findings      {len(p.findings)}")
for f in p.findings:
    print(f"    [{f.kind}] {f.message[:150]}")
print(f"\n  unmatched bank lines ({len(p.unmatched_bank)}):")
bmap = {t.id: t for t in bank}
for bid in p.unmatched_bank:
    t = bmap[bid]
    print(f"    {t.txn_date}  {t.amount:>14,.2f}  {t.description[:56]}")
print(f"\n  unmatched book lines ({len(p.unmatched_book)}), top 12 by size:")
kmap = {b.id: b for b in books}
for bid in sorted(p.unmatched_book, key=lambda i: -abs(kmap[i].amount))[:12]:
    b = kmap[bid]
    print(f"    {b.voucher_date} {b.amount:>13,.2f} [{b.contra_kind:<14}] {(b.summary or '')[:52]}")
