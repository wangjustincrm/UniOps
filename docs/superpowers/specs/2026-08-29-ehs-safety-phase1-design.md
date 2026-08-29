# Safety (EHS) 模块 — Phase 1 详细设计

> 依据 PRD v0.3 · 2026-08-29 · 服务 `ehs-api`:8012 / 前端 `ehs`:5180 / 子域名 `safety.canadaroyalmilk.com`
> 骨架样板 `booking-api`（五层）· 平台接入样板 `vms-api`

---

## 0. 范围与前提

### 0.1 Phase 1 交付边界

| 进 Phase 1 | 不进 |
|---|---|
| 服务骨架与全套上线路径 | 表单引擎（Phase 2） |
| Settings：词表 + 区域树 + 行为参数 | 巡检排程、JHSC、政策库 |
| 事故与伤害管理全量（含三张 day-one 表单） | 危害登记、风险矩阵、JSA |
| CAPA 全量 | 报表引擎、趋势分析（Phase 2） |
| 法定时钟与合规日历 | 化学品、许可、承包商（Phase 3） |
| 法定培训台账与证书到期 | 课程、考试、Toolbox（Phase 3） |
| 出站邮件 + 通信归档 | **入站回复**（待 6.4 决定，建议 Phase 1.5） |
| 草稿暂存（全表单） | |

### 0.2 两个未决问题的处理方式

设计不停等，但把未决点收敛到**单一可改点**：

| 未决 | 设计对策 |
|---|---|
| **6.1 分级口径**（lost time 与 modified duties） | 分级本身按两维度落库（见 §3.3）。`lost time` 的判定不写死在代码里，而是落成 `ehs_config.lost_time_rules` JSONB + 一个纯函数 `classify_lost_time()`，HSE 答复后只改这一个函数与它的单测 |
| **6.2 分批停用 SiteDocs** | 只影响上线节奏，不影响任何表结构。设计按"Phase 1 就要能用"做 |

### 0.3 ★贯穿全局的四条铁律

1. **所有 → `users` 的外键一律 `ondelete="SET NULL"`，并冗余 `*_name varchar(255)` 姓名快照**。人离职后历史记录必须完整且能显示当时姓名。
2. **词表引用一律双写**：`*_id`（聚合用，改名不断裂）+ `*_label`（显示用，签署时的原话）。
3. **法定记录 append-only**：`ehs_incidents` / `ehs_first_aid_log` / `ehs_training_records` / 所有签署表由 DB 触发器拒绝 `UPDATE`/`DELETE`（样板 `vms-api/alembic/versions/20260528_0002_audit_immutable.py`）。
   ★例外：事故在 `status='draft'` 期间可改 —— 触发器条件为 `OLD.status <> 'draft'`。
4. **时间一律 `DateTime(timezone=True)`**；纯日期用 `Date` 且**前端禁止 `new Date(str)`**（见 `feedback_uniops_date_only_utc_parse`：UTC-4 下会少一天）。

---

## 1. 服务骨架

```
ehs-api/
  alembic.ini · alembic/env.py           ★ version_table="alembic_version_ehs"（offline+online 两处）
  alembic/versions/20260829_0001_ehs_initial.py
  app/main.py                            create_app() 工厂；lifespan 起调度器 + drain()
  app/core/config.py                     Settings + @lru_cache get_settings()
  app/core/deps.py                       SessionDep / CurrentUserPayload / BearerToken
  app/core/security.py                   decode_token()（只验签，token 由 epms-api 签发）
  app/core/authz.py                       require_permission = bind(get_session, get_current_user_payload)
  app/core/permissions.py                Annotated 依赖别名
  app/core/background.py                 ★复制 epms-api 那份 spawn/drain（禁止裸 create_task）
  app/db/base.py                         Base / TimestampMixin / UUIDPrimaryKey（照抄 booking-api）
  app/db/session.py                      async engine + get_session()
  app/models/                            见 §3
  app/schemas/ · app/crud/ · app/services/ · app/api/v1/
  app/tasks/statutory_scheduler.py       法定时钟扫描器
  Dockerfile                             ★根 context，COPY packages/authz
  pyproject.toml · requirements.txt · tests/
```

★ `Dockerfile` 用 **booking-api 的根 context 写法**（`COPY packages/authz /packages/authz` + `RUN pip install /packages/authz`），不要用 vms-api 的子目录 context —— 那种拿不到共享 authz 包。

---

## 2. 词表与主数据

### 2.1 `ehs_vocabularies` — 词表定义

