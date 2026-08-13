# MRP 周排产 + 意向产品设计（1A/1B 修订，1C 前置）

- 日期：2026-08-12
- 状态：待评审
- 前置文档：`2026-08-03-mrp-subsystem-design.md`（V2.0 主设计）、`2026-08-06-continuous-sales-forecast-design.md`、`2026-08-07-mps-production-lead-time.md`、`2026-08-07-production-plan-matrix-design.md`
- 影响阶段：修订已上线的 1A/1B；**必须在 1C（物料展开→采购建议）开工前完成**，因为 1C 吃的 `mrp_demands` 口径在本设计里从月改周

## 0. 为什么现在改

MRP 1A/1B 已于 2026-08-08 随 `fe636df` 上生产。业务侧提出两项与既有设计冲突的诉求：

1. **销售预测只能录 ERP 里已有物料编码的产品**，但策划中的产品（只有名字、还没建码）同样需要被记录进预测，否则这部分意向只能留在 Excel 里，滚动预测不完整。
2. **排产粒度从月改周**。月计划对车间没有指导意义——换线成本、连续生产、周产能都发生在周这一层。工厂产能设置项的单位同步改周。

两项都动 1A/1B 的既有数据模型，且第 2 项决定 `mrp_demands` 的时间口径，所以在 1C 之前做，避免 1C 建成后再返工。

## 1. 用户拍板记录（2026-08-12）

| # | 问题 | 决策 |
| --- | --- | --- |
| D1 | 周化边界划在哪 | **销售预测仍按月**（18 个月 × 产品大表不动），**MPS 拆到周** |
| D2 | 月需求怎么变周计划 | 不做机械均分。按三条业务原则排：**P1 单品连续生产**（换线成本高）、**P2 月计划不满时周周有产**（不能全塞前几周）、**P3 每周尽量只做一个品** |
| D3 | 周怎么定义 | **做成设置项**，三选一：①ISO 周·跨月周归周四所在月 ②ISO 周·跨月周归含月初的月 ③每月固定 4/5 周（按日期切） |
| D4 | 跨月机制 | **生产提前期 lead 与产能不足前移 pre-build 都改成以周计** |
| D5 | 产能规则 | 单纯换算成周，**新增周例外**（某周停产/检修/节假日） |
| D6 | P2 的边界（用户补充） | 摊平不能不顾实际需求——20 吨拆成 4 周每周 5 吨是浪费能源。**新增"每周最低产能"设置**，摊到这个下限就停 |
| D7 | 意向产品绑定真实码 | **数字原地保留**，行直接变成正常产品行，变更日志留审计 |
| D8 | 意向产品下游可见性 | **进 outlook 快照**（带 intent 标记）**但不排产**，MPS 生成摘要里点名列出被跳过的意向产品 |
| D9 | 存量月口径数据 | **一刀切清掉重来**（历史 MPS run / 已发布需求不保留） |
| D10 | 采购建议（`mrp_demands`）是否周化 | **周化** |
| D11 | 绑定时目标物料码已有预测行 | 业务上不会发生（意向都是策划中产品）→ 实现为**硬报错拒绝绑定**，不静默相加 |

## 2. 周排产引擎

### 2.0 总体流水线

```
① 取净需求（月 × 产品，口径不变，仍来自 outlook 快照扣库存）
② lead 位移：target_week = 需求月末周 − production_lead_weeks
              若早于当前周 → 钳到当前周，标 lead_shortfall
③ 按 target_week 的【归属月】分桶（归属月由周日历模块判定，见 §3）
④ 每个桶内跑两体制排布（§2.2 / §2.3）
⑤ 桶内装不下的，按周向前溢出（pre-build，可跨月），逐周做保质期硬校验
⑥ 前移触底（当前周）或违反保质期 → 标 capacity_gap，不静默丢弃
```

**排布的单位是"桶"（= 目标周归属月的那 4/5 周），不是自然月**。lead 位移发生在分桶之前，所以 lead 把需求推到上个月时，它就参与上个月那一桶的排布，与该桶原有需求一起竞争产能。

