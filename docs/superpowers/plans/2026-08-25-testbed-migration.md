# 迁移到 Ubuntu 服务器 + 团队远程开发 — 实施计划

> **For agentic workers:** 本计划的多数步骤是**主机管理与物理操作**(装系统、传文件、配用户),
> 必须由人在两端执行, 不适合全程交给子代理。可交给代理的只有 Task 2(代码改动)与各 Task 的验证命令。
> 步骤用 `- [ ]` 复选框跟踪。

**Goal:** 把 UniOps 全部开发测试环境从工作笔记本迁到 Ubuntu 虚拟机, 让同事能通过 IP 访问测试,
并让 2-3 名开发者各自用 VS Code + Claude Code 远程改调服务器上的代码。

**Architecture:** Ubuntu Server 22.04.5 + 原生 Docker Engine, 代码放 `/srv/uniops`(组共享)。
同一份 compose 文件靠 `STACK_PREFIX` + 端口 override + `LAN_HOST` 分出 4 套栈:
1 套主环境(钉「生产当前发布版」, 占标准端口, 对内网开放, 同事测试用) + 每位开发者 1 套。传输走 tar 打包直传, 不经过 git。

**Tech Stack:** Ubuntu 22.04、Docker Engine + compose plugin、PostgreSQL 15、alembic、Vite 8、OpenSSH、tar/scp

**Spec:** `docs/superpowers/specs/2026-08-25-testbed-migration-design.md`

## Global Constraints

- 服务器路径统一 `/srv/uniops`(对应原 `C:\Project`), 属主 `root:uniops`, 权限 `2775`(setgid)
- **笔记本这套全程不删**, 服务器跑通一周后才清理 —— 这是唯一的回退路径
- 迁移期间**冻结开发**, 不要两处同时改代码
- 生产库 `10.10.50.20` **全程只读**, 只做 `pg_dump`
- Windows 侧命令在 Git Bash 里跑; 给容器传 `/tmp/xxx` 路径时先 `export MSYS_NO_PATHCONV=1`
- 服务器上装的是 **Docker Engine**, 不是 Docker Desktop —— Docker Desktop 绑定单桌面用户, 多人 SSH 用不了
- 任何"验证"都要**正面证据**: 有预期输出才算通过, "没报错"不算
- 端口段分配: 测试栈 `5173-5179 / 8000-8011`; 开发栈依次 `+100`(`5273-5279 / 8100-8111`、
  `5373-5379 / 8200-8211`、`5473-5479 / 8300-8311`)

---

### Task 1: 笔记本侧 — 冻结、固化拓扑、记录基线

把即将丢失的隐性信息落成文件。**这些文件会随打包一起过去, 是服务器上复原的唯一依据。**

**Files:**
- Create: `C:\Project\uniops\db-snapshots\dev-stack-topology-20260825.txt`
- Create: `C:\Project\uniops\db-snapshots\unpushed-baseline-20260825.txt`

- [ ] **Step 1: 停止在多处同时开工**

关掉所有 Claude Code 会话与编辑器, 迁移期间只在笔记本上操作。

- [ ] **Step 2: 固化当前 dev 栈的真实挂载拓扑**

最关键的一步。当前栈的源码来自 5 棵不同的树, **没有任何 compose 文件记录这个组合**。

```bash
cd /c/Project/uniops
for c in $(docker ps -a --format '{{.Names}}' | grep '^uniops_'); do
  echo "### $c"
  docker inspect -f '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}} => {{.Destination}}
{{end}}{{end}}' "$c"
done | sed 's|\\|/|g' > db-snapshots/dev-stack-topology-20260825.txt
cat db-snapshots/dev-stack-topology-20260825.txt
```

预期: 20 个容器段落, 能看到 `uniops-vendor-credit` / `uniops-tabs` / `uniops-mrp-phase0` /
`uniops-delegation` / `uniops-nc-po-edit` / `uniops` 六个来源, 且每个服务除 `/app` 外还有
`/packages/authz` 或 `/packages/shell` 的挂载。

- [ ] **Step 3: 记录未 push 的 commit 基线**

```bash
cd /c/Project/uniops
{
  echo "uniops(主) unpushed: $(git log --oneline origin/main..HEAD | wc -l)"
  echo "uniops-release unpushed: $(git -C /c/Project/uniops-release log --oneline origin/main..HEAD | wc -l)"
  echo "uniops-nc-price unpushed: $(git -C /c/Project/uniops-nc-price log --oneline origin/main..HEAD | wc -l)"
  echo "worktree 总数: $(git worktree list | wc -l)"
  echo "--- 全部本地分支 ---"
  git branch -a --format='%(refname:short) %(objectname:short)'
} > db-snapshots/unpushed-baseline-20260825.txt
head -5 db-snapshots/unpushed-baseline-20260825.txt
```

预期: `17` / `2` / `1` / `62`。数字不同说明有会话动过, 先查清再继续。

- [ ] **Step 4: 记录测试套件的失败基线(大小写敏感问题的唯一探针)**

Linux 区分大小写而 NTFS 不区分, 可能有"引用了错误大小写却一直没暴露"的代码。
迁移后要用同一套测试比对失败**集合**(只比数字会误判):

```bash
cd /c/Project/uniops/epms-api
# 按 reference_uniops_epms_test_invocation 的姿势跑, 覆盖 POSTGRES_* 到本地 docker
pytest -q 2>&1 | tail -30 > /c/Project/uniops/db-snapshots/epms-api-test-baseline-20260825.txt
grep -E "^(FAILED|ERROR)" -r . 2>/dev/null | head -0
tail -5 /c/Project/uniops/db-snapshots/epms-api-test-baseline-20260825.txt
```

同样对 finance-api 跑一次, 存成 `finance-api-test-baseline-20260825.txt`。
**记下失败用例的完整 id 列表**, 不只是数字。

---

### Task 2: 笔记本侧 — 代码改动(LAN 化 + 多栈支持 + 关掉 polling)

在笔记本上先改先验证, 改好的文件随打包过去, 服务器上不用再改。

**Files:**
- Modify: `docker-compose.dev.yml`(57 行 LAN + 20 行 container_name + MailHog)
- Create: `make-stack-env.sh` —— 每套栈的 .env 生成器
- Create(可选): `docker-compose.devtopology.override.yml` —— 仅当要复原笔记本旧拓扑时
- Modify: `epms/vite.config.ts`, `portal/`, `oa/`, `vms/`, `finance/`, `booking/`, `mrp/` 各一个

**Interfaces:**
- Produces: 环境变量 `LAN_HOST`(默认 `localhost`)与 `STACK_PREFIX`(默认 `uniops`),
  供 Task 7 / Task 8 在各栈的启动脚本里设值

- [x] **Step 1: 备份原文件(不要用 git stash)**

`git stash` 是仓库级共享的, 在 worktree 里 pop 会弹出别的会话的 WIP。用普通拷贝:

```bash
cd /c/Project/uniops
cp docker-compose.dev.yml docker-compose.dev.yml.bak-20260825
```

- [x] **Step 2: 两条参数化 sed**

第一条**只改 `VITE_*` 和 `ALLOWED_ORIGINS` 两类行**。healthcheck 里的
`http://localhost:8000/api/v1/health` 是容器**自检**, 改了会让全部容器立刻 unhealthy ——
这条已在 scratchpad 空跑验证, 精确命中 57 行、healthcheck 零误伤:

```bash
cd /c/Project/uniops
sed -i -E '/^[[:space:]]+(VITE_[A-Z_0-9]+|ALLOWED_ORIGINS):/ s|http://localhost:|http://\$\{LAN_HOST:-localhost\}:|g' docker-compose.dev.yml
```

第二条修多栈并存的阻塞项: 20 个服务写死 `container_name: uniops_*`,
而 **`container_name` 全局唯一、不受 project name 隔离** ——
不改的话第二套栈会报 `container name "/uniops_postgres" is already in use`:

```bash
cd /c/Project/uniops
sed -i -E 's|^([[:space:]]+container_name: )uniops_|\1\$\{STACK_PREFIX:-uniops\}_|' docker-compose.dev.yml
```

- [x] **Step 3: 验证两条替换的范围都正确**

**不要照抄任何绝对数字。** 这些计数随 main 变化 —— 实测同一天笔记本主 checkout 是 57 行,
而 `386d257` 上是 59 行(main 期间新增了 `VITE_MRP_API_URL` 与 `VITE_VMS_URL`)。
下面改成**自比对**: 拿改动前的备份当基线, 版本无关。

```bash
cd /c/Project/uniops
BEFORE=$(grep -cE '^[[:space:]]+(VITE_[A-Z_0-9]+|ALLOWED_ORIGINS):.*http://localhost:' docker-compose.dev.yml.bak-20260825)
AFTER=$(grep -c 'LAN_HOST' docker-compose.dev.yml)
echo "改前该参数化的行数: $BEFORE"
echo "改后已参数化的行数: $AFTER   $([ "$BEFORE" = "$AFTER" ] && echo '✅ 相等' || echo '❌ 不等')"

HC_BEFORE=$(grep -c 'test:.*localhost' docker-compose.dev.yml.bak-20260825)
echo "healthcheck 改前/改后: $HC_BEFORE / $(grep -c 'test:.*localhost' docker-compose.dev.yml)  (必须相等)"
echo "healthcheck 被误伤(必须为 0): $(grep -c 'test:.*LAN_HOST' docker-compose.dev.yml)"

CN_BEFORE=$(grep -c 'container_name: uniops_' docker-compose.dev.yml.bak-20260825)
echo "container_name 改前 $CN_BEFORE / 改后已参数化 $(grep -c 'container_name: \${STACK_PREFIX' docker-compose.dev.yml)  (必须相等)"
echo "container_name 残留写死(必须为 0): $(grep -c 'container_name: uniops_' docker-compose.dev.yml)"

echo "--- 漏网的裸 localhost(排除 healthcheck 与注释, 必须为空) ---"
grep -nE "http://localhost:" docker-compose.dev.yml | grep -v 'test:' | grep -vE "^\s*[0-9]+:\s*#" | grep -v 'LAN_HOST'
```