| 列 | 类型 | 说明 |
|---|---|---|
| `code` | varchar(40) **PK** | `immediate_cause` / `root_cause` / `hazard` / `ppe` / `shift` / `body_part` / `nature_of_injury` / `incident_category` / `safety_asset_type` / `cert_type` / `position` |
| `name` | varchar(120) | Settings 里显示的名字 |
| `description` | text | |
| `is_hierarchical` | bool | true=树（root_cause 分组）、false=平铺 |
| `is_system_locked` | bool | true=不可增删条目（`hierarchy_of_control` 五级、`injury_class` 三档），仅可改 label |
| `attr_schema` | JSONB | 该词表条目允许的差异化属性定义，供 Settings UI 渲染 |

**Phase 1 内置词表**（迁移里 seed 定义，条目留空待 HSE 提供 → PRD 6.10）：
`incident_category` · `immediate_cause` · `root_cause`(树) · `hazard` · `ppe` · `shift` · `body_part` · `nature_of_injury` · `position` · `cert_type` · `hierarchy_of_control`(locked) · `injury_class`(locked)

### 2.2 `ehs_vocabulary_items` — 词表条目

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | |
| `vocabulary_code` | varchar(40) FK→`ehs_vocabularies.code` | |
| `parent_id` | UUID FK→self `ondelete=RESTRICT` | 层级词表用 |
| `code` | varchar(60) | |
| `label` | varchar(200) | |
| `sort_order` | int | |
| `is_active` | bool default true | ★**停用而非删除** |
| `attrs` | JSONB | 如课程的 `{"statutory": true}`、严重度的 `{"color":"#c62828"}` |
| `path` | varchar(500) | 物化路径，树形筛选用 |

约束：`UNIQUE(vocabulary_code, code)` · `INDEX(vocabulary_code, is_active, sort_order)`
★**没有硬删除端点**。`DELETE` 语义 = `is_active=false`。

### 2.3 `locations` — 厂区区域（★建在 mdm-api，不带 `ehs_` 前缀）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | |
| `code` | varchar(50) UNIQUE | 如 `102-PRE-MILKREC` |
| `name` | varchar(200) | |
| `parent_id` | UUID FK→self `ondelete=RESTRICT` | |
| `level` | varchar(20) | `site`\|`building`\|`department`\|`line`\|`sub_line` |
| `path` | varchar(500) | `102/Pretreatment/Milk Receiving`，写入时维护 |
| `depth` | smallint | 0-based，**允许不满四层**（PRD 6.7 待确认，设计上不强制） |
| `access_area` | varchar(30) | 与 VMS `AccessArea` 同词表，**普通列不是 PG enum** |
| `department_id` | UUID | mdm `departments`，无 FK（跨服务惯例） |
| `qr_token` | varchar(64) UNIQUE | 扫码报事故 / 开检查表 |
| `is_active` | bool | |

★ **绝不 `ALTER TYPE` VMS 的 `vms_access_area` 枚举**。
★ ehs-api 侧建只读镜像 `app/models/location_mirror.py`。
★ 维护 UI 在 **Safety Settings**（HSE Manager 是 owner），数据归属 mdm —— 有意分离。

### 2.4 `ehs_config` — 行为参数（单行表，`id=1` CHECK）

| 列 | 默认 | 说明 |
|---|---|---|
| `capa_remind_before_days` | 3 | PRD §2 Q8 |
| `capa_escalate_supervisor_days` | 5 | 逾期天数 |
| `capa_escalate_manager_days` | 10 | |
| `cert_warn_days` | `[90,60,30]` JSONB | |
| `allow_anonymous_report` | true | |
| `statutory_scan_interval_minutes` | 60 | ★`0`=关闭调度器 |
| `incident_notify_groups` | JSONB | `{incident_category_code: [user_id...]}`，E14 |
| `lost_time_rules` | JSONB | ★**PRD 6.1 的答复落这里**，见 §0.2 |
| `email_templates` | JSONB | `{tpl_key:{subject,body}}` |

### 2.5 `ehs_holidays` — 安省法定假日

`year smallint` · `holiday_date date` · `name varchar(80)` · `UNIQUE(holiday_date)`
迁移里 seed **2026–2030 五年**。★每年运维清单里要补新年份，否则 WSIB 三业务日会算错。

### 2.6 `ehs_worker_profiles` — 员工安全档案（1:1 副表，★不动 `users`）

| 列 | 类型 | 说明 |
|---|---|---|
| `user_id` | UUID **PK** FK→`users.id` `ondelete=CASCADE` | |
| `employee_no` | varchar(30) UNIQUE NULL | ★与 `users.erp_person_code` 不等价 |
| `hire_date` | date | |
| `employment_type` | varchar(20) | `full_time`\|`part_time`\|`temp`\|`student`\|`contractor` |
| `shift_code` | varchar(20) | 取自 `shift` 词表 |
| `phone_mobile` | varchar(30) | |
| `primary_location_id` | UUID | `locations`，无 FK |
| `emergency_contact` | JSONB | `{name, relation, phone, alt_phone}` |
| `medical_notes` | text NULL | ★受 `ehs.worker.medical.read` 保护，**PHIPA 敏感** |
| `is_safety_sensitive` | bool | 安全敏感岗位 |
| `jhsc_role` | varchar(20) NULL | `member`\|`certified_member`\|`co_chair` |

