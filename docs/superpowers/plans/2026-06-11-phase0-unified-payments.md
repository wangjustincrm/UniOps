# Phase 0-B1.5: 统一 Payment 入口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。Steps use checkbox syntax.

**Goal:** 把三处独立的付款实现(approval-api engine `process` 分支、expense-api `record_payment`、expense-api `process_pay`)收敛为 finance-api 的单一执行入口 `POST /finance/v1/payments/execute`;三个既有 HTTP 入口保留为转发(前端零改动)。

**Architecture:** finance-api 是支付域 owner:统一 can_pay(JWT 角色 ∪ role_management 指派)、单事务完成状态翻转 + payment_records(通用化)+ posting 发射 +(PA-PO)发票标记。OA claim 的域内附属动作(审批事件、预算入账)留在 expense-api,转发返回后执行(预算入账本就是幂等 HTTP,风险不变)。engine 的 process 分支删除。

**Tech Stack:** 同 B1。测试连本地 docker postgres。

**修复的三个缺陷:** ① EPMS 打款路径无角色校验;② OA PA 丢弃付款元数据;③ payment_records 孤儿表接入。

---

### Task 1: finance-api — payment_records 通用化(迁移 0003)+ 镜像模型

**Files:**
- Create: `finance-api/alembic/versions/0003_generalize_payment_records.py`
- Modify: `finance-api/app/models/payment.py`(加 doc_kind/doc_id/doc_number,vendor 列转可空)
- Modify: `finance-api/app/models/pa.py`(po_id 改 nullable——Direct PA 实际为 NULL,镜像声明错误)
- Create: `finance-api/app/models/mirrors.py`(Invoice / CompanyConfig / ExpenseClaim 只读镜像)
- Modify: `finance-api/app/main.py` + `finance-api/alembic/env.py`(注册 mirrors)
- Modify: `finance-api/tests/conftest.py`(stub 换成 create_all 镜像表后再跑 alembic)

迁移 0003 内容:`pa_id`/`vendor_id`/`vendor_name` DROP NOT NULL;新增 `doc_kind VARCHAR(20)`、`doc_id UUID`、`doc_number VARCHAR(40)`(回填旧行 doc_kind='pa', doc_id=pa_id, doc_number=pa_number);索引 `(doc_kind, doc_id)`。

mirrors.py:Invoice(status 列,抄 approval-api)、CompanyConfig(role_management JSONB)、ExpenseClaim(claim_number/claim_type/status/paid_at/total_amount/tax_amount/net_amount/employee_name/currency)。

conftest 改造:`_migrate()` 里删掉单列 stub,改为先 `Base.metadata.create_all(tables=[PaymentApplication, Invoice, CompanyConfig, ExpenseClaim 的 __table__])` 再跑 alembic(0001 的 FK 目标 payment_applications 由镜像建全列版本)。

- [ ] 失败测试:`tests/test_payment_execute.py`(见 Task 2 的测试,先建文件跑红)
- [ ] 写迁移 + 模型 + mirrors + conftest 改造
- [ ] `pytest tests/test_posting_models.py tests/test_posting_api.py` 保持绿(回归)
- [ ] Commit: `feat(finance-payments): generalize payment_records + doc mirrors (Phase 0-B1.5)`

### Task 2: finance-api — POST /payments/execute 统一执行

**Files:**
- Create: `finance-api/app/services/posting.py`(emit_event 帮助器,同 B1 各服务拷贝,import 自 app.models.posting)
- Create: `finance-api/app/schemas/payment_execute.py`
- Create: `finance-api/app/crud/payment_execute.py`(can_pay 解析 + PA/claim 两分支)
- Modify: `finance-api/app/api/v1/payments.py`(加 /execute 路由)
- Test: `finance-api/tests/test_payment_execute.py`

行为契约:
- body: `{doc_kind: pa|pa_dir|expense_claim, doc_id, payment_date?, payment_method?='bank_transfer', reference?, amount_paid?, notes?}`
- 权限:JWT role ∈ {ap_clerk, finance_manager, finance_bp, system_admin} **或** role_management 指派 finance_bp / finance_manager(_user_holds_role 逻辑抄 expense-api expenses.py:72)
- PA 分支:实际 kind 由 po_id 推导(pa_dir = po_id IS NULL);status 必须 approved → processed;PA-PO 把 invoice_ids 中 matched/approved 的发票置 paid;写 payment_records;emit `payment` 事件(应付/银行两行)
- claim 分支:approved → paid + paid_at=now;写 payment_records(vendor 列空,partner=员工);emit `expense_paid`(净额/税额/银行三行)
- 全部单事务,路由显式 commit;非法状态 409;无权 403;未找到 404
- 幂等:posting 靠唯一键;重复 execute 因状态非 approved 而 409(支付本身不允许重复)

