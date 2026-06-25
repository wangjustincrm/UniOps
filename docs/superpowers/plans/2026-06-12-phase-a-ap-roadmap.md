# Phase a — AP 模块总路线图(PRD Phase 1 的 AP 部分)

> 模块级排序与边界文档,沿用 Phase 0 模式:每个工作流(A0-A5)有独立实施计划,
> 各自交付可测试的软件。对应 PRD:FIN-AP-*、FIN-MD-002、FIN-CASH-001/002/005、
> FIN-TAX-004、FIN-EXP-008(UI)。

**继承自 Phase 0 的地基(全部就绪):** posting_events 脊柱(七维+期间)、统一支付
执行器(can_pay/SoD/关账闸门/payment_records/任务关闭)、税判定引擎与发票/费用税
字段、business_partners(税号/币种/付款条件)、identity(审计/SoD)。

**已拍板约束:** IFRS;标准成本+差异(成本法不影响 AP 本体);库存先集成不自建;
生产库当前为测试性质,迁移可直接打。

---

## 工作流排序

| # | 工作流 | 内容 | 规模 |
|---|--------|------|------|
| **A0** | ✅ **完成(2026-06-12)** — 执行器全权拥有 claim 付款(审计行 + 预算入账,幂等键不变);epms 遗留 action() 删除;旧 POST /payments → 410;run_tests 覆盖 identity/mdm | S |
| **A1** | ✅ **完成(2026-06-12)** — chart_of_accounts(IFRS 模板 58 科目种子);account_mappings(line_role 已种,budget_account 桥待财务配);执行器从此给每条 posting line 打 account_code | M |
| **A1.5** | ✅ **完成(2026-06-12,应用户需求插入)** — **辅助核算项**:科目级 aux_dimensions(供应商/成本中心/物料/批次/仓库/项目/渠道,与 posting 七维 1:1);科目 CRUD + **批量 CSV 导入(按 code upsert,在用 NC65 COA 整套灌入的入口)**;**Portal /finance/coa 配置页**(科目表+辅助核算勾选+映射管理+导入)。执行硬校验(过账缺维度拦截)留 GL 阶段 | M |
| **A1.7** | ✅ **完成(2026-06-15,NC65 科目对照后)** — 辅助核算升级为**可扩展目录**(aux_dimension_types,种子含部门/收支项目/销售类型/国家地区/银行,管理员可加自定义);脊柱**混合存储**(高频维度=posting_lines 列[+新增 department_id],长尾=posting_line_dimensions 侧表,emit_event 两边都写);COA 补 NC 元数据(数量核算/默认计量单位/默认币种/生效日期/现金分类/助记码/表外,迁移 0010,CSV 进出带);文件式 CSV 导入/导出 + DELETE 未引用科目 + 必填/选填(A1.5 迭代)。控制类(受控模块/凭证必输项/方向控制/提前关账)留 GL 阶段。**迁移 0010 待 10.10.50.20 恢复后上线** | M-L |
| **A2** | ✅ **完成(2026-06-12)** — ① accrual 事件(发票 matched 三处路径发射,借费用+不可抵扣税/逐税行 ITC/贷应付,科目已打,幂等);② `GET /ap/open-items` + `/ap/aging`(vendor×币种×5 账龄桶);③ 三单匹配容差(`invoice_match_tolerance_pct`,容差内自动 matched 留痕,默认 0 保持历史行为);④ EPMS 发票详情页税行编辑器(税码下拉接 B2 引擎、ITC 标记、表头派生)。**取舍:open items 暂读发票表,事件回放对账归 GL 阶段;无税行发票按"无码可抵"入账并留给 A5 异常清单** | L |
| **A3-UI** | ✅ **完成(2026-06-16)** — Bank Reconciliation 工作台(portal /finance/bank):账户选择(11 个真实账户按银行分组)+建/改(含 GL 码);**列映射导入弹窗**(读 CSV 表头,映射 日期/摘要/参考 + 单列带符号金额 或 借/贷分列,日期格式 + RBC 无年份补 default_year),映射按账户存盘下次预填;流水列表(状态筛选)+ auto-match/manual-match(选 payment)/exclude(手续费/利息/内部换汇);汇总卡 + 差异面板(未对上的付款)。后端补 `import_statement_csv` 列映射(借贷分列/日期格式/容错金额)、`bank_accounts.import_mapping` JSONB(迁移 0012,已上共享库)、POST `/transactions/{id}/exclude`、PUT `/bank/accounts/{id}`;`seed_bank_accounts.py` 幂等种 11 个真实账户(Chase US+多伦多/BoC/ICBC/RBC,CAD/USD/CNY,已种)。Payment Batches 工作台(/finance/payment-batches):due 列表(单币种)→勾选→建草稿批次→批次详情→执行(逐行 paid/failed + 错误)。路由+FINANCE 导航已接 | L |
| **A3** | ✅ **后端完成(2026-06-15)** — bank_accounts/bank_transactions(带符号金额、import_hash 去重)/exchange_rates(迁移 0009);通用 CSV 导入(date,amount,description[,reference],银行专用格式上游归一化);自动核销(精确额+币种+日期窗,唯一→匹配/歧义→参考号消歧或留人工)+ 人工核销(允许手续费差额)+ 解核;`GET /bank/reconciliation` 差异清单(FIN-CASH-002);exchange_rates 查询 + 执行器/accrual 锁 fx_rate(FIN-CASH-003,无率留 1 入 A5)。**对账工作台 UI 待银行实际导出格式确认后单独一轮;迁移 0009 待 10.10.50.20 网络恢复后上线** | M-L |
| **A4** | ✅ **后端完成(2026-06-15)** — ~~付款审批矩阵~~已取消(approval 全链路覆盖)。**付款批次**(payment_batches/lines + payment_records.batch_id,迁移 0011;GET /payments/due、POST /batches、/batches/{id}/execute 逐行过执行器+per-line savepoint 隔离失败;门=can_pay 含 ap_clerk)。**部分付款**复用既有 prepayment PA:预付 PA→发票 partially_paid 仍在 open-items,尾款/常规→paid(非通用分期引擎);open-items/aging 纳入 partially_paid。**批次工作台 UI + 迁移 0011 上共享库待办** | M |
| **A5** | ✅ **完成(2026-06-15)** — 执行器把 claim 的销售税按行 tax_code 拆成多条 coded sales_tax posting 行(无码余额并入 NULL 行入异常);`GET /finance/v1/tax/gst-hst-return?period=YYYY-MM`(按期间×tax_code 聚合 ITC + 无码异常 + 合计;销项 0 待 AR;净额=-ITC)+ CSV 导出;OA 报销创建页加逐行税码选择(经新 mdmApi 取 B2 税码),贯通 expense_line_items.tax_code → 申报底稿。无新增迁移 | M |

