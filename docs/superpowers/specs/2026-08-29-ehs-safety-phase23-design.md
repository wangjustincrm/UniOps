# Safety (EHS) 模块 — Phase 1.5 / 2 / 3 详细设计

> 依据 PRD v0.3 · 2026-08-29 · 承接 `2026-08-29-ehs-safety-phase1-design.md`
> 三个大件：**表单引擎**（Phase 2 开局）· **报表引擎**（Phase 2）· **双向邮件**（Phase 1.5）

---

## Phase 1.5 — 入站邮件（≈ 12–18 人天）

★ 独立成 1.5 期的理由：这是 UniOps **从未有过**的平台能力（全仓只有 aiosmtplib 发信），且依赖 IT 提供专用邮箱与 IMAP 凭据这个**外部依赖**。把它绑进 Phase 1 会让一个法规驱动的交付期被一个 IT 工单卡住。

### 1.1 关联策略：plus-addressing 为主，Message-ID 为辅

发信时 `Reply-To: safety+<doctype>-<shortid>@canadaroyalmilk.com`，`shortid` = 单据 UUID 的前 8 位 hex。
回信落地时三级回退：

1. **收件地址解析** `To`/`Delivered-To` 里的 `+<doctype>-<shortid>` → 最可靠，用户点"回复"就自动带上
2. **`In-Reply-To` / `References`** 匹配 `ehs_communications.message_id` → 覆盖客户端重写收件人的情况
3. **主题行 `[INC-2026-0001]` 标记** → 最后兜底，容易被 `Re:`/`转发:` 污染，只做补充

三级都失败 → 落进 `ehs_communications` 但 `doc_id=NULL`，进 Settings 的"未关联邮件"人工挂接队列。**绝不丢弃**。

### 1.2 ★防污染护栏（最容易出事的地方）

法定记录的审计链里混进一封自动回复，比少一封回复严重得多。落地前逐条判定，命中即 `is_auto_reply=true` 且**不进审计链主时间线**（只留在"其他邮件"折叠区）：

| 判据 | 依据 |
|---|---|
| `Auto-Submitted:` 存在且 ≠ `no` | RFC 3834 |
| `X-Auto-Response-Suppress` / `X-Autoreply` / `Precedence: bulk\|auto_reply\|junk` | 事实标准 |
| `Return-Path: <>` 空信封 | 退信 |
| `Content-Type: multipart/report` + `report-type=delivery-status` | DSN 退信 |
| 同一 `(doc_id, from_addr)` 24h 内 > 5 封 | 兜底节流 |

★ **发信侧也要防环**：所有系统发信带 `Auto-Submitted: auto-generated` 与 `Precedence: bulk`，避免对方的自动回复再触发我们的自动回复。

### 1.3 实现

- `app/services/inbound_mail.py`：`imaplib` + `email` 标准库（★不引第三方，与仓库现有风格一致）；IDLE 不用，走**轮询**（复用现有裸 asyncio 调度器模式，间隔存 `ehs_config.inbound_poll_minutes`，`0`=关）
- 正文抽取：优先 `text/plain`；剥引用历史（`On ... wrote:` / `-----Original Message-----` / `>` 前缀行）与签名档（`-- ` 分隔）
- 附件 → `file-api`（`service='ehs'`, `doc_type='ehs_mail'`），★沿用 50MB 上限与白名单
- 幂等：`ehs_communications` 对 `message_id` 建 **UNIQUE**，重复投递直接跳过
- 处理成功后 IMAP 标 `\Seen` 并移入 `Processed` 文件夹；失败移入 `Failed` 并告警

`ehs_communications` 表 Phase 1 已建，三列（`message_id` / `in_reply_to` / `is_auto_reply`）已预留，**Phase 1.5 不改表**。

---

## Phase 2 — 表单引擎（≈ 30–38 人天，五个模块的前置）

### 2.1 表结构

