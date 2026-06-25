# Phase a-A4: 付款批次 + 部分付款修正(后端)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。

**Goal:** 付款批次(payment run,FIN-AP-004:攒批付款)+ 部分付款修正(复用既有
prepayment PA;修执行器"任意 PA 即标发票全额已付"的 bug)。**付款审批矩阵已取消**
(approval-api 全链路已覆盖)。批次工作台 UI 待后续一轮。

## A4a 付款批次(finance 迁移 0011)

**payment_batches**: batch_number(PR-YYYYMMDD-NNNN), batch_date, status
(draft|executed|cancelled), currency, total Numeric(15,2), payment_method,
created_by, executed_at, entity_id。
**payment_batch_lines**: batch_id FK CASCADE, doc_kind(pa|pa_dir), doc_id,
doc_number, amount, status(pending|paid|failed), payment_record_id NULL, error。
**payment_records.batch_id**: UUID NULL(批次付款打标)。

行为:
- `GET /finance/v1/payments/due`:可付清单 = 已审批未处理 PA(status=approved),
  按 vendor/币种/到期。
- `POST /finance/v1/payments/batches {doc_ids:[pa...], payment_method, payment_date}`:
  建 draft 批次,行=选中 PA 快照(校验都是 approved、同币种),total=Σ。
- `POST /finance/v1/payments/batches/{id}/execute`:对每行调统一执行器
  `payment_execute.execute`(沿用 can_pay/SoD/关账闸门/posting/发票标记),
  payment_records 打 batch_id;逐行 paid/failed(单行失败不滚整批,记 error);
  全成功→executed,部分→executed(行级 error 可见)。
- `GET /finance/v1/payments/batches` + `/{id}`(含行)。
- 门:can_manage(coa._require_manage,角色∪指派)。

## A4b 部分付款修正

执行器 PA-PO 分支(payment_execute,标发票 paid 处):不再无条件 `paid`。
- 取该 PA 的 pa_type(finance pa 镜像已有);
- `prepayment` → 发票置 `partially_paid`(新状态,仍属 open-items);
- 其余(regular/balance/settlement)→ 仍 `paid`。
open-items / aging 的 open 集合加入 `partially_paid`。

## Tasks
1. 迁移 0011 + 模型(PaymentBatch/PaymentBatchLine + payment_records.batch_id)
2. crud/payment_batch(due 查询 + 建批 + 执行批,执行器加可选 batch_id 参数)
3. API + A4b 执行器发票标记修正 + open-items/aging 纳入 partially_paid
4. 测试(建批校验/批量执行/单行失败不滚批/预付→partially_paid/尾款→paid/due 清单)
5. 迁移上线(待 DB)+ 回归 + roadmap

## Self-Review
- FIN-AP-004 批次覆盖;部分付款用既有 prepayment PA 不造分期引擎(用户:仅一供应商 50/50)
- 取舍:批次执行逐行独立事务语义由执行器各自 commit?否——批次在一个请求里循环
  执行器(每次 execute 内部 flush,批末统一 commit);单行失败 catch 记 error 继续
- UI 工作台后置;EFT 文件导出后置
- 教训:迁移待 10.10.50.20 恢复;新表全 entity_id;门用 can_manage
