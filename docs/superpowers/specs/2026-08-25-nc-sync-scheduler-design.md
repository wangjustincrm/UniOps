# NC Sync：定时同步 + MDM 改直读 NC — 设计

日期：2026-08-25 ｜ 事实依据：`2026-08-25-nc-mdm-survey.md`

## 1. 目标

1. Portal → Admin Panel 的 `NC Purchase Sync` 菜单改为 **`NC Sync`**，页面内按类别分成
   **Purchase / JV / MDM** 三个 tab，每类可**独立设置定时同步间隔**。
2. JV Sync 目前只存在于 Finance 前端的弹窗里，Portal Admin 无入口 —— 收进 NC Sync
   （Finance 那个手动弹窗**保留**，两边打同一个端点）。
3. ERP MDM 不再走 `10.10.95.66` 的 HTTP webapi，**直读 NC65 Oracle**。
4. 顺带修：`erp_materials.part_status` 库里全是 `'2'`、而前端按 `'A'` 过滤导致的静默故障。

## 1.1 分两期发布（用户决策，2026-08-25：**先做 Phase A**）

| | Phase A（本轮） | Phase B（后续另开一轮） |
|---|---|---|
| 范围 | 调度子系统 + NC Sync 菜单合并 | MDM 数据源换成直读 NC |
| 章节 | §4、§6.1、§8、§9.1、§10.1、§11 | §5、§6.2、§7、§9.2、§10.2、§12 |
| MDM 取数 | **仍走 webapi**，只是变成能定时触发 | 直读 NC65 Oracle，删 webapi |
| 迁移 | **1 条**（epms-api 给 `company_config` 加两列） | 1 条（重置 `erp_sync_state.last_ts`） |
| 回滚 | 把间隔设成 `0` 即恢复原状 | 回滚镜像即可（表结构不变） |

拆分理由：Phase A 风险低、能独立上线见效，且 `enabled` 默认关闭意味着上线当天行为与现在完全一致；
Phase B 是纯数据源替换，独立成一轮后回滚面干净。

**Phase A 不做的事**（避免看错范围）：不碰 `erp_client.py`、不碰 NC 直读、不删 UOM 链路、
不改 `erp_materials` 的任何写入口径。

## 2. 现状

| 类别 | 服务 | 触发端点 | 数据源 | 运行记录 | 定时器 |
|---|---|---|---|---|---|
| Purchase | epms-api | `POST /admin/nc-purchase-sync` | NC Oracle 直读 | `nc_purchase_sync_runs` | ✅ **已有**（无 UI） |
| JV | finance-api | `POST /nc-sync` | NC Oracle 直读 | `nc_sync_runs` | ❌ 无 |
| MDM | mdm-api | `POST /erp/sync/{kind}` | **HTTP webapi** | `erp_sync_state` | ❌ 无 |

采购的后端定时器已在 main 上（§4.1），但 Portal 里没有任何界面能设它的间隔；JV 与 MDM 只能手点。

`nc_host/nc_port/nc_service/nc_user/nc_password` 配置在
epms-api / finance-api / mdm-api **三个服务都已具备**，`docker-compose.prod.yml` 也已在传
（80/136/188 行），mdm-api 直读**不需要新增任何环境变量**。

## 3. 已确定的取舍（用户决策，2026-08-25）

| 决策点 | 结论 |
|---|---|
| 调度粒度 | **间隔 N 分钟/小时**（不是 cron，不是固定时刻）；`0` = 关闭，沿用 main 既有语义 |
| 调度落点 | **各服务自跑自的**；间隔统一存共用的 `company_config`，Portal 聚合展示 |
| MDM 粒度 | material / supplier / person **共用一个定时器**，到点顺序跑三类 |
| JV 入口 | **两边都留** —— Portal Admin 加 tab，Finance 弹窗保留 |
| `part_status` | **一起修**，直读时 `ENABLESTATE` 2→`'A'`、3→`'B'`，前端不动 |
| UOM 换算 | **删掉这条死链路**（0 行、无消费方、模型与 NC 不兼容） |

## 4. 调度子系统 — **Phase A**