### 2.1 两种体制

引擎按"周数够不够"分两种体制。设某月可用周集合为 `W`（|W| = k，4 或 5），产品 p 的当月净需求 `q_p`，周产能上限 `cap`，每周最低产能 `min_out`。

```
need_weeks(p) = ceil(q_p / cap)          # 产能决定的最少周数
Σ need_weeks(p) >  k   →  体制一（紧张）
Σ need_weeks(p) <= k   →  体制二（富余）
```

**★为什么是 `>` 而不是 `>=`**（2026-08-13 修正，实施中发现）：`need_weeks` 是**向上取整**的，零头不代表产能真被占满。`Σneed == k` 时每个产品恰好能独占自己的周、无需共周，这是富余体制的理想输入，不是紧张。原来写 `>=` 会把它推进紧张体制：`{A:60, B:60}`、cap 40、4 周 → A 占 W1(40)+W2(20)、B 续 W2(+20)+W3(40)，**W4 空着而 W1 满载**，违反 P2；改成 `>` 后走富余体制得 30/30/30/30 铺满四周，正是计划员手排的样子。

### 2.2 体制一 · 周数紧张：降序装箱 + 余量拼单

```
按 q_p 降序（同量按 material_code 稳定排序）遍历产品：
  若 q_p > cap（非拆不可）：
      从最早的未满周起逐周填至 cap，余量落在紧邻的下一周（连续，满足 P1）
  否则（q_p <= cap，能不拆就不拆）：
      **先**找最早一个【整空且容得下】的周，整体放入
      没有空周了 → 再回落去找最早一个【剩余容量 >= q_p 且品种数未达 max_sku_count】的周
```

**★空周优先于剩余空间**（2026-08-13 修正）：反过来写（先捡剩余空间）会违反 P2/P3。`{A:100, B:15}`、cap 40、4 周下，先捡剩余会把 B 塞进 A 的零头周，**留 W4 空着**；先开空周则 B 独占 W4，周周有产且每周单品。★但**跨周产品（`q_p > cap`）相反**，必须用「最早还有余量的周」续上：若它也先开空周，`{A:60, B:60, C:30}` 会把 W2 剩的 20 晾着，让 C 凭空变成一条产能缺口。两者不矛盾——可拆分的品能吸收任意碎片，跳过碎片是纯浪费；不可拆分的品需要整块，而降序处理保证「现在装得下这块剩余空间的品，后面更小的品也一定装得下」，所以推迟使用剩余空间不损可行性。

**关键：`q_p <= cap` 的产品绝不拆分**。若照"逐周填满"的朴素贪心走，黄金用例里 C(30) 会被切成 W2 的 20 + W3 的 10，既多一次换线（违反 P1）又让 W2 变成三个品（违反 P3）。

**放不下的处理**：按 §2.0 第⑤步向前溢出；前移触底（当前周）或违反保质期 → 标 `capacity_gap`，不静默丢弃。

**此体制下 `min_out` 不生效**——本来就在挤，没有摊薄余地。强行让每周量 ≥ `min_out` 反而会多占周数（见 §2.5 反例）。

**黄金用例**（用户给定，固化为测试）：`A60 B20 C30 D30`，`cap=40`，`k=4`

| 周 | 排产 |
| --- | --- |
| W1 | A 40 |
| W2 | A 20 + B 20 |
| W3 | C 30 |
| W4 | D 30 |

推演：`need_weeks` = A:2, B:1, C:1, D:1，合计 5 > 4 → 体制一。降序 A(60) 填满 W1、溢 20 进 W2；W2 余 20；C(30) 塞不进 20 → 另起 W3；D(30) → W4；B(20) 正好塞进 W2 余量。

### 2.3 体制二 · 周数富余：按最低产能摊平

