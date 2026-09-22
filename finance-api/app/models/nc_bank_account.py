"""NC bank-account master mirror (NCSC.BD_BANKACCSUB, 银行账户).

The auxiliary-accounting value behind account 100201. `100201 Checking` is ONE
postable account carrying a REQUIRED `bank_account` auxiliary — there are no
per-bank sub-accounts — so this mirror is the only thing that says which bank a
GL line belongs to. Measured 2026-09-22 on the live book: 100201 for 2026-07,
bank `1033760` (RBC, CAD) = 260 lines, debits 1,414,741.74, credits
1,269,496.36 — to the penny and to the line count, the RBC July statement.

Columns are named after NC's, per the mirror convention: code / accnum /
accname are BD_BANKACCSUB.CODE / .ACCNUM / .ACCNAME verbatim.
"""
import uuid

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class NcBankAccount(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "nc_bank_accounts"
    __table_args__ = (
        UniqueConstraint("code", name="uq_nc_bank_accounts_code"),
        UniqueConstraint("nc_pk", name="uq_nc_bank_accounts_nc_pk"),
    )

    # BD_BANKACCSUB.PK_BANKACCSUB is CHAR — always stored rtrim'd (see nc_sync).
    nc_pk: Mapped[str] = mapped_column(String(40), nullable=False)
    code: Mapped[str] = mapped_column(String(60), nullable=False)          # .CODE
    acc_num: Mapped[str | None] = mapped_column(String(60), nullable=True)  # .ACCNUM
    # .NAME — what NC's own 科目余额表 prints as 银行账户名称:
    # "RBC加拿大元活期户", "BOC - Checking - CAD - 0128". This is the label.
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # .ACCNAME — the account HOLDER ("RBC", "Canada Royal Milk ULC"). Repeats
    # across accounts, so it is kept for reference and never used as a label.
    acc_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # BD_BANKDOC.NAME via BD_BANKACCBAS — "RBC-York Street",
    # "Bank of China Toronto Branch".
    bank_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(10), nullable=True)  # BD_CURRTYPE.CODE
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    @property
    def label(self) -> str:
        """What NC's account-balance report shows: "1033760/RBC加拿大元活期户"."""
        return f"{self.code}/{self.name}" if self.name else self.code