```
ehs_form_templates      code(UNIQUE) · name · category · current_version_id · is_active
                        category: inspection|jsa|permit|audit|toolbox|orientation|drill|observation
ehs_form_versions       template_id · version_no · schema JSONB · status(draft|published|retired)
                        published_at · published_by/_name · change_note
                        UNIQUE(template_id, version_no)
ehs_form_template_audit template_id · version_id · action · actor_id/_name · diff JSONB · at
                        ★HSE 要的"模板修改历史审计追踪"
ehs_form_submissions    template_id · version_id · schema_snapshot JSONB(★整份拷贝)
                        subject_type/subject_id(多态: location|asset|worker|permit|contractor)
                        status(draft|submitted|reviewed) · result(pass|fail|na) · score/max_score
                        submitted_by/_name · submitted_at · signature(base64 PNG)
                        + §Phase1 的公共维度 mixin
ehs_form_answers        submission_id · section_key · field_key · field_type(冗余,免回读 snapshot)
                        value_text/value_num/value_date/value_json · result(pass|fail|na)
                        note · file_ids JSONB
                        INDEX(field_key, result)  ★支撑"某检查项 12 个月失败率"
ehs_form_findings       submission_id · answer_id · severity(critical|major|minor|observation)
                        description · file_ids · action_id(→ehs_actions,自动创建的 CAPA)
ehs_form_assignments    ★Shared Form 派发: version_id · assignee_user_id · due_date
                        submission_id(完成后回填) · status · UNIQUE(version_id,assignee_user_id,due_date)
```

★ **双层保真**：`schema_snapshot` 让历史记录渲染**永不 join 回模板**（模板改了历史不变）；`ehs_form_answers` 的结构化行让跨提交聚合成为可能。VMS 的健康问卷只做了前者，因为它不需要"过去 12 个月第 3 题失败率"——EHS 需要。

### 2.2 schema JSONB 结构（13 种题型）

```jsonc
{
  "sections": [{
    "key": "general", "title": "General Information",
    "fields": [{
      "key": "occurred_at", "label": "When did this occur?",
      "type": "date",                    // 见下表
      "required": true,
      "help": "...",
      "options": [{"code":"...","label":"..."}],      // select 类
      "vocabulary_code": "immediate_cause",            // ★或直接绑词表，二选一
      "fail_on": ["no"],                               // 判定不合格的值
      "weight": 5,                                     // 评分制
      "photo_required_on_fail": true,
      "visible_when": {"field":"talked_to_operator","op":"eq","value":"no"}  // ★条件显示
    }]
  }]
}
```

| type | 说明 |
|---|---|
| `yes_no_na` | ★实测确认在用（Near Miss "Did you talk to the operator…"） |
| `text` / `textarea` / `number` | |
| `date` / **`time`** | ★分离字段（实测 "When did this occur?" 与 "- Time" 是两个） |
| `select` | `options` 内联 或 `vocabulary_code` 绑词表 |
| **`person_single`** / **`person_multi`** | ★实测：Lead Investigator 单选、Supporting Investigator(s) 多选 |
| **`location`** | 挂四层区域树 |
| `photo` / `signature` | |
| **`instruction`** | ★非输入的说明文字块 |

★ **`visible_when` 只支持单字段比较**（`eq`/`ne`/`in`/`empty`/`not_empty`），**不做表达式语言**。理由：实测样本里的条件逻辑全部是"上一题答否则显示"这一种；引入表达式解析器等于引入一个需要单测的小语言，收益不成比例。真需要复合条件时，用两个 `visible_when` 字段串联。

### 2.3 校验与判定

- **服务端是唯一权威**：提交时用 `version.schema` 重新跑一遍必填、类型、`visible_when`（★隐藏字段不校验必填，否则条件题永远交不上）、`fail_on`
- `result` 判定：任一 `fail_on` 命中 → `submission.result='fail'`，生成 `ehs_form_findings`
- 评分：`score = Σ(通过项 weight)`，`max_score = Σ(适用项 weight)`（`na` 不计入分母）

### 2.4 前端渲染器

`ehs/src/components/formEngine/`：`FormRenderer.tsx`（读 schema 递归渲染）+ 每种题型一个 `fields/*.tsx` + `ConditionEval.ts`（与后端同一套语义，★两边各有单测且共用同一份 fixture JSON）。
设计器 `FormDesignerPage.tsx` 首版 = **字段列表增删 + 上下移 + 属性面板 + JSON 导入/导出**，★不做拖拽画布（拖拽是排版工作量，不增加表达能力）。

