# JV 往来维度(供应商/客户)设计

> 状态:设计稿(2026-07-13/14 brainstorm,经用户确认)。
> 归属:Finance 重构 —— JV 分支主线最后一块(之后合并)。
> 前置:多维展开已完成(DIMENSIONS 注册表/coa_aux_items/dims_values 钻取);ERP MDM 模块已同步
> erp_suppliers(dev 1,148 行);集成 API **无客户端点**(v1.1 文档 11 个端点核对过;vendor=生产商,用户确认)。

## 1. 决策(经用户确认)

- **供应商档案** = mdm 的 `erp_suppliers`(不另建);finance 加只读镜像 `mirrors.ErpSupplier`
  (子集 erp_supplier_code/supplier_name;物理表列实现前按 information_schema 核对)。
- **客户档案** = finance 侧从 NC `bd_customer` 直接导:新表 `nc_customers` + 导入脚本
  (集成 API 无端点;将来有了再迁 mdm)。
- **JV 行存法** = 复用 `partner_id` 单列 + `partner_name` 文本冗余,不加新列;
  供应商维/客户维靠 `coa_aux_items` 按科目区分(已导入:supplier 10 科目、customer 8 科目)。
- **存量回填** = NC Sync 按钮 **Full Reload** 一次(导入器升级后重灌自带),不写专用回填。

## 2. 数据模型(迁移 0020)

`nc_customers(id uuid pk, code varchar(40) unique not null, name varchar(255) not null,
is_active bool not null default true, created_at/updated_at)`。
finance `mirrors.ErpSupplier`(表 erp_suppliers,mdm-api 拥有,**列子集** erp_supplier_code/supplier_name;
conftest 注册建表,沿用镜像惯例)。`nc_customers` 是 finance 自有表(alembic 建),模型放 `models/nc_customer.py`。

## 3. 客户导入脚本 `scripts/nc_migration/customers_import.py`

- 读 NC `bd_customer`(code/name[或 ename 优先英文,同 COA 取名惯例:英文→中文→code]/enablestate),
  只取 `enablestate=2`(已启用);幂等 = 清表重灌(小表);`--dry-run/--load`;硬拒 10.10.50.*。

## 4. NC 凭证导入器扩展(services/nc_sync.py + voucher_import.py 同构)

- **辅助类型 pk 动态解析**:读 `BD_ACCASSITEM`(pk_accassitem/name),按名称(含"供应商"/"客户")
  取 供应商档案/客户档案 的类型 pk;**运行时校验**——同法解析出的 部门/成本中心/收支项目 pk 必须等于
  现有 AUX_DEPT/AUX_COSTCENTER/AUX_IOITEM 常量,不等则报错停止(证明 GL_FREEVALUE typevalue 前缀
  = pk_accassitem 的假设成立);dry-run 打印解析结果。
- `load_aux`/fetch 扩展:GL_FREEVALUE 槽位解析出 supplier_pk/customer_pk → 经
  `bd_supplier(pk→code)`/`bd_customer(pk→code)` → 本地 `erp_suppliers.erp_supplier_code` /
  `nc_customers.code` map → `partner_id` + `partner_name`(取档案名)。
- `NcExtract.aux` 值从三元组扩为五元组 `(dept, cc, io, sup_code, cust_code)`;transform 行 tuple
  再加 partner_id/partner_name 两元(execute_values 列清单/模板同步,两份导入器一致)。
- 供应商码在 erp_suppliers 缺失 / 客户码在 nc_customers 缺失 → partner_id 空、partner_name 存
  NC 档案名(文本兜底);缺失情况**只记日志 warning + dry-run 打印计数**,nc_sync_runs 不加列(YAGNI)。

## 5. 报表注册(一行式接入,多维展开已铺好)

`DIMENSIONS` 加:`"supplier": (JournalVoucherLine.partner_id, mirrors.ErpSupplier)`、
`"customer": (JournalVoucherLine.partner_id, models.nc_customer.NcCustomer)`。
mirror 的 code/name 属性名不同(erp_supplier_code/supplier_name vs code/name)——注册表值扩为
`(column, model, code_attr, name_attr)` 或给镜像加 property 别名;实现取其一(倾向注册表带 attr 名,
不改镜像)。coa_aux_items 里 supplier/customer 的 `supported` 自动翻 true。
mirror 查不到的 partner_id(如 go-forward 业务凭证的 EPMS vendor id)分组仍正确,code/name 为 null,
UI 显示 `(unknown)`(DimExpansion 的 label 逻辑:id 非空但 code 空 → '(unknown)';id 空 → '(none)')。

## 6. 验证

- transform:fake aux 五元组 → 行 partner_id/partner_name 正确;缺档案码 → id 空 name 兜底。
- 类型 pk 动态解析:fake BD_ACCASSITEM 行 → 校验逻辑(常量不匹配报错)。
- expand:supplier/customer 维分组 + 名称解析(种子 mirror/nc_customers 行);(unknown) 场景。
- dev 实测:customers_import 跑一次 → NC Sync **Full Reload** → `2202`(应付,挂 supplier)按 supplier
  展开、AR 科目按 customer 展开,单维合计与 CC 维合计一致;凭证详情 partner chips 显示。

## 7. 不在范围

- mdm-api 改动(erp_customer 同步等集成 API 有端点后再说);B3 business partner 统一;
  档案人工 confirm-gate(NC 按 code 直引,无合并);AP/AR 业务侧 partner 体系改造。
