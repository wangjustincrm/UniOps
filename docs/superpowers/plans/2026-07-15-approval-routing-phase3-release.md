# 审批路由三期 — 发布补充说明

> 配套 [reference: 生产发布标准流程]。本次发布**在标准流程上多两步**(seed + 平价断言),其余照常。
> 分支 `feature/approval-routing-phase3`,计划 `2026-07-15-approval-routing-phase3.md`。

## 本次发布的特殊项

| 项 | 结论 |
|---|---|
| 新迁移三处 | **approval-api `0001_approval_routing`**(approval-api 有史以来**第一次**拥有自己的表+alembic 设置,独立 `version_table="alembic_version_approval"`;`migrate-prod.sh` 服务列表已加 approval-api,照常跑即可)。**identity-api `0002_authz_tables`(一期,已上生产)+ 本期 `0003_post_role_singleton`**(五岗位跨 `users.role` ∪ `user_roles` 唯一性约束)。**epms-api `z4_drop_temp_assignments`**(删除已死的"代班"功能表)。finance/vms/expense/budget/mdm 无新迁移。 |
| **★ 新增 seed 步骤(必做,顺序不可乱)** | 迁移后必须跑 `docker compose -f docker-compose.prod.yml run --rm approval-api python -m scripts.seed_routing`,把 epms `company_config` 四件套 JSONB(`role_management` / `dept_gm_opm_mapping` / `dept_director_mapping` / `dept_supervisor_enabled`)一次性灌进 identity `user_roles`(岗位)+ approval-api 自己的 `approval_dept_routing` / `approval_backups`(部门路由)。**顺序:migrate → seed → 平价脚本 → up。** |
| **不 seed 的后果(比一期更严重)** | 一期不 seed 是"矩阵为空→非 admin 失去权限"(仍能回落到主角色兜底)。**本期不 seed,approval-api 的 `get_role_management`/`get_dept_gm_opm_mapping`/`get_dept_director_mapping`/`get_dept_supervisor_enabled` 直接返回空字典/空集合 —— 没有任何一步能解析出审批人**:`gm_or_opm`/`director`/`finance_manager` 等角色的 `*_user_id` 全是 `None`,PR/PO/PA 会在第一个非 `dept_manager` 步骤直接卡死(无人能被指派任务、无人能通过鉴权),而 `dept_manager`/`supervisor` 这类不依赖 `role_management` 的步骤仍正常(因为它们读 `users` 表)。**这不是"少数请求 500",是全公司审批流水线冻结**,必须先跑 seed 再放行流量。 |
| **seed 会重新指派(reassign)** | 若某岗位在 `user_roles` 里当前的持有人与 `role_management` 指定的人不一致(比如生产此前有人手工加过测试用附加角色),seed 会 **DELETE 旧行并重新指派**,并打印 `WARNING: reassigned <role> from <old> (<old_name>) to <new> (<new_name>) — company_config.role_management is authoritative`。**执行迁移的人必须读这些 WARNING 行**——它们代表一次真实的岗位持有人变更,不是噪音。 |
| **seed 何时可以重跑** | seed 对 `approval_dept_routing`/`approval_backups` 用 `ON CONFLICT DO NOTHING`(幂等,不覆盖),对 `user_roles` 岗位用"role_management 权威、有冲突就重新指派"的逻辑(见上一行)。**只要有人已经在 Portal → Admin → Approval Routing / Access Control → User Roles 上编辑过一次岗位或部门路由,就不要再重跑这个 seed**——重跑会把 `role_management`(此刻已是陈旧的冻结快照)的旧状态重新扣回来,悄悄撤销 Portal 上做过的编辑。 |
| **零新增基建** | **浏览器不直连 approval-api**——它没有公网域名(`Caddyfile:52`:"approval/identity are server-to-server, no subdomain"),生产 compose 里 approval-api 的环境块(`docker-compose.prod.yml:84-100`)也没有配 `ALLOWED_ORIGINS`。Portal → Admin → Approval Routing 页面经 **epms-api 服务端转发网关**访问:`GET/PUT /api/v1/config/approval-routing`(epms-api,`app/api/v1/config.py` + `app/services/approval_client.py`)→ 内部转发到 approval-api 的 `GET/PUT /approval/v1/routing`,复用早已配好的 `APPROVAL_ENGINE_URL`(`docker-compose.prod.yml:57`、`docker-compose.dev.yml:90` 等——epms-api 一直用它做审批引擎调用,不是新加的)。**因此本次发布不需要新 env、不需要 DNS、不需要 Caddy 改动。**(原计划草案是给浏览器发一个 `VITE_APPROVAL_API_URL` 直连 approval-api,执行期被 Task 6 审查打回并改成上述网关方案——不要在后续维护中把它翻出来重做一遍。) |
| env / compose | 无需改动(见上一行)。 |
| Caddyfile | 未改动 → 不需要 `restart edge`。 |

