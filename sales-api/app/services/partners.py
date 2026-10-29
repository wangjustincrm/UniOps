"""Trading partners (design §3.1, FR-OB-03, FR-OB-33): the configuration
screen's use cases. CRUD, CSV import / export and the permission checks come
from the platform's CrudService; this module adds the partner's rules."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from app.domain.partners import check_partner
from app.models.config import Partner
from app.platform.errors import BusinessError
from app.platform.repository import FilterRule, Repository, parse_bool
from app.platform.services import CrudService
from app.schemas.partners import PartnerCreate, PartnerRead, PartnerUpdate

READ = "sales.read"
CONFIG = "sales.config"


class PartnerRepository(Repository[Partner]):
    model = Partner
    search_fields = ("code", "name", "abbrev")
    sortable = {
        "code": Partner.code, "name": Partner.name, "channel": Partner.channel,
        "settlement_mode": Partner.settlement_mode, "active": Partner.active,
        "updated_at": Partner.updated_at,
    }
    filterable = {
        "active": FilterRule(Partner.active, ops=frozenset({"eq"}), cast=parse_bool),
        "channel": FilterRule(Partner.channel),
        "settlement_mode": FilterRule(Partner.settlement_mode),
        "invoice_method": FilterRule(Partner.invoice_method),
    }
    default_sort = ("code",)


# The columns the partner rules look at (check_partner).
_RULE_FIELDS = ("invoice_method", "invoice_email")


class PartnerService(CrudService[Partner, PartnerRead, PartnerCreate, PartnerUpdate]):
    repo_cls = PartnerRepository
    read_schema = PartnerRead
    create_schema = PartnerCreate
    update_schema = PartnerUpdate
    read_permission = READ
    write_permission = CONFIG
    csv_key = "code"
    unique_constraints = {"uq_sales_partners_code_ci": ("partner.code_taken", "code")}
    csv_columns = (
        "code", "name", "abbrev", "channel", "settlement_mode", "sending_mode", "delay_minutes",
        "invoice_method", "invoice_email", "invoice_cc", "payment_terms", "dc_routing_required",
        "min_shelf_life_days", "label_code", "carrier_defaults", "active",
    )

    async def validate_create(self, data: PartnerCreate) -> None:
        check_partner(data.model_dump())
        # Codes are compared as the CSV import compares them: "abc " is "ABC".
        taken = (await self.uow.session.execute(
            select(Partner.code).where(func.lower(Partner.code) == data.code.lower())
        )).scalar_one_or_none()
        if taken is not None:
            raise BusinessError(
                "partner.code_taken", f"partner code {taken!r} already exists",
                {"field": "code", "existing": taken},
            )

    async def before_update(self, entity: Partner, changes: dict[str, Any]) -> None:
        check_partner({**{k: getattr(entity, k) for k in _RULE_FIELDS}, **changes})
