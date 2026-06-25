# Phase 0 — ERP 地基改造总路线图

> 本文档是 Phase 0 的排序与边界说明,不是可执行计划。每个工作流有/将有独立的实施计划
> (见下方链接),各自独立交付可测试的软件。

**背景:** UniOps 正从"采购系统带几个朋友"演进为小型 ERP(目标模块:AP → Sales/CRM →
AR → CMMS → 固定资产 → GL 总账闭环)。Phase 0 是所有后续模块的共同地基。

**目标形态升级(2026-06-11):** 完整财务系统 PRD 落定
(`c:\Project\canada-milk-powder-finance-prd.md`,对标 SAP B1 / NetSuite 的食品制造业
财务 ERP)。复盘结论:Phase 0 全部决策被 PRD 反向验证(posting 脊柱 ≈ FIN-GL-001 凭证
前身;统一支付 ≈ FIN-CASH-005/SoD 挂载点;B2/B3/B4 对应 FIN-TAX/FIN-MD-006/FIN-AUD-003,
均 P0)。三个战略决策已拍板:

1. **库存/生产:先集成** — NC65/WMS/MES 继续管货,UniOps 通过接口拉 InventoryTransaction
   在财务侧建账(批次/成本/追溯),"先架空、后替换";不自建库存内核。
2. **会计准则:IFRS** — COA 模板、FA 折旧、收入确认按 IFRS 设计。
3. **成本法:标准成本 + 差异**(FIN-INV-004)— NC65 现行移动平均问题多,弃用;
   迁移期两套成本法并行是已知痛点,需差异桥表。

**关键前提变更(2026-06-10):** 生产库 10.10.50.20 当前**没有真实数据**,可当测试库使用。
因此原 P0 安全加固(dev compose 直连生产、reset-db 保险丝)降级为"上线前清单",
Phase 0 直接从 posting_events 开工,迁移可直接打 10.10.50.20。

---

## 工作流排序

