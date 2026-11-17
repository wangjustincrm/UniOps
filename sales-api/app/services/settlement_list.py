"""The Settlements list (Finance, user 2026-11-17): one list of deposits, its uses as system views.
Read-only; sales.read to look."""
from __future__ import annotations

from app.models.settlement_list import GATEWAY_NAMES, ISSUES, SettlementRow
from app.platform.authorization import requires
from app.platform.query import QuerySpec
from app.platform.repository import Repository
from app.platform.services import ReadOnlyService
from app.platform.views import Preset
from app.schemas.settlement_list import SettlementRowRead

READ = "sales.read"
_NEWEST = [{"field": "settled_at", "desc": True}]


class SettlementRowRepository(Repository[SettlementRow]):
    model = SettlementRow
    search_fields = ("batch_id",)
    suggestable = ("batch_id",)
    list_fields = ("id", "gateway", "batch_id", "settled_at", "settled_date", "settled_week", "settled_month", "state",
                   "payment_method", "currency", "sales_amount", "sales_count", "refund_amount", "refund_count", "fee_amount",
                   "net_amount", "declined_count", "voided_count", "chargeback_amount", "chargeback_count", "returned_amount",
                   "returned_count", "other_count", "pending_count", "transaction_count", "matched_net", "difference",
                   "unmatched_count", "probable_count", "transactions_read_at", "issue")
    labels = {"id": "Settlement no.", "batch_id": "Batch ID", "settled_at": "Settled", "settled_date": "Date", "settled_week": "Week",
              "settled_month": "Month", "state": "State", "payment_method": "Payment method", "sales_amount": "Sales",
              "sales_count": "Sales (count)", "refund_amount": "Refunds", "refund_count": "Refunds (count)",
              "fee_amount": "Fees", "net_amount": "Net", "declined_count": "Declined", "voided_count": "Voided",
              "transaction_count": "Transactions", "matched_net": "Matched in Shopify", "difference": "Difference",
              "unmatched_count": "Unmatched", "probable_count": "Probable matches",
              "transactions_read_at": "Transactions read", "issue": "Issue", "chargeback_amount": "Chargebacks",
              "chargeback_count": "Chargebacks (count)", "returned_amount": "Returned items",
              "returned_count": "Returned items (count)", "other_count": "Other transactions",
              "pending_count": "Still matching"}
    enums = {"gateway": GATEWAY_NAMES, "issue": ISSUES}
    default_sort = ("-settled_at", "-id")
    groupable = ("gateway", "settled_week", "settled_month", "state", "issue", "currency")
    summable = ("sales_amount", "refund_amount", "net_amount", "matched_net", "difference")
    sum_currency = "currency"
    presets = (
        Preset("all", "All"),
        Preset("monthly", "By month", {"group": [{"field": "settled_month", "desc": True}], "sort": _NEWEST}),
        Preset("issues", "Needs a look", {
            "filters": [{"field": "issue", "op": "not_null", "value": None}],
            "group": [{"field": "issue", "desc": False}], "sort": _NEWEST}),
    )


REPORT_MAX = 2000  # deposits in one report (about five years of daily batches)


class SettlementRowService(ReadOnlyService[SettlementRow, SettlementRowRead]):
    repo_cls = SettlementRowRepository
    read_schema = SettlementRowRead
    read_permission = READ

    @requires(READ)
    async def statement_pdf(self, id: int) -> tuple[str, bytes]:
        """One deposit's settlement statement (sales.read)."""
        from app.services import print_docs
        row = await self.get(id)
        return f"settlement-{row.id}.pdf", await print_docs.settlement_pdf(self.uow.session, row)

    @requires(READ)
    async def report_pdf(self, spec: QuerySpec) -> tuple[str, bytes]:
        """The list as it is filtered and sorted now, as one report (sales.read)."""
        from app.services import print_docs
        rows, page_no = [], 1
        while len(rows) < REPORT_MAX:
            page = await self.list(spec.model_copy(update={"page": page_no, "page_size": 200}))
            rows += page.items
            if page_no * 200 >= page.total:
                break
            page_no += 1
        truncated = len(rows) > REPORT_MAX or (rows and len(rows) < page.total)
        rows = rows[:REPORT_MAX]
        dates = [r.settled_date for r in rows]
        period = f"{min(dates):%b %d, %Y} – {max(dates):%b %d, %Y}" if dates else "No deposits"
        name = f"settlements-{min(dates)}-{max(dates)}.pdf" if dates else "settlements.pdf"
        return name, await print_docs.settlements_report_pdf(rows, period=period, truncated=bool(truncated))
