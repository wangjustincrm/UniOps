# MRP 生产计划版本模型设计（1B 修订，收口 plan-versioning）

- 日期：2026-08-14
- 状态：待评审
- 前置文档：`2026-08-12-mrp-weekly-planning-and-intent-products-design.md`、`2026-08-14-mrp-min-lot-week-start-and-time-fence-design.md`
- 代码基线：分支 `feature/mrp-min-lot-and-time-fence`（最小批量+周起始日+锁定区，401 passed），本设计在其之上
- 影响阶段：修订已上线的 1B（MPS）。**收口上一轮留下的 `TODO(plan-versioning)`**：锁定区的继承源目前是「最近一次 released 的 run」，本设计把它换成明确的生效版

## 0. 为什么现在改

三条**实测确认**的现状（不是推测）：

1. **`confirm-release` 只把自己置 `released`，不动前任** → 库里可以同时存在多条 `released` 的 run，"哪一版在生效" 没有唯一答案。上一轮的锁定区被迫用 "最近一次 released" 这个临时定义去猜。
2. **没有 `GET /mps/runs` 列表端点** → 历史计划无法枚举。
3. **前端 `runId` 只活在 `useState`** → **刷新页面就回不到任何计划**，包括刚发布的那一版。

第 1 条是数据正确性问题（`mrp_demands` 是喂 1C 的唯一现行需求集，两版同时"生效"= 采购口径不明），第 2、3 条是可达性问题（东西在库里，用户走不到）。

## 1. 用户拍板记录（2026-08-14）

| # | 问题 | 决策 |
| --- | --- | --- |
| V1 | 跨组能否回退（9 月组发布后切回 8 月组） | **不能，只能往前走**。8 月组的 18 月计划已缺最新一个月的需求，切回去等于整月的料不买 |
| V2 | Default 的粒度 | **全局恰好一个**（收紧自上次的"每组一个"）：跨组既然不能切，旧组的 Default 标记没有用途，只会让"哪版在生效"有两个答案 |
| V3 | 历史版可见性 | 永远可**只读回放**（快照，不重算），只是不能再生效 |
| V4 | 迁移是否处理存量 | **要**。生产上可能已有多条同时 `released`，迁移必须收敛，否则新逻辑的前提不成立 |

上一轮已定、本设计沿用：分组 = `horizon_start_month`；同组内可自由切换生效版；**切回旧版就是回放当时那份计划**，哪怕预测与产能规则已变。

## 2. 状态与生效版

### 2.1 三态

| 状态 | 含义 | 可做什么 |
| --- | --- | --- |
| `draft` | 刚生成，未发布 | 重算、调整、发布 |
| `released` | 已发布，**且仍与当前生效版同组** | 只读；可被选为生效版（Set as active） |
| `superseded` | 已发布，但**更新的组接管了** | 永久只读，不可再生效 |

`is_default`：**全局恰好一条**，就是当前喂 `mrp_demands`、也是锁定区继承源的那一版。

### 2.2 发布一个新版时（同一事务）

1. 该 run → `released` + `is_default = true`，写 `released_at`；
2. 同组的前任生效版 → 保持 `released`，`is_default = false`（**仍可切回去**）；
3. **所有更早组的 run → `superseded`**（含它们的 draft：一个比生效组更早的 draft 发布出来就是倒退，见 §2.4）；
4. 清空 `mrp_demands` 的 `demand_type='mps'` 行并从该 run 重写（§4.1）。

### 2.3 切换生效版（`set-default`）

- 目标必须 `status='released'`；
- 目标必须与**当前生效版同组**（`horizon_start_month` 相等），否则 422 并说明"计划组只能往前走"；
- 生效标记转移 + `mrp_demands` 重写，同一事务。

### 2.4 ★旧组的 draft 不许发布

发布即成为生效版，所以发布一个 `horizon_start_month` 早于当前生效组的 draft = 生效组倒退，直接绕过 V1。**发布时校验组不早于当前生效组，否则 422**。（这条不写，V1 就只是前端的一句话。）

**还没有生效版时**（全新库，或迁移后一条 `released` 都没有）：任何 draft 都可以发布，它成为第一个生效版。校验只在存在生效版时才有比较对象。

### 2.5 恰好一个生效版：交给数据库

`mrp_mps_runs(is_default) WHERE is_default` 上建**部分唯一索引**。应用层的先清后置在并发下不可靠，而这条不变量一旦破（两版同时生效），`mrp_demands` 的口径就说不清了。

## 3. 端点

### 3.1 `GET /mps/runs`（新增）

列表：按 `horizon_start_month` 倒序、组内按 `released_at`（未发布的按 `created_at`）倒序。每行返回 `id`/`run_no`/`horizon_start_month`/`horizon_months`/`status`/`is_default`/`released_at`/`created_at`/`stats`。

