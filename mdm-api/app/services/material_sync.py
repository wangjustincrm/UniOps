"""erp_material(原始镜像) → materials(正式主数据) 升格同步。

ERP 为源；本地治理字段(item_type/procurement_type/shelf_life_months/
product_family/factory_code)不被覆盖。

注：mdm-api/app/models/erp_material.py 的真实列名与本任务 brief 中的猜测列名
不同（无 exp/part_product_family 等列，也没有对应的原始报文字段——见
tests/test_erp_sync.py 的 material_payload 夹具）。保质期月数/产品族在
erp_material 里没有源数据，因此不参与本次映射，留给数据维护后续手工补录。
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.erp_material import ErpMaterial
from app.models.material import Material

# Fields sourced from the ERP mirror and refreshed on every sync. Deliberately
# excludes item_type/procurement_type/shelf_life_months/product_family/
# factory_code — those are local governance fields (or, for the latter two,
# simply have no ERP source data) and must never be clobbered by a sync run.
_ERP_FIELDS = ("name", "spec", "base_uom", "erp_item_type", "erp_id")


def _from_erp(row: ErpMaterial) -> dict:
    """Map an erp_material mirror row to Material fields.

    物料编码 erp_part_no -> code；名称 description -> name；
    规格 dim_quality -> spec；计量单位 unit_meas -> base_uom；
    ERP物料类型原值 item_mes_type -> erp_item_type；
    erp_id = erp_part_no (the only natural ERP identifier the mirror carries;
    mirrors the vendors erp_id dedup pattern).
    """
    return {
        "code": row.erp_part_no,
        "name": row.description,
        "spec": row.dim_quality,
        "base_uom": row.unit_meas,
        "erp_item_type": row.item_mes_type,
        "erp_id": row.erp_part_no,
    }


async def sync_materials(db: AsyncSession) -> dict:
    created = updated = 0
    existing = {m.code: m for m in (await db.execute(select(Material))).scalars()}
    for row in (await db.execute(select(ErpMaterial))).scalars():
        data = _from_erp(row)
        m = existing.get(data["code"])
        if m is None:
            db.add(Material(**data))
            created += 1
        else:
            for f in _ERP_FIELDS:
                setattr(m, f, data[f])
            updated += 1
    await db.commit()
    return {"created": created, "updated": updated, "skipped": 0}
