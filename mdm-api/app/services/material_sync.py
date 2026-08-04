"""erp_material(原始镜像) → materials(正式主数据) 升格同步。

ERP 为源；本地治理字段(item_type/procurement_type/factory_code)不被覆盖。

注：mdm-api/app/models/erp_material.py 的真实列名与本任务 brief 中的猜测列名
不同（erp_part_no/description/unit_meas/dim_quality/item_mes_type，非 brief
猜测名）——见 tests/test_erp_sync.py 的 material_payload 夹具。

Task 3 scope extension：NC ERP 物料接口报文另带 exp（保质期月数）与
part_PRODUCT_FAMILY（产品族），erp_material 已新增同名镜像列 exp /
part_product_family（迁移 0008），本同步随之把它们升格为
materials.shelf_life_months / materials.product_family —— 这两个字段现在
由 ERP 驱动，每次同步刷新，不再是本地治理字段。
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.erp_material import ErpMaterial
from app.models.material import Material

# Fields sourced from the ERP mirror and refreshed on every sync. Deliberately
# excludes item_type/procurement_type/factory_code — those are local
# governance fields with no ERP source data and must never be clobbered by a
# sync run.
_ERP_FIELDS = (
    "name", "spec", "base_uom", "erp_item_type", "erp_id",
    "shelf_life_months", "product_family",
)


def _from_erp(row: ErpMaterial) -> dict:
    """Map an erp_material mirror row to Material fields.

    物料编码 erp_part_no -> code；名称 description -> name；
    规格 dim_quality -> spec；计量单位 unit_meas -> base_uom；
    ERP物料类型原值 item_mes_type -> erp_item_type；
    保质期月数 exp -> shelf_life_months；产品族 part_product_family -> product_family；
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
        "shelf_life_months": row.exp,
        "product_family": row.part_product_family,
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