| # | 工作流 | 计划文件 | 状态 |
|---|--------|----------|------|
| B1 | **posting_events 记账事件脊柱** — finance-api 拥有表,打款动作发事件 | [2026-06-10-phase0-posting-events.md](2026-06-10-phase0-posting-events.md) | ✅ 完成(2026-06-10) |
| B1.5 | **统一 Payment 入口** — finance-api `POST /payments/execute` 是唯一支付实现(统一 can_pay 含 role_management 指派、payment_records、posting、发票标记、关闭待办);EPMS action=process / OA 两个 /pay 均转发;engine process 分支已删除 | [2026-06-11-phase0-unified-payments.md](2026-06-11-phase0-unified-payments.md) | ✅ 完成(2026-06-11) |
| B1.7 | **posting 维度扩展 + 会计期间** — posting_lines 七维列 + entity_id(迁移 0004);fiscal_periods 软/硬关账 + 期间管理 API(GET/close/reopen,硬关不可重开);事件期间戳(仅 finance emit 打,B1.5 收口红利);支付执行器关账闸门 | [2026-06-11-phase0-b17-dimensions-periods.md](2026-06-11-phase0-b17-dimensions-periods.md) | ✅ 完成(2026-06-11) |
| B2 | **税判定引擎 + 发票/费用税字段** — mdm 的 tax_codes(生效日期版本化税率 + ITC 标记)+ tax_rules(place-of-supply 四元组判定,NULL 通配,无匹配显式 422)+ `GET /mdm/v1/tax/determine`;种子覆盖 2026 各省税制(含 NS 14% 切换、零税/免税/出口);epms invoice_tax_lines(行级税码、表头派生)+ OA 费用行 tax_code。**UI 接入随 Phase a** | [2026-06-11-phase0-b2-tax-engine.md](2026-06-11-phase0-b2-tax-engine.md) | ✅ 完成(2026-06-11,后端) |
| B3 | **Business Partner 主数据** — business_partners(双角色 + FIN-MD-006:税号/customer_type/province[喂税引擎]/信用额度);671 行 vendors 保 id 迁入;EPMS 5 个单据 FK 平移;vendors 留兼容视图(expense 只读镜像零改动);`/mdm/v1/partners` CRUD;EPMS /vendors API 与前端零改动 | [2026-06-11-phase0-b3-business-partner.md](2026-06-11-phase0-b3-business-partner.md) | ✅ 完成(2026-06-11) |
| B4 | **身份抽离 + SoD** — identity-api(:8009)接管全部认证(login/MFA OTP/refresh 黑名单/me/改密),epms /auth/* 变薄代理(前端零改动,JWT 不变各服务零改动);sod_rules(种子 self_payment,支付执行器强制)+ append-only audit_log(认证事件已接,≥6年留存)。**范围裁剪**:tasks/notification 留 workflow 域(Phase a 议);epms /users 管理端点与 users 表所有权留后续清理 | [2026-06-11-phase0-b4-identity-sod.md](2026-06-11-phase0-b4-identity-sod.md) | ✅ 完成(2026-06-12) |

**为什么 B1 最先:** 成本最低(两张表 + 两个发射点),对路线图全部六个目标生效;
B2/B3 的产出(tax_code、partner_id)在 posting_lines 上已预留列,落地后回填即可。

## 设计决议(各计划共同遵守)

1. **posting_lines 不建跨服务 FK** — partner_id / cost_center_id 是裸 UUID,松耦合,
   避免再制造 approval-api 式的镜像模型连锁。
2. **同库同事务发事件** — 发射方直接 INSERT finance-api 拥有的表(与现有 tasks 表的
   共享库模式一致),不引入消息队列;幂等靠 `UNIQUE(source_doc_type, source_doc_id, event_type)`。
3. **account_code 可空** — COA 科目表是 GL 阶段(路线 f)的事;Phase 0 用 `line_role`
   (accounts_payable / bank / employee_expense / sales_tax)语义槽位占位。
4. **schema 仍用 public** — postgres schema 按域分区是后续重构,YAGNI。

## 上线前清单(原 P0 安全项,生产启用真实数据前必须完成)

- [ ] dev compose 改回连本地 postgres 容器;另建 `docker-compose.staging.yml` 连 10.10.50.20
- [ ] `reset-db.sh` 加保险丝:检测到 POSTGRES_HOST 为 10.10.50.x 时拒绝执行
- [ ] 5 个服务的 JWT_SECRET_KEY 去掉默认值 fallback(缺失即拒绝启动)
- [ ] 轮换根 `.env` 里的 Anthropic API key 并移出项目目录
- [ ] approval-api / file-api / finance-api / mdm-api 的 CORS `allow_origins=["*"]` 收敛
- [ ] file-api 上传类型白名单 + 下载强制 `Content-Disposition: attachment`
- [ ] 审批引擎 `engine.py` 状态变更加 `with_for_update` 行锁(防并发双审)

## Phase 0 之后(对齐 PRD Phase 1-3,取代原 a-f 排序)

```
PRD Phase 1 ≈ 原路线 a+c:AP 子账(COA 同步建,IFRS 模板)→ 银行流水导入+对账+
              多币种汇兑(FIN-CASH-001~003)→ AR 子账(客户信用/账期/账龄)
              EPMS 顺手补强:三单匹配自动拦截(FIN-AP-001,数据已齐只差规则层)
PRD Phase 2 ≈ 库存/批次财务侧(集成 NC65/WMS/MES 拉 InventoryTransaction,不自建)
              → 标准成本+差异核算 → 批次追溯/召回财务影响(FOOD-FIN-*)
PRD Phase 3 ≈ 原路线 f:GL 回放历史 posting_events 建总账 → 关账自动化 →
              13 周现金流 → CFO 驾驶舱
CMMS(原路线 d)PRD 中无位置,降级至 Phase 3 之后或独立小项目。
```

**多法人注意**(FIN-MD-001 P0 vs 现状 CompanyConfig 单例):彻底多法人化是大手术,
缓解策略 — 从 B1.7 起所有新财务表一律带 `entity_id` 列(可空,默认单法人),
禁止再扩散单例假设。

## Phase a(AP 模块)预留事项 — 来自 B1.5 的取舍

- OA claim 付款的两个域内附属动作(ExpenseApprovalEvent 审计行、budget book-expense)
  目前在转发返回后由 expense-api 本地执行(两段式,booking 幂等)。Phase a 把它们
  收编进 finance 执行器,消除两段。
- 部分付款 / 分期付款:execute 已接收 amount_paid 并原样入 payment_records,
  但状态翻转是全额语义;Phase a 设计部分付款状态机。
- 付款本身的审批流(大额付款需复核)挂到 execute 之前。

## 顺手发现的清理项(不阻塞,见缝插针)

- `epms-api/app/crud/pa.py` 的 `action()` 中 `act == "process"` 分支是无人调用的遗留代码,确认后删除。
- finance-api 旧 `POST /finance/v1/payments`(payment_records 手工登记)从未被任何
  前端调用;B1.5 后由 /payments/execute 自动写入 — 标记 deprecated,Phase a 移除。
- approval-api 测试基建已由 B1 建立(B1 前为 0 测试),引擎状态机测试持续补充中。