**不返回 lines**：列表是导航用的，一次带上几千行会把选择器拖垮。行由 `GET /runs/{id}` 单独取。

权限沿用 `mrp.report.view`。

### 3.2 `POST /mps/runs/{id}/set-default`（新增）

按 §2.3 校验后切换，返回与 `GET /runs/{id}` 相同的结构（切完就是要看它）。权限沿用 `confirm-release` 的写权限键。

### 3.3 `POST /mps/runs/{id}/confirm-release`（改）

按 §2.2 扩展。**幂等性**：对已经是生效版的 run 再调一次，返回 200 且不产生副作用（重写一遍相同的 `mrp_demands` 是无害的，但不许把同组前任的 `released` 状态搞坏）。

## 4. 共用逻辑与连带影响

### 4.1 ★`mrp_demands` 的重写只能有一份实现

`confirm-release` 与 `set-default` 都要「清 `demand_type='mps'` 全部行 + 从该 run 的非缺口、非 covered、qty>0 行重写」。抽成 `publish_demands_from_run(db, run)` 一个函数，两处共用。

**复制第二份的后果是采购量翻倍或漏买** —— 这正是本项目历史上反复踩的那类静默错误（见 `mrp_demands` 无条件清空的既有注释）。

### 4.2 锁定区改读生效版

上一轮的 `_live_released_run()`（"最近一次 released"）替换为 `_default_run()`（`is_default = true`）。行为差异是真实的：切回同组的旧版后，下一次生成继承的锁定区来自**那一版**，这正是"切换生效版"应有的语义。

### 4.3 迁移 `mrp12`

- 加 `is_default BOOLEAN NOT NULL DEFAULT false`、`released_at TIMESTAMPTZ NULL`；
- 建部分唯一索引 `WHERE is_default`；
- **收敛存量**：把 `status='released'` 的 run 里 `created_at` 最新的一条设为 `is_default` 并回填 `released_at = created_at`（真实发布时间没有记录，`created_at` 是最接近的可得值，且用于排序足够）；其余 `released` 的 run 中，`horizon_start_month` 早于生效组的 → `superseded`，同组的 → 保持 `released`。
- 一条 `released` 都没有 → 什么都不做（全新库或只有 draft）。

### 4.4 前端

- **`runId` 进 URL**（`?run=<id>`）。进页面无参数时打开当前生效版；没有生效版则打开最新的 run；一个都没有才是空状态。这是"刷新就找不回计划"的真修法。
- 顶部**版本选择器**：按组分节，行内显示 `run_no`、状态徽章、`Active` 标记、发布时间。
- **历史版（`superseded`）整体只读**：不给 Recalculate / Confirm & Release / Adjust，顶部一条说明"更新的计划组已接管这一版"。
- 同组 `released` 非生效版：给 **Set as active**；跨组的显示**禁用 + 原因**（按钮消失会让计划员以为坏了）。
- 切换前确认弹窗：**这会重写给采购的现行需求集**。

## 5. 测试

现基线：mrp-api **401 passed**（本分支起点）。前端 tsc **0 诊断**（自己重量，别照抄）。

1. **列表**：排序（组倒序、组内发布时间倒序）、不含 lines、权限门禁。
2. **恰好一个生效版**：发布第二版后，前任 `is_default=false` 且仍 `released`；数据库部分唯一索引在直插第二条 `is_default` 时报错。
3. **同组切换**：`set-default` 成功，`mrp_demands` 变成目标版的行（数量与行数都比对）。
4. **跨组切换 422**：且 `mrp_demands` 一行未动。
5. **旧组发布 422**：且该 run 仍是 `draft`。
6. **新组发布**：旧组的 `released` 与 `draft` 全部变 `superseded`。
7. **幂等**：对生效版再发布一次 → 200、状态与需求集不变。
8. **迁移收敛**：造三条 `released`（两组），跑迁移后恰好一条 `is_default`、旧组那条 `superseded`。
9. **锁定区跟着生效版走**：切回同组旧版后新建计划，锁定区继承的是那一版的行。
10. **前端**：URL 带 `?run=` 刷新回到同一版；历史版无写按钮。

## 6. 不做（YAGNI）

- **跨组回退**（V1 已砍）。误发布的补救是在新组里重新生成一版并发布，不是把生效组往回拨。
- **每组一个 Default**（V2 已砍）。
- **版本间差异视图**（"这一版和上一版差在哪"）——真正有价值，但它是独立一块工作量，等版本模型落地后单独做，不塞进本设计。
- **删除历史版**。计划是审计对象，只读留着。