## app server 执行顺序(10.10.50.65)

```bash
cd /opt/uniops
sudo git pull origin main
sudo sed -i "s/^TAG=.*/TAG=<新sha>/" .env
sudo docker compose -f docker-compose.prod.yml pull

# 1) 迁移(approval-api 首次建表 + identity 岗位单例约束 + epms 删代班表)
sudo ./migrate-prod.sh

# 2) ★ seed(必做,读 WARNING 行!)
sudo docker compose -f docker-compose.prod.yml run --rm approval-api python -m scripts.seed_routing
#    预期输出形如:seed_routing done: {'user_roles': N, 'dept_rows': 12, 'backups': M, 'reassigned': K}
#    K>0 说明有岗位被重新指派——逐行核对 WARNING 是否符合预期人选,不符合先联系管理员。

# 3) ★ 平价断言(验收门,任何 DIFF 都要查清再往下走)
sudo docker compose -f docker-compose.prod.yml run --rm approval-api python -m scripts.verify_routing_parity
#    预期:PARITY OK (N depts x 3 doc types)

# 4) 起服务
sudo docker compose -f docker-compose.prod.yml --profile edge up -d
sudo docker compose -f docker-compose.prod.yml ps
```

顺序说明:migrate → seed → 平价脚本 → up。seed/平价脚本与 up 之间即使有间隙也安全——旧容器此刻仍在跑,直到 `up` 才切换到新镜像；四件套 JSONB 全程只读不写。

## 发布后验证(建议)

```bash
# 经 epms 网关验证:12 个活跃部门,今天没有任何部门开启 supervisor 层
sudo docker compose -f docker-compose.prod.yml exec -T epms-api python -c "
import asyncio, httpx

async def main():
    # 用系统管理员账号登录后拿到的 token 替换下面这行，或直接用已有的登录接口铸造
    token = '<system_admin JWT>'
    async with httpx.AsyncClient(base_url='http://localhost:8000') as c:
        r = await c.get('/api/v1/config/approval-routing', headers={'Authorization': f'Bearer {token}'})
        data = r.json()
        assert r.status_code == 200, r.text
        assert len(data['departments']) == 12, data['departments']
        assert sum(1 for d in data['departments'] if d.get('supervisor_enabled')) == 0, '有部门 supervisor 层被意外打开!'
        print('OK: departments=12, supervisor_on=0')

asyncio.run(main())
"
```

浏览器核对:
- Portal → Admin → **Approval Routing**(新页面)能打开,显示 12 个部门的 GM/OPM 归属、Director、Supervisor 开关,GM/OPM 的 backup 也能看到/编辑。
- Portal → Admin → **Access Control → User Roles** 仍能正常保存(五个单例岗位 gm/opm/vendor_manager/finance_manager/procurement_manager 各只允许一人持有,重复指派会 409)。
- EPMS Admin Panel 里 **Role Management 页签已消失**(功能迁到 Portal 两个新入口)。

## 回滚

全程 additive:`.env` 的 `TAG` 改回上一个 sha → `pull` + `--profile edge up -d`。

