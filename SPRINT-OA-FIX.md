# UniOps OA — 修复 Sprint 计划(SPRINT-OA-FIX)

**版本:** 1.1
**日期:** 2026-06-02
**依赖:** PRD-OA v2.0(`epms/docs/PRD-OA.md`)
**状态:** ✅ 已执行(阶段 0–4 完成;任务 4.3 按设计延后到生产验证后)
**背景:** 对 OA 模块 PRD 与代码做完整核对后,汇总所有遗留问题并制定修复计划。OA 核心功能 S1–S5 已实质落地,本计划聚焦"最后一公里"缺口、安全隐患、架构对齐与文档同步。

---

## 0. 问题清单(核对结论)

| # | 问题 | 类型 | PRD 依据 | 证据 |
|---|------|------|---------|------|
| P0-1 | TRV / CFM 在"New Claim"下拉无入口,前台无法进入 | 功能阻断 | §7.1 | `oa/src/pages/expenses/ExpenseListPage.tsx:109-130` |
| P0-2 | OCR API key 打进浏览器 bundle(`VITE_ANTHROPIC_API_KEY` + `dangerouslyAllowBrowser`) | 安全 | §13 | `oa/src/lib/invoice-parser.ts:106-112` |
| P1-1 | EXP/TRV 收据 OCR("Scan Receipt")未实现 | 功能缺失 | EXP-008, OCR-001 | `oa/src/pages/expenses/ExpenseCreatePage.tsx` |
| P1-2 | EXP/TRV 创建页无附件上传(提交前必须有附件) | 功能缺失 | EXP-007, TRV-008 | 后端 `expense-api/app/api/v1/expense_attachments.py` 已有,前端未接 |
| P1-3 | 后端 `ocr_service.py` 是死代码(未注册路由/未 import) | 技术债 | OCR-007 | `expense-api/app/main.py` |
| P2-1 | CFM 用 policy JSON 存储,非 PRD 规定的独立表/端点 → **决议:迁移(方案 B)** | 架构偏离 | §5, §10.1, §12 | `oa/src/pages/admin/CfmAdminPage.tsx:211` |
| P2-2 | budget-api(:8007)拆分未写进文档 | 文档滞后 | §8, §12 | `budget-api/` 独立服务 |
| P2-3 | 审批已改为委派 approval-api,文档仍写"内部状态机" | 文档滞后 | §6.1 | `expense-api/app/services/approval_client.py` |
| P2-4 | v1.9 状态表过期(S3/S4 标 Planned 实则已做);根 `PRD-OA.md` 引用已失效的 `expense-api/budget.py` | 文档滞后 | — | — |

---

## 1. 阶段总览

| 阶段 | 目标 | 工作量 | 阻塞关系 |
|------|------|--------|---------|
| 阶段 0 | 止血:补 TRV/CFM 下拉入口 | 0.5d | 无 |
| 阶段 1 | OCR 后端化 + 收据扫描(P0-2/P1-1/P1-3) | 2–3d | 无 |
| 阶段 2 | EXP/TRV 附件上传接线(P1-2) | 1–2d | 无 |
| 阶段 3 | CFM 迁移到独立表 + 端点(P2-1,方案 B) | 2–3d | 阶段 0 的 CFM 入口随之改调 |
| 阶段 4 | 文档同步(P2-2/3/4) | 1d | 在其它阶段落地后收尾 |

```
阶段0 ──► 阶段1 ──► 阶段2 ──► 阶段3 ──► 阶段4
 0.5d     2-3d      1-2d      2-3d      1d
```
总计约 **6.5–9.5 个工作日**。

---

## 2. 阶段 0 — 止血:补全 New Claim 入口

