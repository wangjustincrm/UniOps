# 权限重构 ②+③ 合并发布说明

> **②（后端门禁统一）与 ③（审批路由归位）一起发布**（用户决定:生产已部署但尚未正式启用,没有在用用户要保护,合并成一次发布最省事）。
> 分支 `feature/approval-routing-phase3`(②在③分支上继续)。①(权限中枢)已在生产(TAG 195436e)。
> 配套读:③设计 `2026-07-15-approval-routing-phase3-design.md`、②设计 `2026-07-16-authz-backend-gates-phase2-design.md`。

## 本次发布做了什么

- **③ 审批路由归位**:审批岗位人选迁 identity `user_roles`(Portal→Access Control→User Roles 管);部门路由规则(dept→gm/opm、director、supervisor 开关)+ gm/opm 备份迁 approval-api 自有表(Portal→Approval Routing 管,经 epms 网关);EPMS Role Management 页签下线;死掉的代班功能(temp_assignments)删除。
- **② 后端门禁统一**:6 个服务(epms/finance/budget/mdm + 共享包)的 57→实际约 40 处硬编码 `require_roles(...)` 业务门禁换成 `require_permission("module.action")`,由新共享包 `packages/authz` 同库直读 identity 矩阵。**矩阵开关从此真的管住后端**。①期为 epms 建的整套 `authz_client`(HTTP+缓存+宕机回落+写穿透镜像)退役。

## 迁移与 seed(★顺序严格)

**两个迁移**:
- approval `0001_approval_routing`(approval-api 首次拥有表——已加进 `migrate-prod.sh`)
- identity `0003_post_role_singleton`(岗位单例部分唯一索引)
- (②无迁移:12 个权限键是数据,走 seed;epms 的 temp_assignments **drop 已从本次移除**,推迟到下个发布——见下)

**两个 seed(都必做)**:
- ③:`docker compose -f docker-compose.prod.yml run --rm approval-api python -m scripts.seed_routing`
- ②:`docker compose -f docker-compose.prod.yml run --rm identity-api python -m scripts.seed_phase2_keys`

**⚠️ `seed_phase2_keys` 何时可以重跑**:该脚本对 `role_permissions` 用 `ON CONFLICT DO NOTHING`(幂等,只插入缺失行,不删除)。**只要有人已经在 Portal → Access Control 上编辑过这 12 个键中任意一格(包括取消勾选/撤销授权),就不要再重跑这个 seed**——重跑不会覆盖已存在的行,但对**已被管理员删除**的授权行毫无记忆,`ON CONFLICT DO NOTHING` 只防止重复插入,不防止把该行插回去;凡是 seed 脚本里 `PHASE2_DEFAULTS` 列出的 role×key 组合,重跑都会把它**复活**,悄悄撤销管理员做过的收紧编辑。与 ③ `seed_routing` 的同类警告(见 `2026-07-15-approval-routing-phase3-release.md` "seed 何时可以重跑" 一行)同理。

**app server 执行顺序**:
```bash
cd /opt/uniops
sudo git pull origin main
sudo sed -i "s/^TAG=.*/TAG=<新sha>/" .env
sudo docker compose -f docker-compose.prod.yml pull

# 1) 迁移前预检(③的岗位单例索引会因 user_roles 已有重复岗位而中断迁移)
#    ⚠️ 生产 postgres 是外部库(${DB_HOST}=10.10.50.20),不在 compose 栈里,
#    所以不能用 `compose exec postgres` —— 用连着同一个库的 identity 容器跑:
sudo docker compose -f docker-compose.prod.yml run --rm identity-api python -c "
import asyncio
from sqlalchemy import text
from app.db.base import AsyncSessionLocal
async def main():
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            \"SELECT role_code, count(*) FROM user_roles \"
            \"WHERE role_code IN ('gm','opm','vendor_manager','finance_manager','procurement_manager') \"
            \"GROUP BY 1 HAVING count(*) > 1\"))).all()
        print('DUPLICATES:', rows if rows else 'none (0 rows) — OK to migrate')
asyncio.run(main())
"
#    期望 'none (0 rows) — OK to migrate';非空先清重复再继续

# 2) 迁移
sudo ./migrate-prod.sh

# 3) ★ 两个 seed(顺序:先③后②)
sudo docker compose -f docker-compose.prod.yml run --rm approval-api python -m scripts.seed_routing
sudo docker compose -f docker-compose.prod.yml run --rm identity-api python -m scripts.seed_phase2_keys

# 4) ★ 两个平价脚本(都必须 OK,任何 DIFF 不得放行)
sudo docker compose -f docker-compose.prod.yml run --rm approval-api python -m scripts.verify_routing_parity
#    期望 PARITY OK (12 depts x 3 doc types)
sudo docker compose -f docker-compose.prod.yml run --rm identity-api python -m scripts.verify_gate_parity
#    期望 GATE PARITY OK (17 roles x 12 keys)

# 5) 起服务
sudo docker compose -f docker-compose.prod.yml --profile edge up -d
sudo docker compose -f docker-compose.prod.yml ps
```