> **2026-08-25 重大修正**：本节原先设计了一张新的 `sync_schedules` 表。实地核查 `main`
> 后发现 **epms-api 的采购同步定时器早已存在并合入 main**，而且用的是另一套机制。
> 同一个功能不能有两套并存的调度模型 —— 本节已改为**沿用 main 上的既有模式**。
> 原设计（新表 / `enabled` 布尔 / `next_run_at` 列）作废。

### 4.1 main 上已经有什么

| 已存在 | 位置 |
|---|---|
| 采购同步调度循环 | `epms-api/app/tasks/nc_purchase_sync_scheduler.py` |
| 间隔配置列 | `company_config.nc_purchase_sync_interval_minutes`（Integer，nullable） |
| 设置端点 | `PATCH /api/v1/admin/nc-purchase-sync/interval` |
| 状态端点已含调度字段 | `GET .../status` 返回 `interval_minutes` + `next_due_at` |
| 环境总开关 | `settings.nc_sync_scheduler_enabled`（默认 `True`） |
| 同形状的第二个实例 | `mrp-api/app/services/wms_sync/scheduler.py`（WMS 库存同步） |

**唯一缺的是 Portal UI** —— `grep portal/src` 对 `nc_purchase_sync_interval` 零命中，
管理员在界面上设不了这个间隔。这正是本轮要补的那一半。

### 4.2 既有模式的形状（新增的两个照抄它）

```python
DEFAULT_INTERVAL_MINUTES = 60      # NULL(没人设过) 解析成这个
MAX_INTERVAL_MINUTES     = 1440    # 一天。再长就不是"排期"了
TICK_SECONDS             = 60      # 心跳,远短于 interval,所以改设置一分钟内生效

resolve_interval_minutes(raw) -> int   # NULL→默认;非 int→默认+告警;<0→0;>MAX→钳到 MAX
is_due(last_started_at, interval_minutes, now) -> bool
run_tick() -> str   # 'disabled'|'not_configured'|'not_due'|'already_running'|'synced'|'failed'
```

四条刻意的设计，新增的两个必须原样保留：

1. **间隔从库里每 tick 重读** —— 管理员改完一分钟内生效，不用重启。
2. **`0` = 关闭**，不是布尔开关；`NULL` = 没人设过，回落默认。
3. **只跑 `incremental`，永不自动 `full`** —— full 是删了重建，API 层专门做了 confirm 门禁。
4. **到点从上次 run 的「开始时间」算**，不是完成时间，也不存 `next_run_at`。
   这样 NC 连不上时是「每个 interval 重试一次」，而不是每个 tick 都撞一次。
   时钟回拨（`started_at` 在未来）算作未到点 —— 多等一个 interval 无害，当成逾期会连续同步。

单飞已经由 `service.start_run` 的 `SyncAlreadyRunning` 解决（它还会清扫 stale 行），
调度器把它当成「行，有人在跑了」即可，不需要额外锁。

### 4.3 本轮新增

`company_config` 加两列（该表由 **epms-api 的 alembic 拥有**，所以迁移写在 epms-api）：

```sql
alter table company_config add column nc_jv_sync_interval_minutes integer;
alter table company_config add column erp_mdm_sync_interval_minutes integer;
```

三个服务**共用同一个 `epms` 库**，所以另两个服务各自加一个 mirror 模型读这张表
（finance-api 已有 `models/mirrors.py:CompanyConfig`，加列即可；mdm-api 需要**新建**一个 mirror）。

⚠️ mdm-api 新建 mirror 必须**逐列核对物理表**（`feedback_uniops_mirror_models_match_reality`，已三踩），
且**绝不能**把 `company_config` 写进 mdm-api 自己的 alembic —— 那张表不归它管。
mdm-api 的测试用 `create_all`，mirror 模型会在测试库里被建出来，这是 finance-api 已有的做法。