**任务 0.1 New Claim 下拉补 TRV + CFM** `[S]`
- 文件:`oa/src/pages/expenses/ExpenseListPage.tsx`
- 在 MIL 项后追加 **TRV** 按钮 → `navigate('/expenses/new/trv')`(图标 `Plane`/`MapPin`)
- 从 `GET /api/v1/policy` 的 `custom_forms`(仅 `is_active`)动态渲染 **CFM** 入口 → `/expenses/new/cfm/:code`
  - ⚠️ 注:阶段 3 完成后,此处数据源改为 `GET /api/v1/expenses/custom-forms`(见任务 3.5)
- 更新页面副标题(第 96 行)加入 "travel"
- ✅ 验收:
  - 下拉出现 EXP / MIL / TRV + 各启用的自定义表单
  - 点击 TRV 打开 `TrvCreatePage` 并能成功提交(后端已支持 `claim_type='TRV'`)
  - 点击 CFM 项打开对应自定义表单

---

## 3. 阶段 1 — OCR 后端化 + 收据扫描

**核心思路:启用已存在的 `ocr_service.py`,一举解决密钥泄露(P0-2)+ 收据 OCR 缺失(P1-1)+ 死代码(P1-3)三个问题。**

**任务 1.1 注册后端 OCR 端点** `[M]`
- 新增 `expense-api/app/api/v1/ocr.py`,在 `main.py` 注册路由
- 实现 `POST /api/v1/ocr/{mode}`(`mode = invoice | receipt`),复用 `ocr_service.py` 两套 prompt
- 服务端从环境变量读 `ANTHROPIC_API_KEY`;模型按 PRD §13 用 `claude-haiku-4-5`(或与现有前端一致评估后定)
- 返回 `confidence` + `low_confidence_fields`(< 0.75)
- ✅ 验收:curl 上传发票/收据图片返回结构化 JSON;前端 bundle 不再含 API key

**任务 1.2 前端发票流改调后端** `[S]`
- 文件:`oa/src/lib/invoice-parser.ts`、`oa/src/pages/pa/PaDirectCreatePage.tsx`
- `parseInvoiceFile` 改为 `POST /api/v1/ocr/invoice`(multipart);删除 `@anthropic-ai/sdk` 浏览器客户端与 `VITE_ANTHROPIC_API_KEY`
- 从前端依赖/构建配置中移除该环境变量
- ✅ 验收:PA-DIR 三步流程 OCR 行为不变,密钥不再暴露

**任务 1.3 EXP/TRV 行项目收据扫描** `[M]`
- 文件:`oa/src/pages/expenses/ExpenseCreatePage.tsx`、`oa/src/pages/expenses/TrvCreatePage.tsx`
- 每行加"Scan Receipt"按钮 → `POST /api/v1/ocr/receipt` → 回填 date/description/total/tax
- 低置信度字段标琥珀色高亮(OCR-003),允许手动覆盖
- ✅ 验收:上传收据可回填字段;扫描失败时降级为手动录入(OCR-006)

---

## 4. 阶段 2 — 附件上传接线

**任务 2.1 EXP/TRV 创建页附件上传** `[M]`
- 文件:`oa/src/pages/expenses/ExpenseCreatePage.tsx`、`oa/src/pages/expenses/TrvCreatePage.tsx`
- 接现有后端 `expense-api/app/api/v1/expense_attachments.py`
  - `POST /api/v1/expenses/{id}/attachments`(multipart,转发 Bearer 到 file-api)
- **提交前至少一个附件**约束(EXP-007 / TRV-008)
- 因附件需 claim id,采用顺序:**先存草稿(POST /expenses)→ 上传附件 → 提交(action=submit)**
- ✅ 验收:
  - 无附件时阻止提交并提示
  - 附件存 file-api,DB 仅存元数据(`file_id`)
  - 草稿/退回状态可增删附件,其它状态只读(FS-006)

---

## 5. 阶段 3 — CFM 迁移到独立表 + 端点(方案 B)

**决议(2026-06-02):** CFM 从 policy JSON 迁移到 PRD §10.1 规定的独立 `custom_form_definitions` 表 + §12 的 `/expenses/custom-forms` 端点,完全对齐 PRD,为 Phase 3 Finance Core 统一表单引擎打基础。

