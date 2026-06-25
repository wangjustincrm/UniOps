# Phase 0-B2: 税判定引擎 + 发票/费用税字段 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。

**Goal:** 建立加拿大销售税主数据(GST/HST/PST/QST,FIN-TAX-001)与 place-of-supply
判定引擎(FIN-TAX-002/003,禁止硬编码),给 EPMS 发票补行级税码拆分、OA 费用行补
税码字段(FIN-EXP-008 ITC 地基)。**后端先行**:UI 接入随 Phase a(AP)走。

**Architecture:** mdm-api 拥有 tax_codes(税码,含税率生效区间、ITC 可抵扣标记)与
tax_rules(判定规则:方向 × 省份 × 客户类型 × 物项税务类别 → 税码组合,NULL=通配,
priority 决先后)两张表 + `GET /mdm/v1/tax/determine` 判定端点;种子数据涵盖 2026
各省税制(含零税率/免税/出口),全部可配置。epms-api 新增 invoice_tax_lines
(行级税码,表头 tax_amount/total 由行派生);expense-api 的 expense_line_items
加 tax_code 列。posting_lines 的 tax_code 列 B1 已备,AP 事件(Phase a)开始回填。

**Tech Stack:** 同前。mdm 测试用 TEST_DATABASE_URL + create_all(现有模式)。

---

## 契约

**tax_codes**(mdm 迁移 0003):code, name, tax_type(GST|HST|PST|RST|QST|NONE),
province CHAR(2) NULL, rate NUMERIC(7,5)(小数,0.05=5%), recoverable BOOL(ITC),
effective_from DATE, effective_to DATE NULL, active BOOL;UNIQUE(code, effective_from)。

**tax_rules**:priority INT(大者先), direction(purchase|sale|any),
province CHAR(2) NULL(通配), customer_type(business|consumer|export|NULL),
item_tax_class(standard|zero_rated|exempt|NULL), tax_code_list JSONB(code 数组),
active, effective_from/to。

**determine**:`GET /mdm/v1/tax/determine?direction=&province=&customer_type=&item_tax_class=&date=`
→ `{rule_id, codes: [{code, tax_type, rate, recoverable}], combined_rate}`;
无匹配规则 → 422 显式报错(不静默回退)。匹配:active+生效期内,各条件
NULL 或相等,按 priority 降序取首条。

**种子(数据,非代码)**:GST 5%;HST ON .13 / NS .14 / NB·NL·PE .15;
GST+PST:BC .07 / SK .06 / MB(RST).07;QC GST+QST .09975;AB/YT/NT/NU 仅 GST;
ZERO 0%(零税率)、EXEMPT 0%(免税,不可抵扣)。规则:zero_rated→ZERO、
exempt→EXEMPT、export→ZERO(priority 100);各省 standard 组合(priority 10);
兜底 any→GST(priority 1)。PST 默认 recoverable=false(非 ITC),GST/HST/QST=true。

**epms invoice_tax_lines**(epms 迁移):invoice_id FK CASCADE, line_no, tax_code,
taxable_amount NULL, tax_amount, recoverable BOOL default true。
`PUT /api/v1/invoices/{id}/tax-lines`(整组替换,表头 tax_amount=Σ行、
total=amount+tax 同步派生)+ `GET .../tax-lines`;权限同发票编辑。

**expense_line_items**(expense 迁移):+ tax_code VARCHAR(20) NULL;
create/response schema 透传。

### Task 1: mdm-api — tax 模型 + 迁移 0003 + 种子 + 模型测试
### Task 2: mdm-api — determine 判定 + 税码查询 API + 测试(判定矩阵:ON/QC/BC/AB、zero_rated、export、无匹配 422、税率生效日期切换)
### Task 3: epms-api — invoice_tax_lines 迁移/模型/端点 + 表头派生 + 测试
### Task 4: expense-api — 行级 tax_code 列 + schema 透传 + 测试
### Task 5: 共享库三处迁移上线 + roadmap 状态 + 三套回归

## Self-Review
- FIN-TAX-001(税率生效日期维护)→ UNIQUE(code, effective_from) 多版本行;
  FIN-TAX-002(同 SKU 异场景异结果)→ 规则四元组;FIN-TAX-003(零税/免税/出口)→
  税务类别 + 显式 422;FIN-EXP-008(ITC)→ recoverable 标记贯穿码表/发票行/费用行
- 取舍:判定引擎放 mdm(主数据域)而非 finance;Phase a 的 GST/HST return
  (FIN-TAX-004)在 finance 消费这些数据
- 取舍:EPMS 发票行项是 JSONB 历史遗留,本次不动它,税行独立成表
- 脏文件预案:mdm deps/db.base、epms/expense 部分文件在途;新文件可提交,
  改动既有脏文件随 WIP
