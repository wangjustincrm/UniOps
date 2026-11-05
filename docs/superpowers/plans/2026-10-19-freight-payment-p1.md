# Freight 付款 P1 实施计划

- 设计：`docs/superpowers/specs/2026-10-19-freight-payment-design.md`(D1–D13)
- 分支：`feat/epms-freight`(基于 main `cfc23949`)
- 原则：按逻辑单元 commit；每个后端服务用一次性容器跑自己的测试；前端用各自的 `tsc -p tsconfig.app.json`(并证明真编译了文件)；最后用无头浏览器走一遍可达性。

## 任务顺序

依赖关系：T1 → T2 → T3 → T4/T5/T6 并行 → T7 → T8 → T9。

### T0 消费者清单(先做，不改代码)
grep 出所有判断「PA 是不是 Direct PA」或「po_id/agreement_id 是否为空」的地方，逐个归类：改为看 `source`，或者不受影响(只对 OA Direct PA 有意义)。清单放在本文件末尾的附录，每改一处勾一处。

### T1 数据层(epms-api 迁移 + 四个服务的模型)
- 迁移 `fr01_freight`(revision id ≤ 32 字符；先 `alembic heads` 确认是单 head `ob01_pr_ob_mode_applied`)：
  - `freight_carriers`、`freight_shipments`、`freight_invoice_lines`、`pa_cost_allocations`
  - `payment_applications.source` VARCHAR(20) NOT NULL，server_default `'po'`，回填：agreement_id 非空 → `agreement`；两个都空 → `direct`；其余 → `po`
  - `invoices.freight_carrier_id` UUID 可空，FK
- 模型：epms-api 新增 `app/models/freight.py`，并**注册进 `app/models/__init__.py`**(测试库表来自 create_all)；`PaymentApplication.source` 在 epms / approval / expense / finance 四份模型里都加；`is_direct` 改成 `source == 'direct'`。
- 测试：迁移在 dev 库空跑 upgrade/downgrade；四个服务的模型 import 正常。

### T2 Freight 后端(epms-api `app/api/v1/freight.py` + `app/crud/freight.py`)
- 承运商：list / create / update(`epms.freight.admin`)
- 运单：list(范围：register 看自己的，manage 看全部)/ create / update / void；Track No 规范化；(carrier, tracking) 唯一冲突返回 409，并告诉对方已被谁登记
- 默认成本中心：`GET /freight/default-cost-center?department_id=` → 按 D11(调 budget-api 当前计划；budget-api 不可用时退回「部门唯一成本中心」，否则要求手选)
- 发票：`POST /freight/invoices`(表头 + 明细 + 附件 key)
  - 服务端重复发票检查：同一供应商同一发票号，在 `invoices` 和 `expense_invoices` 两张表里都查
  - 落 `invoices`(match_route=freight，status=unmatched)+ `freight_invoice_lines`
  - 调 `finance_sync.sync_ap_invoice`(和现有建发票一致)
- 匹配：`POST /freight/invoices/{id}/auto-match`(幂等)、`PATCH /freight/invoice-lines/{id}`(改成本中心、挂运单、挂到某票运费)、`POST /freight/invoices/{id}/finalize`(校验金额合计、税合计、每行都有成本中心 → matched，运单 → billed，未登记的补登 `from_invoice`)、`POST /freight/invoices/{id}/reopen`(只有不在有效 PA 里才能撤销)
- 生成 PA：`POST /freight/pa`(invoice_ids)
  - `SELECT … FOR UPDATE` 锁住发票；检查同一承运商、同一币种、都是 matched、没有被有效 PA 占用(`invoice_ids @>`)
  - `next_number` 取号；`source=freight`；表头金额汇总；行项目一张发票一行；`pa_cost_allocations` 按(发票 × 成本中心)汇总，budget_code 固定 `CRM0150102`，经 `ensure_known_budget_code` 校验
- 权限键：`epms.freight.register` / `epms.freight.manage` / `epms.freight.admin`
- 测试：`tests/test_freight_*.py`，覆盖匹配规则 §6 的每一条、重复发票、并发占用(两个请求抢同一张发票)、D13 税分配、D11 默认值、范围(register 看不到别人的运单)；**否定断言配肯定断言**