测试(占位列表,代码在实施时按 B1 测试风格写全):execute_pa_dir 翻状态+payment_record+事件两行借贷相等;execute_pa 标发票 paid;execute_claim 翻 paid+三行事件;非 approved 409;无权角色 403;role_management 指派的 finance_bp 放行。

- [ ] 跑红 → 实现 → 跑绿(全套 finance tests)
- [ ] Commit: `feat(finance-payments): unified POST /payments/execute (Phase 0-B1.5)`

### Task 3: expense-api — 三个入口改转发

**Files:**
- Create: `expense-api/app/services/finance_client.py`(httpx POST execute,透传 token;模式同 approval_client)
- Modify: `expense-api/app/api/v1/pa.py`:`record_payment` 改为转发(权限交给 finance 统一判;保留 404 检查);`pa_action` 拦截 action=='process' 转发
- Modify: `expense-api/app/api/v1/expenses.py` + `app/crud/expense.py`:`/pay` 路由改为先转发 finance(状态/记录/posting),返回后本地补 ExpenseApprovalEvent + `_book_budget`(幂等);`process_pay` 中状态翻转/posting 部分移除
- Modify: `expense-api/tests/test_pa.py`、`tests/test_posting_emit.py` 相应改 mock(finance_client mock 化,模式同 delegate_action mock)

- [ ] 跑红 → 实现 → expense-api 全套绿
- [ ] Commit(注意 pa.py / expenses.py / expense.py 带用户 WIP,新文件与测试可提交,接线随 WIP)

### Task 4: epms-api — process 拦截转发

**Files:**
- Create: `epms-api/app/services/finance_client.py`(同上)
- Modify: `epms-api/app/api/v1/pa.py` `pa_action`:body.action=='process' 时不再 delegate engine,改调 finance_client
- Test: `epms-api/tests/test_pa_process_forward.py`(mock finance_client,断言转发与 422 透传)

- [ ] 跑红 → 实现 → 新测试绿(epms 全套有 82 个既有失败,只要求不新增)
- [ ] Commit

### Task 5: approval-api — 删 engine process 分支 + roadmap

**Files:**
- Modify: `approval-api/app/crud/engine.py`:删除 `elif act == "process"` 整个分支(含 posting 发射与 emit import 若再无引用);process 由 finance-api 全权负责
- Modify: `docs/superpowers/plans/2026-06-10-phase0-roadmap.md`:新增 B1.5 行(完成)、Phase a 注记(claim 附属动作收编 finance / 部分付款 / 付款审批流)、清理项更新(epms pa_crud 遗留 process 分支、finance 旧 POST /payments 孤儿端点标记 deprecated)
- [ ] approval-api 全套测试绿(用户新增的 engine 测试若覆盖 process,相应更新)
- [ ] 全局回归:`./run_tests.sh --unit-only` 中 finance/approval/expense 三套绿
- [ ] Commit

## 执行偏差记录(实施时确认)

1. **OA `pa_action` 不拦截 process**:`PaActionRequest` 的 Literal 本就不含
   "process"(422 schema 拒绝)——OA 付款唯一入口是 /pay,无需第三条路;
   测试 `test_pa_action_rejects_process` 固化此契约。
2. **epms-api 转发未写专属 API 测试**:epms 测试基建的 action 类测试依赖真实
   approval-api 且引擎 DB 与测试 DB 不一致(82 个既有失败的一部分),pytest-mock
   未安装。转发逻辑与 OA 侧完全同构(OA 侧已测),执行器在 finance 有 7 测试;
   epms 侧以模块导入编译验证 + UI 冒烟代替。
3. **新增 Task 镜像与待办关闭**:引擎被删的 process 分支原本会 `_complete_tasks`
   关闭 process_pa 待办——计划遗漏,实施中发现并补进执行器
   (`_complete_open_tasks`,PA 与 claim 均关闭),含测试断言。

## Self-Review
- 三入口全覆盖:EPMS action=process(Task 4)、OA /pa/pay + action=process(Task 3)、OA /expenses/pay(Task 3);实现唯一化(Task 2);旧实现移除(Task 3 process_pay 瘦身、Task 5 engine 分支删除;epms pa_crud 遗留分支本就无人调用→roadmap 清理项)
- 已知取舍:claim 的审批事件+预算入账在转发后本地执行(两段),失败模式与现状一致(budget 本就 HTTP 事后);Phase a 彻底收编
- 风险:engine.py / expense-api pa.py / expenses.py / expense.py 为脏文件,接线不提交,随用户 WIP
