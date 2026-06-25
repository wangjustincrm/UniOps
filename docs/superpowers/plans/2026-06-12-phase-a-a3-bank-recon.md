# Phase a-A3: 银行与对账(后端骨架)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。

**Goal:** 银行账户主数据、银行流水 CSV 导入(通用格式,银行专用格式留适配接口)、
流水 ↔ payment_records 自动/人工核销、对账差异视图(FIN-CASH-001/002)、
exchange_rates 与记账锁汇率(FIN-CASH-003,posting_lines.fx_rate 列 B1.7 已备)。
**UI 对账工作台待银行格式确认后单独一轮。**

## 契约(finance 迁移 0009,全部带 entity_id)

**bank_accounts**: name, bank_name, account_masked(尾号), currency,
ledger_account_code(对应 COA 银行科目,如 1010), is_active。

**bank_transactions**: bank_account_id FK, txn_date, description(500),
reference NULL, amount Numeric(15,2)(**带符号:负=流出**), currency,
status(unmatched|matched|excluded), matched_payment_id FK payment_records NULL,
matched_at/matched_by, import_hash UNIQUE(防重复导入:sha256(账户|日期|金额|描述|参考))。

**exchange_rates**: from_currency, to_currency, rate Numeric(18,8),
effective_date, UNIQUE(from,to,effective_date)。

## 行为

- `POST /finance/v1/bank/accounts` CRUD(manage 门=COA 同款 can_manage)
- `POST /finance/v1/bank/{account_id}/import`:multipart CSV
  (`date,amount,description[,reference]`,utf-8-sig)→ {imported, duplicates, errors}
- `POST /finance/v1/bank/match/auto?account_id&window_days=5`:对未匹配流出行,
  候选 = 同币种、金额相等(|txn.amount|==payment.amount)、payment 未被占用、
  日期窗内;唯一候选 → 自动核销;多候选时 reference 含 doc_number 可消歧,
  否则留待人工(宁缺勿错)
- `POST /bank/transactions/{id}/match {payment_record_id}` / `POST .../unmatch`:
  人工核销/解核(金额不一致允许——银行手续费等由人判断,matched_by 留痕)
- `GET /finance/v1/bank/reconciliation?account_id&from&to`:期间汇总
  (流入/流出/已核销额)+ 未核销流水清单 + 期间内未被核销的 payment_records 清单
  (FIN-CASH-002 差异清单)
- **锁汇率**:执行器与 accrual 发射时,非 CAD 行按业务日查 exchange_rates
  最近生效汇率写入 fx_rate;查无 → 留 1 并入 A5 异常口径

### Task 1: 迁移 0009 + 模型 + 银行账户 CRUD
### Task 2: CSV 导入(哈希去重)+ 测试
### Task 3: 自动/人工核销 + 对账视图 + 测试矩阵(唯一匹配/多候选歧义/参考号消歧/手工含差额/解核/汇总)
### Task 4: exchange_rates + 发射点锁汇率 + 测试
### Task 5: 迁移上线 + 回归 + roadmap

## Self-Review
- FIN-CASH-001(流水导入+自动匹配)/-002(差异+责任人)/-003(多币种锁汇率)覆盖;
  13 周现金流(-004)按 roadmap 归 Phase 3;付款审批矩阵(-005)归 A4
- 取舍:自动匹配宁缺勿错(只在无歧义时落);银行专用格式(OFX/具体银行 CSV)
  在导入函数留 parser 参数位,确认格式后加适配器
- 教训应用:新表先无镜像问题(全 finance 自有);UI 全英文;门=can_manage 服务端解析
