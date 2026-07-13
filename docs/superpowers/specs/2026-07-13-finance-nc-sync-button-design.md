# NC 凭证手工同步按钮（全量/增量）设计

> 状态:设计稿(2026-07-13 brainstorm,逐点经用户确认)。
> 归属:Finance 重构 —— NC65 迁移工作流的 UI 化(并行期运维能力)。
> 关联:`2026-07-04-nc65-finance-migration-mapping-design.md`(映射规则来源)、
> `2026-07-07-finance-jv-subsystem-design.md`(JV 容器)、
> `finance-api/scripts/nc_migration/voucher_import.py`(被移植的脚本,保留)。

## 1. 背景与目标

NC 凭证导入目前是宿主机手工脚本:全量、清空重灌、仅本地 dev(硬拒生产)。
本设计把它 UI 化:凭证中心页一个「NC Sync」按钮,支持**全量导入**与**增量导入**,
供本地 dev 对账与**将来生产并行期**(NC 仍在记账,UniOps 定期拉新凭证)使用。

用户已确认的决策:
- **增量语义 = 只补新凭证**:按 `nc_source_pk` 跳过已存在的,只插入 NC 新出现的凭证;
  用 `GL_VOUCHER.ts` 时间戳水位加速。已导凭证永不改动(不处理 NC 侧修改)。
- **场景 = dev + 生产并行期**:按生产可用来设计;生产前提=app server(10.10.50.65)到
  NC(10.10.95.67:1521)网络可达,上生产前需验证。
- **权限 = 两种模式都仅 `system_admin`**(JWT role),财务角色不可见不可用。
- **入口 = 凭证中心页(Journal Vouchers)headerActions 按钮 + 弹窗**。
- **架构 = 方案 A**:导入逻辑移植进 finance-api,后台任务 + `nc_sync_runs` 记录表。

## 2. 数据模型:`nc_sync_runs`(finance alembic 新迁移)

| 字段 | 说明 |
|---|---|
| id (uuid pk) / created_at / updated_at | |
| mode | `full` / `incremental` |
| status | `running` / `success` / `failed` |
| started_by / started_at / finished_at | 操作人(uuid)与起止时间 |
| watermark_from / watermark_to | 本次扫描的 NC `GL_VOUCHER.ts` 水位区间(字符串,NC ts 原格式) |
| vouchers_deleted | 全量模式清掉的旧凭证数(增量恒 0) |
| vouchers_inserted / lines_inserted / dims_inserted | 导入计数 |
| unmapped_cc_count | 未命中成本中心映射的行数(照导,仅计数) |
| error | 失败原因(text,可空) |

这张表同时承担:审计记录、增量水位存储、进行中进度展示(计数字段边跑边更新)。

## 3. 后端服务:`app/services/nc_sync.py`

移植 `voucher_import.py` 的抽取/转换逻辑(三维辅助核算解析、CC_BY_CODE/CC_BY_DEPT
整定映射、双边行 net 归一、posted JV 组装),**最大化照搬已对账 0 差异的代码**。
整个 run 是同步函数(oracledb 读 NC + psycopg2 写本库,连接参数来自 settings),
由 API 层用 `asyncio.to_thread` 在后台跑。原脚本保留不删(应急/切换仍可命令行)。

### 两种模式
- **增量**:取最近一次 `status='success'` run 的 `watermark_to` 作为起点,拉 NC
  `ts > watermark` 的凭证;**兜底**:无论水位如何,先读库中已有 `nc_source_pk` 全集,
  只插入不存在的 —— 水位缺失(首次/历史脚本导入过)时依然正确,只是全表扫描慢一些。
- **全量**:`delete from journal_vouchers where nc_source_pk is not null`(级联删行与
  dims)+ 全量重灌,同一事务提交。

### 并发与容错
- **单飞**:进程内 asyncio.Lock + 启动前查 `nc_sync_runs` 有无 `running` 行;有则 409。
- **陈旧 running 兜底**:发现 `started_at` 早于 30 分钟的 running 行,自动标 `failed`
  (error='abandoned')后允许新 run(容器崩溃恢复)。
