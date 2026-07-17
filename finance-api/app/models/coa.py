"""Chart of Accounts (IFRS) + account mappings — finance-api owns both (Phase a A1).

COA is the accounting backbone (FIN-MD-002): IFRS-oriented template for a
Canadian dairy manufacturer, seeded by migration 0005 and maintained as data.
account_mappings bridges operational codes to ledger accounts:
  - line_role        → posting line roles (accounts_payable / bank / ...)
  - budget_account   → budget-api account codes (预算科目 ↔ 会计科目桥)
The payment executor stamps account_code on posting lines via these mappings —
GL replay (PRD Phase 3) depends on them being populated from now on.
"""
import uuid
from datetime import date

from sqlalchemy import Boolean, Date, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class AuxDimensionType(UUIDPrimaryKey, TimestampMixin, Base):
    """辅助核算项目录 (Phase a A1.7) — extensible catalog of dimension types an
    account can subscribe to. `storage='column'` dims map to a posting_lines
    column; `storage='aux_table'` dims live in posting_line_dimensions."""
    __tablename__ = "aux_dimension_types"

    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    master_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    storage: Mapped[str] = mapped_column(String(12), nullable=False, default="aux_table")
    builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class ChartOfAccount(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "chart_of_accounts"

    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[str] = mapped_column(String(20), nullable=False)   # asset|liability|equity|revenue|expense
    subtype: Mapped[str | None] = mapped_column(String(50), nullable=True)  # current_asset | accounts_payable | ...
    normal_balance: Mapped[str] = mapped_column(String(6), nullable=False)  # debit|credit
    is_postable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # headers are not
    parent_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 辅助核算项: JSONB array of {"code": <aux_dimension_types.code>, "required": bool}
    # (required = mandatory on postings; optional otherwise — NC's 允许为空 inverted)
    aux_dimensions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # ── NC-parity metadata (A1.7) ───────────────────────────────────────────
    quantity_accounting: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_uom: Mapped[str | None] = mapped_column(String(20), nullable=True)
    default_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    cash_flow_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    mnemonic: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_off_balance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class AccountMapping(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "account_mappings"
    __table_args__ = (
        UniqueConstraint("mapping_type", "source_code", name="uq_account_mappings_source"),
    )

    mapping_type: Mapped[str] = mapped_column(String(20), nullable=False)   # line_role | budget_account
    source_code: Mapped[str] = mapped_column(String(50), nullable=False)
    account_code: Mapped[str] = mapped_column(String(10), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class CoaAuxItem(UUIDPrimaryKey, TimestampMixin, Base):
    """科目→辅助核算项挂接(从 NC BD_ACCASS 导入,只读;报表据此列可勾维度)。"""
    __tablename__ = "coa_aux_items"
    __table_args__ = (UniqueConstraint("account_code", "dim_code",
                                       name="uq_coa_aux_items_acct_dim"),)

    account_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    dim_code: Mapped[str] = mapped_column(String(40), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # NC BD_ACCASS.ISEMPTY 取反:允许为空=N → 必填
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                           server_default="false")