---

## Phase 2 — 报表引擎（≈ 15–20 人天）

### 3.1 为什么它能"一个引擎 + N 个配置"

前提是 Phase 1 已落地的**公共维度 mixin**（`occurred_at / location_id / department_id / shift_code / category_id / severity / owner_id / status / closed_at`）。八类实体共用同一组列名，报表定义才可能是数据而不是代码。

### 3.2 表结构

```
ehs_report_defs      code(UNIQUE) · name · source(★枚举白名单) · dimensions JSONB
                     measures JSONB · filters JSONB · sort JSONB · chart_type · is_system
ehs_report_schedules report_def_id · cron_expr · recipients JSONB(user_ids + 外部邮箱)
                     format(pdf|xlsx|inline) · last_run_at · is_active
ehs_report_runs      report_def_id · params JSONB · row_count · file_id · run_by/_name · at
```

### 3.3 ★安全：绝不让配置变成 SQL 注入面

`source` 是**枚举白名单**（`incident` / `action` / `form_submission` / `finding` / `training` / `certification` / `hazard` / `deadline`），每个 source 在代码里映射到一个 **SQLAlchemy select 构造器**；`dimensions` / `measures` / `filters` 里的字段名必须命中该 source 的**允许字段字典**，命不中直接 422。
★ 不接受任何形式的原始 SQL 片段、不做字符串拼接、不暴露 `ORDER BY` 任意表达式。

```python
SOURCES = {
  "incident": SourceSpec(
      model=Incident,
      dims={"location_id","department_id","shift_code","category_id","injury_class",
            "mol_reportable","severity","occurred_month","occurred_quarter","occurred_year"},
      measures={"count","lost_days_sum","days_to_close_avg"},
      date_field="occurred_at"),
  ...
}
```

### 3.4 趋势与同比（E05）

同一个引擎加一个 `compare` 参数：`{"mode":"yoy"|"period","baseline":{"from":...,"to":...}}`。
返回结构 `{current, baseline, delta_abs, delta_pct}`，按 dimension 分组后排序取 top-N，直接支撑 HSE 给的样例：
*"Hand injuries increased 42% compared with the previous year. 68% occurred in Production Area 104 and 54% occurred on night shift."*
→ 即 `source=incident` · `dims=[body_part]` · `measure=count` · `compare=yoy`，再对命中项下钻 `dims=[location_id]` 与 `dims=[shift_code]` 取占比。

★ **分组一律按 `*_id`，显示用 `*_label`** —— 词表改名不打断趋势连续性（Phase 1 的双写设计在这里兑现）。

### 3.5 定时投递

复用现有裸 asyncio 调度器；cron 解析**自己实现**（只支持 `daily@HH:MM` / `weekly@DOW,HH:MM` / `monthly@DD,HH:MM` 三种），★不引 croniter —— 三种够用，少一个依赖。
PDF 走 ReportLab（★`leading` 默认 12，与 fontSize 无关，改字号必须同改 leading）；xlsx 走 openpyxl（★epms-api 已有先例：**新增此依赖的镜像必须真建，不能 retag**）。

---

## Phase 2 — 其余模块（数据模型骨架）