| | finance-api（JV） | mdm-api（MDM） |
|---|---|---|
| 配置列 | `nc_jv_sync_interval_minutes` | `erp_mdm_sync_interval_minutes` |
| 默认间隔 | 60 分钟 | 1440 分钟（主数据变动少，且是外部 HTTP 调用） |
| 循环文件 | `app/tasks/nc_sync_scheduler.py` | `app/tasks/erp_sync_scheduler.py` |
| 触发什么 | `nc_sync.start_run("incremental", ...)` | `sync_kind(db, k)` 顺序跑 material→supplier→person |
| 「上次开始」取自 | `nc_sync_runs.started_at` | `erp_sync_state.last_synced_at` |
| 设置端点 | `PATCH /finance/v1/nc-sync/interval` | `PATCH /mdm/v1/erp/sync/interval` |
| 状态端点补字段 | `/nc-sync/status` 加 `interval_minutes`/`next_due_at` | `/erp/sync/status` 同 |
| 总开关 | `settings.nc_sync_scheduler_enabled`（各服务各一个） | 同 |

epms-api 的采购部分**后端一行不改**。

### 4.4 一个必须说清楚的行为变化

沿用既有模式意味着 **`NULL` 回落到默认值 = 部署后自动开始跑**（既有采购调度器就是这个语义，
其 docstring 明确论证过：「只有按钮触发」正是让镜像几周没人发现的原因，所以关闭要是个**选择**
而不是默认）。

因此本轮上生产后：JV 同步会开始每小时跑一次增量，MDM 每天一次。两者都是 incremental、
都受 `SyncAlreadyRunning` 保护。**若不希望如此，管理员在 Admin 里把对应间隔设为 `0` 即可。**
这一点必须写进发布说明。

## 5. mdm-api 改直读 NC — **Phase B**

新建 `mdm-api/app/services/nc_mdm_sync/{reader,transform,service}.py`，结构照抄同目录下
已有的 `nc_bom_sync/`（同一套 `settings.nc_*`、同样的 `nc_configured()` 全有全无门禁）。

**核心约束：`transform` 的输出 dict 形状与现有 `_map_material` / `_map_supplier` / `_map_person`
完全一致** → `erp_materials` / `erp_suppliers` / `erp_persons` 表结构不动，下游
（PR Type 1 选料、`ErpVendorImportDrawer`、`material_sync`、epms `mdm_client`）**零改动**。

字段映射见 survey §2/§3/§4。两处**有意**的口径变化：

| 变化 | 原因 |
|---|---|
| `part_status`：`ENABLESTATE` 2→`'A'`、3→`'B'` | 修 §6 的静默故障 |
| `description` / `supplier_name`：`ENAME ?? NAME` | webapi 只取 `ENAME` 且不回落，导致大量描述为空（survey §1） |

增量水位用 `nvl(MODIFIEDTIME, TS)` —— `2026-08-03-nc-bom-survey.md` 已证明 NC 首次建档时
`MODIFIEDTIME` 为 NULL（本次 survey 中 CR0297/M0438 的 `MODIFIEDTIME` 同样为 NULL 而 `TS` 有值），
只认 `MODIFIEDTIME` 会漏行。

⚠️ 人员必须按 `BD_PSNJOB.ISMAINJOB='Y'` 取主职，否则一人多职会产生多行（survey §4）。

删除：`app/services/erp_client.py`、`erp_sync.py` 里的 HTTP fetch 路径、
`settings.erp_base_url` / `erp_timeout_seconds`、`docker-compose.prod.yml` 的 `ERP_BASE_URL`。

### 5.1 切换水位

`erp_sync_state.last_ts` 存的是 webapi 的 `rowversion`（形如 `20260710022949`）。切换后水位改由
`nvl(MODIFIEDTIME, TS)` 产生，两者虽然同源，但**不保证逐行一致**（webapi 可能只取了 `MODIFIEDTIME`，
而直读要回落 `TS`，导致部分历史行的水位比新口径高，从而被增量跳过）。

因此**切换当天必须强制跑一次 full**（`last_ts` 重置为 epoch），三类各一次。这一步同时完成
§6 的 `part_status` 存量覆盖，不需要单独的回填 SQL。迁移里把 `erp_sync_state.last_ts` 置 NULL 即可
触发下一次 sync 走 full 分支（`erp_sync.py` 现有逻辑：`state.last_ts is None` → `mode='full'`）。

## 6. 顺带修复：`part_status` 静默故障

`erp_materials.part_status` 全库 **2573 行全是 `'2'`**（NC 的 `ENABLESTATE` 原值），
但两处消费方都按 `'A'` 等值过滤（`crud/erp.py:17` 是 `==`）：

