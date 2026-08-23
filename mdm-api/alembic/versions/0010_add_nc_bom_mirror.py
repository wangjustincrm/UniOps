"""NC65 BOM raw mirror: nc_bom / nc_bom_b / nc_bom_repl (MRP phase0 task 4)

Revision ID: 0010_add_nc_bom_mirror
Revises: 0009_add_uom_conversions
Create Date: 2026-08-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0010_add_nc_bom_mirror"
down_revision = "0009_add_uom_conversions"
branch_labels = None
depends_on = None


def _ts_cols():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade():
    # --- nc_bom (NCSC.BD_BOM header raw mirror) ---
    op.create_table(
        "nc_bom",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("nc_source_pk", sa.String(50), nullable=False),
        sa.Column("cbomid", sa.String(50)),
        sa.Column("approver", sa.String(255)),
        sa.Column("billmaker", sa.String(50)),
        sa.Column("bkititem", sa.String(5)),
        sa.Column("creationtime", sa.String(30)),
        sa.Column("creator", sa.String(50)),
        sa.Column("dr", sa.Numeric(18, 4)),
        sa.Column("fbillstatus", sa.Numeric(5, 0)),
        sa.Column("fbomtype", sa.Numeric(5, 0)),
        sa.Column("hbcustomized", sa.String(5)),
        sa.Column("hbdefault", sa.String(5)),
        sa.Column("hbisfeature", sa.String(5)),
        sa.Column("hcassmeasureid", sa.String(50)),
        sa.Column("hcecnid", sa.String(50)),
        sa.Column("hcfeatureclassid", sa.String(50)),
        sa.Column("hcfeaturecode", sa.String(100)),
        sa.Column("hcmaterialid", sa.String(50)),
        sa.Column("hcmaterialvid", sa.String(50)),
        sa.Column("hcmeasureid", sa.String(50)),
        sa.Column("hcprojectid", sa.String(50)),
        sa.Column("hfbomsource", sa.Numeric(5, 0)),
        sa.Column("hfversiontype", sa.Numeric(5, 0)),
        sa.Column("hnassparentnum", sa.Numeric(24, 8)),
        sa.Column("hnparentnum", sa.Numeric(24, 8)),
        sa.Column("hrtversion", sa.String(50)),
        sa.Column("hsrcid", sa.String(50)),
        sa.Column("hvchangerate", sa.String(50)),
        sa.Column("hvecnbillcode", sa.String(255)),
        sa.Column("hversion", sa.String(50)),
        sa.Column("hvnote", sa.String(1000)),
        sa.Column("modifiedtime", sa.String(30)),
        sa.Column("modifier", sa.String(50)),
        sa.Column("pk_group", sa.String(50)),
        sa.Column("pk_org", sa.String(50)),
        sa.Column("pk_org_v", sa.String(50)),
        sa.Column("taudittime", sa.String(30)),
        sa.Column("tmaketime", sa.String(30)),
        sa.Column("ts", sa.String(30)),
        sa.Column("vbillcode", sa.String(500)),
        sa.Column("vbilltype", sa.String(20)),
        *_ts_cols(),
    )
    op.create_index("ix_nc_bom_nc_source_pk", "nc_bom", ["nc_source_pk"], unique=True)
    op.create_index("ix_nc_bom_hcmaterialid", "nc_bom", ["hcmaterialid"])
    op.create_index("ix_nc_bom_pk_org", "nc_bom", ["pk_org"])
    op.create_index("ix_nc_bom_ts", "nc_bom", ["ts"])

    # --- nc_bom_b (NCSC.BD_BOM_B component-line raw mirror) ---
    op.create_table(
        "nc_bom_b",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("nc_source_pk", sa.String(50), nullable=False),
        sa.Column("batpcheck", sa.String(5)),
        sa.Column("bbchkitemforwr", sa.String(5)),
        sa.Column("bbisfeature", sa.String(5)),
        sa.Column("bbsteploss", sa.String(5)),
        sa.Column("bbunibatch", sa.String(5)),
        sa.Column("bcanreplace", sa.String(5)),
        sa.Column("bcfeatureclassid", sa.String(50)),
        sa.Column("bcfeaturecode", sa.String(100)),
        sa.Column("bcustommaterial", sa.String(5)),
        sa.Column("bdeliver", sa.String(5)),
        sa.Column("bischoice", sa.String(5)),
        sa.Column("bkitmaterial", sa.String(5)),
        sa.Column("bmainmaterial", sa.String(5)),
        sa.Column("bmixedmaterial", sa.String(5)),
        sa.Column("boutsource", sa.String(5)),
        sa.Column("bprojectmaterial", sa.String(5)),
        sa.Column("bupint", sa.String(5)),
        sa.Column("cassmeasureid", sa.String(50)),
        sa.Column("cbeginperiod", sa.String(30)),
        sa.Column("cbom_bid", sa.String(50)),
        sa.Column("cbomid", sa.String(50)),
        sa.Column("ccustomerid", sa.String(50)),
        sa.Column("cendperiod", sa.String(30)),
        sa.Column("cmaterialid", sa.String(50)),
        sa.Column("cmaterialvid", sa.String(50)),
        sa.Column("cmeasureid", sa.String(50)),
        sa.Column("cnumfeature", sa.String(50)),
        sa.Column("cproductorid", sa.String(50)),
        sa.Column("cprojectid", sa.String(50)),
        sa.Column("cvendorid", sa.String(50)),
        sa.Column("dr", sa.Numeric(18, 4)),
        sa.Column("fbackflushtime", sa.Numeric(10, 0)),
        sa.Column("fbackflushtype", sa.Numeric(5, 0)),
        sa.Column("fcontrol", sa.Numeric(5, 0)),
        sa.Column("fitemsource", sa.Numeric(5, 0)),
        sa.Column("fitemtype", sa.Numeric(5, 0)),
        sa.Column("freplacetype", sa.Numeric(5, 0)),
        sa.Column("fsupplymode", sa.Numeric(5, 0)),
        sa.Column("ibasenum", sa.Numeric(24, 8)),
        sa.Column("ileadtimenum", sa.Numeric(24, 8)),
        sa.Column("nassitemnum", sa.Numeric(24, 8)),
        sa.Column("nbfixshrinkastnum", sa.Numeric(24, 8)),
        sa.Column("nbfixshrinknum", sa.Numeric(24, 8)),
        sa.Column("ndissipationum", sa.Numeric(24, 8)),
        sa.Column("nitemnum", sa.Numeric(24, 8)),
        sa.Column("pk_group", sa.String(50)),
        sa.Column("pk_org", sa.String(50)),
        sa.Column("pk_org_v", sa.String(50)),
        sa.Column("ts", sa.String(30)),
        sa.Column("vchangerate", sa.String(50)),
        sa.Column("vconfigversion", sa.String(50)),
        sa.Column("vitemversion", sa.String(50)),
        sa.Column("vmatingno", sa.String(50)),
        sa.Column("vnote", sa.String(1000)),
        sa.Column("vpackversion", sa.String(50)),
        sa.Column("vrowno", sa.String(20)),
        sa.Column("vselectcond", sa.String(500)),
        *_ts_cols(),
    )
    op.create_index("ix_nc_bom_b_nc_source_pk", "nc_bom_b", ["nc_source_pk"], unique=True)
    op.create_index("ix_nc_bom_b_cbomid", "nc_bom_b", ["cbomid"])
    op.create_index("ix_nc_bom_b_cmaterialid", "nc_bom_b", ["cmaterialid"])
    op.create_index("ix_nc_bom_b_ts", "nc_bom_b", ["ts"])

    # --- nc_bom_repl (NCSC.BD_BOM_REPL substitute-material raw mirror) ---
    op.create_table(
        "nc_bom_repl",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("nc_source_pk", sa.String(50), nullable=False),
        sa.Column("cbom_bid", sa.String(50)),
        sa.Column("cbom_replaceid", sa.String(50)),
        sa.Column("creplmaterialoid", sa.String(50)),
        sa.Column("creplmaterialvid", sa.String(50)),
        sa.Column("ireplorder", sa.Numeric(10, 0)),
        sa.Column("dr", sa.Numeric(18, 4)),
        sa.Column("pk_group", sa.String(50)),
        sa.Column("pk_org", sa.String(50)),
        sa.Column("pk_org_v", sa.String(50)),
        sa.Column("ts", sa.String(30)),
        sa.Column("vreplaceindex", sa.String(50)),
        sa.Column("vrowno", sa.String(20)),
        sa.Column("vnote", sa.String(500)),
        *_ts_cols(),
    )
    op.create_index("ix_nc_bom_repl_nc_source_pk", "nc_bom_repl", ["nc_source_pk"], unique=True)
    op.create_index("ix_nc_bom_repl_cbom_bid", "nc_bom_repl", ["cbom_bid"])
    op.create_index("ix_nc_bom_repl_creplmaterialoid", "nc_bom_repl", ["creplmaterialoid"])
    op.create_index("ix_nc_bom_repl_ts", "nc_bom_repl", ["ts"])


def downgrade():
    op.drop_table("nc_bom_repl")
    op.drop_table("nc_bom_b")
    op.drop_table("nc_bom")