预期: 三组"相等"、两个 0、最后一行无输出。
任何一项对不上就 `cp docker-compose.dev.yml.bak-20260825 docker-compose.dev.yml` 回滚重来。

- [x] **Step 4: 确认默认行为逐字不变**

最强的一条证据: 不设任何变量时, compose **解析后的完整结果**与改动前逐字相同。
这比数行数可靠得多 —— 它覆盖了所有服务的所有字段, 不依赖任何计数。

```bash
cd /c/Project/uniops
diff <(docker compose -f docker-compose.dev.yml.bak-20260825 --env-file .env config 2>/dev/null)      <(docker compose -f docker-compose.dev.yml --env-file .env config 2>/dev/null)   && echo "✅ 解析结果完全一致 —— 真默认值, 对现有用法零影响"

# 再确认变量确实能生效
STACK_PREFIX=uniops-test docker compose -f docker-compose.dev.yml --env-file .env config 2>/dev/null | grep 'container_name: uniops-test_postgres'
LAN_HOST=10.0.0.9 docker compose -f docker-compose.dev.yml --env-file .env config 2>/dev/null | grep -m1 'VITE_API_URL: http://10.0.0.9'
```

预期: 先输出"完全一致", 再分别出现 `uniops-test_postgres` 与 `http://10.0.0.9`。
**注意**: 此步在 Step 4b / Step 5 加入 postgres 调优和 MailHog **之前**做 ——
那两项是有意的新增, 加完之后 diff 当然不再为空。

- [x] **Step 4b: 为机械盘阵列调 postgres**

服务器落在机械盘阵列上(与生产同构)。生产跑烤好的镜像、磁盘几乎不动, 所以"生产不卡"成立;
但 dev 栈多了 `npm install`、Vite 预打包和**测试套件的高频事务提交**, 后者是机械盘上唯一
需要主动处理的点 —— WAL 的 fsync 延迟。

在 `docker-compose.dev.yml` 的 `postgres` 服务加 `command`:

```yaml
    command: >
      postgres
      -c synchronous_commit=off
      -c shared_buffers=2GB
      -c effective_cache_size=8GB
      -c max_wal_size=4GB
```

`synchronous_commit=off` 的代价: 机器突然断电时可能丢最后几百毫秒的事务。
**对一个随时能从 dump 重建的测试库完全可以接受**, 而在机械盘上通常让写密集的测试快数倍。
生产绝不能这么设, 测试栈应该这么设。

`shared_buffers` 默认只有 128 MB, 对 48 GB 的机器太小; 4 套栈 × 2 GB = 8 GB, 在预算内。

验证:

```bash
cd /c/Project/uniops
docker compose -f docker-compose.dev.yml --env-file .env config | grep -A2 "synchronous_commit"
```

预期: 能看到该参数。服务器上起栈后再正面确认一次:

```bash
docker exec uniops-test_postgres psql -U epms -tAc "show synchronous_commit; show shared_buffers;"
```

预期: `off` 与 `2GB`。

- [x] **Step 5: 加 MailHog 服务**

在 `docker-compose.dev.yml` 的 `services:` 末尾加:

```yaml
  mailhog:
    image: mailhog/mailhog:latest
    container_name: ${STACK_PREFIX:-uniops}_mailhog
    restart: unless-stopped
    ports:
      - "${MAILHOG_SMTP_PORT:-1025}:1025"
      - "${MAILHOG_UI_PORT:-8025}:8025"
```

**★ 只有两个服务从环境变量读 SMTP。** 逐服务核查的结果(不要凭印象):

| 服务 | SMTP 来源 | 这一步要做什么 |
|---|---|---|
| `epms-api`、`identity-api` | **环境变量** | 加下面两行 |
| `finance-api`、`vms-api`、`booking-api` | **数据库 `company_config` 表** | 环境变量对它们无效, 见 Task 7 Step 11b |
| `expense-api`、`approval-api` | 不发邮件(实测无发信代码) | 无需处理 |

给 `epms-api` 和 `identity-api` 的 `environment:` 加:

```yaml
      # 测试/开发栈: 所有外发邮件进 MailHog, 绝不外发
      SMTP_HOST: mailhog
      SMTP_PORT: 1025
```

验证(用解析后的结果查, 不要 grep 源文件):

```bash
cd /c/Project/uniops
python - <<'PYCHK'
import subprocess, yaml
out = subprocess.run(["docker","compose","-f","docker-compose.dev.yml","--env-file",".env","config"],
                     capture_output=True, text=True).stdout
for name, svc in (yaml.safe_load(out).get("services") or {}).items():
    env = svc.get("environment") or {}
    if isinstance(env, dict) and "SMTP_HOST" in env:
        print(f"  {name}: SMTP_HOST={env['SMTP_HOST']}")
PYCHK
```

预期: 恰好两行, `epms-api` 与 `identity-api`, 都是 `mailhog`。

- [x] **Step 6: 7 个前端加 allowedHosts 并关掉 usePolling**

`usePolling: true` 是为 Windows bind mount 开的, 实测让**每个前端常驻占 12% CPU**
(7 个 ≈ 1 整核/栈, 4 套栈 ≈ 4 核纯浪费)。Linux 原生 inotify 不需要它。
改成 env 驱动, 默认关闭, 万一还要在 Windows 上跑就设 `VITE_POLL=1`:

```ts
server: {
  allowedHosts: true,                              // 内网 dev/test 栈; 不要用于生产
  watch: { usePolling: process.env.VITE_POLL === '1' },
  // ...原有 port / proxy 等保持不变
}
```

`portal/vite.config.ts` 现在是单行写法 `server: { port: 5174, watch: { usePolling: true } }`,
改成 `server: { port: 5174, allowedHosts: true, watch: { usePolling: process.env.VITE_POLL === '1' } }`。

- [x] **Step 7: 验证 7 个前端都改到了**

```bash
cd /c/Project/uniops
echo "allowedHosts(应为 7): $(grep -l 'allowedHosts' epms/vite.config.ts portal/vite.config.ts oa/vite.config.ts vms/vite.config.ts finance/vite.config.ts booking/vite.config.ts mrp/vite.config.ts | wc -l)"
echo "VITE_POLL(应为 7): $(grep -l 'VITE_POLL' epms/vite.config.ts portal/vite.config.ts oa/vite.config.ts vms/vite.config.ts finance/vite.config.ts booking/vite.config.ts mrp/vite.config.ts | wc -l)"
echo "残留写死 usePolling: true(必须为 0): $(grep -l 'usePolling: true' */vite.config.ts 2>/dev/null | wc -l)"
```

预期: `7` / `7` / `0`。

- [ ] **Step 8:(可选, 已跳过)写 5 树拓扑 override**

**这一步不是迁移的必要条件, 默认可以跳过。**

原设计要在服务器上复现笔记本那套"源码来自 5 棵不同树"的拓扑。用户明确要求
**主环境跟生产当前发布版一致**(设计 D5)后, 那个 Frankenstein 组合就不再是起栈的前提 ——
所有栈都从一份干净的 `<PROD_SHA>` checkout 起, 各人再切到自己的分支。

Task 1 Step 2 抓下来的 `dev-stack-topology-20260825.txt` 仍然要带走, 作为**历史记录**:
万一将来要复原笔记本上某次实验的确切状态, 它是唯一的依据。

只有在那种情况下才需要下面这个 override(路径已改成 Linux 的):

```bash
cat > /c/Project/uniops/docker-compose.devtopology.override.yml <<'YAML'
# 开发栈专用: 复现 2026-08-25 笔记本上的真实挂载拓扑(源码来自 5 棵不同的树)。
# 依据: db-snapshots/dev-stack-topology-20260825.txt
# compose 按挂载目标路径合并 volumes, 所以这里的 /app 会覆盖基础文件里的 ./xxx:/app。
# 未列出的服务(booking-api, vms-api)走基础文件的主 checkout, 无需覆盖。
services:
  epms-api:         { volumes: ["/srv/uniops/uniops-vendor-credit/epms-api:/app"] }
  finance-api:      { volumes: ["/srv/uniops/uniops-vendor-credit/finance-api:/app"] }
  expense-api:      { volumes: ["/srv/uniops/uniops-vendor-credit/expense-api:/app"] }
  epms-frontend:    { volumes: ["/srv/uniops/uniops-vendor-credit/epms:/app"] }
  finance-frontend: { volumes: ["/srv/uniops/uniops-vendor-credit/finance:/app"] }
  oa-frontend:      { volumes: ["/srv/uniops/uniops-tabs/oa:/app"] }
  booking-frontend: { volumes: ["/srv/uniops/uniops-tabs/booking:/app"] }
  vms-frontend:     { volumes: ["/srv/uniops/uniops-tabs/vms:/app"] }
  mrp-frontend:     { volumes: ["/srv/uniops/uniops-tabs/mrp:/app"] }
  mrp-api:          { volumes: ["/srv/uniops/uniops-mrp-phase0/mrp-api:/app"] }
  mdm-api:          { volumes: ["/srv/uniops/uniops-mrp-phase0/mdm-api:/app"] }
  budget-api:       { volumes: ["/srv/uniops/uniops-mrp-phase0/budget-api:/app"] }
  file-api:         { volumes: ["/srv/uniops/uniops-mrp-phase0/file-api:/app"] }
  portal-frontend:  { volumes: ["/srv/uniops/uniops-delegation/portal:/app"] }
  approval-api:     { volumes: ["/srv/uniops/uniops-delegation/approval-api:/app"] }
  identity-api:     { volumes: ["/srv/uniops/uniops-nc-po-edit/identity-api:/app"] }
YAML
```