```
起点  weeks(p) = need_weeks(p)                                  # 产能决定，硬下限
上限  cap_weeks(p) = max( need_weeks(p), floor(q_p / min_out), 1 )
再按"周均负荷降序"把剩余空闲周依次追加给产品（每次 +1 周，直到 cap_weeks 或周用完）
每个产品在其连续周块内均分 q_p
```

**`need_weeks` 永远赢**：产能是硬约束、最低产能是软下限，所以 `cap_weeks` 取两者的较大值。例：`q=100, cap=40, min_out=50` → `need_weeks=3` 而 `floor(100/50)=2`，此时按 3 周排（每周 33.3，低于 min_out 但无可奈何），不会为了凑最低产能去违反产能上限。

| 场景（`cap=40`, `min_out=20`, `k=4`） | 结果 | 依据 |
| --- | --- | --- |
| 单品 20t | 1 周 × 20t，其余 3 周空 | `floor(20/20)=1` |
| 单品 10t | 1 周 × 10t | `floor(10/20)=0` → 钳到 1 |
| 单品 40t | 2 周 × 20t | `floor(40/20)=2` |
| 单品 100t | 3 周（33.333/33.333/33.334） | `need_weeks=ceil(100/40)=3` 已达下限；块内**均分**，不是填满前两周 |
| A60 + B20 | A 3 周 × 20，B 1 周 × 20 | A 的 `cap_weeks=max(2,floor(60/20),1)=3`，拿走那个空闲周 |

**空周是合法结果**，不是缺陷：摊到最低产能仍用不满日历，剩下的周就该空着。计划员可在调整抽屉里手工合并（见 §5.2）。

### 2.4 约束与告警

- `max_output_qty`（周产能上限）：硬约束，超出触发前移或 capacity_gap
- `max_sku_count`（周品种数上限）：硬约束
- `min_output_qty`（周最低产能）：**软下限**，只约束摊薄程度，永不产生 capacity_gap
- 校验：`min_output_qty <= max_output_qty`，否则规则保存被拒
- 周例外覆盖同周同约束的常规规则（例：某周 `max_output_qty=0` 表示停产检修）
- **★排布函数必须逐周接收产能**（2026-08-13 修正）：`pack_bucket` 若只收一个桶级 `CapacityLimits`，「某周停产」这个旗舰用例根本无法表达。签名要接受「单个 limits（适用于所有周）或与周列表等长的序列」，`max_output_qty=0` 的那一周应当**被跳过、产量挪到其他周**，而不是让整桶变成缺口

### 2.5 为什么紧张体制下不管 min_out（设计取舍留档）

黄金用例里 A 的溢出量是 20t。若强制"每周量 ≥ min_out=25"，A 只能改成 30+30，W1 就空出 10t、W2 只余 10t，B(20) 再也塞不进，需要第 5 周——4 周装不下，凭空造出一个 capacity_gap。**结论：`min_out` 只回答"要不要摊开"，不回答"挤的时候怎么挤"。**

### 2.6 跨月机制（周化）

- **生产提前期**：`production_lead_weeks`（默认 4 ≈ 原来的 1 个月，范围 0–52）。目标周 = 需求月的末周 − lead 周；若已早于当前周则钳到当前周并标 `lead_shortfall`（琥珀警告，照产不丢，语义与现行月版一致）
- **pre-build**：产能不足时逐周前移，跨月边界不特殊处理（周是连续序列）
- **保质期硬校验**：**按真实日期差比较，不用"4.33 周/月"近似**——
  `(需求月首日 − 目标周首日) 的天数 <= (需求月首日 − 需求月首日回退 shelf_life_months 个自然月) 的天数 × (1 − safety_margin)`
  近似换算在 18 个月窗口上会累积到整周级偏差，直接用日期差没有这个问题。
  **参照点取需求月首日**（不是月末），与现行月版引擎 `prebuild_months <= floor(shelf_life × (1−safety))` 的语义保持一致
