# Vendor Master 权限联动（消除 `vendor_master` / `mdm.vendor.write` 双闸漂移）

- **日期**: 2026-08-01
- **分支**: `fix/vendor-master-authz-coupling`（worktree `c:/Project/uniops-vendor-authz`，基于 `origin/main` = 27c51ca，即当前生产）
- **状态**: 设计待评审

## 1. 问题

管理员在 Portal → Access Control 给某角色（如自建的「Purchase officer」）勾选了 **"Vendor Master"** 权限，但该角色用户新建 Vendor 保存时报 **"权限不足"**（403 Insufficient permissions）。

## 2. 根因（已核实）

新建 Vendor 要穿过**两道各自独立的权限闸，key 不同**：

| 层 | 位置 | 要求的 permission key | UI 里对应的行（Access Control 矩阵） |
|---|---|---|---|
| ① EPMS 网关 | `epms-api/app/api/v1/vendors.py:27` `require_permission("vendor_master")` | `vendor_master` | **"Vendor Master"**（EPMS 组） |
| ② mdm 实际写入 | `mdm-api/app/api/v1/partners.py:24` `require_permission("mdm.vendor.write")` | `mdm.vendor.write` | **"Edit Vendor Master Data"**（MDM 组） |

`create_vendor`（vendors.py:62）先过 ① `vendor_master`，随后把写操作转发给 mdm 的 `create_partner`（partners.py:68），那里再校验 ② `mdm.vendor.write`。管理员只勾了 "Vendor Master"（`vendor_master`），第二道没过 → mdm 返回 403 → EPMS 原样透传（vendors.py:40-42）→ 前端显示 "权限不足"。

两个 key 是 `permission_defs` 里**两条独立的行**，UI 上是**两个互不相干的勾**，名字还不一样（一个 "Vendor Master"，一个 "Edit Vendor Master Data"），极易漏勾。

**默认授予现状：**
- `vendor_master` → `procurement_officer` / `procurement_manager` / `vendor_manager` / `system_admin`
- `mdm.vendor.write` → `system_admin` / `vendor_manager` / `finance_manager`
- 唯一错配角色：`finance_manager`（有 `mdm.vendor.write` 但无 `vendor_master`）。

**`mdm.vendor.write` 的作用面（blast radius）**：只 gate mdm-api 的 `create_partner` + `update_partner`（partners.py:24 绑定为 `PartnerWriteDep`），无 delete、不碰读接口。自动授予它 = 放开**建 Vendor 与改 Vendor**。

## 3. 选定方案：B —「合并成一个勾」

不改任何网关语义，只修**授予侧**：让「Vendor Master」这一个勾在授予时自动带上 `mdm.vendor.write`，并在 UI 上把冗余的 "Edit Vendor Master Data" 行隐藏，使矩阵里只剩一个可控勾。

不选方案 A（让 mdm 网关接受 `vendor_master` / 加隐含映射）的原因：用户要求保持网关语义不变，改「授予侧」而非「校验侧」。

## 4. 设计（4 个部件）

### 4.1 后端联动（identity `patch_matrix`）

文件：`identity-api/app/api/v1/authz.py`，`patch_matrix` 的写入循环（当前 authz.py:64-75）。

在既有的 INSERT/DELETE 循环里加**联动镜像**：当某角色的 delta 里出现 `vendor_master` 时，对**同一角色**镜像同样的操作到 `mdm.vendor.write`：
- `val` 为真 → 对 `mdm.vendor.write` 也做 `INSERT ... ON CONFLICT DO UPDATE`；
- `val` 为假 → 对 `mdm.vendor.write` 也做 `DELETE`。

要点：
- payload 是 **delta**，联动只在管理员真正切换 `vendor_master` 时触发，不会误动其它角色/其它勾。
- 两个 key 都是合法 `permission_defs`，已过循环前的 `perm_keys` 校验（authz.py:57-59），无需放宽校验。
- 用同一个 `db` session、同一个 `actor`（updated_by），保持事务一致。
- 锁（`role_permission_locks`）语义不变：联动只写 `role_permissions`，不碰锁表；若某角色 `vendor_master` 被锁，现有锁校验（authz.py:60-63）照常生效。
- 联动关系用一个**显式常量对**表达（`vendor_master → mdm.vendor.write`），不做通用「权限捆绑」框架（YAGNI）；未来若出现更多配对再抽象。

**这一部件保证：以后任何新角色（含自建角色）只勾一次 "Vendor Master" 即端到端可用。**

### 4.2 数据回填（idempotent 脚本）

新增 `identity-api/scripts/backfill_vendor_master_coupling.py`（幂等，仿 `seed_phase2_keys.py`）：给**当前已持有 `vendor_master` 的所有角色**补上 `mdm.vendor.write`。

```sql
INSERT INTO role_permissions (role_code, permission_key, updated_by)
SELECT rp.role_code, 'mdm.vendor.write', rp.updated_by
FROM role_permissions rp
WHERE rp.permission_key = 'vendor_master'
ON CONFLICT (role_code, permission_key) DO NOTHING;
```