- `epms/src/components/pr/PrLineItems.tsx:234` — PR Type 1 物料选择器 → **永远 0 行**
- `portal/src/pages/admin/AdminPanel.tsx` ERP MDM Materials tab 默认 `Active (A)` → **默认空表**

### 6.1 Phase A — 向前兼容的读端修复

Phase B 要等下一轮，但选料器现在就是坏的。Phase A 只改读端一处，**两个前端都不动**：

```python
# mdm-api/app/crud/erp.py — list_materials
_STATUS_ALIASES = {"A": ("A", "2"), "B": ("B", "3")}
if part_status:
    q = q.where(ErpMaterial.part_status.in_(_STATUS_ALIASES.get(part_status, (part_status,))))
```

`'A'` 同时匹配 `'A'` 和 `'2'`，所以 Phase B 把写端改成 `'A'`/`'B'` 之后**这段代码不用再改一次**，
两期之间也不存在混合状态失效的窗口。

### 6.2 Phase B — 写端归一

直读时在 transform 层把 `ENABLESTATE` 转成 `'A'` / `'B'`。
存量 2573 行由 §5.1 的强制 full resync 覆盖，不需要单独的回填 SQL。
归一完成后 §6.1 的别名映射可以留着（无害）也可以清掉，由那一轮决定。

**验证要正面证据**（`feedback_verification_positive_evidence`）：两期都必须实际打开 PR Type 1
选料器看到非空列表，不能只看「没报错」。

## 7. 删除 UOM 换算死链路 — **Phase B**

依据 survey §5：`uom_conversions` 0 行、`erp_sync_state` 无该 kind、全仓无消费方，
且 NC 的换算是**物料级**、19 个单位对里 4 对有多个不同率（`PIECES→KGM` 有 9 种），
全局 `(from_uom,to_uom,rate)` 模型强行聚合会静默给出错误换算率。

本轮删除：`app/api/v1/uom_conversions.py` 的 `POST /sync`、`erp_sync.sync_uom_conversions`、
`fetch_unit_tranf`、`_map_uom_conversion`。

**保留** `uom_conversions` 表和 `GET /uom-conversions`（本来就是空的，删表属于额外风险，
且将来 MRP 真需要时会按物料级重建）。在模型 docstring 里写清楚「NC 的换算是物料级，
不要再用全局对填充本表」。

## 8. Portal UI — **Phase A**

菜单项 `nc_purchase` → key 改 `nc_sync`、label 改 **`NC Sync`**，icon 保持 `DatabaseZap`。

| Tab | 组成 |
|---|---|
| Purchase | `<ScheduleCard kind="purchase">` + 现有 `NcPurchaseSyncSection` 原样 |
| JV | `<ScheduleCard kind="jv">` + Portal 自己的 JV 手动触发区块（打 `financeApi`） |
| MDM | `<ScheduleCard kind="mdm">` + 从 `AdminPanel.tsx` 的 `ErpSyncToolbar` 抽出的同步区块 |

⚠️ Portal 与 Finance 是两个独立 Vite 应用，没有共享 UI 包。JV tab **在 Portal 里另写一份**
（Portal 的 `lib/api` 已经有 `financeApi` 客户端，见 `portal/src/lib/api.ts:199`），
**不动 `finance/src`** —— 因此 Phase A **不需要重建 finance-web 镜像**。
为共享 200 行 UI 去新建一个包不划算。

`<ScheduleCard>` 是新增的共用组件：开关 + 数字输入 + 单位下拉（Minutes / Hours）+
「下次运行 / 上次触发」只读展示 + Save 按钮。动作按钮两层都挡 `isPending`
（`feedback_uniops_action_button_pending_gate`）。

`ERP MDM` 菜单**保留**，退化成纯数据浏览器（Materials / Suppliers / Persons 三个表格），
同步动作统一收到 NC Sync。

Finance 前端的 Journal Vouchers 页面上那个 Sync 弹窗**保留不动**，与 Portal 的 JV tab
共用同一个 finance-api 端点。

前端 user-facing 文案**纯英文**（`feedback_uniops_ui_english_only`）。

## 9. 测试

### 9.1 Phase A