| 模块 | 表 | 关键设计点 |
|---|---|---|
| **巡检排程** | `ehs_inspection_schedules`(template_id · frequency · **rotation_scope**(department 列表) · next_due_at) · `ehs_inspection_assignments` | ★HSE Q4：JHSC 巡检是**每月一个部门轮转**，不是随机——`rotation_scope` 存有序部门列表 + `rotation_cursor` |
| **危害与风险** | `ehs_hazards` · `ehs_risk_assessments`(likelihood/severity/**residual_**) · `ehs_risk_controls`(hierarchy_of_control) | 风险矩阵档位来自 Settings（PRD 6.11），★不硬编码 5×5 |
| **JSA** | `ehs_jsa`(版本化,走 `ehs_jsa` 审批) · `ehs_jsa_steps`(step→hazard→control) · `ehs_jsa_acknowledgements` | 作业前工人签署 → 复用 signature 组件 |
| **观察/未遂** | 复用 `ehs_incidents`，`form_kind='near_miss'` | ★不另建表：Near Miss 本来就是 day-one 三表单之一，Phase 1 已建 |
| **JHSC** | `ehs_jhsc_committees` · `_members`(certified 标记, 23人/20certified) · `_meetings` · `_agenda_items` · `_recommendations` | 21 天答复挂 `ehs_statutory_deadlines`(`kind='jhsc_reply_21d'`)，★边际成本≈0；纪要出**张贴用 PDF** |
| **文档与政策** | `ehs_documents` · `_versions` · `_distributions` · `ehs_acknowledgements` | 年审挂 `statutory_deadlines`(`policy_annual`)；★Read&Sign 范围含 **toolbox talk**，要同时看到已签/未签 |
| **安全设备** | `ehs_safety_assets`(★只管安全设备) · 检查复用表单引擎 + `ehs_inspection_schedules` | ★与未来 cMMs 的边界：生产设备维保不进来，两者共用 mdm `locations` |
| **统一 QR** | 无新表，`GET /ehs/v1/qr/{token}` 解析路由 | token 前缀区分四类：`L-`(location) `S-`(sds) `C-`(chemical label) `Q-`(quiz) |

---

## Phase 3 — 作业许可 / 承包商 / 培训扩展

| 模块 | 表 | 关键点 |
|---|---|---|
| **作业许可** | `ehs_permit_types`(配置化) · `ehs_permits`(走 `ehs_pmt` 审批) · `_workers` · `_isolations`(LOTO 挂锁点) · `_gas_readings`(受限空间气体检测,带时间序列) · `_signoffs` | ★许可**自动失效**：`valid_until` 到点由调度器置 `expired`，不靠人工；受限空间必须有救援待命签署 |
| **承包商** | ★**复用 VMS**（PRD Q9："Configure to use the same as the VMS application"）→ 只加 `ehs_contractor_documents`(COI/WSIB clearance 到期挂 `statutory_deadlines`) | 不重建访客/PPE/orientation，走 vms-api 现有 `compliance.py` |
| **培训扩展** | `ehs_quizzes` · `_questions` · `_attempts`(★QR 扫码答题) · `ehs_course_materials` · 内训证书自动生成(ReportLab) | QR token `Q-<attempt_token>`，一次性、绑 user、有效期 24h |

---

## Phase 3 — E40 AI 生成 GHS 工作场所标签（≈ 10–15 人天）

### 5.1 现有基础与差异

仓库**已在用 Anthropic Claude API**：`expense-api/app/services/ocr_service.py` 做发票识别，PDF 走 `document` content block、图片走 `image` block —— 这个模式直接复用。差异有三处：

| | 现有 OCR | E40 GHS 提取 |
|---|---|---|
| 模型 | `claude-haiku-4-5-20251001` | ★**`claude-opus-5`** —— 危害分类判断错=WHMIS 违规，不为成本降级 |
| 输出 | prompt 里要 JSON，再手工 `_parse` | ★**structured outputs**（`output_config.format`），schema 强约束 |
| SDK | `anthropic==0.43.0` | ehs-api 自己的 `requirements.txt` 用 **1.x**（独立服务，与 expense-api 不冲突；★1.x 依赖 `httpx2`） |

### 5.2 调用形状

```python
import anthropic, base64
client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

resp = client.messages.create(
    model="claude-opus-5",
    max_tokens=16000,
    thinking={"type": "adaptive"},           # ★不要 budget_tokens（Opus 5 会 400）
    output_config={
        "effort": "high",
        "format": {"type": "json_schema", "schema": GHS_LABEL_SCHEMA},
    },
    messages=[{"role": "user", "content": [
        {"type": "document",
         "source": {"type": "base64", "media_type": "application/pdf", "data": b64_sds}},
        {"type": "text", "text": GHS_EXTRACT_PROMPT},
    ]}],
)
```

`GHS_LABEL_SCHEMA` 约束：`product_identifier` · `signal_word`(`Danger`|`Warning`|null) ·
`pictograms`(GHS01–GHS09 枚举数组) · `hazard_statements`(H 码 + 文本) ·
`precautionary_statements`(P 码 + 文本) · `supplier` · `confidence`(0–1 每字段)。

★ 不要用 assistant prefill（Opus 5 上返回 400）；不要 `citations`（与 `output_config.format` 不兼容，400）。

### 5.3 ★合规闸门（比提取本身更重要）

```
上传 SDS → AI 提取 → status='draft'（★永不自动发布）
        → 人工逐字段复核，任一字段被改则记 reviewed_fields
        → 有 ehs.chemical.label.approve 权限者签署 → status='approved' → 才允许打印
```
`ehs_chemical_labels`：`chemical_id` · `sds_file_id` · `extracted JSONB` · `reviewed JSONB` ·
`status`(draft|approved|superseded) · `approved_by/_name` · `approved_at` · `label_qr_token` · `model_used` · `extraction_confidence`

★ 标签 PDF 上**不印置信度**，但记录里留 `model_used` 与 `extraction_confidence` 供审计追溯。
★ SDS 换版 → 所有引用它的已批准标签自动 `superseded` 并给化学品负责人开 CAPA。

---

## Phase 3 — 自定义看板与 COR 取证

| 项 | 设计 |
|---|---|
| **E51 自定义看板** | ★按 PRD 6.5 的 **Option A** 设计（从图表库挑选并排版，~8 人天）：`ehs_dashboards`(owner_id · layout JSONB) 引用 `ehs_report_defs` 的图表，**不做自由查询构建器**（那是 BI 子系统，量级差数倍）。若 HSE 坚持 Option B，改为在报表引擎上加一个受限的字段选择器 UI，仍不放开原始 SQL |
| **E53 COR 取证包** | `ehs_audit_packages`(period_from/to · sections JSONB · file_id · generated_by)。一次性 ReportLab 汇编：政策与年审签署 → JHSC 名册与全年纪要 → 培训合规率与证书 → 事故台账与 CAPA 关闭率 → 巡检完成率 → 危害与风险评估。★按 COR 19 要素分节输出，每节带证据条目数与缺口提示 |
| **审核员只读入口** | `ehs_auditor_grants`(auditor_user_id · scope JSONB · valid_from/to · created_by)。★用**限时授权**而非新账号类型：`auditor` 角色 + grant 行双重判定，过期自动失效；所有审核员访问写 `audit_log`。绝不共享管理员账号（这正是 SiteDocs 让审核员进 Admin Panel 的做法，我们不复制） |

---

## 分期工作量汇总

| 阶段 | 内容 | 人天 |
|---|---|---|
| **1.5** | 入站邮件与关联归档 | 12–18 |
| **2** | 表单引擎 30–38 · 报表引擎 15–20 · 趋势 8–12 · 巡检 · 危害风险 · JSA · JHSC · 政策 · 安全设备 · 统一 QR · mobile-first 补齐 | 115–140 |
| **3** | 许可 16–20 · 化学品与 AI 标签 20–28 · 承包商 14–18 · 培训扩展 16–23 · 自定义看板 8–12 · COR 取证与审核员入口 18–24 | 135–170 |

---

## 跨期风险登记

| # | 风险 | 缓解 |
|---|---|---|
| R1 | **表单引擎是五个模块的前置**，它滑五个一起滑 | Phase 2 开局第一件事；首版 13 种题型 + JSON 导入式设计器，不做拖拽画布 |
| R0 | **入站邮件污染法定审计链**（一封 out-of-office 进事故记录） | §1.2 的五条判据 + 发信侧 `Auto-Submitted` 防环 + 未关联邮件进人工队列绝不丢弃 |
| R00 | **报表引擎的字段口径不可逆** | 公共维度 mixin 已在 Phase 1 落地并强制复用 |
| R12 | **AI 标签贴错 = WHMIS 违规** | 永不自动发布；人工逐字段复核 + 签署才能打印；SDS 换版自动作废旧标签 |
| R13 | 🆕 **报表配置变成注入面** | `source` 白名单 + 字段字典校验 + SQLAlchemy 构造器，零字符串拼接 |
| R14 | 🆕 **anthropic SDK 1.x 依赖 httpx2** | ehs-api 独立 `requirements.txt`，不影响 expense-api 的 0.43.0；★镜像必须真建不能 retag |