**任务 3.1 新建数据模型 + 迁移** `[M]`
- 新增 `expense-api/app/models/custom_form.py`,按 PRD §10.1:
  ```
  custom_form_definitions
    id UUID PK · code VARCHAR(10) UNIQUE · name VARCHAR(100)
    description TEXT NULL · workflow_key VARCHAR(50)
    default_currency VARCHAR(3) DEFAULT 'CAD'
    field_schema JSONB        -- 有序字段定义列表(§5.2 九种类型)
    is_active BOOLEAN DEFAULT TRUE
    created_at TIMESTAMPTZ · updated_at TIMESTAMPTZ
  ```
- Alembic 迁移:创建表(注意遵守 alembic 安全规范,验证列存在后再 stamp)

**任务 3.2 数据迁移脚本** `[S]`
- 把现有 `expense_policy_config.custom_forms` JSON 中每条表单迁移为 `custom_form_definitions` 行
- 字段映射:`code/name/description/field_schema/is_active`;`workflow_key` 缺省按 §9.2 取 `cfm` 或 `cfm_<code>`
- 迁移后保留 policy.custom_forms 一段时间作回滚兜底,验证无误再于阶段 4 移除

**任务 3.3 后端端点(§12)** `[M]`
- 新增 `expense-api/app/api/v1/custom_forms.py` + `schemas/custom_form.py` + `crud/custom_form.py`,在 `main.py` 注册:
  - `GET /api/v1/expenses/custom-forms` — 列出定义(支持 `active_only`)
  - `POST /api/v1/expenses/custom-forms` — 创建(code 唯一,自动大写)
  - `PATCH /api/v1/expenses/custom-forms/{code}` — 更新 / 启停
- 校验:CFM-001~005(唯一 code、九种字段类型、走标准审批引擎、budget_account 字段触发超预算、可启停不删除)

**任务 3.4 字段类型与计算字段** `[S]`
- 校验 `field_schema` 支持:`text / textarea / number / date / select / budget_account / cost_centre / attachment / calculated`(§5.2)
- `budget_account` / `cost_centre` 字段自动触发超预算逻辑(CFM-004)

**任务 3.5 前端改调** `[M]`
- `oa/src/pages/admin/CfmAdminPage.tsx`:由 `PATCH /api/v1/policy` 改为 `/expenses/custom-forms` CRUD
- `oa/src/pages/expenses/CfmCreatePage.tsx`:由 `policy.custom_forms.find` 改为 `GET /expenses/custom-forms/{code}`(或列表筛选)
- `oa/src/pages/expenses/ExpenseListPage.tsx`(任务 0.1):CFM 下拉数据源改为 `/expenses/custom-forms`
- ✅ 验收:
  - Admin 可建/改/启停自定义表单,落到独立表
  - 员工可按定义提交自定义表单,走标准审批
  - 旧 policy.custom_forms 数据已迁移且功能等价

---

## 6. 阶段 4 — 文档同步

**任务 4.1 更新 `epms/docs/PRD-OA.md`(v1.9 → v2.0)** `[S]`
- 顶部状态表:S3/S4 改 ✅;补充本次修复 Sprint 引用
- §8 / §12:标注预算逻辑已拆分至 **budget-api(:8007)**,提供 `/accounts`、`/hierarchy`、跨服务写 `/commit`/`/release`/`/actualize`/`/book-expense`
- §6.1:注明审批已委派 **approval-api(:8003)**,PA history 读共享 `approval_events`
- §13:更新 OCR 为后端 `POST /api/v1/ocr/{mode}` 路径与实际模型
- §5/§10.1/§12:CFM 标注已对齐(独立表 + 端点已落地)

**任务 4.2 根 `PRD-OA.md` 收尾** `[S]`
- 标注 `expense-api/app/api/v1/budget.py` 已迁移至 budget-api;或将该文档归档为历史变更记录(在抬头加说明)