每个服务（epms-api / finance-api / mdm-api）：

- `PATCH .../interval` 的边界校验（`0`、`MAX`、`MAX+1`、非整数、非 system_admin 403）
- `resolve_interval_minutes`：NULL→默认、负数→0、超 MAX→钳制、非 int→默认
- `is_due`：未到点 / 恰好到点 / 从未跑过 / `interval=0` / `started_at` 在未来（时钟回拨）
- `run_tick` 的六种返回值各覆盖一次
- mdm-api 额外：一次调度顺序跑完 material → supplier → person，且单类失败不连坐
- mdm-api 额外：`list_materials(part_status='A')` 在库里只有 `'2'` 时返回非空（§6.1）

**可达性必须进完成定义**（`feedback_uniops_reachability_in_done`）：后端测试全绿 ≠ 管理员在
Portal 里点得到。Phase A 收尾必须用无头浏览器实际走一遍
Admin → NC Sync → 三个 tab 都能看到并保存 schedule（`reference_uniops_headless_browser_check`）。

### 9.2 Phase B

mdm-api transform：

- 对 survey 里那几行**真实 NC 数据**做 golden 比对
- **新旧两条链路对同一批 code 的输出 diff 必须为空**，`part_status` / `description`
  两处有意改动除外

回归判定按 `feedback_uniops_regression_compare_same_conditions`：**同条件同子集跑两版比失败
集合**，只比数字必误判。跑测环境见 `reference_uniops_epms_test_invocation`（epms 必须宿主跑）、
`reference_uniops_finance_api_test_env`、`reference_uniops_mrp_mdm_test_env`
（★mdm 要 `TEST_DATABASE_URL`，否则 52 个用例静默 skip）。

## 10. 迁移

### 10.1 Phase A — **只有一条**，写在 epms-api

```sql
alter table company_config add column nc_jv_sync_interval_minutes integer;
alter table company_config add column erp_mdm_sync_interval_minutes integer;
```

`company_config` 由 epms-api 的 alembic 拥有，finance-api / mdm-api 只加 mirror 模型读它，
**各自的 alembic 一条都不加**。两列都可空、无 server_default，**无数据改写**，可安全回滚。

### 10.2 Phase B

4. mdm-api：`update erp_sync_state set last_ts = null`（强制切换后首次走 full，见 §5.1）

### 通用约束

约束：

- **revision id ≤ 32 字符**（`version_num` 是 `varchar(32)`，超了会在 `migrate-prod.sh`
  中途炸成半迁移状态）
- 新迁移前先 `alembic heads`，`down_revision` 挂真实链尾
- budget-api 共用 epms 库但走独立 `alembic_version_budget`，本轮不涉及

## 11. 发布注意（Phase A）

- ⚠️ **上线即生效**：JV 每小时、MDM 每天开始自动增量同步（§4.4）。发布说明必须写明，
  并告诉管理员「间隔设 0 = 关闭」。
- 真建的镜像：**finance-api / mdm-api / portal-web** 三个 + **epms-api**（只因迁移，代码未改）。
  `finance/src` 一行不动（§8），所以 **finance-web 不重建**。
  以 `git diff --stat <上一版>..HEAD` 逐条对改动面为准（`feedback_uniops_tested_is_not_committed`），
  其余 retag，全部 push 同一个 sha（`reference_uniops_prod_release_workflow`）
- **构建前必须载 `.env.prod.example`**，worktree 里的 `.env` 是 dev 的
- 发布前后都 `curl` 线上 `/assets/index-*.js` 比指纹 —— 记忆里的生产 TAG 会过期
- 本轮**有迁移**，需要跑 `migrate-prod.sh`
- 上线后间隔列是 NULL，会回落成默认值 → **JV 每小时、MDM 每天自动开跑**（§4.4）。
  要关就在 Admin 里把间隔设成 `0`。

## 12. 遗留未定项 — **Phase B**

survey §6 的四项（`item_mes_type` / `itemtype` / `company_code` / `itemvalidityunit`）
在实施 **Task 1** 收尾。其中 `item_mes_type` **有真实消费方**
（`material_sync.py` → `parts.erp_item_type`），必须查清来源，不能留空 —— 这是 Task 1 的**验收条件**。