理由：`users` 被 10 个服务各建镜像读，加列会让 10 份镜像同时进入"声明与实际不一致"；且紧急联系人/医疗限制属 PHIPA 敏感数据，副表才有可卡住的物理边界。

### 2.7 岗位矩阵（Phase 1 **只建表 + API，不建 UI**）

- `ehs_job_positions`：`id` · `code` · `name` · `is_active`
- `ehs_position_requirements`：`id` · `position_id` · `requirement_type`(`training`\|`ppe`\|`medical`) · `vocabulary_item_id` · `is_mandatory`
- `ehs_worker_positions`：`id` · `user_id` · `position_id` · `effective_from` · `effective_to` · `UNIQUE(user_id,position_id,effective_from)`

理由：§5 的证书到期报表要 join 它算"谁缺哪门课"，但 UI 排到 Phase 2 的 E22。

---

## 3. 事故与伤害

### 3.1 `ehs_incidents` — 主表

**公共维度列（★E06 报表引擎的字段口径，所有 EHS 主表共用同一组，做成 `EhsCommonDims` mixin 强制复用）**：

| 列 | 类型 | 说明 |
|---|---|---|
| `occurred_at` | timestamptz | 事发时间（日期+时间两个前端字段合成） |
| `location_id` | UUID | 四层区域 |
| `location_path` | varchar(500) | ★快照，区域改名后历史报表仍可读 |
| `department_id` | UUID | |
| `shift_code` | varchar(20) | E05 要按班次分析 |
| `category_id` / `category_label` | UUID / varchar(200) | `incident_category` 词表 |
| `severity` | varchar(10) | |
| `owner_id` / `owner_name` | UUID / varchar(255) | |
| `status` | varchar(20) | |
| `closed_at` | timestamptz | |