**任务 4.3 移除 policy.custom_forms 兜底** `[S]`
- 阶段 3 数据迁移验证稳定后,从 `expense_policy_config` 与相关 schema 移除 `custom_forms`,清理过渡代码

---

## 7. 验收总览(Definition of Done)

- [x] 前台 New Claim 下拉可见并可用:EXP / MIL / TRV / 各启用 CFM
- [x] 浏览器 bundle 不含任何 Anthropic API key;OCR 全部经后端代理(`POST /api/v1/ocr/{mode}`)
- [x] EXP/TRV 支持收据扫描回填 + 提交前强制附件(前端门控 + 后端 409 双重校验)
- [x] CFM 落在独立 `custom_form_definitions` 表,经 `/expenses/custom-forms` 端点管理,迁移脚本回填旧数据
- [x] PRD-OA 文档与实现一致(budget-api、approval-api、OCR、CFM)
- [x] 前端 `tsc -p tsconfig.app.json --noEmit` 通过;后端全模块 import 通过
- [ ] (待运行环境)`alembic upgrade head` 应用 0009;`run_tests.sh` 全绿;`check-health.sh` 全部健康

---

## 9. 执行记录(2026-06-02)

| 阶段 | 交付 | 关键文件 |
|------|------|---------|
| 0 | New Claim 下拉补 TRV + 动态 CFM 项 | `oa/src/pages/expenses/ExpenseListPage.tsx` |
| 1.1 | 后端 OCR 端点(复用 ocr_service,key 服务端) | `expense-api/app/api/v1/ocr.py` + `main.py` |
| 1.2 | 发票流改调后端,删除浏览器 SDK/key + PDF 栅格化 | `oa/src/lib/invoice-parser.ts`、`oa/src/lib/api.ts`(新增 `postForm`) |
| 1.3 | EXP/TRV 收据扫描组件 | `oa/src/components/ReceiptScanButton.tsx` + EXP/TRV 创建页 |
| 2 | 附件提交门控(EXP/TRV) | `oa/src/pages/expenses/ExpenseDetailPage.tsx` + `expense-api/app/api/v1/expenses.py`(submit 409) |
| 3.1–3.4 | CFM 独立表/模型/schema/crud/端点 + 迁移回填 | `models/custom_form.py`、`schemas/custom_form.py`、`crud/custom_form.py`、`api/v1/custom_forms.py`、`alembic/versions/0009_custom_form_definitions.py` |
| 3.5 | CFM 前端改调新端点 | `CfmAdminPage.tsx`、`CfmCreatePage.tsx`、`ExpenseListPage.tsx` |
| 4 | 文档同步 | `epms/docs/PRD-OA.md` v2.0、根 `PRD-OA.md` 历史声明 |

**任务 4.3(移除 policy.custom_forms 兜底):按设计延后** —— 迁移 `0009` 刻意保留该列作回滚安全网,待生产 `alembic upgrade head` + 数据核对稳定后,再用后续迁移移除,避免过早删除兜底。

**注:** 数据库迁移 `0009` 需在运行环境执行 `alembic upgrade head` 后生效;本次未连接数据库,仅完成代码与离线校验(import + tsc)。

---

## 8. 风险与注意事项

- **Alembic 迁移**:新增 `custom_form_definitions` 表与数据迁移须先在本地 `reset-db.sh` 验证;切勿手动 INSERT alembic_version 注册孤立迁移。
- **附件顺序依赖**:EXP/TRV 强制附件需"先草稿后附件"顺序,注意草稿被弃用时的清理。
- **OCR 模型一致性**:后端化时确认 `claude-haiku-4-5`(PRD)与现前端 `claude-sonnet-4-6` 的精度/成本取舍,统一一处配置。
- **CFM 过渡期**:数据迁移后双写/兜底窗口内,确保 Admin 改动同步到新表,避免新旧不一致。