- **`weeks_early` 的定义**：相对 **lead 位移后的目标周**又额外提前了几周，即纯 pre-build 量。`lead` 本身造成的提前不计入（否则每一行都会显示"提前 4 周"，告警失去意义）
- `lead_weeks=0` 必须字节级复现"不提前"的行为

## 3. 周日历

### 3.1 三种模式

| 模式 | 周边界 | 跨月周归属 | 每月周数 |
| --- | --- | --- | --- |
| `iso_thursday`（默认） | 周一–周日，ISO 8601 | 归周四所在月 | 4 或 5 |
| `iso_first_day` | 周一–周日，ISO 8601 | 归含"该周最早那一天所属新月"的月 | 4 或 5 |
| `month_fixed` | 每月 1 号起每 7 天一周 | 不跨月 | 4 或 5（W5 为 1–3 天零头） |

### 3.2 规范键与显示

**不用 `'2026-W32'` 字符串做键**——`month_fixed` 模式编不出 ISO 周号。统一用 **`plan_week_start DATE`（周首日）** 作规范键，三模式下都无歧义；另存派生列 `plan_week_month CHAR(7)`（归属月）供分组与报表。

显示标签由日历模块生成：ISO 两模式 → `2026-W32 · Aug 3–9`；`month_fixed` → `Aug W2 · Aug 8–14`。

### 3.3 模块边界

新增纯函数模块 `mrp-api/app/services/week_calendar.py`，是**唯一**知道三种模式差异的地方：

- `weeks_of_month(month: str, mode: str) -> list[date]`
- `owning_month(week_start: date, mode: str) -> str`
- `week_label(week_start: date, mode: str) -> str`
- `shift_weeks(week_start: date, delta: int) -> date`

排产引擎只接收周列表，不感知模式。`month_fixed` 模式下 `shift_weeks` 需要跨月重新取该月周序列（周长不恒为 7 天），由日历模块内部处理。

## 4. 数据模型

一支破坏性迁移 `mrp09_weekly_planning`（`down_revision = 'mrp08'`；动手前先跑 `alembic heads` 核实单 head）。

### 4.1 表改动

| 表 | 改动 |
| --- | --- |
| `mrp_mps_runs` | `production_lead_months` → `production_lead_weeks INT NOT NULL DEFAULT 4`（0–52 服务端校验）；新增 `week_calendar_mode VARCHAR(20) NOT NULL`（生成时的模式快照，已发布计划不随设置漂移） |
| `mrp_mps_lines` | `plan_month CHAR(7)` → `plan_week_start DATE NOT NULL` + `plan_week_month CHAR(7) NOT NULL`；新增 `weeks_early INT NOT NULL DEFAULT 0`；`demand_month` 保留不变；其余列（`is_prebuild`/`shelf_life_ok`/`capacity_gap`/`locked_by_planner`/`manual_adjusted`/`demand_forecast`/`opening_stock`）不变 |
| `mrp_demands` | 新增 `plan_week_start DATE NOT NULL`；`demand_month` 保留（来源月，便于回溯）。**1C 的物料展开与采购建议按周产出** |
| `mrp_capacity_rules` | 无 DDL 改动。`constraint_type` 增加取值 `min_output_qty`；三种约束语义由"每月"改为"**每周**"（文档 + UI 文案 + 校验） |
| `mrp_capacity_exceptions`（新） | `id, week_start DATE, scope_type, scope_ref, constraint_type, limit_value NUMERIC(18,3), uom, reason TEXT, is_active BOOL`；唯一约束 `(week_start, scope_type, scope_ref, constraint_type)` |
| `mrp_planning_params`（新） | `key VARCHAR(50) PK, value JSONB, updated_by UUID, updated_at`。本期只放 `week_calendar_mode`；**1C 的 `raw_material_loss_rate` / `packaging_loss_rate` 复用同表** |
| `mrp_intent_products`（新） | `id, code VARCHAR(50) UNIQUE`（`INTENT-` + 8 位）、`name VARCHAR(200)`、`note TEXT`、`status VARCHAR(20)`(active/bound/dropped)、`bound_material_code VARCHAR(50)`、`bound_at`、`bound_by`、`created_by` |
| `mrp_forecast_lines`（快照） | 新增 `is_intent BOOL NOT NULL DEFAULT false` + `intent_name VARCHAR(200)`。快照必须能脱离 `mrp_intent_products` 自解释，否则意向被绑定或删除后回看历史快照只剩看不懂的占位码 |
| `mrp_demand_series` / `mrp_forecast_change_log` | **结构不动**。意向行用占位码存进现有 `material_code`，粘贴、自动保存、变更日志、KG/吨切换全部零改造复用 |

