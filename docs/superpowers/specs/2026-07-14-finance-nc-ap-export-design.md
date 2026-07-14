# 并行期 AP 导出(Export to NC)设计

> 状态:设计稿(2026-07-14 brainstorm,逐点经用户确认)。
> 归属:Finance 并行期同步子项目(spec 总纲 `2026-07-04-nc65-finance-migration-mapping-design.md` §9.4)。
> 模板:`AP_template.xlsx`(已解剖,结构见 §2;实现时拷入 `finance-api/assets/nc_ap_template.xlsx`)。

## 1. 目标

财务在 UniOps 勾选已入账应付单 → 生成 NC 应付模块引入 xlsx → 人工引入 NC → NC 审核制单自动生成凭证;
`billno = UniOps AP 号` 作两边 JV 定期复核锚点。不做自动传输;复核对账工具是后续另一小块。

## 2. 模板结构(实测解剖,硬约束)

- Sheet1:R1 导入须知(不可增删列/全文本格式/主子表间必须空行/主子行号关联)。
- **Head 块**:R2=技术行(A 列 CSV:`payablebill_$head,pk_org,billno,pk_tradetypeid,pk_busitype,billdate,busidate,objtype,supplier,pk_deptid_v,pk_psndoc,pk_subjcode,pk_currtype,pk_tradetype,taxcountryid,def26,def14`)+B..Q 列标;R3 起每单一行,**A 列=单序号(0,1,2…)**。
- 空一行。
- **Body 块**:技术行(`bodys,subjcode,invoiceno,scomment,material,pk_payterm,objtype,supplier,pk_deptid_v,def5,pk_psndoc,project,pk_subjcode,pk_currtype,rate,money_cr,quantity_cr,taxcodeid,taxrate,taxprice,notax_cr,local_tax_cr,taxtype,pk_deptid,def11,def7,def6,buysellflag,付款协议编码,税码主键`)+列标;数据行 **A 列=所属单序号**。
- **档案全按名称填**(Vendor/Department/Cost Center(def5)/Rev-Exp Item(pk_subjcode));**Account(subjcode)=全路径** `510102\Manufacturing Overhead\…`;金额:`money_cr(含税)=notax_cr+local_tax_cr`。
- 多单导出 = head 行连续 + 空行 + body 行连续,序号关联(非逐单交替)。

## 3. 用户已确认的决策

1. **入口** = Accounts Payable 页筛选+勾选 → Export to NC(生成即下载);**批次记录** `nc_export_batches` + `ap_invoices.nc_exported_at/nc_export_batch_id`;已导出默认过滤但**可重导**(不拦,提示)。
2. **枚举全固定、做成可改配置**(`NC_EXPORT_DEFAULTS` 常量):A/P Type=`Payable of Expense`、Business Process=`选择付款`、A/P Type Code=`F1-Cxx-017`、Purchase & Sales Type=`Domestic Purchases`、taxtype=`Tax Excluded`、税码=`001`/13%、付款协议=`net 30 days`、org=`Canada Royal Milk ULC`、taxcountryid=`Canada`。税率优先取 AP 税行真实值。
3. **body 行重组 = 按费用行多行,税按行不含税金额比例分摊**(行自带税额优先用行税;尾差进末行)。
4. **维度从源单取**(两条链,能力如实):
   - **OA 来源**(2026-07-14 按同步代码核实):`source_invoice_id` = OA `expense_invoices.id`,其挂接是 **Direct PA**(`expense_invoices.pa_id → payment_applications`),维度在 PA 头:`cost_center_id`(→CC 名称)、`budget_account_code`(→收支项目名称)、部门=CC 的 department→名称、Employee=`PA.created_by`→users 姓名。body **单行**(金额=AP amount/tax_amount)。报销单(claim)路径属将来 Claim Accrual 后的扩展,不在本期。需扩镜像:`PaymentApplication` 加 cost_center_id/budget_account_code/created_by 三列(物理已核对)、新增 `ExpenseInvoice(id, pa_id)` 子集镜像。
   - **EPMS 来源**(2026-07-14 用户纠正后核实,链条完整):**PR 头上维度齐全且 100% 覆盖**——`purchase_requests(cost_center_id, budget_code[=budget_accounts.code 收支项目], department_name, po_id)`(dev 实测 6293/6293 有 CC)。链:发票 → `invoice_po_allocations(invoice_id, po_id, allocated_amount/allocated_tax/allocated_total)` 行级分摊(无分摊行时退回 `invoices.po_id` 单 PO)→ 各 PO 反查 `purchase_requests`(PR.po_id=PO)→ CC 名称(cost_centers)/收支项目名称(budget_accounts by budget_code)/部门名(PR.department_name 直取)。**body 按分摊行拆多行**(金额=allocated_amount,税=allocated_tax);Employee 空。兜底:PR 缺失时部门经 PO→PR 创建人推导、CC 经 CC_BY_DEPT 整定映射(极少用)。需补只读镜像:`invoice_po_allocations` 子集、`purchase_requests` 子集(物理列实现前按 information_schema 核对;`mirrors.Invoice` 已有,po_id 列缺则补)。