**事故专有列**：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` · `incident_no` | UUID · varchar(20) UNIQUE | `INC-2026-0001`，★用 advisory lock + max 尾号+1（见 `project_uniops_document_number_collision`） |
| `form_kind` | varchar(20) | ★`medical` \| `equipment` \| `near_miss` —— **三张 day-one 表单** |
| `reported_by` / `reported_by_name` | UUID / varchar(255) | |
| `is_anonymous` | bool | |
| `reported_to_id` / `_name` | UUID / varchar(255) | |
| **`injury_class`** | varchar(15) NULL | ★`first_aid` \| `medical_aid` \| `lost_time` —— 驱动 **WSIB 3 业务日** |
| **`mol_reportable`** | bool default false | ★**正交**于 injury_class，驱动 **MOL 48h**；critical injury / fatality |
| `mol_reportable_reason` | varchar(40) NULL | `critical_injury` \| `fatality` |
| `employer_aware_at` | timestamptz NULL | ★**WSIB 时钟起点**（雇主知悉），与 `occurred_at` 分开 |
| `description` | text | |
| `equipment_involved` | text | Equipment / Near Miss 表单 |
| `witnesses` | text | |
| `status` | varchar(20) | `draft`→`submitted`→`under_investigation`→`pending_closure`→`closed`\|`cancelled` |
| `approval_status` / `approval_step_idx` / `submitted_at` | varchar(20) / int / timestamptz | ★approval-api 写这三列（`ehs_inc`） |

★ **`occurred_at` 与 `employer_aware_at` 必须分开存**：MOL 48h 从 occurrence 起算（OHSA s.51(1)），WSIB 3 业务日从雇主知悉起算。合成一个字段会让两个时钟之一必然错。

### 3.2 `ehs_incident_persons` — 涉及人员（支持"多选 worker"与非 CRM 人员）

`id` · `incident_id` FK CASCADE · `role`(`injured`\|`involved`\|`witness`\|`first_aider`) ·
`user_id` FK→users SET NULL · `person_name` varchar(255)（★非 CRM 员工只有名字）·
`external_company` varchar(200) · `department_id` · `body_part_id`/`_label` · `nature_of_injury_id`/`_label` ·
`treatment` text

### 3.3 `ehs_incident_investigations` — Section C

`id` · `incident_id` FK · `investigator_id`/`_name` · `identified_hazards` JSONB（词表 id 数组）·
`ppe_that_could_prevent` JSONB · `sequence_of_events` text · `root_cause_narrative` text ·
`completed_at` · `signed_by`/`_name` · `signature` text(base64 PNG)

### 3.4 `ehs_incident_causes` — 原因（★根因↔整改项关联的枢纽）

`id` · `incident_id` FK · `cause_type`(`immediate`\|`root`) ·
`vocabulary_item_id` · `label`（★快照）· `note` text · `sort_order`

→ `ehs_actions.source_type='incident'` + 新增 `cause_id` 列指向这里，实现 PRD E10 的
"根因树上显示关联整改项及其完成状态"。

### 3.5 `ehs_first_aid_log` — Reg 1101（★独立表，append-only）

`id` · `log_no` · `incident_id` NULL（可以先记急救、后升级成事故）· `occurred_at` ·
`location_id`/`path` · `injured_user_id`/`_name` · `first_aider_id`/`_name` ·
`body_part_id`/`_label` · `treatment_given` text · `sent_offsite` bool · `follow_up` text

### 3.6 `ehs_rtw_plans` / `ehs_rtw_checkins`

`ehs_rtw_plans`：`incident_id` · `worker_user_id`/`_name` · `functional_abilities` JSONB ·
`restrictions` text · `modified_duties` text · `start_date` · `expected_end_date` ·
`actual_end_date` · `status` · `approval_status`（★`ehs_rtw` 排 Phase 3，Phase 1 用简单状态机）
`ehs_rtw_checkins`：`plan_id` FK · `checkin_date` · `notes` · `by_user_id`/`_name`

---

## 4. 法定时钟

### 4.1 `ehs_statutory_deadlines` — ★一张表装下所有法定时钟

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | |
| `source_type` / `source_id` | varchar(20) / UUID | `incident`\|`cert`\|`jhsc_rec`\|`document`\|`sds`\|`drill` |
| `kind` | varchar(24) | `mol_48h` \| `wsib_form7` \| `jhsc_reply_21d` \| `cert_expiry` \| `policy_annual` \| `sds_3y` |
| `regulation_ref` | varchar(60) | `OHSA s.51(1)` / `WSIA` / `Reg 1101 s.5` —— ★页面直接显示法条出处 |
| `clock_type` | varchar(10) | `calendar` \| `business` |
| `starts_at` / `due_at` | timestamptz | ★**存绝对时间戳，不用计算列** |
| `satisfied_at` / `satisfied_by` / `evidence_file_id` | | |
| `escalation_level` | smallint | 0=正常 1=T-24h 2=T-4h 3=逾期 |

索引：`CREATE INDEX ... ON ehs_statutory_deadlines(due_at) WHERE satisfied_at IS NULL`（部分索引，扫描器只看未完成）

### 4.2 纯函数 `app/services/statutory.py`

```python
def compute_due(kind: str, starts_at: datetime, holidays: set[date]) -> datetime:
    """每条法规一个分支 + 一个带条款号的常量。纯函数、不碰 DB、不读时钟 —— 可单测。"""
```

| kind | 规则 | 条款 |
|---|---|---|
| `mol_48h` | `starts_at`(=`occurred_at`) + 48 自然小时 | **OHSA s.51(1)** "within forty-eight hours after the occurrence" |
| `wsib_form7` | `starts_at`(=`employer_aware_at`) + 3 **业务日**（跳周末与 `ehs_holidays`），★到期含义是"**WSIB 收到**"→ 内部目标再提前 1 天 | WSIA |
| `jhsc_reply_21d` | +21 自然日 | OHSA s.9(20) |

★ 为什么不用数据库计算列：业务日要扣安省假日（PG 里没有假日表），48h 是自然小时 —— 两种时钟混在一个表达式里不可维护也不可单测。

### 4.3 调度器 `app/tasks/statutory_scheduler.py`

照抄 `epms-api/app/tasks/nc_purchase_sync_scheduler.py` 的结构：

```python
TICK_SECONDS = 60
async def run_tick() -> str:
    interval = await load_interval_minutes()      # 每 tick 重读 ehs_config
    if interval <= 0: return "disabled"           # ★0 = 关闭
    if not is_due(last_started_at, interval, now): return "not_due"
    # T-24h / T-4h / overdue 三档 → spawn() 发通知 + 写 tasks 行 + escalation_level++
async def statutory_loop():
    while True:
        try: await run_tick()
        except asyncio.CancelledError: raise      # ★必须放过否则关不掉
        except Exception: logger.exception(...)
        await asyncio.sleep(TICK_SECONDS)
