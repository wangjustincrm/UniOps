# Claim Accrual(员工报销应付)设计 —— 占位稿

> 状态:**占位稿**(关键决策已定,实现前需完整 brainstorm 细化)。日期:2026-07-07。
> 归属:Finance 重构子项目①。关联:`2026-07-07-finance-jv-subsystem-design.md`、
> `2026-06-20-finance-ap-invoice-*`、[[project_uniops_finance_jv_nc65]]。

## 1. 背景与问题
当前**员工报销(ExpenseClaim)不进 AP、也不做权责发生制**:expense-api 的 `emit_event` **无调用**,
Claim 提交/审批**不产生任何会计分录**;只在付款时由 finance `payment_execute` 的 `expense_claim` 分支
出「**借 费用/税 贷 银行**」——纯收付实现制,**已批未付 = 未确认的隐性负债**(不进账龄/资产负债表/现金
预测)。详见 AP 模块讨论。

## 2. 决策(方案 B,已定)
给 Claim 补权责发生制,且用**独立控制科目**(不污染 trade AP):
- **审批通过时** accrual:`借 费用/税  贷 员工报销应付(employee_reimbursement_payable)`
- **付款时**:`借 员工报销应付  贷 银行`(替换现「借费用贷银行」)
- 会计口径:Claim = 欠员工 = **「其他应付款 / 应付职工薪酬-报销」**,与 trade AP(欠供应商)**分账**。

## 3. 设计草图(待细化)
- 新 `line_role` `employee_payable` + `account_mappings` 映射到「其他应付款-报销」科目。
- **expense-api**:Claim 审批通过钩子调用 `emit_event`(event_type=`expense_accrual`,借费用/税 贷员工应付)
  —— 这是 expense-api 首个真实 emit 点。
- **finance `payment_execute`** claim 分支:从「借费用贷银行」改「借员工应付贷银行」;付款回写 accrual。
- **JV**:accrual 与付款事件都经 `emit_event` → 自动生成 draft JV(见 JV 子系统),无需特殊处理。
- 可选:**员工应付账龄**视图;**应付总览**聚合页(trade AP + 员工应付,分账不分屏)。

## 4. 待解决(实现前 brainstorm)
- 控制科目具体编码(沿用 NC 中式科目下的哪个「其他应付款」子目)。
- accrual 的税处理(可抵扣 ITC 与 trade AP 一致?)。
- Claim 驳回/作废时 accrual 冲销(红冲,依赖 JV 红冲能力)。
- 预付/差旅借支(员工往来另一方向)是否纳入。
- 与现有 OA 审批流/预算入账(`_book_claim_budget`)的先后与幂等。

## 5. 依赖与范围
- **依赖 ② JV 子系统**(accrual → draft JV)。**独立于 NC 迁移**,可较早落地。
- 不含:应付总览页完整实现、员工往来借支(后续)。
