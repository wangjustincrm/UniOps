# 权限中枢一期 — 发布补充说明

> 配套 [reference: 生产发布标准流程]。本次发布**在标准流程上多一步 seed**,其余照常。
> 分支 `feature/authz-hub-phase1`,计划 `2026-07-15-authz-hub-phase1.md`。

## 本次发布的特殊项

| 项 | 结论 |
|---|---|
| 新迁移 | **identity-api `0002_authz_tables`**(5 张表)。`migrate-prod.sh` 的服务列表已含 identity-api,照常跑即可。finance/epms/vms 无新迁移。 |
| **新增 seed 步骤(必做)** | 迁移后必须跑一次 seed,把现有权限矩阵从 epms `company_config` JSONB 灌进 identity 表。**不跑的话矩阵是空的**——epms 代理会因 identity 返回空矩阵而让非 admin 失去权限(不是回落,回落只在 identity 不可达时触发)。⚠️seed 用 `ON CONFLICT DO NOTHING`——只保护已存在的行,不保护"已被删除"的行:**只在矩阵第一次通过 Portal 编辑之前重跑才安全;一旦有人在 Portal 上做过一次权限收回(改矩阵),此后绝不要再重跑这个 seed**,否则会把那次收回悄悄复活。 |
| env / compose | **无需改动**。`IDENTITY_API_URL: http://identity-api:8009` 生产 compose 早已存在(epms 一直用 identity 做 auth 转发)。 |
| Caddyfile | 未改动 → 不需要 `restart edge`。 |
| 浏览器直连 identity | 不存在。identity 无公网域名,全部经 epms-api 服务端代理 → 不动 DNS/Caddy/CORS。 |

## app server 执行顺序(10.10.50.65)

```bash
cd /opt/uniops
sudo git pull origin main
sudo sed -i "s/^TAG=.*/TAG=<新sha>/" .env
sudo docker compose -f docker-compose.prod.yml pull

# 1) 迁移(建 5 张 authz 表)
sudo ./migrate-prod.sh

# 2) ★ seed(仅本次发布需要,且只在本次这一次跑;矩阵一旦被 Portal 编辑过就不要再重跑,见上表)
sudo docker compose -f docker-compose.prod.yml run --rm identity-api python -m scripts.seed_authz
#    预期输出:seed_authz done: {'granted_inserted': N}   (N>0;重跑时 N=0)

# 3) 起服务
sudo docker compose -f docker-compose.prod.yml --profile edge up -d
sudo docker compose -f docker-compose.prod.yml ps
```

顺序说明:先 migrate 后 seed 再 up。seed 与 up 之间即使有间隙也安全——旧容器仍读 epms 本地镜像 JSONB。

## 发布后验证(建议)

```bash
# 矩阵非空且与发布前一致(17 角色)
curl -s -H "Authorization: Bearer <admin token>" \
  https://epms.canadaroyalmilk.com/api/v1/config/role-permissions | python -c "import sys,json;d=json.load(sys.stdin);print('roles:',len(d))"
```
浏览器:Portal → Admin → **Access Control** 出现;矩阵可勾选保存;User Roles 可给用户加附加角色;EPMS Admin Panel 里 Access Control Matrix / Custom Roles 两个页签已消失,Users 页角色下拉仍可改主角色。

## 回滚

全程 additive:`.env` 的 `TAG` 改回上一个 sha → `pull` + `--profile edge up -d`。identity 的 5 张表留着无害(旧代码不读它们),epms 旧代码回到读 company_config JSONB(数据仍在,未被本次改动写过)。

## 一期后仍在 EPMS 的东西(不是遗漏)

- **Role Management 页签**(GM/OPM/Finance BP 指派 + 部门映射):属审批岗位指派,③期迁 approval-api。
- **booking 前端**:仍走 `/config/role-permissions` 兼容代理(该端点保留),不受影响;是否迁移待定。