```
★ `is_due` 从**上次 run 的开始时间**算（失败也算，避免连环重试）。
★ 前端倒计时用 `due_at` 在浏览器算剩余秒数，**不 poll 后端**。

---

## 5. CAPA

### 5.1 `ehs_actions`

含 §3.1 的公共维度 mixin，另加：

| 列 | 说明 |
|---|---|
| `action_no` varchar(20) UNIQUE | `CAPA-2026-0001` |
| `source_type` varchar(20) | ★CHECK IN (`incident`,`inspection`,`jhsc`,`audit`,`observation`,`hazard`,`drill`,`permit`,`manual`) |
| `source_id` UUID · `source_ref` varchar(60) | ★无 FK（多态，先例=`tasks` 表跑了两年）；`source_ref` 是单号快照，列表页免 join |
| **`cause_id` UUID NULL** | → `ehs_incident_causes.id`，实现根因↔整改关联 |
| `action_type` varchar(15) | `corrective` \| `preventive` |
| `hierarchy_of_control` varchar(20) | ★COR 要素，取自 locked 词表 |
| `due_date` date NOT NULL **INDEX** | |
| `status` varchar(20) | `open`→`in_progress`→`pending_verification`→`closed`\|`cancelled` |
| `escalation_level` smallint | 0 正常 / 1 已提醒 / 2 已升主管 / 3 已升 HSE Manager |

### 5.2 `ehs_action_updates`（append-only）· `ehs_action_verifications`

- updates：`action_id` FK CASCADE · `author_id`/`_name` · `body` · `file_ids` JSONB · `new_status`
- verifications：`action_id` · `verified_by`/`_name` · `verified_at` · **`is_effective` bool** · `evidence` · `file_ids`
  ★COR 要求验证"措施**有效**"，不是"做完了"。

### 5.3 升级链（`ehs_config` 驱动，PRD §2 Q8）

| 时点 | 通知 | escalation_level |
|---|---|---|
| 到期前 3 天 | Owner | 1 |
| 到期日 | Owner | 1 |
| 逾期 5 天 | Owner + Supervisor | 2 |
| 逾期 10 天 | Owner + Supervisor + HSE Manager | 3 |

★ **不走 approval-api**，简单状态机 + 写 `tasks` 行。

---

## 6. 培训与证书（Phase 1 子集）

- `ehs_courses`：`id` · `code` · `name` · `is_statutory` bool · `validity_months` int NULL · `is_active`
  ★迁移 seed **PRD §2 Q2 的 8 门**：全员 WHMIS / Lockout Tagout / Worker+Supervisor Awareness / **Working at Heights**；岗位 Forklift / Mobile Equipment Work Platform / Confined Space / First Aid
- `ehs_training_records`（append-only）：`id` · `user_id`/`_name` · `course_id`/`_label` · `completed_on` ·
  `expires_on` · `delivery`(`internal`\|`external`) · `certificate_file_id` · `recorded_by`/`_name`
- `ehs_worker_certifications`：`id` · `user_id` · `cert_type_id`/`_label` · `cert_no` · `issued_on` ·
  `expires_on` **INDEX** · `issuer` · `file_id` · `is_blocking` bool（★过期是否阻断派工）

★ 到期提醒**不另写扫描器**，统一写进 `ehs_statutory_deadlines`（`kind='cert_expiry'`），复用同一个调度器 —— 这是那张表的价值所在。

---

## 7. 通信（Phase 1 只做出站）

`ehs_communications`：`id` · `doc_type` varchar(20) · `doc_id` UUID · `direction`(`out`\|`in`) ·
`from_addr` · `to_addrs` JSONB · `cc_addrs` JSONB · `subject` · `body_text` · `body_html` ·
`message_id` varchar(255) **INDEX** · `in_reply_to` varchar(255) · `attachments` JSONB(file ids) ·
`sent_at` / `received_at` · `template_key` · `is_auto_reply` bool

★ Phase 1 只写 `direction='out'`。`message_id` / `in_reply_to` / `is_auto_reply` 三列**现在就建**，
入站能力（Phase 1.5）落地时不用改表。

---

## 8. 权限键与角色

### 8.1 新角色（identity `role_defs`）

`ehs_manager` · `ehs_coordinator` · `area_supervisor` · `jhsc_member` · `first_aider` · `worker` · `auditor`
★ `assignable_as_primary` 按 identity 0009 迁移的字段设置；`worker` 作为主角色。

### 8.2 权限键（identity `permission_defs`，module=`ehs`）

| 键 | 默认授予 |
|---|---|
| `ehs.incident.report` | **所有角色**（含 worker）★漏授 = 200 人集体 403 |
| `ehs.incident.read` | ehs_manager, ehs_coordinator, area_supervisor, jhsc_member, auditor |
| `ehs.incident.investigate` | ehs_manager, ehs_coordinator, area_supervisor |
| `ehs.incident.close` | ehs_manager |
| `ehs.incident.medical.read` | ehs_manager, first_aider ★PHIPA |
| `ehs.action.read` / `.write` / `.verify` | 递进 |
| `ehs.firstaid.write` | first_aider, ehs_manager, ehs_coordinator |
| `ehs.training.read` / `.write` | |
| `ehs.worker.read` / `.write` / `.medical.read` | |
| `ehs.settings.manage` | ehs_manager |
| `ehs.statutory.manage` | ehs_manager ★标记 MOL/WSIB 已提交 |

★ **每个键都要显式给 `system_admin`**（`require_permission` 对它短路，漏了就是收紧行为）。
★ 注册走 **identity-api 的 alembic 迁移**（样板 `0012_po_signoff_perm.py`），不用 seed 脚本 —— 迁移幂等且随 `migrate-prod.sh` 自动跑。

---

## 9. 平台接入点

### 9.1 审批（Phase 1 只上 `ehs_inc`）

`ehs_inc`（7 字符 ✓）事故调查结案：**HSE Coordinator → HSE Manager**
★已核实 `ACTION_KEYS` 现有 **13** 个（`pr/po/posign/agr/pa/pa_dir/exp/mil/trv/tra/cfm/budget_plan/vms_visit`），Phase 1 加 `ehs_inc` → 14 个，那排横向 tab 还撑得住；Phase 2/3 再加满 6 个到 19 时才需要分组改造。

★**5 处协同编辑，漏一处就出事**：
1. `approval-api/app/models/ehs_incident.py` —— 新建瘦镜像（★`amount_attr`/`vendor_attr` 置 `None`）
2. `approval-api/app/crud/engine.py` —— `_DOC_META` + `_WORKFLOW_DEFAULTS`
3. `approval-api/app/api/v1/workflows.py` —— `_DOC_TYPES`
4. `portal/src/pages/admin/AdminPanel.tsx` —— `ACTION_KEYS` + `ACTION_KEY_LABELS` + `WORKFLOW_DEFAULTS` + `WORKFLOW_ROLES` 加 `ehs_manager`
   ★漏加 key，下次有人保存审批流会把它**整列删掉**（`workflow_defs` 是 wholesale 替换）
5. 深链三处：`epms-api/.../notification.py::_task_link` · `portal/.../PortalHome.tsx` 的 `DOC_PATH` · `epms/src/lib/taskTypes.ts`

`_DOC_META` 条目：
```python
"ehs_inc": {
    "model": EhsIncident, "number_attr": "incident_no",
    "amount_attr": None, "vendor_attr": None,
    "task_approve": "approve_ehs_inc", "task_revise": "revise_ehs_inc",
    "status_attr": "approval_status",          # ★不碰 ehs_incidents.status
    "valid_submit": ("draft",), "valid_approve": ("submitted", "in_review"),
    "valid_return": ("submitted", "in_review"),
    "valid_cancel": ("draft", "submitted", "in_review"),
},
```

### 9.2 任务收件箱

`app/models/task_mirror.py` 照抄 vms 那份（★`document_type` 声明 **String(20)**）。

| task type | doc_type | 触发 |
|---|---|---|
| `ehs_do_action` | `ehs_act` | CAPA 指派 ★**不能以 `approve` 开头** —— 已核实 `epms-api/app/api/v1/tasks.py:47` 就是 `if task.type.startswith("approve")` 直接 409 |
| `ehs_investigate` | `ehs_inc` | 事故进入调查 |
| `ehs_statutory` | `ehs_inc` | MOL/WSIB 到期临近 |
| `ehs_verify_action` | `ehs_act` | 待有效性验证 |
| `ehs_cert_expiry` | `ehs_cert` | 证书到期 |
| `approve_ehs_inc` / `revise_ehs_inc` | `ehs_inc` | 引擎自动写 |

### 9.3 通知与附件

- 发信：复用 `epms-api/app/services/email.py` 模式；★fire-and-forget 一律 `app/core/background.py::spawn`，lifespan 里 `drain()`
- 附件：`file-api`（`service='ehs'`），★需给 file-api 补 `GET /files?service=&doc_type=&doc_id=`（约 30 行）
- 照片：★**客户端压缩是 P0** —— `ehs/src/lib/imageCompress.ts`（canvas 长边 1920 + q0.8，8MB→400KB）；HEIC 走 try/catch fallback（原样直传 + 标记"无预览"）

---

## 10. API 端点（Phase 1，前缀 `/ehs/v1`）

| 分组 | 端点 |
|---|---|
| health | `GET /health` `GET /health/db`（★另在根路径挂一份，docker healthcheck 依赖） |
| incidents | `GET/POST /incidents` · `GET/PATCH /incidents/{id}` · `POST /{id}/submit` · `POST /{id}/classify` · `POST /{id}/investigation` · `POST /{id}/causes` · `GET /{id}/timeline` · `POST /{id}/persons` · `GET /{id}/wsib-form7`（导出） |
| first aid | `GET/POST /first-aid` · `POST /first-aid/{id}/escalate`（升级成事故） |
| actions | `GET/POST /actions` · `PATCH /actions/{id}` · `POST /{id}/updates` · `POST /{id}/verify` · `GET /actions/mine` |
| statutory | `GET /deadlines` · `POST /deadlines/{id}/satisfy` · `GET /calendar` |
| training | `GET/POST /training-records` · `GET/POST /certifications` · `GET /training/gaps` |
| workers | `GET/PATCH /workers/{user_id}` · `GET /workers`（★`medical_notes` 受独立权限键） |
| settings | `GET/PUT /settings/config` · `GET /vocabularies` · `GET/POST/PATCH /vocabularies/{code}/items` · `GET/POST/PATCH /locations` · `POST /locations/import` · `GET /locations/{id}/qr` |
| qr | `GET /qr/{token}` → 解析成 `{type, target_id, redirect}`（★统一入口，4 类 token 共用） |

---

## 11. 前端（`ehs`:5180，★mobile-first）

★ **不共享 epms 的表格布局约定**（epms 113 个 tsx 里只有 48 个含断点、29 个靠 `overflow-x-auto`= 手机上不可用）。
一律**卡片列表 + 抽屉详情**；共享 `@uniops/shell` 的 UI 原语与 `tokens.css`。
★ 页面级 tab hook **不能用会 throw 的 `useTabStoreApi`**（iframe 嵌入模式下无 Provider，`9321dc7` 生产白屏事故）。

```
src/app/routes.tsx                    RouteDef[]（含 tab 元信息）
src/pages/
  IncidentListPage · IncidentCreatePage(★三种 form_kind) · IncidentDetailPage
  FirstAidLogPage · MyActionsPage · ActionDetailPage
  ComplianceCalendarPage · TrainingMatrixPage · WorkerProfilePage
  settings/{VocabulariesPage, LocationsTreePage, RulesPage, NotificationsPage}