### T3 PA 接入(epms-api)
- 归属过滤和 404 守卫改为 `source IN ('po','agreement','freight')`：`crud/pa.py:147`、`api/v1/pa.py` 五处、`core/ontology.py` 的 `scope_pa` 和 `pa_scope_conditions`
- `is_pa_visible`：Freight PA 对 manage 权限持有人、建单人、当前审批步骤角色的持有人可见(广播任务问题，设计 §8.3)
- `PaResponse` 加 `source` 和 `cost_allocations`(带成本中心名称和部门名称)
- 提交审批时 doc_type 用 `pa_frt`(`delegate_action` 调用处)；`_assert_invoice_link_on_submit` 对 freight 也要求有发票
- PA 取消或驳回 → 发票和运单**不自动撤销匹配**，只是释放「被 PA 占用」的状态，可以重新建 PA
- PDF(`services/pdf_pa.py`)：source=freight 时去掉 PO 那一格，加分摊表
- 任务表：`crud/task.py` 的 `TASK_LIVENESS` 等加 `pa_frt`；`tests/test_task_liveness_registry.py`
- `knowledge/pa_types.yaml`、向导层

### T4 approval-api
- `_DOC_META["pa_frt"]`(同 PaymentApplication 模型，任务类型 approve_pa / revise_pa)
- `_WORKFLOW_DEFAULTS["pa_frt"] = opm → gm → finance_manager → finance_bp`
- 路由用户和部门：`pa_frt` 走建单人(引擎现有兜底)
- 驳回 → cancelled；`_POST_APPROVE["pa_frt"]` → process_pa 任务；`_READ_OUT_TYPES`；`main.py` 启动补默认值
- 测试：默认链四步、OPM 和 GM 各自独立解析(不走 gm_or_opm)、驳回、批准后生成付款任务

### T5 finance-api
- `is_direct` 改成看 `source`；doc_kind：freight 用 `pa`(这样付款后 EPMS 发票会被标成已付)
- 待付列表和批量付款：新增类型 `freight`，标签「Freight PA」；类型筛选加一项
- NC 应付导出(`services/nc_ap_export.py`)：`match_route='freight'` 的发票按 `pa_cost_allocations` 每个成本中心一行，revexp=CRM0150102，维度沿用 `NC_CC_BY_UNIOPS` 和 `_dept_code_from_cc`
- 测试：付款回写、待付列表标签、NC 导出多行(金额、税额合计各自等于表头)

### T6 expense-api
- `is_direct` 改成看 `source`(OA 的 `PA_DOC_TYPES`、任务列表)
- 新 OCR 模式 `freight_invoice`：提示词按设计 §3.5(本期应付 vs 累计未付、一票多件、charge_type、逐行税和类别税)；返回 `{header, lines, tax_by_category}`；在服务端做 D13 的税分配建议和合计校验，结果标 ok / mismatch
- 测试：用三张样本的**已知正确结果**做解析和校验的单元测试(模型调用打桩)；另写一个手动脚本，用真实 Haiku 读三张样本对比(不进 CI，量极小)

### T7 identity-api
- 三个权限键的种子：register → procurement_officer、requester；manage → system_admin(专人等用户给名单再配)；admin → system_admin

### T8 前端
- **epms**：侧边栏 PROCUREMENT 加 Freight(权限门禁 `epms.freight.register` 或 `manage`)；Freight 页面三个标签(Shipments / Invoices / Create PA)；上传发票 → 识别结果确认 → 匹配页；PA 详情页在 source=freight 时显示分摊表；`taskTypes.ts`、`useTasks.ts`
- **portal**：`ACTION_KEYS` 加 `pa_frt`(标签「Freight PA」)；`PortalHome.tsx` 的 `DOC_PATH`；配置界面对 pa_frt 提示「不要用按部门解析的角色」
- **finance**：PaymentBatchPage 类型筛选加 Freight PA
- 门禁：三个前端各自 tsc，用 `--listFiles` 证明真编译了文件