**为什么是脚本而非 alembic 迁移**：`role_permissions.permission_key` 对 `permission_defs.key` 有外键（authz.py 模型 38-39 行），而 `mdm.vendor.write` 这条 `permission_defs` 是由**脚本** `seed_phase2_keys.py` 播种、不是迁移。裸 alembic 迁移在「只跑迁移未跑 seed 脚本」的全新库（CI/test）里会因外键缺失而失败。用脚本、在 seed 之后运行，与代码库现有「authz 数据靠脚本」的惯例一致。

**方向：仅单向（vendor_master → mdm.vendor.write）**，见 §5 finance_manager 决策。

### 4.3 前端合并成一个勾（AccessControl.tsx）

文件：`portal/src/pages/admin/AccessControl.tsx`，`permissions` memo（当前 197-200 行）。

在渲染矩阵时**过滤掉 `mdm.vendor.write` 这一行**，使矩阵里只剩 EPMS 组的 "Vendor Master" 一个勾控制整条 Vendor 链路：

```ts
const permissions = useMemo(
  () => [...(defsQ.data?.permissions ?? [])]
    .filter((p) => p.key !== 'mdm.vendor.write')  // 合并进 "Vendor Master"，由后端联动授予
    .sort((a, b) => a.sort - b.sort),
  [defsQ.data],
)
```

要点：
- 纯渲染过滤，不动数据层，不影响其它读矩阵的消费者（如 booking 前端读 `role_matrix`）。
- MDM 组仍有 "Edit Finance Master Data"（`mdm.finance.write`），组头不会空。
- 用常量而非硬编码散落，便于将来对齐。

### 4.4 测试（TDD）

- **identity 单测**（`identity-api/tests/`）覆盖 `patch_matrix` 联动：
  1. delta 打勾 `vendor_master` → DB 中该角色同时出现 `vendor_master` 与 `mdm.vendor.write`。
  2. delta 取消 `vendor_master` → 两者同时删除。
  3. delta 只动其它 key（不含 `vendor_master`）→ `mdm.vendor.write` 不受影响。
  4. 幂等：重复打勾不报错、不产生重复行。
  5. 锁场景：`vendor_master` 被锁时取消操作仍按现有 409 行为，联动不绕过锁。
- **回填脚本测试**：在只有 `vendor_master` 授予的库上运行后，对应角色获得 `mdm.vendor.write`；重复运行幂等；无 `vendor_master` 的角色（如 finance_manager）不被反向触碰。

## 5. finance_manager 处理（已定：方案 a — 保持不动）

`finance_manager` 有 `mdm.vendor.write` 但无 `vendor_master`。合并成一个勾后，它那格显示未勾，但 DB 里仍有独立的 `mdm.vendor.write` 行 → **仍能改 Vendor**（该行被 UI 隐藏，成为「隐形授予」）。

**采用方案 (a)**：回填只走单向（`vendor_master → mdm.vendor.write`），`finance_manager` 原样保留，**零能力变更**。唯一代价：合并后的那格对 finance_manager 显示未勾（几乎无人会去审 finance_manager 这一格）。§4.1 的联动也只在切换 `vendor_master` 时触发，天然不碰 finance_manager 的独立授予。

（已否决 (b) 反向补 `vendor_master`：会扩大 finance_manager 的 EPMS Vendor 管理权限；已否决 (c) 删除 finance_manager 的 `mdm.vendor.write`：会削掉其现有改 Vendor 能力。）

## 6. 影响面 / 边界

- **服务**：identity-api（后端联动 + 回填脚本）、portal（前端过滤）。**不动** epms-api、mdm-api 的网关代码。
- **迁移**：无 alembic 迁移；仅一支幂等回填脚本（部署时作为一次性命令运行，见 §7）。
- **兼容**：`mdm.vendor.write` 仍是独立 `permission_defs` 行，`get_defs` 仍返回它（仅前端不渲染）；直接调 identity API 者不受影响。
- **权限收紧/放开**：现有持 `vendor_master` 的角色（procurement_officer / procurement_manager / vendor_manager）将获得 `mdm.vendor.write`（= 改 Vendor 能力），这是修复目标本身；无非预期越权。

## 7. 发布 / 部署（遵循并行开发纪律与发布流程）

1. 本分支自测通过后，按发布单点汇合：合入 main → 全 15 镜像同 sha（identity-api、portal 为真建，其余 retag）。
2. app server 无 ssh，给命令清单：`git pull` → 改 `.env` TAG → `--env-file .env pull` → **无 alembic 迁移，不跑 migrate-prod.sh** → 运行一次性回填命令（在 identity 容器内）：
   `docker compose exec identity-api python -m scripts.backfill_vendor_master_coupling`
   → `--profile edge up -d`。
3. 冒烟：① 用「Purchase officer」角色用户新建 Vendor 能保存成功；② Access Control 矩阵里 MDM 组不再出现 "Edit Vendor Master Data"，"Vendor Master" 单勾即可；③ 现有已勾 vendor_master 的角色回填后立即可用。
4. R5：push / 发布前复核生产当前 TAG（勿默认 == origin/main）。

## 8. 非目标（Out of scope）

- 不动 mdm 网关语义、不加通用权限隐含/捆绑框架。
- 不处理 `mdm.finance.write` 或其它 MDM 权限的类似合并（如需另开）。
- 不清理 epms-api 里已退休的硬编码 `PERMISSION_KEYS` 死路径（无害，另议）。