上面的 `/app` 映射是实测的, 直接可用。**`/packages/*` 那几行必须对照
`dev-stack-topology-20260825.txt` 补全** —— 抓取时只记录了 `/app`。
是否真的复现对了, 由 Task 7 Step 3 的 diff 精确判定, 不靠肉眼。

- [x] **Step 9: 参数化宿主端口, 并写 .env 生成器**

原设计是给每套栈写一个端口 override 文件。**实施时改成了更安全的做法**, 原因是:

容器内端口永远不变, 变的只是宿主映射。但 `VITE_*` 里写的是**宿主端口** ——
如果只 override `ports:` 而不同步改 `VITE_*`, 开发栈的页面会去打标准端口, 也就是**主环境**,
数据看起来正常但其实是别人的库。这是最难发现的一种串台。

解法是让两者**共用同一批变量**, 结构上就不可能对不上:

```bash
cd /c/Project/uniops
python - <<'PYEOF'
import io, re
PORTS = {
    "5432": "POSTGRES_PORT", "6379": "REDIS_PORT",
    "8000": "EPMS_API_PORT", "8002": "MDM_API_PORT", "8003": "APPROVAL_API_PORT",
    "8004": "FINANCE_API_PORT", "8005": "FILE_API_PORT", "8006": "EXPENSE_API_PORT",
    "8007": "BUDGET_API_PORT", "8008": "VMS_API_PORT", "8009": "IDENTITY_API_PORT",
    "8010": "BOOKING_API_PORT", "8011": "MRP_API_PORT",
    "5173": "EPMS_PORT", "5174": "PORTAL_PORT", "5175": "OA_PORT",
    "5176": "VMS_PORT", "5177": "FINANCE_PORT", "5178": "BOOKING_PORT", "5179": "MRP_PORT",
}
p = "docker-compose.dev.yml"
lines = io.open(p, encoding="utf-8").read().split("
")
n_ports = n_urls = 0
for i, ln in enumerate(lines):
    m = re.match(r'^(\s+- ")(\d+)(:\d+".*)$', ln)          # ports: 只改宿主侧
    if m and m.group(2) in PORTS:
        lines[i] = f'{m.group(1)}${{{PORTS[m.group(2)]}:-{m.group(2)}}}{m.group(3)}'
        n_ports += 1
        continue
    if re.match(r'^\s+(VITE_[A-Z_0-9]+|ALLOWED_ORIGINS):', ln):   # 只改浏览器可见的 URL
        def sub(mm):
            global n_urls
            if mm.group(1) not in PORTS: return mm.group(0)
            n_urls += 1
            return f'${{{PORTS[mm.group(1)]}:-{mm.group(1)}}}'
        lines[i] = re.sub(r'(?<=:)(\d{4})(?=[/"\s\],]|$)', sub, ln)
io.open(p, "w", encoding="utf-8", newline="
").write("
".join(lines))
print(f"ports 映射 {n_ports} 处, 浏览器可见 URL 里的端口 {n_urls} 处")
PYEOF
```

实测输出: `ports 映射 20 处, 浏览器可见 URL 里的端口 125 处`。
**内部 service-to-service 的 URL(如 `http://epms-api:8000`)不受影响** —— 它们不在
`VITE_*` / `ALLOWED_ORIGINS` 行上, 走的是容器网络, 端口本来就不该变。

然后写生成器 `make-stack-env.sh`(内容见仓库同名文件), 用法:

```bash
./make-stack-env.sh 0 10.10.50.64 > /srv/uniops/uniops-prod/.env   # 主环境, 标准端口
./make-stack-env.sh 1 10.10.50.64 > .env.dev1                      # 开发栈 1, 端口 +100
./make-stack-env.sh 2 10.10.50.64 > .env.dev2                      # 开发栈 2, 端口 +200
```

索引 `i` 的端口 = 标准端口 + `i*100`, `STACK_PREFIX` 自动是 `uniops-test`(i=0)或 `uniops-dev<i>`。

- [x] **Step 10: 验证多套栈之间零冲突**

这是整个多栈设计的决定性验证 —— 不是"能解析"就行, 而是**互相之间不能撞**:

```bash
cd /c/Project/uniops
for i in 0 1 2; do
  ./make-stack-env.sh $i 10.10.50.70 > /tmp/env$i
  docker compose -f docker-compose.dev.yml --env-file /tmp/env$i config 2>/dev/null     | grep -E "container_name:|published:" > /tmp/stack$i.txt
done
echo "容器重名(必须为空):"; cat /tmp/stack{0,1,2}.txt | grep container_name | sort | uniq -d
echo "端口撞车(必须为空):"; cat /tmp/stack{0,1,2}.txt | grep published | sort | uniq -d
for i in 0 1 2; do echo "栈$i: $(grep -c published /tmp/stack$i.txt) 端口 / $(grep -c container_name /tmp/stack$i.txt) 容器"; done
```

实测结果: 两个"必须为空"都为空, 三套栈各 **21 容器 / 22 端口**。

- [x] **Step 11: 提交(需 Justin 同意后再执行)**

主 checkout 当前在 `test/mrp-1c-local` 且带 26 个脏文件。**不要直接提交到那个分支。**

```bash
cd /c/Project/uniops
git checkout -b chore/multi-stack-lan-dev-server
git add docker-compose.dev.yml make-stack-env.sh \
  epms/vite.config.ts portal/vite.config.ts oa/vite.config.ts vms/vite.config.ts \
  finance/vite.config.ts booking/vite.config.ts mrp/vite.config.ts \
  docs/superpowers/specs/2026-08-25-testbed-migration-design.md \
  docs/superpowers/plans/2026-08-25-testbed-migration.md
git commit -m "chore: support multi-stack LAN dev server (STACK_PREFIX + LAN_HOST + native inotify)"
```

---

### Task 3: 笔记本侧 — 数据库导出

导两份 dump。**不预先挑 MRP 表清单** —— 直接导整个 dev 库, 服务器上按需从里面挑表灌回。

- [ ] **Step 1: 导出笔记本 dev 库全量(含那 23 张 MRP 表)**

```bash
export MSYS_NO_PATHCONV=1
docker exec uniops_postgres pg_dump -U epms -d epms -Fc -f /tmp/local_dev_full.dump
docker cp uniops_postgres:/tmp/local_dev_full.dump /c/Project/uniops/db-snapshots/local_dev_full_20260825.dump
ls -la /c/Project/uniops/db-snapshots/local_dev_full_20260825.dump
```

预期: 文件存在, 约 45-55 MB。

- [ ] **Step 2: 从生产导全量快照(只读, 约 40 秒)**

```bash
export MSYS_NO_PATHCONV=1
DB_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2)
docker exec -e PGPASSWORD="$DB_PASSWORD" uniops_postgres \
  pg_dump -h 10.10.50.20 -U epms -d epms -Fc -f /tmp/epms_prod.dump
docker cp uniops_postgres:/tmp/epms_prod.dump /c/Project/uniops/db-snapshots/epms_prod_20260825.dump
ls -la /c/Project/uniops/db-snapshots/epms_prod_20260825.dump
```

预期: 文件存在, 不小于 51 MB。

- [ ] **Step 3: 记录表数基线**

```bash
export MSYS_NO_PATHCONV=1
{
  echo "本地 dev 库表数: $(docker exec uniops_postgres psql -U epms -d epms -tAc "select count(*) from information_schema.tables where table_schema='public'")"
  echo "本地 mrp_* 表数: $(docker exec uniops_postgres psql -U epms -d epms -tAc "select count(*) from information_schema.tables where table_schema='public' and table_name like 'mrp%'")"
} | tee /c/Project/uniops/db-snapshots/db-baseline-20260825.txt
```

预期: 本地约 164 张表。**记下这两个数**, Task 7 要用。

---

### Task 4: 服务器 — 装 Ubuntu 与基础环境 ✅ 全部完成 2026-08-25

**实际环境**(与初版设计的差异已在此更正):

| 项 | 实际值 |
|---|---|
| 虚拟化 | VMware, VMware Paravirtual SCSI + VMXNET3 |
| 规格 | 12 vCPU / 48 GB / 700 GB(机械盘阵列) |
| 系统 | **Ubuntu 22.04.5 LTS**(初版设计写的是 24.04; 22.04 逐条核对可用, 见下) |
| 主机名 | `uniops-dev` |
| IP | **10.10.50.64/24**(ens192, **静态**) |
| 时区 | America/Toronto (EDT, -0400), NTP 已同步 |
| 管理员 | `crmadmin` |
| 磁盘 | `/` 100 G + `/boot` 2 G + **`/var/lib/docker` 400 G 独立 LV** + 卷组留 198 G 未分配 |

**为什么 22.04 可用**: Docker CE 官方仓库支持 jammy(下面的 `$VERSION_CODENAME` 自动解析);
内核 6.8 HWE; cgroup v2 默认开启; 标准支持到 2027-04。所有服务都在容器里跑, 宿主 Python 版本差异碰不到。

**为什么 `/var/lib/docker` 单独一个卷**: Docker 是唯一会失控增长的东西, 而本项目**已经因此出过事**
(`import_pms/data` 2 GB 被烤进镜像导致磁盘耗尽)。放在 `/` 里的话, 一次失控的构建缓存会填满根分区,
然后 SSH 登不进去、日志写不了、整机瘫痪。单独一个卷的话最坏只是 Docker 不可用, 系统还在。
卷组留白是为了将来一条 `sudo lvextend -r -L +100G /dev/ubuntu-vg/docker-lv` 在线扩容。

