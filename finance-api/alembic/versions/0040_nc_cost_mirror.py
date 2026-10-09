"""NC65 inventory-accounting + cost mirror for AI Financial Analysis.

Spec: docs/superpowers/specs/2026-10-09-ai-financial-analysis-design.md §4.

Read-only copies of the NC tables the cost chain is computed from — inventory
ledger (IA_DETAILLEDGER), month-end balances (IA_MONTHNAB), product cost
(CM_PRODCOST/_B), material collection incl. amount-only adjustments
(CM_STUFF/_B), cost objects, and sales invoices. 2026 onwards only (decision
D4); the floor is a constant in services/nc_cost_sync.py.

Shape choices, all following from the volume (≈20k rows for 2026):

  * Every sync REPLACES the whole scope in one transaction. There is no TS
    watermark: a row deleted outright in NC advances no timestamp, so an
    incremental mirror can only ever lose that fact, and at this size a full
    read costs seconds.
  * Headers are denormalised onto lines (material code/name, trade-type name,
    element name, cost-object code) at read time, in NC, so every reader of
    the mirror sees the same labels NC showed on the day — no join to a master
    table that may have moved since.
  * `dr` is kept. NC deletes logically (1,982 of 14,762 ledger rows for 2026
    on 2026-10-09); the analysis filters dr = 0, the mirror stays faithful so
    the tie-out compares like with like.
  * No foreign keys between these tables: they are a snapshot of another
    system's rows, joined on NC's own ids.

`fa_config` is this module's own settings row (sync interval, anomaly
thresholds). Deliberately NOT columns on company_config: that table is
epms-owned, and every column a finance migration adds to it has to be
shadowed by hand in tests/conftest.py or the whole suite dies on
DuplicateColumn.

Revision ID: 0040_nc_cost_mirror
Revises: 0039_ap_inv_dup_reviews
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0040_nc_cost_mirror"
down_revision = "0039_ap_inv_dup_reviews"
branch_labels = None
depends_on = None

_Q = sa.Numeric(28, 8)   # NC quantities and unit prices carry up to 8 places
_M = sa.Numeric(18, 2)   # money


def _id():
    return sa.Column("id", UUID(as_uuid=True), primary_key=True,
                     server_default=sa.text("gen_random_uuid()"))


def _tail():
    return [
        sa.Column("dr", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("nc_ts", sa.String(19), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "nc_ia_ledger",
        _id(),
        sa.Column("nc_pk", sa.String(25), nullable=False, unique=True),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("bill_type", sa.String(4), nullable=False),        # I2 I3 I4 I5 I6 I7 I9 IA …
        sa.Column("bill_code", sa.String(40), nullable=True),
        sa.Column("bill_id", sa.String(25), nullable=True),
        sa.Column("bill_bid", sa.String(25), nullable=True),
        sa.Column("row_no", sa.String(20), nullable=True),
        sa.Column("biz_date", sa.Date(), nullable=True),
        sa.Column("direction", sa.SmallInteger(), nullable=False),   # 0 in, 1 out
        sa.Column("material_pk", sa.String(25), nullable=False),
        sa.Column("material_code", sa.String(40), nullable=True),
        sa.Column("material_name", sa.String(300), nullable=True),
        sa.Column("qty", _Q, nullable=True),
        sa.Column("price", _Q, nullable=True),
        sa.Column("amount", _M, nullable=True),
        sa.Column("batch_code", sa.String(60), nullable=True),
        sa.Column("mo_code", sa.String(40), nullable=True),
        sa.Column("src_type", sa.String(20), nullable=True),
        sa.Column("src_code", sa.String(60), nullable=True),
        sa.Column("first_code", sa.String(60), nullable=True),
        # The document BOTH legs of a warehouse transfer come from — read off
        # the other-receipt / other-issue row (IC_GENERALIN_B / _OUT_B), not
        # off the ledger: the two legs carry DIFFERENT src_codes (TR… vs TC…)
        # and only share this one (ZK… transfer order, 4K; XT… form
        # conversion, 4N). Pairing on src_code reported every transfer.
        sa.Column("upstream_type", sa.String(20), nullable=True),
        sa.Column("upstream_code", sa.String(60), nullable=True),
        sa.Column("upstream_bid", sa.String(25), nullable=True),
        sa.Column("trantype_code", sa.String(40), nullable=True),
        sa.Column("trantype_name", sa.String(200), nullable=True),
        sa.Column("estimate_flag", sa.String(1), nullable=True),     # Y = provisional (暂估)
        sa.Column("audit_seq", sa.Integer(), nullable=True),
        sa.Column("price_mode", sa.SmallInteger(), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        *_tail(),
    )
    op.create_index("ix_nc_ia_ledger_mat_period", "nc_ia_ledger", ["material_code", "period"])
    op.create_index("ix_nc_ia_ledger_period_type", "nc_ia_ledger", ["period", "bill_type"])
    op.create_index("ix_nc_ia_ledger_src_code", "nc_ia_ledger", ["src_code"])
    op.create_index("ix_nc_ia_ledger_mo_code", "nc_ia_ledger", ["mo_code"])
    op.create_index("ix_nc_ia_ledger_upstream", "nc_ia_ledger", ["upstream_code"])

    op.create_table(
        "nc_ia_month_balances",
        _id(),
        sa.Column("nc_pk", sa.String(25), nullable=False, unique=True),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("material_pk", sa.String(25), nullable=False),
        sa.Column("material_code", sa.String(40), nullable=True),
        sa.Column("qty", _Q, nullable=True),
        sa.Column("amount", _M, nullable=True),
        sa.Column("price", _Q, nullable=True),
        *_tail(),
    )
    op.create_index("ix_nc_ia_month_bal_mat_period", "nc_ia_month_balances",
                    ["material_code", "period"])

    op.create_table(
        "nc_cm_cost_objects",
        _id(),
        sa.Column("nc_pk", sa.String(25), nullable=False, unique=True),
        sa.Column("code", sa.String(60), nullable=True),
        sa.Column("name", sa.String(300), nullable=True),
        sa.Column("material_pk", sa.String(25), nullable=True),
        sa.Column("material_code", sa.String(40), nullable=True),
        *_tail(),
    )

    op.create_table(
        "nc_cm_prod_costs",
        _id(),
        sa.Column("nc_pk", sa.String(25), nullable=False, unique=True),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("cost_object_pk", sa.String(25), nullable=True),
        sa.Column("material_pk", sa.String(25), nullable=True),
        sa.Column("material_code", sa.String(40), nullable=True),
        sa.Column("material_name", sa.String(300), nullable=True),
        sa.Column("cost_center_pk", sa.String(25), nullable=True),
        sa.Column("fee_type", sa.SmallInteger(), nullable=True),
        sa.Column("qty", _Q, nullable=True),
        sa.Column("amount", _M, nullable=True),
        *_tail(),
    )
    op.create_index("ix_nc_cm_prod_costs_mat_period", "nc_cm_prod_costs",
                    ["material_code", "period"])

    op.create_table(
        "nc_cm_prod_cost_lines",
        _id(),
        sa.Column("nc_pk", sa.String(25), nullable=False, unique=True),
        sa.Column("prod_cost_pk", sa.String(25), nullable=False),
        sa.Column("period", sa.String(7), nullable=True),
        sa.Column("element_pk", sa.String(25), nullable=True),
        sa.Column("element_code", sa.String(40), nullable=True),
        sa.Column("element_name", sa.String(200), nullable=True),
        sa.Column("material_pk", sa.String(25), nullable=True),
        sa.Column("material_code", sa.String(40), nullable=True),
        sa.Column("material_name", sa.String(300), nullable=True),
        sa.Column("child_type", sa.SmallInteger(), nullable=True),
        sa.Column("qty", _Q, nullable=True),
        sa.Column("price", _Q, nullable=True),
        sa.Column("amount", _M, nullable=True),
        *_tail(),
    )
    op.create_index("ix_nc_cm_prod_cost_lines_head", "nc_cm_prod_cost_lines", ["prod_cost_pk"])

    # Material collection, header denormalised onto the line. source_type 18 is
    # an issue the cost object consumed; 31 is the amount-only outbound
    # adjustment where a settlement variance lands — the column that hid the
    # CR0059 August cost.
    op.create_table(
        "nc_cm_stuff_lines",
        _id(),
        sa.Column("nc_pk", sa.String(25), nullable=False, unique=True),
        sa.Column("stuff_pk", sa.String(25), nullable=False),
        sa.Column("bill_code", sa.String(40), nullable=True),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("cost_object_pk", sa.String(25), nullable=True),
        sa.Column("cost_object_code", sa.String(60), nullable=True),
        sa.Column("cost_object_name", sa.String(300), nullable=True),
        sa.Column("source_type", sa.SmallInteger(), nullable=True),
        sa.Column("business_date", sa.Date(), nullable=True),
        sa.Column("element_code", sa.String(40), nullable=True),
        sa.Column("element_name", sa.String(200), nullable=True),
        sa.Column("material_pk", sa.String(25), nullable=True),
        sa.Column("material_code", sa.String(40), nullable=True),
        sa.Column("material_name", sa.String(300), nullable=True),
        sa.Column("qty", _Q, nullable=True),
        sa.Column("price", _Q, nullable=True),
        sa.Column("amount", _M, nullable=True),
        *_tail(),
    )
    op.create_index("ix_nc_cm_stuff_lines_mat_period", "nc_cm_stuff_lines",
                    ["material_code", "period"])
    op.create_index("ix_nc_cm_stuff_lines_obj_period", "nc_cm_stuff_lines",
                    ["cost_object_code", "period"])

    op.create_table(
        "nc_so_invoice_lines",
        _id(),
        sa.Column("nc_pk", sa.String(25), nullable=False, unique=True),
        sa.Column("invoice_pk", sa.String(25), nullable=False),
        sa.Column("invoice_code", sa.String(40), nullable=True),
        sa.Column("bill_date", sa.Date(), nullable=True),
        sa.Column("status_flag", sa.SmallInteger(), nullable=True),
        sa.Column("customer_code", sa.String(60), nullable=True),
        sa.Column("customer_name", sa.String(300), nullable=True),
        sa.Column("orig_currency", sa.String(10), nullable=True),
        sa.Column("exchange_rate", _Q, nullable=True),
        sa.Column("row_no", sa.String(20), nullable=True),
        sa.Column("material_pk", sa.String(25), nullable=True),
        sa.Column("material_code", sa.String(40), nullable=True),
        sa.Column("material_name", sa.String(300), nullable=True),
        sa.Column("qty", _Q, nullable=True),
        # Revenue basis (decision D5): local currency (CAD), tax excluded.
        sa.Column("amount", _M, nullable=True),
        sa.Column("orig_amount", _M, nullable=True),
        sa.Column("tax", _M, nullable=True),
        *_tail(),
    )
    op.create_index("ix_nc_so_invoice_lines_mat_date", "nc_so_invoice_lines",
                    ["material_code", "bill_date"])

    op.create_table(
        "nc_cost_sync_runs",
        _id(),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("started_by", UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counts", JSONB(), nullable=True),
        sa.Column("tie_out_ok", sa.Boolean(), nullable=True),
        sa.Column("tie_out", JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_nc_cost_sync_runs_status", "nc_cost_sync_runs", ["status"])

    op.create_table(
        "fa_config",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        # NULL = default; 0 = schedule off (button only). Same contract as the
        # AP / voucher sync intervals.
        sa.Column("sync_interval_minutes", sa.Integer(), nullable=True),
        # Anomaly thresholds; missing keys fall back to the defaults in
        # services/cost_analysis.py.
        sa.Column("thresholds", JSONB(), nullable=True),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint("id = 1", name="ck_fa_config_single_row"),
    )
    op.execute("INSERT INTO fa_config (id) VALUES (1)")


def downgrade() -> None:
    for t in ("fa_config", "nc_cost_sync_runs", "nc_so_invoice_lines", "nc_cm_stuff_lines",
              "nc_cm_prod_cost_lines", "nc_cm_prod_costs", "nc_cm_cost_objects",
              "nc_ia_month_balances", "nc_ia_ledger"):
        op.drop_table(t)
