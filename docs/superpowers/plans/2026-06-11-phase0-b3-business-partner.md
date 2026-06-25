# Phase 0-B3: Business Partner 主数据 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。

**Goal:** vendors 升级为 business_partners(supplier/customer 双角色 + FIN-MD-006:
税号/客户类型/省份/信用额度),数据迁入保 id,EPMS 5 个单据 FK 平移,旧名留兼容视图。
CRM(Phase b)的客户主数据从此就位;customer_type/province 直接喂税引擎 determine。

**Architecture:** mdm-api 拥有新表(迁移 0004:建表 + INSERT...SELECT 保 id 拷贝);
epms-api 迁移 x5(**必须在 mdm 0004 之后跑**,开头防御性断言)做 FK 手术:5 个约束
drop+recreate 指向 business_partners → DROP TABLE vendors → CREATE VIEW vendors
(旧列全集,WHERE is_supplier)兜底未知读方。代码面:epms Vendor 模型与 mdm vendor
镜像 `__tablename__` 一行切到 business_partners(ORM 不感知多余列,EPMS /vendors API
与前端零改动;is_supplier 靠 server_default true);expense 的只读镜像走视图**零改动**。
mdm 新增 BusinessPartner 模型 + `/mdm/v1/partners` CRUD(Phase b 的主入口)。

**事实依据(已侦察):** FK=goods_receipts/invoices/payment_applications/
purchase_orders/purchase_requests 共 5 处,全 epms 域;全仓零裸 SQL 引用 vendors;
`__tablename__="vendors"` 共 3 处(epms 主、mdm 镜像、expense 只读镜像)。

## 契约

**business_partners**(mdm 0004)= vendors 全列(id/code/erp_id/name/category/
contact_name/contact_email/phone/address/payment_terms/max_prepayment_pct/currency/
is_active/notes/created_at/updated_at)+ 新增:
`is_supplier BOOL NOT NULL DEFAULT true`、`is_customer BOOL NOT NULL DEFAULT false`、
`tax_number VARCHAR(50) NULL`、`customer_type VARCHAR(20) NULL`(business|consumer|export,
税引擎输入)、`province CHAR(2) NULL`(place of supply)、
`credit_limit NUMERIC(15,2) NULL`、`entity_id UUID NULL`(B1.7 习惯)。
code 保持 UNIQUE。

**/mdm/v1/partners**:GET 列表(role=supplier|customer 过滤 + search)、GET /{id}、
POST、PATCH;写需 system_admin / vendor_manager / finance_manager。

### Task 1: mdm 0004 建表+拷贝;BusinessPartner 模型;partners API + 测试
### Task 2: epms x5 FK 手术 + 视图;epms Vendor 模型与 mdm vendor 镜像 tablename 切换
### Task 3: 回归基线对比(epms test_vendors.py 改前后必须同等或更好;expense 54 全绿)
### Task 4: 共享库迁移上线(顺序:mdm→epms)+ 活体验证(EPMS /vendors API + FK 完整性)+ roadmap

## Self-Review
- FIN-MD-006 字段齐(税号/信用额度/币种/付款条件——后两者 vendors 原有);
  双角色为 Phase b CRM 留位;customer_type/province 与 B2 determine 参数同名同义
- 风险:epms test_vendors.py 在工作区 WIP 下已有既有失败 → Task 3 用改前基线对比而非绝对全绿
- 风险:跨流迁移顺序(mdm 0004 先于 epms x5)→ x5 开头 SELECT to_regclass 断言 + DEPLOYMENT 文档
- 取舍:视图只为兜底读方;唯一已知视图消费者是 expense 只读镜像(SELECT-only,合法)