- [x] **Step 1: 装系统 + 基础确认**

```bash
hostnamectl                       # 主机名 uniops-dev, 不能带下划线(下划线不是合法 DNS 标签)
ip -4 addr show | grep inet       # 10.10.50.64/24
sudo timedatectl set-timezone America/Toronto
timedatectl                       # 必须设: 时区错会让日志与数据库时间戳全部错位
sudo cat /etc/netplan/*.yaml      # 确认是静态 IP 而非 dhcp4: true
```

**★ IP 必须固定** —— 一变则四套栈的 `LAN_HOST` 全部失效, 同事全部打不开。

- [x] **Step 2: 装 Docker Engine(不是 Docker Desktop, 更不是 snap 版)**

snap 版 Docker 有严格的路径限制(只能访问 `/home` 下的目录), 会和 `/srv/uniops` 冲突。

```bash
sudo apt update && sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable"   | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

验证:

```bash
sudo docker run --rm hello-world
docker compose version
docker info | grep -E "Docker Root Dir|Storage Driver"
df -h /var/lib/docker
```

预期: `Docker Root Dir: /var/lib/docker`、`Storage Driver: overlay2`;
`df` 显示 `/dev/mapper/ubuntu--vg-docker--lv` 约 400 G。
**如果 `df` 显示的是 `/` 那个 100 G 的卷, 说明挂载没生效, 先别往下走。**

- [x] **Step 3: 内核参数**

```bash
sudo tee /etc/sysctl.d/60-uniops.conf > /dev/null <<'EOF'
# 4 套栈 × 7 个 Vite 会监听大量文件, 默认上限会让 HMR 静默失效
fs.inotify.max_user_watches=524288
fs.inotify.max_user_instances=1024
# 多套栈 + 多用户, 提高文件句柄上限
fs.file-max=2097152
EOF
sudo sysctl --system
sysctl fs.inotify.max_user_watches fs.inotify.max_user_instances
```

预期: `524288` 与 `1024`。

- [x] **Step 4: 用户与组**

初期 2 人 = 主环境栈 + 2 套开发栈(索引 0/1/2)。第三位以后再加。

```bash
sudo groupadd -f uniops
sudo usermod -aG uniops,docker crmadmin
# 第二位开发者 amir(2026-08-26 由管理员用密码方式建好, 此处只需补两个组)
sudo usermod -aG uniops,docker amir
id crmadmin; id amir
```

**★ 建了用户不等于能干活。** 不加 `docker` 组连不上 daemon(`permission denied ... docker.sock`),
不加 `uniops` 组写不了 `/srv/uniops`。两个都要, 且**改完必须重新登录才生效**。

由本人验证:

```bash
docker ps                                        # 不带 sudo 能出表头
touch /srv/uniops/_t && rm /srv/uniops/_t        # 能写
```

**★ 组变更要重新登录才生效。** 退出重连后:

```bash
docker ps
```

预期: 能列出且**不需要 sudo**。这一条正是 Windows + Docker Desktop 做不到的事,
也是 D2 选 Ubuntu 的核心原因 —— Docker Desktop 绑定单个桌面登录用户, 其他人 SSH 进来拿不到 docker。

- [x] **Step 5: 代码目录**

```bash
sudo mkdir -p /srv/uniops
sudo chgrp uniops /srv/uniops
sudo chmod 2775 /srv/uniops
ls -ld /srv/uniops
```

预期: `drwxrwsr-x ... root uniops` —— 注意那个 **`s`**(setgid),
它让任何人在此新建的文件自动继承 `uniops` 组, 多人协作才不会互相锁死。

- [x] **Step 6: SSH key 登录**

笔记本上生成(实测该机原本没有 key):

```bash
ssh-keygen -t ed25519 -C "crmadmin@uniops-dev" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

服务器上追加(**是 `>>` 不是 `>`**):

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "<公钥整行>" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

**★ 先验证 key 能登录, 再关密码登录** —— 顺序反了会把自己锁在外面。
笔记本上另开窗口测试 `ssh crmadmin@10.10.50.64 "hostname"`, **成功之后**再:

**★ 改主文件是没用的 —— 实测踩到。** Ubuntu Server 的 `/etc/ssh/sshd_config` 顶部有
`Include /etc/ssh/sshd_config.d/*.conf`, 而 cloud-init 在那里放了 `50-cloud-init.conf`,
内容就是 `PasswordAuthentication yes`。**OpenSSH 取第一个出现的值**, include 在最前面,
所以主文件里改的那行**根本轮不到**:

```
/etc/ssh/sshd_config:57:                      PasswordAuthentication no    ← sed 改成功了
/etc/ssh/sshd_config.d/50-cloud-init.conf:1:  PasswordAuthentication yes   ← 这个赢
```

正确改法是加一个**排序更靠前**的文件盖住它(不要去改 cloud-init 那个, 它可能被重新生成):

```bash
sudo tee /etc/ssh/sshd_config.d/00-uniops-hardening.conf > /dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
sudo systemctl restart ssh
sudo sshd -T | grep -iE "passwordauthentication|kbdinteractive"     # 两个都要是 no
```

`KbdInteractiveAuthentication` 也要关 —— 只关前者的话, 某些 PAM 配置下还能走键盘交互绕回密码。

**★ 本机的实际决定(2026-08-26): 密码登录已重新打开, key 与密码并存。**
理由是这是一台**纯内网服务器**, 不需要这一级的严格控制, 而多人多工具接入时密码回落更省事。
这是有意为之, **不是配置疏漏, 不要"顺手修好"**:

```bash
sudo sed -i 's/^PasswordAuthentication no/PasswordAuthentication yes/' /etc/ssh/sshd_config.d/00-uniops-hardening.conf
sudo systemctl restart ssh
sudo sshd -T | grep -iE "pubkeyauthentication|passwordauthentication"    # 两行都应是 yes
```

`PubkeyAuthentication` 与 `PasswordAuthentication` 是独立开关, 都 `yes` 即两种方式并存
(客户端先试 key, 失败回落密码)。若将来这台机器要接触内网以外的流量, 需要重新评估此决定。

**救援通道(与 SSH 配置无关)**: VMware 的 VM 控制台不走网络协议, 永远可进 ——
所以 `crmadmin` 的密码要留好, 它的用途是 sudo 与控制台救援。

**验证必须查 `sshd -T`(生效值), 不能 grep 配置文件** —— 这正是"命令跑对了却没生效"的典型:
只看 sed 没报错就以为关掉了, 等于留了个密码登录的口子还自以为安全。

(改的时候**保持一个已连上的 SSH 窗口别关**, 配错了还能补救。)

- [x] **Step 7: 防火墙 —— 但要知道它管不住 Docker**

```bash
sudo ufw allow 22/tcp
sudo ufw allow 5173:5179/tcp     # 主环境前端
sudo ufw allow 5273:5279/tcp     # 开发栈 1
sudo ufw allow 5373:5379/tcp     # 开发栈 2
sudo ufw allow 8000:8011/tcp     # 主环境 API
sudo ufw allow 8100:8111/tcp
sudo ufw allow 8200:8211/tcp
sudo ufw allow 8025,8125,8225/tcp   # MailHog
sudo ufw --force enable
sudo ufw status numbered
```

**★ ufw 管不住 Docker 发布的端口。** Docker 直接往 iptables 的 `DOCKER` 链插规则,
**优先于 ufw**。所以那些应用端口**不加 ufw 规则也能从局域网访问**, 而 `ufw deny 5173` **是无效的**。
真正被 ufw 保护的只有主机自身的服务(SSH)。

这是内网测试机, 可以接受。**但别以为 ufw 在给应用端口把关** ——
将来若要真正限制访问来源, 要写 `DOCKER-USER` 链, 不是 ufw。

- [x] **Step 9: 装 Claude Code(服务器侧)**

**初版计划漏了这一步。** 它是"团队各自 VS Code + Claude Code 改调服务器代码"这个需求的必要条件 ——
VS Code Remote-SSH 模式下 Claude Code 扩展跑在**服务器侧**, 服务器上没有 `claude` 命令整条链路就断了。