### T9 文档与验收
- PRD：EPMS.md(新 §3.21，§3.15，§6)、FINANCE.md(FIN-PAY-06，NC 导出)、PORTAL.md(审批类型、Inbox)
- 无头浏览器：以采购员身份登记运单 → 专人上传 UPS 样本 → 匹配 → 生成 PA → 以 OPM/GM 身份打开待办和单据 → Finance 待付列表能看到
- 写发布手册：迁移在 epms-api，五个后端加三个前端要真建镜像

## 附录：消费者清单(T0，2026-10-19 grep `is_direct|pa_dir|po_id/agreement_id 为空判断`，排除 tests/alembic/_retired)

**关键取舍**：Freight PA 在 finance-api 里的 `doc_kind` 仍是 `pa`(它是 EPMS 单据，付款后要回写 EPMS 发票)。所以 finance 侧只需把 `is_direct` 改成看 `source`，凡是按 `('pa','pa_dir')` 分支的代码**都不用动**；「Freight PA」标签另由待付列表返回的 `pa_source` 字段驱动。

| 位置 | 现在的判断 | 处理 |
|---|---|---|
| epms-api `crud/pa.py:147-150` | 归属过滤 po/agreement 非空 | 改 `source IN (po, agreement, freight)` |
| epms-api `api/v1/pa.py:651,662,788,876,894` | 两个都空 → 404 | 改 `source == 'direct'` → 404 |
| epms-api `api/v1/pa.py:677` | agreement_id 为空才允许改 invoice_ids | freight 也不允许改(发票由 Freight 流程管) |
| epms-api `core/ontology.py:263-277` | 助理的 PA 范围 | 同归属过滤；freight 加一个条件 |
| epms-api `core/access_scope.py` `is_pa_visible` | PO/协议/建单人/指派任务 | 加 freight 分支(设计 §8.3) |
| epms-api `crud/task.py:249-271` | 任务表 `("pa","pa_dir")` | 加 `pa_frt` |
| epms-api `services/notification.py:60` | 邮件链接按 doc_type | 加 `pa_frt` → EPMS `/pa/{id}` |
| epms-api `services/guide_view.py:31` | 向导层名称 | 加 `pa_frt` |
| epms-api `admin/cascade.py`、`admin/service.py:526` | DM 按 pa/pa_dir 找审批状态 | 加 `pa_frt` |
| epms-api `services/finance_client.py:46` | 注释 | 不动 |
| epms-api `crud/pa_links.py` | po_id 非空 | 不受影响 |
| approval-api `crud/engine.py:179,399,755,793,808,1290,1749` | pa/pa_dir 元数据、默认链、路由、批准后、驳回 | 各加 `pa_frt` |
| approval-api `api/v1/workflows.py:16` | 读出白名单 | 加 `pa_frt` |
| approval-api `crud/workflow.py:240` | 跨部门判断只看 PO | 不受影响(freight 无 PO → 非跨部门) |
| finance-api `models/pa.py:41-54` | `is_direct` | 改 `source == 'direct'` |
| finance-api `crud/payment_execute.py:395,436` | 回写 / doc_kind | 跟随 is_direct，自动正确 |
| finance-api `crud/payment_batch.py:125,197` | 待付列表 doc_kind | 跟随 is_direct；另加 `pa_source` |
| finance-api `services/nc_ap_export.py:177-252` | 按 PO 分摊取维度、合并一行 | 加 freight 分支(T5) |
| finance-api 其余(`remittance`、`vendor_credits`、`journal_voucher`、`payment` 模型) | `('pa','pa_dir')` | 不动 |
| expense-api `models/pa.py:75-89` | `is_direct` | 改 `source == 'direct'` |
| expense-api `api/v1/pa.py:414,465` | OA 动作的 action_key / doc_kind | 跟随 is_direct |
| expense-api `api/v1/expenses.py:113` | `PA_DOC_TYPES` | 加 `pa_frt` |
| expense-api `api/v1/tasks.py` | OA 任务列表注释 | 不动(OA 不显示 PA) |
| 前端 epms `useTasks.ts`、`taskTypes.ts`，portal `PortalHome.tsx`、`AdminPanel.tsx`，finance `PaymentBatchPage.tsx` | pa_dir 映射 | 加 `pa_frt` / Freight PA(T8) |