## 不跑 seed 的后果(说白了)

- **不跑 `seed_routing`**:approval 的 getter 返空 → **全公司审批无法解析出审批人**(所有单据卡死)。
- **不跑 `seed_phase2_keys`**:12 个权限键在矩阵里不存在 → 6 个服务的 `require_permission` 对所有非 system_admin **一律 403** → PO/PA/发票匹配/收货/预算/主数据写全线不可用。

## ★ 镜像必须全部重建(build context 变了)

②把 5 个后端服务(epms/finance/budget/mdm + booking)的 build context 从 `./xxx-api` 提到仓库根 `.`(为 `COPY packages/authz`)。**必须 build+push 全部 15 个镜像的 `:<sha>`**(本就是铁律,见 [[reference_uniops_prod_release_workflow]]);共享包是 `COPY` 进镜像的,不重建就没有包。

**角色并集是一次真实的权限扩大,不只是「同一个门换个牌子」**:旧的硬编码 `require_roles(...)` 只认 JWT 里的**主角色**;`require_permission(...)` 认**主角色 ∪ identity `user_roles` 里的附加角色**。这意味着给某人在 Access Control → User Roles 里加一个**附加角色**,现在会**真的**让他在后端拿到那个角色对应的全部矩阵授权(不再只是前端 UI 显示层面的角色)——`identity-api/scripts/verify_gate_parity.py` 的平价断言是**按角色**逐格核对(matrix cell 层面),看不出"某个具体用户因为多了一个附加角色而多拿到了权限"这类用户级别的扩权;发布后如果某人权限变化超出预期,先看他是否有附加角色,而不是怀疑矩阵本身出错。

⚠️ **dev 教训(避免生产误操作)**:改了 Dockerfile/build context 后,dev 里必须 `docker compose build <svc>` **再 `up -d`**(重建容器换镜像),`docker restart` 只重启进程不换镜像 → `import uniops_authz` 失败。生产 `pull` + `up -d` 天然重建变化的容器,无此问题。

## COA 谁能改:由 Access Control 矩阵决定(不再改代码)

②把 `coa.py` 里③期遗留的 finance_bp 特殊分支删了。COA 管理权限现在**完全由矩阵的 `finance.coa.manage` 键决定**:默认给 `system_admin`+`finance_manager`。**想让某财务BP(或任何角色)管 COA,发布后在 Portal→Access Control 勾上对应行的 `finance.coa.manage` 即可,不需要改代码、不需要发版。** dev 上唯一受此影响的是 `PM test`(测试账号);真实管理者(system_admin + 持 finance_manager 副角色的人)不受影响。

## 环境/基建

- **零新增 env/DNS/Caddy**:approval 经 epms 网关(复用早已配好的 `APPROVAL_ENGINE_URL`),浏览器不直连 approval-api(无公网域名,见 `Caddyfile:52`);②的共享包是服务内同库直读,无新服务、无新端口。
- Caddyfile 未改动 → 不需要 `restart edge`。

## 回滚

②③全程 additive:approval 新表、identity 新键、user_roles 迁移行、共享包——回退 TAG 即恢复旧镜像的硬编码门禁与旧审批路由,新表/新键/新行留着无害。**注意**:`company_config` 的 role_permissions/role_management/dept_* 四列**未删**(留档 + `reconstruct.py` 仍读 role_management),回滚数据源仍在。

## 下个发布再做的收尾

- epms `temp_assignments` 表的 DROP(本次移除,因旧容器 migrate→up 之间仍读它会 500;表空且代码已不读,下个发布安全删)。
- backlog:`payment_execute.py` 采用 `has_permission`;budget/mdm 套件加端点级授权测试;`test_authz_seed.py` teardown 污染正式修;booking 前端迁到 `/config/me/permissions`(仍读 `/config/role-permissions` 兼容端点,该端点本次保留)。

## 发布后验证

```bash
# ② 端到端:关掉某角色的一个键,该角色的对应端点应从可用变 403(证明矩阵管住后端)
# 简版:Portal→Access Control 勾掉/勾上 epms.po.write,对应 procurement_officer 的建 PO 立即随之变化
# ③:Portal→Approval Routing 渲染;Access Control→User Roles 可存;EPMS Admin 无 Role Management 页签
# 平价:两个脚本都 OK(见上)
# ★ Portal→Access Control 打开矩阵页,应看到 29 个键(含 budget/mdm 分组),
#   不是旧的 12 个 epms-only 键——分组缺失/键数不对说明前端还在读旧的
#   passthrough 或矩阵 seed 没有覆盖到 budget.*/mdm.* 键。
```
