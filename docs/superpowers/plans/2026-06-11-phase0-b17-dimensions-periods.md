# Phase 0-B1.7: posting 维度扩展 + 会计期间 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。

**Goal:** 在 GL 回放无法回填之前,给 posting_lines 补齐 FIN-GL-003 七维核算列与
entity_id;建立 fiscal_periods(FIN-GL-002 软/硬关账),posting_events 打期间戳,
统一支付执行器挂关账闸门。

**Architecture:** 迁移 0004(finance-api)一次完成全部列;期间戳只在 finance-api 的
emit_event 打(B1.5 后生产路径唯一发射点);关账闸门挂在 payment_execute(非 open
期间拒绝付款)。approval/expense 的镜像拷贝补列保持保真(测试 create_all 用)。
新表/新列一律带 entity_id(可空,默认单法人,FIN-MD-001 预埋)。

**Tech Stack:** 同 B1/B1.5。

---

## 契约

**迁移 0004:**
- posting_lines + `item_id` `lot_id` `warehouse_id` `project_id` `entity_id`(UUID NULL)、`channel`(VARCHAR(30) NULL)
- posting_events + `entity_id`(UUID NULL)、`fiscal_period`(VARCHAR(7) NULL,'YYYY-MM')
- payment_records + `entity_id`(UUID NULL)
- 新表 `fiscal_periods`:id, period VARCHAR(7) UNIQUE, entity_id UUID NULL(多法人时
  约束改 (entity_id, period),现单法人 unique(period) 即可), status VARCHAR(12)
  default 'open'(open | soft_closed | hard_closed), closed_at, closed_by, 时间戳

**行为:**
- finance emit_event:`fiscal_period = occurred_at UTC 的 'YYYY-MM'`;lines dict 新增
  可选键 item_id/lot_id/warehouse_id/channel/project_id/entity_id 原样落列
- payment_execute:执行前查当期 fiscal_periods,status != 'open'(soft 或 hard)→
  ValueError 409 "Fiscal period YYYY-MM is closed";无行 = open
- 期间管理 API(finance):GET /finance/v1/periods;POST /periods/{period}/close
  (body: {hard: bool});POST /periods/{period}/reopen(仅 soft_closed 可重开);
  角色:finance_manager / system_admin

### Task 1: 迁移 0004 + 模型(finance 主体 + 两处镜像补列)+ 模型测试
### Task 2: emit 期间戳 + 维度键透传 + 测试
### Task 3: 关账闸门 + 期间管理 API + 测试
### Task 4: 共享库迁移上线 + 回归(finance/approval/expense 三套)+ roadmap 状态更新

## Self-Review
- 覆盖 roadmap B1.7 全部三件事(维度列、entity_id、关账);七维中 cost_center/partner
  B1 已有,本次补 5 维 + channel;FIN-GL-002 的"已关期间普通用户不可改账"在 Phase 0
  语境下 = 付款执行闸门(唯一写入点),GL 阶段再扩展到凭证
- 取舍:fiscal_period 用普通列而非 Postgres 生成列(to_char 非 IMMUTABLE,表达式繁琐;
  发射点唯一,应用层打戳足够)
- 取舍:soft_closed 也阻止付款(付款是经营性动作);软关期间的"调整分录"是 GL 阶段概念