src/components/
  PhotoCapture.tsx      ★<input accept="image/*" capture="environment" multiple> + 压缩
  SignaturePad.tsx      ★移植 vms/src/components/HealthDeclForm.tsx 的 base64 PNG
  DraftBanner.tsx       ★草稿暂存（localStorage，覆盖所有表单）
  StatutoryCountdown.tsx  浏览器本地算剩余时间
  CauseTree.tsx         根因树 + 挂在其上的整改项与完成状态
src/lib/{api,imageCompress,draftStore,formatDate}.ts
```

★ `formatDate`：**纯日期禁止 `new Date(str)`**（UTC-4 下少一天、跨年错年份）。七个前端各有一份拷贝，本模块自带一份正确的。

### 草稿暂存（PRD Q7，覆盖**所有**表单）
`draftStore.ts`：key = `ehs-draft:<form_kind>:<user_id>:<draft_uuid>`，存表单 JSON + 照片的 base64（★照片在草稿期**不上传**，提交时才走 file-api）。列表页顶部显示"你有 N 份未提交草稿"。
★ 不做完整离线同步（无冲突合并、无后台队列），只解决"死区填完、走出来再交"。

---

## 12. 迁移与上线清单

### 12.1 迁移顺序（★必须先于容器起）

| # | 服务 | revision | 内容 |
|---|---|---|---|
| 1 | mdm-api | **`0018_locations`** | `locations` 表（现链尾 `0017_material_accounting_group`，已核实） |
| 2 | identity-api | **`0013_ehs_perms`** | `role_defs` 7 个 + `permission_defs` 14 个 + `role_permissions`（现链尾 `0012_po_signoff_perm`，已核实） |
| 3 | ehs-api | `20260829_0001_ehs_initial` | 本模块全部表 + 词表定义 seed + 假日 seed + 8 门课 seed |
| 4 | ehs-api | `20260829_0002_ehs_immutable` | append-only 触发器 |

★ revision id **≤32 字符**（`version_num` 是 `varchar(32)`，超了会在 `migrate-prod.sh` 中途炸成半迁移状态）。
★ 新迁移前先 `alembic heads` 确认 `down_revision` 挂在真实链尾。

### 12.2 接线清单（漏一处上不了线）

`docker-compose.dev.yml`（ehs-api:8012 + ehs-frontend:5180 + `ehs_node_modules` 卷）·
`docker-compose.prod.yml`（两个 service + portal build args）· `Caddyfile`（`safety.` + `safety-api.` 两个站点）·
`.env.prod.example` / `.env.lan.example`（`EHS_URL` / `EHS_API_URL` + `ALLOWED_ORIGINS` 加新 origin）·
`make-stack-env.sh` · `migrate-prod.sh`（`SERVICES` 加 `ehs-api`）· `check-health.sh` · `run_tests.sh` ·
`portal`（navConfig / lib/api / PortalHome / PortalSidebar / PortalPageLayout / Dockerfile 的 `VITE_EHS_URL`）·
`file-api` 的 `ALLOWED_ORIGINS` · `DEPLOYMENT.md` 加一节

---

## 13. 测试计划

| 层 | 内容 |
|---|---|
| **法规单测**（最高优先） | `compute_due()` 每条规则一个带条款号的用例：48h 自然小时跨午夜/跨月；WSIB 3 业务日跨周末、跨安省假日、跨年；21 天日历日。★这是把法律风险变成可回归资产的唯一办法 |
| 分级 | `classify_lost_time()` 覆盖 modified duties 四种场景（★HSE 答复 6.1 后补齐断言） |
| 单号 | 并发建单不撞 `incident_no` UNIQUE（advisory lock） |
| 不可改 | `UPDATE ehs_incidents WHERE status<>'draft'` 必须被触发器拒绝 |
| 权限 | worker / area_supervisor / ehs_manager / auditor 四身份各跑全端点，确认无漏授权 |
| 端到端 | 手机浏览器报 lost-time 事故 → WSIB 倒计时上看板 → 调查、整改项挂到根因 → 根因树显示状态 → 逾期 5 天升主管、10 天升 HSE Manager → 关闭并验证有效性 |
| 草稿 | ★真机**关 WiFi** 填完事故表存草稿 → 恢复网络 → 提交成功且照片一并上传 |
| 回归 | 同条件同子集跑两版比失败**集合**（存量失败多，只比数字必误判） |

★ 测试库：`TEST_EHS_DB` 环境变量（★旋钮不是 `POSTGRES_DB`），库名 `ehs_test`；worktree 里还须传 `JWT_SECRET_KEY`。

---

## 14. 待决与风险

| # | 事项 | 影响面 |
|---|---|---|
| 1 | **PRD 6.1 lost time 口径** | 只影响 `ehs_config.lost_time_rules` + `classify_lost_time()` 一个函数，**不影响表结构** |
| 2 | PRD 6.7 区域是否必须满四层 | 设计已按"允许不满四层"做（`depth` 列 + 可空层级），HSE 若要求强制四层则加 CHECK |
| 3 | PRD 6.9 Medical Section B 字段 | 影响 `ehs_incident_persons` 与 WSIB Form 7 导出的字段完整性 |
| 4 | PRD 6.10 词表初始内容 | 不影响结构；空词表可上线，但 HSE 要在首周手工录入 |
| 5 | 6.4 入站邮件 | `ehs_communications` 已预留三列，Phase 1.5 落地不改表 |

---

## 15. 设计前的正面核实（2026-08-29 实测，非二手信息）

| 断言 | 实测结果 |
|---|---|
| 端口 8012 / 5180 空闲 | compose 已占 **8000, 8002–8011** 与 **5173–5179**（8001、8005 空缺：8005 是 file-api，跑在独立文件服务器 10.10.50.66）。→ 取 **8012 / 5180** 连续递增，无歧义 |
| identity 迁移链尾 | `0012_po_signoff_perm.py` → 新迁移 **`0013_ehs_perms`** |
| mdm 迁移链尾 | `0017_material_accounting_group.py` → 新迁移 **`0018_locations`** |
| tasks 完成端点拒绝 approve 类 | ✅ `epms-api/app/api/v1/tasks.py:47` `if task.type.startswith("approve")` → 409。`ehs_do_action` 命名安全 |
| `tasks.document_type` 已是 varchar(20) | ✅ 迁移 `t0o1p2q3r4s5_widen_tasks_document_type` 同时加宽了 `tasks` 与 `approval_events` 两列。EHS doc_type 仍按 ≤10 规划（`ehs_inc`=7 / `ehs_act`=7 / `ehs_cert`=8） |
| `ACTION_KEYS` 现有数量 | ✅ 13 个，Phase 1 后为 14 |
| 模型基类约定 | ✅ `booking-api/app/db/base.py` 的 `Base` / `TimestampMixin` / `UUIDPrimaryKey` 三件套；`vms-api/app/models/task_mirror.py` 已正确声明 `String(20)` |
| `_DOC_META` 插件面 | ✅ `vms_visit` 条目实测含 `status_attr: "approval_status"` 间接层，`amount_attr`/`vendor_attr` 显式 `None` |