Ubuntu 22.04 自带的 Node 太老(Claude Code 要 18+), 先装 Node 22:

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
node --version && npm --version
sudo npm install -g @anthropic-ai/claude-code
claude --version && which claude
```

**全局装(`sudo npm -g`)是有意的**: 一次安装所有用户都能用; 凭据与配置各自存在自己的 `~/.claude`。

2026-08-25 实测: `claude` **2.1.246** 在 `/usr/bin/claude`, Node **v22.23.2**。

**★ 账号不能共享。** 个人订阅(Pro/Max)按人授权, 多人共用违反使用条款, 且并发会话会撞速率限制、
用量无法归因。两条正规路径:
- **Team/Enterprise 按席位**, 每人 `claude` 各自 `/login`
- **组织 API Key**(本项目已有 Console 账号) —— **给每人发独立的一把**, 各自写进自己的 `~/.bashrc`:
  `export ANTHROPIC_API_KEY='...'`。**不要写进 `/etc/environment` 等全局位置** ——
  那等于又变回共享, 还多一个所有用户可读的明文凭据。
  (`uniops/.env` 里那个 `ANTHROPIC_API_KEY` 是给应用容器的, 与开发者用 Claude Code 是两回事, 别混用。)

- [ ] **Step 10: 服务器上的 GitHub 凭据(每人各自配)**

git 操作发生在**服务器上**(代码在 `/srv/uniops`), 所以凭据要在服务器各自的家目录里, 不是笔记本上。
服务器自带 git **2.34.1**, 无需安装 —— 三项依赖实测均可用:
`worktree repair`(Task 6 修 62 个 worktree 路径靠它)、`safe.directory`、`core.sharedRepository`。
★ `git worktree -h` 的 usage 文本里**没有列出 `repair`**, 但实际能跑(exit 0) —— 看帮助会得出错误结论。

amir 已被加为仓库 collaborator, 因此**每人用自己的 GitHub 账号**, 不共用凭据。

**每人必做 —— 身份(决定 commit 作者署名, 与推送凭据无关)**:

```bash
git config --global user.name "<真实姓名>"
git config --global user.email "<各自的 GitHub 邮箱>"
```

不配的话提交会用 `<user>@uniops-dev`, GitHub 认不出是谁, 贡献统计和头像都对不上。

**推荐: SSH remote(无有效期问题)** —— 每人在服务器上生成一把给 GitHub 用的 key:

```bash
ssh-keygen -t ed25519 -C "$USER@uniops-dev-github" -f ~/.ssh/id_github
cat ~/.ssh/id_github.pub          # 加到各自的 GitHub 账号
cat >> ~/.ssh/config <<'EOF'
Host github.com
  IdentityFile ~/.ssh/id_github
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
ssh -T git@github.com             # 首次问指纹输 yes; 成功显示 "Hi <用户名>!"
```

**★ `ssh -T` 显示的名字必须是各自的账号** —— 显示成别人说明 key 配串了。

remote 换成 SSH(**仓库级设置, 改一次全员生效**; 代码搬过去后再做):

```bash
git -C /srv/uniops/uniops remote set-url origin git@github.com:wangjustincrm/UniOps.git
git -C /srv/uniops/uniops remote -v
```

**备选: 保持 HTTPS + 各自的 fine-grained PAT**

```bash
git config --global credential.helper store
# 首次 push 输 用户名 + 自己的 PAT, 然后立刻:
chmod 600 ~/.git-credentials
```

更简单, 但 PAT 会过期要重配。凭据以明文存在 `~/.git-credentials`;
服务器家目录是 `drwxr-x---`(750)且各属自己的组, 两个用户互相读不到, 因此可接受。


- [x] **Step 8: 从笔记本一次性收尾验证**

```bash
ssh -o BatchMode=yes crmadmin@10.10.50.64 "hostname; id; docker ps; df -h /var/lib/docker | tail -1; sysctl -n fs.inotify.max_user_watches; ls -ld /srv/uniops; free -g | head -2; nproc"
```

`BatchMode=yes` 禁止一切交互提示 —— 能成功就证明是**纯 key 认证**, 而不是"其实弹了密码框"。

**2026-08-25 实测结果(全部通过)**:

| 项 | 实测 |
|---|---|
| 主机名 / IP | `uniops-dev` / `10.10.50.64` 静态 |
| 组 | `999(docker)` + `1001(uniops)` |
| **非交互 SSH 下 `docker ps` 免 sudo 可用** | ✅ 这正是 Docker Desktop 做不到的事 |
| `/var/lib/docker` | `ubuntu--vg-docker--lv` 393 G(已用 260 K) |
| `/srv/uniops` | `drwxrwsr-x root uniops`(setgid) |
| inotify watches | 524288 |
| 内存 / CPU | 47 GB(可用 46) / 12 核 |
| ufw | active, 16 条规则; 开启后 SSH 仍通 |
| 密码登录 | 已关(`sshd -T` 正面确认) |

**注意**: 非交互 SSH 里跑 `sudo` 会失败(`a terminal is required`), 需要 `ssh -t` 并仍会提示输密码。
服务器侧的 sudo 步骤在交互会话里跑最顺。


### Task 5: 传输

约 7 GB / 17 万文件。**成本主导是文件数** —— 先打成单个 tar 包再传, 比逐文件同步快一个数量级。

- [ ] **Step 1: 笔记本侧打包**

在 Git Bash 里:

```bash
cd /c/Project
tar -czf /d/uniops-migrate.tar.gz \
  --exclude='node_modules' --exclude='.venv' --exclude='__pycache__' \
  --exclude='.vite' --exclude='dist' --exclude='.pytest_cache' \
  --exclude='nchome' \
  .
