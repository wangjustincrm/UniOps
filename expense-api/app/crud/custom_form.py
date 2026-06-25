"""CRUD for custom form definitions (CFM)."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.custom_form import CustomFormDefinition
from app.schemas.custom_form import CustomFormCreate, CustomFormUpdate


async def list_forms(db: AsyncSession, active_only: bool = False) -> list[CustomFormDefinition]:
    q = select(CustomFormDefinition).order_by(CustomFormDefinition.created_at)
    if active_only:
        q = q.where(CustomFormDefinition.is_active.is_(True))
    return list((await db.execute(q)).scalars().all())


async def get_by_code(db: AsyncSession, code: str) -> CustomFormDefinition | None:
    q = select(CustomFormDefinition).where(CustomFormDefinition.code == code.upper())
    return (await db.execute(q)).scalar_one_or_none()


async def create_form(db: AsyncSession, data: CustomFormCreate) -> CustomFormDefinition:
    form = CustomFormDefinition(
        code=data.code,
        name=data.name,
        description=data.description,
        workflow_key=data.workflow_key or f"cfm_{data.code.lower()}",
        default_currency=data.default_currency,
        field_schema=[f.model_dump() for f in data.fields],
        is_active=data.is_active,
    )
    db.add(form)
    await db.flush()
    await db.refresh(form)
    return form


async def update_form(db: AsyncSession, form: CustomFormDefinition, data: CustomFormUpdate) -> CustomFormDefinition:
    patch = data.model_dump(exclude_none=True)
    if "fields" in patch:
        form.field_schema = [f.model_dump() if hasattr(f, "model_dump") else f for f in data.fields]
        patch.pop("fields")
    for field, val in patch.items():
        setattr(form, field, val)
    await db.flush()
    await db.refresh(form)
    return form