### 4.2 存量数据处理（D9 一刀切）

迁移内执行顺序：

1. `DELETE FROM mrp_demands`
2. `DELETE FROM mrp_mps_lines`
3. `DELETE FROM mrp_mps_runs`
4. `UPDATE mrp_capacity_rules SET is_active = false`（**不删**）
5. 改列 / 加列 / 建新表
6. `INSERT INTO mrp_planning_params` 写入默认 `week_calendar_mode = 'iso_thursday'`

产能规则停用而非删除：旧的月值留着能当填新值的参考，也可回溯；**留着且生效才是真危险**（月上限被当周上限读 = 静默放大约 4 倍产能）。Capacity Rules 页顶部显示一次性横幅："口径已改为周，请重新维护规则"。

`downgrade` 只还原表结构，被删的计划数据不可复原——写进迁移文件 docstring。

## 5. 前端

### 5.1 Production Plan（改动最大）

18 个月 ≈ 78 周，一屏放不下。

- **双层表头**：上层月份（跨该月 4/5 周，沿用现有产品组分隔线风格），下层周（`W32`，悬停显示 `Aug 3–9`）
- **月折叠**：默认展开"当前月 + 后 2 个月"的周列，其余月折叠成一列月汇总；点月表头展开/收起。开屏约 13 列而非 78 列
- 三行结构（Demand / Available / Planned）保留：**Demand 与 Available 仍按月**——月展开时显示在该月的**第一周列**并跨列居中，月折叠时显示在月汇总列；**只有 Planned 落到具体周**
- 现有单滚动容器 + sticky 表头/首列/Total、缺口红格、No-BOM 徽章、KG/吨切换、导出全部沿用
- Excel 导出改为周列 + 月分组表头

### 5.2 调整抽屉（AdjustDrawer）

- "改物理生产月" → "**改生产周**"（周选择器列出当月各周，允许跨月，触发保质期校验）
- 新增 **Merge into adjacent week**：把本周该行的量并进相邻周，合并后标 `manual_adjusted`，重算时受锁定保护。这是 D6 场景"两周 20 吨合成一周 40 吨"的入口

### 5.3 Sales Forecast（意向产品）

- Add Product 弹窗增加第二入口 **Add intent product**（填名称 + 备注），落库生成占位码
- 意向行整行琥珀底色（复用现有 `tintRowIds`）+ 行首 `Intent` 徽章（复用 `rowBadge`）
- 行操作新增 **Bind to material**：选真实物料码 → 确认框显示"将带走 N 个月共 X 吨" → 事务绑定 → 行当场变为正常产品行；目标码已有预测行时**报错拒绝**（D11）
- Generate Outlook 模态提示"本次快照含 N 个意向产品（不参与排产）"
- MPS 生成后的摘要卡片点名列出被跳过的意向产品

### 5.4 Capacity Rules

- 三种约束文案改周：`Max output / week`、`Max SKUs / week`、`Min output / week`
- 顶部新增 **Planning Calendar** 区块：周定义三选一（下拉 + 每种模式一行说明），存 `mrp_planning_params`，明确提示"改动只影响之后新生成的计划，已发布计划沿用生成时的模式"
- 新增 **Week Exceptions** 区块：列表 + 新增（选周 / 选约束 / 填值 / 填原因），典型用法"某周 max_output_qty=0（年度检修）"