ls -lh /d/uniops-migrate.tar.gz
```

(`/d/` 换成实际有空间的盘。)预期: 包大小 3-5 GB(压缩后)。

- [ ] **Step 2: 验证包内容完整**

```bash
tar -tzf /d/uniops-migrate.tar.gz | wc -l
tar -tzf /d/uniops-migrate.tar.gz | grep -c "^./uniops/.git/"
tar -tzf /d/uniops-migrate.tar.gz | grep -E "^\./(uniops/\.env|qbo_conn\.env|nc65_conn\.env|wms_conn\.env)$"
tar -tzf /d/uniops-migrate.tar.gz | grep -c "^./nchome/" || echo "0 (nchome 已排除, 正确)"
```

预期: 文件数约 17 万; `.git` 条目数 > 7000; 四个凭据文件都在; nchome 为 0。

- [ ] **Step 3: 传到服务器**

```bash
scp /d/uniops-migrate.tar.gz crmadmin@10.10.50.64:/tmp/
```

千兆网下 4 GB 约 1-2 分钟。

- [ ] **Step 4: 服务器上解包到 /srv/uniops**

```bash
sudo mkdir -p /srv/uniops
sudo tar -xzf /tmp/uniops-migrate.tar.gz -C /srv/uniops
ls /srv/uniops | head
du -sh /srv/uniops
```

预期: 能看到 `uniops`、`uniops-*` 等目录; 总大小约 7 GB。

- [ ] **Step 5: 设置属主与 setgid**

```bash
sudo chgrp -R uniops /srv/uniops
sudo chmod -R g+rw /srv/uniops
sudo find /srv/uniops -type d -exec chmod g+s {} \;
ls -ld /srv/uniops /srv/uniops/uniops
```

预期: 组是 `uniops`, 目录权限含 `s`(如 `drwxrwsr-x`)。
setgid 让任何人新建的文件自动继承 `uniops` 组, 多人协作才不会互相锁死。

- [ ] **Step 6: 传 Claude Code 记忆库与 git 配置**

**★ 两个坑, 任何一个踩到都会让记忆"搬过去了却读不到"。**

**坑一: 目录名是从项目路径推导的, 换机后对不上。**
笔记本上记忆在 `~/.claude/projects/`**`c--Project`**`/memory/` —— 这个 `c--Project` 来自 `C:\Project`。
服务器上代码在 `/srv/uniops`, 从那里启动 `claude` 会新建一个完全不同名的目录,
**照搬过去的 `c--Project` 不会被读到**: 文件在, 但对不上号。

**坑二: 整目录覆盖会冲掉认证。** Task 4 Step 9 已在服务器上装了 Claude Code 并完成认证,
凭据就在 `~/.claude` 里, `tar -xzf ... -C ~` 整个解开会把它替换成笔记本那份。

**目录名规则(2026-08-25 实测)**: 项目路径里的 `/` 换成 `-`。
`/srv/uniops` → **`-srv-uniops`**; `/home/crmadmin` → `-home-crmadmin`。

**★ 由此定下一条团队约定: 统一从 `/srv/uniops` 启动 `claude`, 不要从 worktree 里启动。**

从 worktree 启动(如 `/srv/uniops/uniops-prod`)会得到键 `-srv-uniops-uniops-prod` ——
**又一份独立的空记忆**, 每个 worktree 一份, 把积累切得稀碎。
笔记本上的做法是从 `C:\Project` 启动(键 `c--Project`), **一份记忆覆盖全部 62 个 worktree**;
从 `/srv/uniops` 启动才能延续这个组织方式, 搬过去的记忆才连贯。
工作目录在 `/srv/uniops` 同样能访问所有 worktree, 与笔记本上的用法一致。

正确做法 —— **先让服务器自己建出目录, 再把记忆文件放进去**:

```bash
# 1) 服务器: 从 /srv/uniops 跑一次 claude, 让它建出项目目录(已于 2026-08-25 完成)
cd /srv/uniops
claude                               # 确认信任, 然后退出
ls -d ~/.claude/projects/*/          # 确认 -srv-uniops 已存在
```

```bash
# 2) 笔记本(Git Bash, 不是 PowerShell —— PowerShell 不展开 ~, 路径转换也不同)
tar -czf /d/claude-memory.tar.gz -C "/c/Users/$USERNAME/.claude/projects/c--Project" memory
tar -czf /d/gitconfig.tar.gz -C "/c/Users/$USERNAME" .gitconfig
scp /d/claude-memory.tar.gz /d/gitconfig.tar.gz crmadmin@10.10.50.64:/tmp/
```

```bash
# 3) 服务器: 解到 -srv-uniops 里
tar -xzf /tmp/claude-memory.tar.gz -C ~/.claude/projects/-srv-uniops/
tar -xzf /tmp/gitconfig.tar.gz -C ~
ls ~/.claude/projects/-srv-uniops/memory/MEMORY.md
ls ~/.claude/projects/-srv-uniops/memory/*.md | wc -l
claude --version                     # 确认认证没被破坏
```

预期: `MEMORY.md` 存在, 记忆文件百余个, `claude --version` 仍正常。

**正面验证(必做)**: 在那个目录下起 `claude`, 问它一个只有记忆里才有的事
(例如"生产当前的 TAG 是什么"、"NC 的 BOM 用量为什么要除 HNPARENTNUM")。
**答得出来才算搬成功** —— 只看文件在不在是假通过, 因为坑一正是"文件在但读不到"。

**漏了这步等于服务器上的 Claude 失忆。** 其他开发者是全新的, 没有这份积累 ——
记忆按用户家目录存, 不会在开发者之间共享。


---

### Task 6: 服务器 — 修复 git 与完整性核对

**在起任何容器之前做完。**

- [ ] **Step 1: 修 62 个 worktree 的路径**

worktree 的元数据里存着 `C:/Project/...` 的绝对路径, 换机后全部失效。
git 自带修复命令:

```bash
cd /srv/uniops/uniops
git worktree repair $(ls -d /srv/uniops/uniops-*)
git worktree list | head
git worktree list | wc -l
```

预期: 62; 列出的路径全是 `/srv/uniops/...`。

- [ ] **Step 2: 让 git 接受多用户共享**

```bash
cd /srv/uniops/uniops
git config core.sharedRepository group
git config --global --add safe.directory '*'
sudo -u amir git -C /srv/uniops/uniops status >/dev/null && echo "✅ 其他用户也能操作仓库"
```

(`safe.directory` 是必要的 —— 仓库属主不是当前用户时 git 会拒绝操作。)

- [ ] **Step 3: 核对未 push 的 commit 一个没丢**

```bash
cd /srv/uniops/uniops
echo "uniops(主) unpushed(应为 17): $(git log --oneline origin/main..HEAD | wc -l)"
echo "uniops-release unpushed(应为 2): $(git -C /srv/uniops/uniops-release log --oneline origin/main..HEAD | wc -l)"
echo "uniops-nc-price unpushed(应为 1): $(git -C /srv/uniops/uniops-nc-price log --oneline origin/main..HEAD | wc -l)"
echo "worktree 总数(应为 62): $(git worktree list | wc -l)"
```

预期: `17` / `2` / `1` / `62`。
**任何一项对不上就停下来**, 回笔记本重新打包 `uniops/.git`, 不要继续。

- [ ] **Step 4: 核对脏文件还在**

```bash
echo "主 checkout 脏文件(应为 26): $(git -C /srv/uniops/uniops status --porcelain | wc -l)"
echo "uniops-release 脏文件(应为 5): $(git -C /srv/uniops/uniops-release status --porcelain | wc -l)"
```

- [ ] **Step 5: 核对凭据、附件与 dump**

```bash
ls -l /srv/uniops/uniops/.env /srv/uniops/qbo_conn.env /srv/uniops/nc65_conn.env /srv/uniops/wms_conn.env
echo "file-storage 文件数(应约 18121): $(find /srv/uniops/uniops/file-storage -type f | wc -l)"
ls -la /srv/uniops/uniops/db-snapshots/*.dump
```

预期: 四个 `.env` 都在; 文件数接近 18121; dump 目录里有 Task 3 导的两份。

**注意**: `uniops-vendor-credit` / `uniops-tabs` / `uniops-delegation` 这几个 worktree
**本来就没有自己的 `.env`, 这是正常的** —— compose 从主 checkout 跑, `--env-file` 指的是
`/srv/uniops/uniops/.env`。不要在这里浪费时间找。

- [ ] **Step 6: 修 .env 里的 Windows 路径**

```bash
grep -n "C:/" /srv/uniops/uniops/.env
sed -i 's|C:/Project/|/srv/uniops/|g' /srv/uniops/uniops/.env
grep -n "QBO_CONN_ENV_HOST" /srv/uniops/uniops/.env
```

预期: 改后是 `QBO_CONN_ENV_HOST=/srv/uniops/qbo_conn.env`, 且该文件确实存在。

---

### Task 7: 服务器 — 起测试栈 + 恢复数据

先起测试栈(它是最干净的一套, 起得通说明基础没问题), 再复制模式给开发栈。

- [ ] **Step 0: ★ 查出「生产当前发布版」的真实 SHA**

主环境要跟生产当前发布的版本一致。**这个值必须实时查, 不能用记忆里的、也不能用本文档里的。**

2026-08-25 当天的实测教训: 记忆里记着生产是 `1e312a7`, 但实际线上 epms bundle 已是
`index-CXHWrVpZ.js`(`1e312a7` 对应 `index-BjRuuOq1.js`), 说明期间有过发布。

权威来源是生产 app 服务器 `.env` 里的 `TAG`:

```bash
# 在生产 app 服务器上(该机全程 sudo 部署)
sudo grep '^TAG=' /path/to/uniops/.env
```

拿到后回来核对它确实是 origin 上的一个 commit:

```bash
cd /srv/uniops/uniops
git fetch origin
git log --oneline -1 <生产TAG>
```

**把 SHA 记在这里: `PROD_SHA = ____________`**

二次确认(可选但推荐): 拉生产 web 镜像看烤进去的 bundle 名, 与线上 `curl` 到的比对 ——
指纹一致才说明这个 TAG 真的就是线上那份。

- [ ] **Step 1: 建主环境 worktree 并配 .env**

```bash
cd /srv/uniops/uniops
git worktree add /srv/uniops/uniops-prod <PROD_SHA> --detach
git -C /srv/uniops/uniops-prod log --oneline -1

cp /srv/uniops/uniops/.env /srv/uniops/uniops-prod/.env
cat >> /srv/uniops/uniops-prod/.env <<EOF

# 主环境: 同事的浏览器用这个地址访问
LAN_HOST=10.10.50.64
STACK_PREFIX=uniops-test
EOF
grep -E "LAN_HOST|STACK_PREFIX" /srv/uniops/uniops-prod/.env
```

预期: 显示的 commit 就是 `<PROD_SHA>`。
**detach 是有意的** —— 主环境不该有可提交的分支; 生产发新版后用
`git fetch && git -C /srv/uniops/uniops-prod checkout <新SHA>` 跟进即可。

**注意: 不要钉 `main`。** main 上有已合并但尚未发布的东西(如成文时的 vendor-credit),
钉 main 会让代码要求的表在生产快照里不存在 —— 这正是 §2.10 那个付款报错的成因。

- [ ] **Step 2: ★ 验证 LAN_HOST 真的解析进去了**

```bash
cd /srv/uniops/uniops-prod
STACK_PREFIX=uniops-test docker compose -f docker-compose.dev.yml --env-file .env config | grep "VITE_API_URL"
```

预期: `VITE_API_URL: http://10.10.50.64:8000/api/v1` —— **必须是 IP, 不是 localhost**。
这里看到 localhost 就说明 `.env` 没生效, 后面全白搭。

- [ ] **Step 3: 起测试栈**

```bash
cd /srv/uniops/uniops-prod
STACK_PREFIX=uniops-test docker compose -f docker-compose.dev.yml --env-file .env -p uniops-test up -d
```

首次会跑 `npm install` × 7 和 pip 安装 × 9 并拉基础镜像, **预计 30-60 分钟**。

- [ ] **Step 4: 验证容器状态**

```bash
docker compose -p uniops-test ps --format "table {{.Name}}\t{{.Status}}"
echo "healthy 数: $(docker compose -p uniops-test ps --format '{{.Status}}' | grep -c healthy)"
```

预期: 13 个 API 容器全 `healthy`, 容器名前缀 `uniops-test_`。
**前端容器显示 `unhealthy` 是长期既有误报**(healthcheck 走 IPv6), 不是故障。

- [ ] **Step 5: 恢复生产快照**

```bash
docker cp /srv/uniops/uniops/db-snapshots/epms_prod_20260825.dump uniops-test_postgres:/tmp/prod.dump
docker cp /srv/uniops/uniops/db-snapshots/local_dev_full_20260825.dump uniops-test_postgres:/tmp/devfull.dump
cd /srv/uniops/uniops-prod
docker compose -p uniops-test stop epms-api mdm-api approval-api finance-api file-api expense-api budget-api vms-api identity-api booking-api mrp-api
docker exec uniops-test_postgres psql -U epms -d postgres -c "DROP DATABASE IF EXISTS epms WITH (FORCE);" -c "CREATE DATABASE epms OWNER epms;"
docker exec uniops-test_postgres pg_restore -U epms -d epms --no-owner --role=epms -h localhost -j 4 /tmp/prod.dump
```

(Linux 上不需要 `MSYS_NO_PATHCONV` —— 那是 Git Bash 的路径转换问题。)

- [ ] **Step 6: ★ 实测生产快照里 mrp_* 表在不在**

**不能假设。** MRP 于 2026-08-08 上生产, 但 8/7 的记录说生产没有这些表:

```bash
docker exec uniops-test_postgres psql -U epms -d epms -tAc \
  "select count(*) from information_schema.tables where table_schema='public' and table_name like 'mrp%'"
```

- 结果 **> 0**: 生产已含 MRP 表, 跳过下一步
- 结果 **= 0**: 执行下一步灌回

- [ ] **Step 7: (仅当上一步为 0)从 dev 全量备份挑表灌回**

```bash
# 先列出备份里所有 mrp 相关表, 确认清单
docker exec uniops-test_postgres pg_restore -l /tmp/devfull.dump \
  | grep -iE "TABLE (DATA )?public (mrp|materials|boms|bom_lines|nc_bom|wms_inventory_lots|uom_conversions|material_suppliers|nc_sync_state|alembic_version_mrp)"
# 按上面的实际输出组织 -t 参数灌回(23 张表自成闭环, 无跨模块外键, 顺序无所谓)
docker exec uniops-test_postgres pg_restore -U epms -d epms --no-owner --role=epms -h localhost \
  -t materials -t boms -t bom_lines -t wms_inventory_lots -t uom_conversions \
  -t material_suppliers -t nc_sync_state -t alembic_version_mrp /tmp/devfull.dump
```

(表名以 `pg_restore -l` 的实际输出为准, 不要照抄。)

- [ ] **Step 8: ★ 验证 alembic 版本一致 —— 但不要 upgrade**

栈钉生产版, 快照来自生产库, 两边 schema 天然对齐。
**这里跑 `upgrade head` 反而会把测试环境推到生产之前, 制造出生产上不存在的状态。**

只做验证:

```bash
cd /srv/uniops/uniops-prod
for svc in finance-api epms-api mdm-api file-api mrp-api budget-api identity-api; do
  echo -n "$svc current: "
  docker compose --env-file .env -p uniops-test run --rm --no-deps "$svc" alembic current 2>/dev/null | tail -1
  echo -n "$svc heads:   "
  docker compose --env-file .env -p uniops-test run --rm --no-deps "$svc" alembic heads 2>/dev/null | tail -1
done
```

预期: 每个服务的 `current` 与 `heads` 一致。

**对不上不要用 upgrade 抹平** —— 那说明栈没钉对版本(或快照不是这个版本的生产库导的),
回 Step 0 重新核对 `PROD_SHA`。

- [ ] **Step 9: 抽查一个 schema 一致性的正面证据**

`alembic current` 只看版本表, 不看真实结构。补一条直观的:

```bash
docker exec uniops-test_postgres psql -U epms -d epms -tAc \
  "select count(*) from information_schema.tables where table_schema='public'"
```

预期: 与生产表数一致(2026-08-25 时生产是 141 张; 若期间发布过带迁移的版本会更多)。
可与 `db-snapshots/db-baseline-20260825.txt` 里记的数对照。

- [ ] **Step 10: 重跑 seed_authz(必做)**

```bash
docker exec uniops-test_identity_api sh -c 'cd /app && PYTHONPATH=/app python scripts/seed_authz.py'
docker exec uniops-test_postgres psql -U epms -d epms -tAc \
  "select count(*) from role_permissions rp join permission_defs pd on pd.id=rp.permission_id where pd.code like 'mrp.%'"
```

预期: 计数 > 0。不做的话 MRP 前端按钮会静默消失。

- [ ] **Step 11: 重置 admin 密码(必做)**

```bash
HASH=$(docker exec -i -e PW='DevTest2026!' uniops-test_identity_api sh -c 'cd /app && PYTHONPATH=/app python' <<'PY'
import os
from app.core.security import hash_password
print(hash_password(os.environ["PW"]))
PY
)
docker exec uniops-test_postgres psql -U epms -d epms -c \
  "UPDATE users SET hashed_password='$HASH', must_change_password=false, is_active=true, mfa_enabled=false WHERE email='admin@epms.local';"
```

- [ ] **Step 11b: ★ 中和 company_config 里的 SMTP —— 防止真发邮件给供应商**

**这一步与 seed_authz、重置密码同级, 不是可选项。**

`finance-api` / `vms-api` / `booking-api` 不读环境变量, 它们从 `company_config` 表读 SMTP 配置。
而这张表刚刚被**生产快照**覆盖 —— 也就是说它们现在握着生产的真实 SMTP 服务器和凭据。

其中 `finance-api/app/crud/remittance_send.py` 是**汇款通知, 收件人是供应商**。
不做这一步, 同事在测试环境点一次付款, 供应商可能真的收到信。

```bash
docker exec uniops-test_postgres psql -U epms -d epms <<'SQL'
-- 先看看生产快照带过来的是什么(留个记录)
SELECT id, smtp_host, smtp_port, smtp_user, smtp_from FROM company_config;

-- 全部指向 MailHog
UPDATE company_config SET
  smtp_host     = 'mailhog',
  smtp_port     = 1025,
  smtp_user     = NULL,
  smtp_password = NULL,
  smtp_use_tls  = false,
  smtp_from     = 'testenv@uniops.local';
SQL
```

- [ ] **Step 11c: 正面确认中和生效**

```bash
docker exec uniops-test_postgres psql -U epms -d epms -tAc "SELECT count(*) FROM company_config WHERE smtp_host IS DISTINCT FROM 'mailhog'"
```

预期: `0` —— 一行都不能剩。
**非 0 就停下来**, 不要让同事碰这套环境。

- [ ] **Step 12: 重启后端并本机自检**

```bash
cd /srv/uniops/uniops-prod
STACK_PREFIX=uniops-test docker compose --env-file .env -p uniops-test start epms-api mdm-api approval-api finance-api file-api expense-api budget-api vms-api identity-api booking-api mrp-api
sleep 30
curl -s -o /dev/null -w "portal: %{http_code}\n" http://localhost:5174
curl -s -o /dev/null -w "epms-api health: %{http_code}\n" http://localhost:8000/api/v1/health
```

预期: 两个都 `200`。真正的验收在 Task 10(从别的电脑验)。

---

### Task 8: 服务器 — 起 2-3 套开发栈

**Files:**
- Create: `/srv/uniops/uniops/.env.dev1` / `.env.dev2` / `.env.dev3`
- Create: `/srv/uniops/uniops/start-dev.sh`

- [ ] **Step 1: 为每位开发者建自己的 worktree**

开发者不共用同一个 worktree(会互相覆盖)。Justin 沿用现有的 5 树拓扑;
其他人各开一个新的:

```bash
cd /srv/uniops/uniops
sudo -u amir git worktree add /srv/uniops/uniops-amir-work <PROD_SHA> -b feature/amir-scratch
git worktree list | tail -3
```

- [ ] **Step 2: 写每套开发栈的 .env**

关键点: **容器内端口没变, 变的只是宿主映射**。所以 `VITE_*` 里的端口必须显式覆盖成
开发者浏览器真正要访问的宿主端口, 否则页面会去打测试栈的 8000。

```bash
cd /srv/uniops/uniops
for i in 1 2 3; do
  fp=$((5173 + i*100)); bp=$((8000 + i*100))
  cp .env .env.dev$i
  cat >> .env.dev$i <<EOF

# ── 开发栈 $i: 浏览器在开发者自己的笔记本上, 所以 LAN_HOST 必须是服务器 IP ──
LAN_HOST=10.10.50.64
STACK_PREFIX=uniops-dev$i
EOF
done
ls -1 .env.dev*
```

**注意**: `VITE_*` 的端口覆盖需要在 compose 层做。最省事的做法是给每套栈的端口 override
文件再加一段 `environment:` 覆盖对应服务的 `VITE_*`, 把 `:8000` 换成 `:$bp` 等。
生成后务必用下一步验证。

- [ ] **Step 3: ★ 验证开发栈的 VITE_ 端口指对了**

```bash
cd /srv/uniops/uniops
docker compose -f docker-compose.dev.yml \
  --env-file .env.dev1 config \
  | grep -E "VITE_API_URL|VITE_PORTAL_URL"
```

预期: `http://10.10.50.64:8100/api/v1` 与 `http://10.10.50.64:5274`。
**如果显示 `:8000` / `:5174`, 说明端口覆盖没做, 开发栈的页面会打到测试栈上** ——
这是最隐蔽的一种串台, 必须在这里拦住。

- [ ] **Step 4: 写统一的启动脚本**

```bash
cat > /srv/uniops/uniops/start-dev.sh <<'SH'
#!/usr/bin/env bash
# 起某套开发栈。用法: ./start-dev.sh 1 up -d   /   ./start-dev.sh 1 logs -f epms-api
set -euo pipefail
i="${1:?用法: start-dev.sh <1|2|3> <compose 子命令...>}"; shift
cd "$(dirname "$0")"
# 拓扑 override 是可选的(Task 2 Step 8), 存在才带上
TOPO=""
[ -f docker-compose.devtopology.override.yml ] && TOPO="-f docker-compose.devtopology.override.yml"
exec docker compose \
  -f docker-compose.dev.yml \
  $TOPO \
  --env-file ".env.dev$i" -p "uniops-dev$i" "$@"
SH
chmod +x /srv/uniops/uniops/start-dev.sh
```

`STACK_PREFIX` 已写在各自的 `.env.dev$i` 里, compose 会自动读取。

- [ ] **Step 5: 起第一套开发栈**

```bash
cd /srv/uniops/uniops
./start-dev.sh 1 up -d
docker compose -p uniops-dev1 ps --format "table {{.Name}}\t{{.Status}}"
```

- [ ] **Step 6: 确认挂载的是自己的 worktree**

```bash
docker inspect -f '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}} => {{.Destination}}
{{end}}{{end}}' uniops-dev1_epms_api | grep "/app"
```

预期: 指向该开发者自己的 worktree, **不是** `/srv/uniops/uniops-prod`
(那是同事在用的主环境, 开发栈挂上去等于在主环境上直接改代码)。

- [ ] **Step 7: 给开发栈恢复数据库**

对 `uniops-dev1` 重复 Task 7 的 Step 5-11c(容器名前缀换成 `uniops-dev1_`)。
**其中 `seed_authz`、重置 admin 密码、中和 `company_config` 的 SMTP 三步都不能省** ——
开发栈同样是从生产快照恢复的, 同样握着真实 SMTP。
`alembic` 那步同样只做**验证不做 upgrade** —— 除非该开发者正在开发带迁移的功能,
那时才在自己的栈上跑 upgrade(这正是每人一套独立栈的意义)。

- [ ] **Step 8: 起其余开发栈**

```bash
cd /srv/uniops/uniops
./start-dev.sh 2 up -d
./start-dev.sh 3 up -d
```

同样各自恢复数据库。

- [ ] **Step 9: 验证四套栈端口不打架、资源在预算内**

```bash
docker ps --format "{{.Names}}\t{{.Ports}}" | sort
echo "--- 各栈内存合计 ---"
for p in uniops-test uniops-dev1 uniops-dev2 uniops-dev3; do
  echo -n "$p: "
  docker stats --no-stream --format "{{.Name}} {{.MemUsage}}" | grep "^${p}_" \
    | awk '{print $2}' | sed 's/MiB//;s/GiB/*1024/' | bc -l | awk '{s+=$1} END {printf "%.1f GB\n", s/1024}'
done
free -h
```

预期: 端口无重复绑定; 每套栈约 5.5 GB; 总计约 22 GB, 系统仍有充足余量。
明显超出 5.5 GB/栈就说明有异常, 先查再往下走。

---

### Task 9: 服务器 — 跑测试套件兜底大小写敏感问题

Linux 区分大小写而 NTFS 不区分, **这是本次跨平台迁移唯一的未知量**。

- [ ] **Step 1: 跑 epms-api 测试**

```bash
cd /srv/uniops/uniops/epms-api
pytest -q 2>&1 | tail -30 | tee /tmp/epms-api-after.txt
```

- [ ] **Step 2: 比对失败集合(不是比数字)**

```bash
diff <(grep -E "^(FAILED|ERROR)" /srv/uniops/uniops/db-snapshots/epms-api-test-baseline-20260825.txt | sort) \
     <(grep -E "^(FAILED|ERROR)" /tmp/epms-api-after.txt | sort) \
  && echo "✅ 失败集合与笔记本完全一致 —— 无大小写问题"
```

预期: diff 无输出。
**只比数字会误判** —— 存量失败本来就多, 数字相同可能是"修好一个又坏一个"。
出现新增的失败用例, 十有八九就是大小写引用问题, 按报错逐个修文件名或 import。

- [ ] **Step 3: 对 finance-api 重复一次**

同样比对失败集合。finance-api 全量约 40 分钟。
**注意: `POST /qbo/sync` 会真连生产 QuickBooks, 跑测试时要确认没有触发它。**

---

### Task 10: 跨机验证(完成定义)

**必须从别的电脑做。** "在服务器上 curl localhost 是 200"不算通过。

- [ ] **Step 1: 从笔记本确认端口真的对外开放**

```bash
nc -zv 10.10.50.64 5174 && nc -zv 10.10.50.64 8000
```

预期: 都 succeeded。失败说明 ufw 没放行(回 Task 4 Step 8)。

- [ ] **Step 2: ★ 浏览器验证 API 打的是 IP 而不是 localhost**

**这是整个 LAN 化唯一真正会翻车的地方。**

1. 笔记本浏览器打开 `http://10.10.50.64:5174`
2. **先按 F12 打开 Network 面板**, 再登录
3. 逐条看请求的 Request URL

预期: 所有 API 请求指向 `http://10.10.50.64:8000/...`。
**看到任何一条 `http://localhost:8000` 就是失败** —— 说明某个 `VITE_*` 没被参数化, 回 Task 2 Step 3 重查。

- [ ] **Step 3: 走通一条完整业务链路**

1. 登录 Portal(`:5174`) → 进 EPMS(`:5173`)
2. 打开一个 PO 详情页, 确认数据加载
3. 上传一个附件, 再下载 —— 应成功
4. 打开一张**历史**发票的附件 —— **404 属预期**(文件在生产 file 卷上, 不是故障)

- [ ] **Step 4: 验证付款链路(证明迁移真跑了)**

在测试栈上执行一次 PA 付款。
预期: 成功, 不出 `ProgrammingError: relation "vendor_credits" does not exist`。
报这个错说明**栈钉错了版本** —— 代码是 main(含 vendor-credit)而快照是尚未部署它的生产库。
回 Task 7 Step 0 重新核对 `PROD_SHA`。

- [ ] **Step 5: 验证邮件被 MailHog 拦下**

触发一个会发通知的动作(如提交一张 PR 送审), 在笔记本浏览器打开 `http://10.10.50.64:8025`。

预期: MailHog 里能看到那封邮件, **同事的真实邮箱收不到任何东西**。

- [ ] **Step 6: 验证多人远程开发真的可用**

两个人同时从各自电脑操作:

- 各自 VS Code Remote-SSH 连 `10.10.50.64`, 打开各自的 worktree
- 各自跑 `./start-dev.sh 1 logs -f epms-api`(crmadmin) / `./start-dev.sh 2 ...`(amir)
- 各自改一个前端文件, 确认**自己的**栈热重载了、**对方的**没动
- 各自在服务器上跑 `claude` 起 Claude Code

预期: 互不干扰; 两人的 `docker ps` 都能正常输出(证明非 sudo 可用 docker)。

- [ ] **Step 7: 验证栈之间隔离**

```bash
cd /srv/uniops/uniops && ./start-dev.sh 1 restart
```

同时在笔记本刷新 `http://10.10.50.64:5174`(测试栈)。
预期: 测试栈毫无影响。

---

### Task 11: 交接与收尾

- [ ] **Step 1: 给同事一张访问说明**

- 测试地址: `http://10.10.50.64:5174`(Portal, 从这里进各模块)
- 用自己的公司账号登录(数据来自生产快照, 账号是真的)
- **历史附件打不开是已知限制**, 不用报
- **邮件不会真发出去**, 想看通知去 `http://10.10.50.64:8025`
- 数据是 2026-08-25 的生产快照
- 遇到问题找 Justin, 说明操作步骤和大致时间

- [ ] **Step 2: 给开发者一张上手说明**

- `ssh <你的用户名>@10.10.50.64`, 或 VS Code Remote-SSH
- 代码在 `/srv/uniops`, 你的 worktree 是 `/srv/uniops/uniops-<你>-work`
- 起自己的栈: `cd /srv/uniops/uniops && ./start-dev.sh <你的编号> up -d`
- 你的前端在 `http://10.10.50.64:52xx`, 你的 MailHog 在 `http://10.10.50.64:8x25`
- **统一从 `/srv/uniops` 启动 `claude`**, 不要从 worktree 里启动 —— 从哪个目录启动决定用哪份记忆, 从 worktree 启动会各自得到一份空记忆
- **一人一个 worktree**, 不要两个人改同一个
- 别动 `uniops-prod` —— 那是主环境, 同事在用

- [ ] **Step 3: 更新记忆**

在服务器上让 Claude 记下: 环境已迁移到 Ubuntu、四套栈的端口划分、`STACK_PREFIX`/`LAN_HOST` 机制、
主环境钉生产发布版的更新方式、多用户权限模型。同时更正 `project_uniops_local_dev_env.md` 里
"跑在笔记本上"的描述, 以及 `reference_uniops_dev_worktree_mount.md` 里的 Windows 路径。

- [ ] **Step 4: 观察一周**

笔记本那套**保持原样不删**。一周内出问题随时切回。

- [ ] **Step 5: (一周后)清理笔记本**

```powershell
docker system prune -a --volumes    # 释放约 76 GB
```

`C:\Project` 再多留一段时间, 确认无遗漏后再删。

---

## 附: 常见故障速查

| 症状 | 原因 | 处理 |
|---|---|---|
| 同事打开页面空白 / 一片报错 | `VITE_*` 仍指 localhost | F12 看 Request URL; Task 2 Step 3 |
| **开发栈的页面数据是测试栈的** | 开发栈 `VITE_*` 端口没覆盖, 打到 8000 去了 | Task 8 Step 3 |
| 请求被 CORS 拦 | `ALLOWED_ORIGINS` 没含访问用的 origin | 检查 `.env` 的 `LAN_HOST` 与实际访问 IP 是否一致 |
| 用主机名访问显示 `Blocked request` | vite `allowedHosts` 没配 | Task 2 Step 6 |
| 第二套栈起不来, 报 container name in use | `container_name` 没参数化 | Task 2 Step 2 第二条 sed |
| 全部容器 unhealthy | healthcheck 里的 localhost 被误改 | `grep 'test:.*LAN_HOST'` 应为 0 |
| 前端容器 unhealthy 但页面能开 | 长期既有误报(healthcheck 走 IPv6) | 忽略 |
| 改代码不热重载 | inotify 上限不够 | Task 4 Step 5 |
| 测试套件比笔记本上慢很多 | 机械盘 WAL fsync 延迟 | 确认 `synchronous_commit=off` 生效(Task 2 Step 4b) |
| 首次起栈特别久 | `npm install` 写十几万小文件 | 一次性的, 属预期; 之后有 page cache |
| 其他用户 `docker ps` 报权限错 | 没加入 docker 组 | `sudo usermod -aG docker <用户>` 后重新登录 |
| git 报 dubious ownership | 多用户共享仓库 | Task 6 Step 2 的 `safe.directory` |
| `git worktree list` 路径还是 C:/ | 没跑 repair | Task 6 Step 1 |
| 付款执行 ProgrammingError | 栈钉的版本比生产快照新(如钉了 main) | Task 7 Step 0, 重新核对 `PROD_SHA` |
| `alembic current` 与 `heads` 对不上 | 同上, 版本没钉对 | **不要用 upgrade 抹平**, 回 Task 7 Step 0 |
| MRP 页面按钮消失 | `seed_authz` 没重跑 | Task 7 Step 10 |
| admin 登不进去 | 密码被生产哈希覆盖 | Task 7 Step 11 |
| 测试出现**新增**失败用例 | 大小写敏感(Linux 区分, NTFS 不区分) | Task 9 Step 2, 按报错修文件名或 import |
| 历史附件 404 | 文件在生产 file 卷上 | 预期行为, 非故障 |
| **测试环境把邮件真发给了供应商** | `company_config` 的 SMTP 没中和(那三个服务不读环境变量) | Task 7 Step 11b, 每套栈都要做 |
| 隔天全站打不开 | 服务器 IP 变了 | Task 4 Step 2, 必须固定 IP |