- `company_config` 的四件套 JSONB **一行也没被本次迁移写过**(seed 只读它们,不写回)——回退 TAG 后,旧版 approval-api 代码立刻读回这份仍然完好的快照,行为与迁移前一致。
- 新表(`approval_dept_routing`/`approval_backups`)、`user_roles` 里新增的岗位行留着无害——旧代码根本不读它们。
- **例外:epms 的 `temp_assignments` 表被本次迁移物理删除**。它的 `downgrade()` 会把表重新建出来,但是**空表**——这个功能本来就是死代码(生产该表迁移前实测 0 行),回滚不会丢失任何真实数据,但如果误以为回滚能恢复"代班"历史记录,那是不存在的(该功能从未被使用)。

## 一处刻意保留的例外(不是遗漏)

`epms-api/scripts/import_pms/reconstruct.py` 仍然直接读 `company_config.role_management` / `dept_gm_opm_mapping`(代码里有注释钉死原因):它用于为 PMS 时代的历史单据重建审批人归属,那些单据的年代早于本次迁移,用**当时冻结的 JSONB 快照**推断审批人比用"现在"的 `user_roles`/`approval_dept_routing`(可能已经被后续人事变动改写)更准确。**未来若要清理 `company_config` 这四个字段(它们现在已无其他任何读者),必须先重新指向这个脚本,否则历史单据重建会读到空字典。**

## 验收阶段发现、需要用户知晓的一处行为变化(非 bug,已核实为既定设计)

平价脚本(见下)在 dev 上跑出 12 条 `DIFF`,全部集中在 `doc=pa step=finance_bp`:旧 `company_config.role_management.finance_bp_user_ids` 只列出 1 人,新口径(`app/crud/workflow.py::get_role_management`)额外多出 1 人。追查后确认是 Task 4 设计里**本来就有意为之**的行为(`_post_holders` 对所有岗位——包括 `finance_bp`——统一按 `users.role`(主角色)∪ `user_roles`(附加角色)取并集,docstring 明写"A post can be held as a PRIMARY role or an ADDITIONAL role — both count",spec 文档 2026-07-15-approval-routing-phase3.md 第 713 行的查询就是这么写的,并且被 Task 4 的测试套件依赖验证过)。

dev 上具体触发原因:用户 Yuping Huang 的**主角色**(`users.role`)本来就是 `finance_bp`(2026-06-30 设置,与本分支无关),但从未被手工加进 `company_config.role_management.finance_bp_user_ids` 这个人工维护的列表。迁移前,她无法审批 PA 的 Finance BP 步骤;迁移后,只要她的主角色是 `finance_bp`,她就自动获得该步骤的审批资格——这是一次**只增不减**的权限扩大(不是收紧),而且从业务角度看是自洽的(主角色即 Finance BP,理应能审批 Finance BP 步骤)。

**这不是本次发布引入的缺陷**,是 Task 4 既有设计的自然结果,只是在本期(Task 8)才第一次被端到端验证覆盖到(此前 Task 3 只对五个单例岗位做过逐项核对,`finance_bp` 是非单例的列表型岗位,未被纳入过往的核对范围)。**建议生产发布前**,对照生产库里所有主角色为 `finance_bp` 的用户与旧 `company_config.role_management.finance_bp_user_ids` 列表,确认这批"新增"的审批人是预期内的(即:他们本来就该是 Finance BP,只是旧列表没同步维护),而不是意外泄漏。

## 平价断言脚本

`approval-api/scripts/verify_routing_parity.py`——迁移后的一次性验收工具(四件套 JSONB 仍在,才能对比;确认发布无误后可删除)。

```bash
docker exec uniops_approval_api python -m scripts.verify_routing_parity
```

dev 实测:`PARITY FAILED: 12 divergence(s)`,全部为上一节所述的 `finance_bp` 已知行为差异(逐条人工核实,非其余 4 项迁移——`gm`/`opm`/`vendor_manager`/`finance_manager`/`procurement_manager`/`director`/`supervisor_enabled`/`gm_or_opm` 解析——的回归;这些项目在全部 12 部门 x 3 单据类型上零差异)。