### 5.5 权限

不新增权限键。周例外与规划参数走 `mrp.param.write`，排产走 `mrp.run.execute`，与现状一致。

## 6. 测试与验收

### 6.1 引擎（纯函数，无 DB）

- **黄金用例**：§2.2 的表固化为一条命名测试
- **三条原则的性质断言**（随机输入）：
  - P1：任一产品的排产周集合必须是**连续区间**
  - P2：不允许"某周为空，同时更早的周中存在某产品周产量 ≥ 2 × `min_out`"
  - P3：每周品种数 ≤ `max_sku_count`；富余体制下的每周品种数不得劣于紧张体制
- **最低产能四档**：20t→1 周｜10t→1 周（钳位）｜40t→2×20｜`min > max` 配置被拒
- **周日历**：`7/29–8/4` 在三模式下的归属；4 周月 vs 5 周月；跨年周（ISO 年与自然年不一致的那几天）
- **lead 周**：`lead=0` 字节级复现不提前；钳到当前周时 `lead_shortfall` 语义保留
- **保质期**：按真实日期差校验（§2.6），含"无保质期数据 → 不可提前"路径

### 6.2 门禁

- mrp-api 现有 **167 passed 不许降**（新增测试在其之上）
- 前端 `npx tsc -p tsconfig.app.json --noEmit` 只许剩既有的 baseUrl 一条；用 `--listFiles` 正面确认改动文件确实被编译（见 `feedback_uniops_frontend_tsc_false_gate`）

### 6.3 用户手工验收（E2E 自动化被安全分类器拦，只能人工点）

1. 真实一个月数据复现 §2.2 的排产结果
2. 最低产能三档（10 / 20 / 40 吨单品）
3. 某周设 `max_output_qty=0` → 计划自动绕开该周
4. 意向产品全链路：建 → 录数 → 进快照 → MPS 摘要点名 → 绑定真实码 → 参与排产
5. 切换三种周定义后新 run 表头变化，**已发布的 run 保持不变**

## 7. 上线

- 无新权限键，`seed_authz` 不需要重跑
- 迁移 `mrp09` 有破坏性删除，`migrate-prod.sh` 已含 mrp-api，按常规发布流程走
- 顺手补一条既有风险的显性化：MPS 生成摘要报"**N 个产品无保质期数据，已按不可提前处理**"——把"ERP `exp` 字段是否有值从没核过、pre-build 可能静默退化"这条已知风险从静默变成可见
- 发布镜像：只涉及 mrp-api / mrp-web，其余 retag

## 8. 明确不做（YAGNI）

- **不做**销售预测周化（D1）：预测仍是 18 个月 × 产品的月度大表，连续预测那套编辑守卫/变更审计/outlook 快照零改动
- **不做**目标函数 + 局部搜索的排产优化：结果不可预判、权重要调参、同输入可能给不同答案，对需要签字发布的计划是负资产
- **不做**月计划与周计划双粒度并存：D9 已定一刀切
- **不做**产品族/产线维度的周产能：`scope_type` 结构已留好，将来加规则行即可，本期只用 factory 级
- **不做**意向产品的 BOM / 库存 / 净需求参与：它没有物料码，这些概念对它不成立

## 9. 遗留风险

| 风险 | 说明 |
| --- | --- |
| 生产 WMS 未配置 | prod `.env` 无 `WMS_*`，Consignment 页为空、WMS 库存腿未通。**不阻塞本设计**（预测与排产不读 WMS 明细），但 **1C 的 PAB 依赖它**，必须在 1C 前解决 |
| 成品保质期数据 | ERP `exp`（shelf_life_months）成品是否有值从未在生产核实。本期通过生成摘要点名让它可见，但真值仍需业务补 |
| 78 周矩阵的性能 | 现有 MatrixGrid 已做行虚拟化，**列未虚拟化**。月折叠把开屏列数压到约 13，先按此实现；若展开全部 18 个月出现卡顿，再补列虚拟化 |