- **进度**:每个 execute_values 批次后 UPDATE run 行计数(独立小事务,不影响主事务)。
  注:全量主事务提交前,库里旧数据仍在 —— 进度计数是「已处理」而非「已可见」。
- 失败:异常捕获→run 标 failed + error;主事务回滚,库保持原状。

## 4. API(`finance-api`,router prefix `/finance/v1/nc-sync`)

- `GET /nc-sync/status`(任何已登录用户,200)→
  `{can_sync: bool, configured: bool, current_run: RunOut|null, last_run: RunOut|null}`
  - `can_sync` = 调用者是 system_admin(与 JV `/permissions` 同模式:UI 显隐看服务端
    能力标志,不看前端 jwt.role)。
  - `configured` = NC 五项 env 齐全。
  - `RunOut` = nc_sync_runs 行的序列化(含计数与水位)。
- `POST /nc-sync`(仅 `system_admin`,否则 403)body
  `{mode: 'full'|'incremental', confirm?: string}` → 202 `{run_id}`
  - `mode='full'` 必须 `confirm === "FULL RELOAD"`,否则 422;
  - 未配置 NC → 503;已有 running → 409。

## 5. 前端(凭证中心页 JournalVouchersPage)

- headerActions 加「NC Sync」按钮:`status.configured && status.can_sync` 才渲染。
- 弹窗(NcSyncModal,新组件):
  1. 顶部显示 last_run 摘要(时间/模式/插入数/水位/状态);
  2. 模式单选:Incremental(默认)/ Full Reload;
  3. Full 选中时出现输入框,须键入 `FULL RELOAD` 才解锁执行按钮,并显示红色警示
     (将删除全部 NC 来源凭证 ~39.8k 张后重灌);
  4. 执行后每 2s 轮询 status,展示 current_run 的插入计数进度;
  5. 完成:显示结果摘要(inserted/deleted/unmapped);失效 `['jv-list']` 与四个报表缓存
     (`account-balance`/`ab-expand`/`ab-vouchers`/`budget-actual`);失败显示 error。
- UI 文案纯英文。

## 6. 配置与部署

- finance-api 新 settings:`NC_HOST / NC_PORT / NC_SERVICE / NC_USER / NC_PASSWORD`
  (任一缺失 → `configured:false`,功能整体隐藏,不报错)。
- dev compose(docker-compose.dev.yml finance-api 服务)按 `c:/Project/nc65_conn.env`
  的值注入;生产 compose 并行期再配。
- `finance-api/requirements.txt` 加 `oracledb`、`psycopg2-binary`(容器镜像需重建)。
- ⚠️ 生产启用前提:验证 10.10.50.65 → 10.10.95.67:1521 可达;NC 账号仍为只读。

## 7. 错误处理与测试

- 校验:mode 枚举、full 确认词、configured、并发 409、非 admin 403。
- 测试(pytest,NC 侧不用真 Oracle —— fetcher 做成可注入,测试注入 fake 返回构造的
  NC 行):增量跳过已有 pk / 只插新 pk / 水位推进与记录 / 全量清空重灌计数 /
  403 / 409 / 422 / 503 / 陈旧 running 自动标 failed。
- 手工验证(dev):对本地库跑增量(应 0 插入,水位建立)→ NC 新凭证后再增量(只进新);
  全量跑一遍,逐科目余额仍与 NC 0 差异(复用脚本的对账 SQL)。

## 8. 不在范围

- COA/档案(部门/成本中心/收支项目/往来)的同步 —— 沿用脚本与 ERP MDM 模块。
- NC 侧修改/作废凭证的回propagate(增量=只补新,用户确认)。
- 定时自动同步(本设计只做手工按钮;将来要 cron 再加)。
- 凭证中心以外的入口(独立 Settings 页、Portal admin)。
