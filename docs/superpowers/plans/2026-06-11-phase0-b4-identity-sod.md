# Phase 0-B4: 身份抽离 + SoD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。

**Goal:** 认证从 epms-api 抽到独立 identity-api(:8009);epms /auth/* 变薄代理
(B1.5 既定切流模式:新 owner + 旧入口转发,前端零改动);SoD 地基(sod_rules +
self_payment 规则接入统一支付执行器,FIN-AUD-003)+ 通用审计表(audit_log,
FIN-AUD-001,登录/改密等认证事件先接入)。

**Architecture:** identity-api 与共享库同一 users / company_config 表(镜像模型,
schema 所有权仍在 epms alembic——B4 只迁代码所有权,不动表);JWT 同 secret 同 claims,
**其余服务 deps 零改动**。identity 自有 alembic 流只建自己的表(sod_rules、audit_log,
均带 entity_id)。OTP/refresh 黑名单沿用 Redis。

**范围裁剪(对 roadmap 原文的偏差,明示):**
- tasks / notification 不随 B4 迁移——它们是 workflow 域不是身份域,Phase a 再议;
- epms /users 管理端点(437 行,管理页用)留在 epms,users CRUD 归属后续清理;
- epms /auth 的实现删除、test_auth.py 随实现迁到 identity(epms 基线 5f/7p)。

**SoD Phase 0 最小集:** 规则 `self_payment`(单据创建人/报销人不得执行该单据付款),
执行点 = finance 统一支付执行器(读共享 sod_rules 表,enabled 时:PA 拦 created_by==payer,
claim 拦 employee_id==payer);规则是数据,可关闭。

### Task 1: identity-api 完整服务(骨架+认证搬迁+自有迁移 0001 sod/audit+审计接线)+ 测试
### Task 2: epms /auth/* 薄代理(catch-all 转发)+ IDENTITY_API_URL + 删 test_auth(随迁)
### Task 3: finance 执行器 self_payment SoD 检查(SodRule 镜像 + claim 镜像补 employee_id)+ 测试
### Task 4: compose(identity-api :8009)+ 迁移上线 + 活体登录冒烟(经 epms 代理)+ roadmap/memory

## Self-Review
- 切流零风险:JWT 不变 → 各服务校验零改动;前端零改动;identity 故障时代理 502(显式)
- 审计:登录成功/失败、改密、MFA 开关 → audit_log(留存策略≥6年,只插不删)
- 已知偏差按上文"范围裁剪"记录;users/company_config 表所有权迁移列入上线前清单后续
