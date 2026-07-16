# 审批路由三期 — 发布补充说明

> 配套 [reference: 生产发布标准流程]。本次发布**在标准流程上多两步**(seed + 平价断言),其余照常。
> 分支 `feature/approval-routing-phase3`,计划 `2026-07-15-approval-routing-phase3.md`。

## 本次发布的特殊项

| 项 | 结论 |
|---|---|
| 新迁移两处 | **approval-api `0001_approval_routing`**(approval-api 有史以来**第一次**拥有自己的表+alembic 设置,独立 `version_table="alembic_version_approval"`;`migrate-prod.sh` 服务列表已加 approval-api,照常跑即可)。**identity-api `0002_authz_tables`(一期,已上生产)+ 本期 `0003_post_role_singleton`**(五岗位跨 `users.role` ∪ `user_roles` 唯一性约束)。**epms-api 本期无迁移**——原计划的 `z4_drop_temp_assignments`(删空表 `temp_assignments`)已从本次发布中**移除**,理由见下方「下个发布」条目。finance/vms/expense/budget/mdm 无新迁移。 |
| **★ 迁移前 pre-flight 检查(必做)** | identity `0003_post_role_singleton` 的偏索引会在 `user_roles` 里已有重复单例岗位时**直接中止迁移**——Portal → Access Control 从一期起就能编辑岗位,存在这种情况是合理的。迁移前必须先跑:<br>`SELECT role_code, count(*) FROM user_roles WHERE role_code IN ('gm','opm','vendor_manager','finance_manager','procurement_manager') GROUP BY 1 HAVING count(*) > 1;`<br>**预期 0 行**。若有行返回,必须先手工解决冲突(保留一个持有人、删除其余行)再跑 `migrate-prod.sh`。 |
| **下个发布**:`temp_assignments` 空表待删 | 本期的代码已经完全不读 `temp_assignments`(Task 7 已删 UI/端点/model/schema/测试),但删表的迁移本身被移出本次发布——原因:`epms-api/app/api/v1/config.py::_full_response` 在旧容器仍服务期间读它,若这次连表一起删,`migrate → seed → 平价 → up` 这段窗口内旧容器的 `GET /api/v1/config` 会整体 500(`useConfig` 全站依赖它)。生产该表实测 0 行,晚一个发布再删没有任何数据风险——标准的"先停止读取、下次发布再删表"顺序。下次发布时把 `z4_drop_temp_assignments`(逻辑不变,`drop_table("temp_assignments")`)重新加回来即可。 |
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

# 0) ★ pre-flight(迁移前,identity 0003 的单例约束会因重复而中止迁移)
#    DB 是外部服务器(${DB_HOST}),不是 compose 里的容器 —— 借 epms-api 容器内已有的
#    asyncpg 连接跑这条检查,不需要另装 psql 客户端。
sudo docker compose -f docker-compose.prod.yml run --rm epms-api python -c "
import asyncio
from app.db.session import engine
import sqlalchemy as sa

async def main():
    async with engine.connect() as conn:
        rows = (await conn.execute(sa.text(
            \"SELECT role_code, count(*) FROM user_roles \"
            \"WHERE role_code IN ('gm','opm','vendor_manager','finance_manager','procurement_manager') \"
            \"GROUP BY 1 HAVING count(*) > 1\"))).all()
        print('dup singleton posts:', rows)
        assert not rows, 'resolve duplicate singleton post holders before migrating'

asyncio.run(main())
"
#    预期 dup singleton posts: []。有行必须先手工解决冲突（保留一个持有人、删除其余
#    user_roles 行）再继续。

# 1) 迁移(approval-api 首次建表 + identity 岗位单例约束;epms 本期无迁移)
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
- `epms-api` 本期没有迁移,`temp_assignments` 表原样留在库里(空表,未被本次发布触碰)——回滚没有任何额外影响。

## 一处刻意保留的例外(不是遗漏)

`epms-api/scripts/import_pms/reconstruct.py` 仍然直接读 `company_config.role_management` / `dept_gm_opm_mapping`(代码里有注释钉死原因):它用于为 PMS 时代的历史单据重建审批人归属,那些单据的年代早于本次迁移,用**当时冻结的 JSONB 快照**推断审批人比用"现在"的 `user_roles`/`approval_dept_routing`(可能已经被后续人事变动改写)更准确。**未来若要清理 `company_config` 这四个字段(它们现在已无其他任何读者),必须先重新指向这个脚本,否则历史单据重建会读到空字典。**

## 验收阶段抓到的一处真实越权(已修复)

平价脚本首跑在 dev 报出 **12 条 `DIFF`**,全部集中在 `doc=pa step=finance_bp`。追查确认这是**真实的权限扩大,不是既定设计**:

- 旧口径:PA 的 Finance BP 审批人 = `company_config.role_management.finance_bp_user_ids` 这个**人工维护的指派名单**(生产只有 1 人:PM test)。
- 出问题的新口径:`_post_holders` 对**所有**岗位统一取 `users.role`(主角色) ∪ `user_roles`(附加角色) —— 于是 **Yuping Huang**(主角色 `users.role='finance_bp'`,2026-06-30 设置,从未被指派进那个名单)凭空获得了**全部 PA** 的 Finance BP 审批权。

**这违反本期「零行为变化」铁律**,已修复(commit `e8f5225`),修法是把一个真实的语义区分写进代码:

| 岗位 | 性质 | 取值口径 |
|---|---|---|
| `gm` / `opm` / `vendor_manager` / `finance_manager` / `procurement_manager` | **公司唯一职位**(identity 强制单例:迁移 `0003_post_role_singleton` + `PUT /authz/users/{id}/roles` 跨表 409) | `users.role` ∪ `user_roles` —— 主角色即身份,你是 GM 就是 GM |
| `finance_bp` | **多人可有的职能角色**(identity 明确豁免单例约束) | **只认 `user_roles` 指派** —— 持有职能 ≠ 被指派为审批人 |

`seed_routing` 相应对 `finance_bp` 去掉了 `primary == code` 的跳过逻辑(否则被指派人的主角色恰好也是 `finance_bp` 时会丢行);五个单例岗位的跳过/改派逻辑未动。

**为什么单元测试照不出来**:engine 一行未改、四个服务零回归、所有套件全绿——每一层单独看都"正确"。只有拿新旧两套口径逐个部门 × 单据类型对撞,这个越权才现形。这正是平价脚本存在的意义。

## 平价断言脚本

`approval-api/scripts/verify_routing_parity.py`——迁移后的一次性验收工具(四件套 JSONB 仍在,才能对比;确认发布无误后可删除)。

```bash
docker exec uniops_approval_api python -m scripts.verify_routing_parity
```

dev 实测(修复后,控制器独立复跑确认):

```
PARITY OK (12 depts x 3 doc types)
```

**生产发布时这里必须是 `PARITY OK`。任何 `DIFF` 都意味着真实的审批人解析偏差 —— 不要放行,把输出贴出来查清。**(尤其:若再次出现 `step=finance_bp` 的 DIFF,那不是"已知现象",是回归。)