5. **科目(subjcode)** = accrual JV 费用行的 account_code(现阶段为兜底费用科目)→ **全路径本地拼**:COA parent 链 `code\name₁\name₂…\name本级`(与 NC 对照校一次;真实科目细分是另一工程,明示不在本期)。
6. **Head 的 Department/Rev-Exp Item** = body 首行同名维度;billdate/busidate=invoice_date;supplier=vendor_name;currency=AP 币种。

## 4. 数据模型(迁移 0021)

- `nc_export_batches(id uuid pk, exported_by uuid, exported_at timestamptz not null, ap_count int not null, filename varchar(120), created_at/updated_at)`。
- `ap_invoices` 加 `nc_exported_at timestamptz NULL`、`nc_export_batch_id uuid NULL`。

## 5. 导出器(`services/nc_ap_export.py`)

- `build_export_rows(db, ap_ids) -> (heads, bodies, errors)`:纯组装可测;每 AP:
  - 校验:status 非 draft/void、存在已生成 accrual(posting_event source_doc_type='ap_invoice')——不满足的进 errors 并整批报出(不部分导出,除非全部通过;或返回可导子集+错误清单,UI 展示——**采用后者**,财务自己决定)。
  - 行角色经 posting_lines.line_role 判定(purchase_expense/sales_tax/accounts_payable),JV 行按 line_no 对应取维度(OA/EPMS 维度链见 §3.4)。
- `write_xlsx(heads, bodies) -> bytes`:openpyxl 打开 assets 模板副本、清样例数据行、写 head 块+空行+body 块(A 列序号),全文本格式。
- API:`POST /finance/v1/ap/nc-export`(body `{ap_ids: [uuid]}`,权限=coa `_require_manage` 同门)→ 成功:xlsx 流(文件名 `NC-AP-{YYYYMMDD-HHMM}.xlsx`)+ 落批次 + 标记 AP;含不可导单时返回 409+错误清单(UI 先剔除再导)。`GET /finance/v1/ap/nc-export/batches` 最近批次列表。
- 下载:前端 POST 收 blob(参照 financeDownload 模式扩 POST 版)。

## 6. UI(AccountsPayablePage)

- 行复选(仅可导状态可勾)+ 工具条「Export to NC」;「Exported」列(时间)+「未导出」筛选;重导时按钮提示 already-exported 数量但放行。

## 7. 验证

- 组装纯逻辑:OA 多行维度/税分摊尾差/EPMS 分摊行拆分+PR 维度(CC/收支项/部门)/全路径拼/多单序号;错误清单(draft/void/无 accrual)。
- xlsx 读回断言(技术行保留、块结构、样例清除、文本格式)。
- API:权限/409 清单/批次落库/AP 标记/重导放行。
- dev 实测:导一批真实 posted AP → 人工核对样例格式一致 → **你拿去 NC 试引入一次终验**(引入结果反馈修口径)。

## 8. 范围外

- 自动传输/接口对接 NC(维持人工引入);档案模板导出;JV 复核对账工具;EPMS 行级真实**费用科目**细分(CC/收支项已从 PR 取到,科目仍是 accrual 兜底科目,细分另一工程);Claim 之外的 OA 单据特化。