**顺序依据:** A0 还债止血;A1 是 A2 的前提(应计事件需要科目);A2 是 AP 的本体;
A3/A4 可在 A2 后并行;A5 收口。A2 是第一个含大块前端的工作流。

## 设计决议(Phase a 各计划遵守)

1. **AP 子账 = 事件回放,不建第二套账**:open items 由 accrual − payment 事件聚合
   (脊柱是唯一事实源),发票/PA 单据仍归 epms/OA 域——与 Phase 0 路线一致。
2. **COA 归 finance**(会计域),预算科目(budget-api)与 COA 经映射表桥接,
   不强行合并两套科目。
3. **应计事件发射点**在 epms 发票 status→approved(匹配通过)处,经转发或直写
   (执行时按 B1.5 模式定);幂等键 `(invoice, accrual)`。
4. 新表一律 entity_id;金额列 Numeric(15,2);镜像建模前先查 information_schema
   (Phase 0 三次踩坑教训)。

## 需要业务输入的三件事(不阻塞 A0/A1 起步)

1. **COA 模板评审**:A1 我先按 IFRS + 加拿大乳品制造业起草科目表种子(资产/负债/
   权益/收入/COGS/费用,4 位编码),需财务过目调整——种子是数据,随时可改;
2. **银行与流水格式**(A3 前):哪家银行、导出格式(CSV/OFX/CAMT)、币种账户清单;
3. ~~付款审批阈值(A4 前)~~ **已取消(2026-06-15)**:付款审批由现有 approval-api 全链路覆盖,不做独立阈值矩阵。

## 上线前清单滚动(Phase 0 遗留,未变)

dev/staging compose 分离、JWT fallback 移除、CORS 收敛(+identity 的 `*`)、
file-api 白名单、引擎行锁、Anthropic key 轮换、reset-db 保险丝;
新增:users/company_config 表所有权迁至 identity、epms /auth 代理退役(前端直连)。
