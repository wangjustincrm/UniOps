# 开发测试环境迁移到 Ubuntu 服务器 + 团队远程开发 — 设计

日期: 2026-08-25
状态: 已定稿, 待实施
作者: Justin Wang / Claude

## 1. 背景与目标

当前 UniOps 的全部开发与测试环境跑在一台工作笔记本上(i9-10885H / 32 GB),
随着系统增长已经吃不消 —— 实测**可用内存只剩 7.5 GB**, 因为 WSL2 独占了 15.48 GB。

本设计把整套环境迁到一台 Ubuntu 虚拟机, 并同时满足两个新需求:

1. **同事通过 IP 直接访问网页做测试** —— 不再是"只有 Justin 的电脑能跑"
2. **2-3 名开发者各自在自己电脑上开 VS Code + Claude Code, 直接改调远程服务器上的代码**

## 2. 已实测的事实基线 (2026-08-25 当天量的, 不是推断)

这些数字是设计的依据。实施时若与现场不符, 先停下来重新量。

### 2.1 dev 栈构成与真实资源占用

`docker-compose.dev.yml` 共 17 个服务(实际运行 20 个容器: postgres + redis + 11 个 API + 7 个前端)。
源码全部走 bind mount, 状态在 9 个 named volume。

`docker stats` 实测**一整套栈占用 5.48 GB 内存**。分布:

| 组件 | 内存 | CPU |
|---|---|---|
| postgres | 929 MiB | 32% |
| 7 个前端(Vite) | 3,116 MiB 合计 | **每个稳定 12%** |
| 11 个 API | 1,428 MiB 合计 | 每个约 2-3% |
| redis | 8 MiB | 7% |

**7 个前端各占 12% CPU 是因为 `vite.config.ts` 全部开了 `usePolling: true`** ——
Windows bind mount 下必须开(inotify 不跨 WSL2 边界), 代价是约 **1 整个核心常驻空转做文件轮询**。
换到 Linux 原生 bind mount 后可关掉, 这是迁移的直接收益之一。

### 2.2 ★ 当前 dev 栈的源码来自 5 棵不同的树

用 `docker inspect` 逐容器量出来的真实拓扑。**没有任何一个 compose 文件记录这个组合**,
它是历次 worktree override 叠加出来的既成事实:

| 源码树 | 服务 |
|---|---|
| `uniops-vendor-credit` | epms-api, finance-api, expense-api, epms 前端, finance 前端 |
| `uniops-tabs` | oa / booking / vms / mrp 前端 |
| `uniops-mrp-phase0` | mrp-api, mdm-api, budget-api, file-api |
| `uniops-delegation` | portal 前端, approval-api |
| `uniops-nc-po-edit` | identity-api |
| `uniops` (主 checkout) | booking-api, vms-api |

两个后果:

1. 新机上直接 `docker compose up -d` 起来的是**纯主 checkout 版本**, 与现在天天用的不是同一个东西。
2. 这套栈**不能直接拿来给同事测试** —— 它混着 5 个未合并分支的代码, 测出的行为不对应任何可发布版本。

### 2.3 ★ 20 个服务写死了 container_name(阻塞多栈并存)

`docker-compose.dev.yml` 里 20 个服务全部写死 `container_name: uniops_*`。
**`container_name` 是全局唯一的, 不受 compose project name 隔离** ——
不改的话第二套栈起到一半就报 `container name "/uniops_postgres" is already in use`。

多栈并存是本设计的核心(1 套测试 + 最多 3 套开发), 因此这是必须先修的阻塞项。

### 2.4 未 push 的工作(会丢的东西)

62 个 worktree 逐个查的结果:

| 位置 | 未 push commit | 脏文件 |
|---|---|---|
| `uniops` 主 checkout (`test/mrp-1c-local`) | 17 | 26 |
| `uniops-release` (`main`) | 2 | 5 |
| `uniops-nc-price` | 1 | 0 |
| 另约 25 个 worktree 分支 `NO-UPSTREAM` (从没推过远端) | — | 零星脏文件 |

**关键**: 这些 commit 全部存在同一个 `uniops/.git` 对象库里(worktree 共享 .git)。
只要连 `.git` 整个拷贝, 一个 commit 都不会丢 —— 这也是本方案**不经过 git 传输、直接文件复制**的底气。

### 2.5 拷贝体量

robocopy `/L` 空跑实测(排除 node_modules / .venv / __pycache__ / .vite / dist / .pytest_cache / *.dump):

- 全量: **28.377 GB / 349,870 文件**
- 其中 `nchome` 占 **21.31 GB / 180,913 文件**(NC65 客户端安装目录, 与 dev 栈运行无关, 不搬)
- `uniops/file-storage` 3.69 GB / 18,121 文件(本地上传的附件, 必须带)
- `uniops/.git` 仅 **0.04 GB**

**排除 nchome 后约 7 GB / 17 万文件。**
成本主导是**文件数**而非体积 —— 因此传输方式选"先打成单个 tar 包再传", 而不是逐文件同步。

### 2.6 LAN 访问的现状

已就绪: 所有 Vite dev server 已是 `--host 0.0.0.0`, 所有 uvicorn 已是 `--host 0.0.0.0`,
端口映射清一色 `"5173:5173"` 写法(已绑 `0.0.0.0`)。

阻塞点: `docker-compose.dev.yml` 里 **57 行**把 `http://localhost:` 硬编码在 `VITE_*` 与
`ALLOWED_ORIGINS` 里。同事打开页面后浏览器里的 JS 会去打**访问者自己的** localhost, 全站 API 立刻失败;
即使修好, 13 个后端的 `ALLOWED_ORIGINS` 只列 localhost, CORS 也会全部拦下。

参考实现已存在: `.env.lan.example` 就是生产侧的"纯 IP:port 无域名无 TLS"模式。

**dev 栈的优势**: `VITE_*` 在 dev 模式下是运行时容器环境变量, 不是 build arg。
改完重启容器即生效, **不需要重建镜像**。

### 2.7 前端 Vite 版本与 host 检查

7 个前端全部 Vite 8 (epms `^8.0.1`, 其余 `^8.0.10`), **没有任何一个配过 `allowedHosts`**。
Vite 默认允许 IP 直连, 但用主机名访问会被 `Blocked request` 挡下。

### 2.8 ★ 邮件安全性: 环境变量只挡住了一半

初版设计据 `epms-api/app/core/config.py:100-105` 的默认值(`SMTP_HOST=localhost` / `SMTP_PORT=1025`)
判断"测试环境不会真发邮件"。**逐服务核查后发现这个结论只对一半服务成立。**

实测各服务的 SMTP 配置来源:

| 服务 | SMTP 来源 | 挡法 |
|---|---|---|
| epms-api、identity-api | **环境变量** | compose 里设 `SMTP_HOST: mailhog` |
| finance-api、vms-api、booking-api | **数据库 `company_config` 表** | 见下 |
| expense-api、approval-api | 不发邮件(实测无任何发信代码) | 无需处理 |

**危险在第二类**: 那三个服务从 `company_config` 表读 `smtp_host` / `smtp_port` / `smtp_user` /
`smtp_password` / `smtp_use_tls` / `smtp_from`, 而测试库是从**生产快照**恢复的 ——
也就是说它们会拿到**生产的真实 SMTP 配置, 并真的把邮件发出去**。

其中 `finance-api/app/crud/remittance_send.py` 是**汇款通知, 收件人是供应商**。
同事在测试环境点一次付款, 供应商可能真的收到信。

**因此每次恢复快照后必须紧接着中和 `company_config` 的 SMTP 设置**, 与 `seed_authz`、
重置 admin 密码同级, 不是可选项。见 §7 第 7 步。

这条也是一个通用教训: **"环境变量没配所以发不出去"是一个只覆盖部分服务的推论**,
配置来源必须逐服务核实。

### 2.9 数据库表数差异

本地库 164 表 vs 生产 141 表, 多出的 23 张全是 MRP 子系统的, 来自未合并的 `uniops-mrp-phase0`。
MRP 已于 2026-08-08 `fe636df` 上生产, 因此**现导的生产快照里是否已含 mrp_\* 表必须实测确认**。

### 2.10 ★ main 的最新变化(来自并行会话)

main 现含 `9f7c0c3`(vendor-credit 合并), 带 3 个 finance-api 迁移。生产尚未部署,
因此生产快照里没有 `vendor_credits` 表。

`finance-api/app/crud/payment_execute.py:340` 无条件 `SELECT` `vendor_credits`。
表缺失 ⇒ 任何 PA 付款执行直接 ProgrammingError。

**本设计已通过 D5 规避该陷阱**: 所有栈钉生产当前发布版, 代码与快照来自同一个版本,
不存在"代码要表而快照没表"的错配。这段保留下来是因为它解释了**为什么不能随手把栈钉到 main**。

**★ 版本会漂移, 必须实时查。** 2026-08-25 当天实测:
线上 epms bundle 已是 `index-CXHWrVpZ.js`, 而记忆里记的 `1e312a7` 对应 `index-BjRuuOq1.js` ——
期间有过发布; 同期 `origin/main` 从 `9f7c0c3` 前进到 `386d257`。
**权威来源是生产 app 服务器 `.env` 里的 `TAG`**, 不是记忆、不是本文档。

### 2.11 Windows → Linux 的两个经典杀手: 已排查, 都安全

- **行尾符**: `.gitattributes` 已声明 `*.sh text eol=lf`, 7 个脚本实测全是 LF ✅。
  `core.autocrlf=true` 让其他文本文件在工作区是 CRLF, 但 `.py`/`.yml`/`.ts` 不受影响。
  **`.env` 系列实测全部是 LF** ✅(CRLF 会让密码值末尾带 `\r`, 是最阴的一种故障)。
- **大小写敏感**: Linux ext4 区分大小写而 NTFS 不区分。源码本来就跑在 Linux 容器里,
  但经由 Windows bind mount 时底层仍是不区分的, 因此**理论上存在"引用了错误大小写却一直没暴露"的代码**。
  由迁移后跑测试套件来兜底(见 §10)。

### 2.12 各 worktree 并不需要自己的 .env

实测 `uniops-vendor-credit` / `uniops-tabs` / `uniops-delegation` 这三个**正在被挂载**的 worktree
根本没有 `.env`, 栈却跑得好好的 —— 因为 compose 是从主 checkout 跑的, `--env-file` 指向主 checkout 的 `.env`。

结论: **`.env` 跟着 compose 调用目录走, 不跟 worktree 走。**
只有当你 `cd` 进某个 worktree 直接跑 compose 时, 那个目录才需要自己的 `.env`。

## 3. 决策记录

| # | 决策 | 理由 |
|---|---|---|
| D1 | 整套迁到虚拟机, 笔记本变瘦客户端 | 笔记本可用内存只剩 7.5 GB(§1) |
| D2 | **Ubuntu Server 22.04.5 LTS + 原生 Docker Engine** | 见下方"D2 详述" |
| D3 | 路径 `/srv/uniops`(对应原 `C:\Project`) | FHS 里 `/srv` 就是"本机对外提供服务的数据"; 需 `git worktree repair` 修 62 个 worktree 的路径 |
| D4 | 数据库现导最新生产快照 | 同事测的是真实单据 / 真实账号 |
| D5 | **所有栈默认钉「生产当前发布版」**, 不是 `main`, 也不复现 5 树拓扑 | 见下方"D5 详述" |
| D6 | `nchome` 不搬 | 21.31 GB / 18 万文件, 占全量 75% 体积 52% 文件数, 与 dev 栈无关 |
| D7 | `cmms-*` 不搬 | 另一套系统; 笔记本不删, 随时可补 |
| D8 | 不经过 git 传输, 直接文件复制 | `.git` 整拷已保住全部 commit(§2.4) |
| D9 | **每个开发者一套独立的栈**, 用 `STACK_PREFIX` + 端口段区分 | 共用一套的话, A 改代码会通过 bind mount 热重载立刻打到 B 脸上 |
| D10 | **共享一份 `/srv/uniops` 代码库**, 靠 Unix 用户组 + setgid 协作 | 62 个 worktree 已经是天然的隔离单位; 每人一份完整 clone 会让磁盘和心智负担翻倍 |
| D11 | VM 配置 **48 GB / 12 vCPU / 700 GB**, VMware Paravirtual + VMXNET3 | 初期 2 人 = 3 套栈 × 5.48 GB ≈ 16.4 GB + 系统与峰值 ≈ 23 GB, 余量充足(§2.1) |
| D12 | **接受机械盘阵列**, 用 postgres 参数抵消 | 见下方"D12 详述" |

### D2 详述: 为什么从 Windows 改成 Ubuntu

最初的决策是 "Windows 11 + Docker Desktop 原样克隆", 理由是迁移风险最低。
**"团队各自远程连上来改代码"这个需求出现后, 该方案有两个硬阻塞**:

1. **Docker Desktop 绑定单个桌面登录用户。** 它需要一个交互式桌面会话才能运行,
   其他人 SSH 进去**拿不到 `docker` 命令** —— 能改代码, 但起不了容器、看不了日志、跑不了测试。
2. **Windows 11 的远程桌面只允许一个活动会话。** 第二个人连上去会把第一个人踢下线。

Linux 上两条都不存在: `dockerd` 是系统服务, 任何加入 `docker` 组的用户都能用;
SSH 天然支持任意多并发会话, VS Code Remote-SSH 连 Linux 是最成熟的路径。

附带收益:

- 关掉 `usePolling` 省下约 1 核/栈的常驻空转(§2.1)
- 同样 48 GB, Linux 能给容器约 44 GB, Windows 套娃后只剩约 28 GB
- 生产环境本来就是 Linux, 测试机与生产同构, 少一整类"本地好好的、上线就炸"
- 不再需要 `MSYS_NO_PATHCONV` 之类的路径转换绕行
- Docker Engine 是 Apache 2.0, 无 Docker Desktop 的商业授权问题

代价: 路径要从 `C:\Project` 改成 `/srv/uniops`, 实施计划约 1/3 的步骤要重写。

### D5 详述: 为什么钉「生产当前发布版」而不是 main

用户明确要求: **"迁移到新设备后, 主环境跟当前生产库发布的最新版一致即可。"**

这带来三个化简, 都是实质性的:

1. **不需要复现 §2.2 那 5 棵树的 Frankenstein 拓扑。** 那个组合是历史 override 叠出来的,
   不对应任何可发布版本; 主环境改钉生产版后, 一份干净的 checkout 就够了。
   §2.2 的拓扑记录仍然要抓下来带走(万一将来要复原某次实验), 但不再是起栈的必要条件。
2. **快照与代码天然一致 ⇒ 零迁移。** 生产快照来自生产库, 生产库的 schema 就是生产代码的 schema。
   钉同一个版本, `alembic` 版本自然对齐, **不需要跑 `upgrade head`** ——
   只需 `alembic current` 做一次一致性**验证**。
3. **§2.10 那个 `vendor_credits` 陷阱自动消失。** 该陷阱的成因是"代码钉 main(含未发布迁移)
   而数据来自生产(无对应表)"的错配。两边都钉生产版就不存在错配 ——
   生产已部署 vendor-credit 则两边都有, 未部署则两边都没有。

**版本号必须实时查, 不能写死。** 本设计成文时(2026-08-25)记忆里记的生产版是 `1e312a7`,
但当天实测线上 epms bundle 已变成 `index-CXHWrVpZ.js`(`1e312a7` 对应的是 `index-BjRuuOq1.js`),
说明期间有过发布; 同期 `origin/main` 也从 `9f7c0c3` 前进到 `386d257`。

因此实施时的权威来源是**生产 app 服务器 `.env` 里的 `TAG`**, 不是记忆、不是本文档。

若将来需要测试尚未发布的东西(如提前验证 main), 那是另开一套栈的事, 不改变主环境的定位。

### D12 详述: 机械盘阵列为什么可以接受

存储只有机械盘阵列(与生产同构), 初版设计要求 NVMe。重新评估后**接受机械盘**, 理由:

1. **生产在机械阵列上不卡是真的, 但不能直接推到 dev 栈** —— 生产跑烤好的镜像,
   没有 bind mount / `npm install` / Vite dev server, 代码起来后常驻 page cache, 磁盘几乎不动。
2. **48 GB 内存是决定性的抵消因素** —— 代码 7 GB + 3 套栈的库约 9 GB, 全部装得进 page cache。
   首次访问之后, 读操作基本是内存速度, 机械盘的寻道劣势消失。
3. **唯一需要主动处理的是 postgres 的 WAL fsync 延迟**(测试套件高频提交时最明显),
   用 `synchronous_commit=off` + `shared_buffers=2GB` 直接消掉。代价是断电时可能丢最后
   几百毫秒事务 —— 对一个随时能从 dump 重建的测试库完全可以接受。**生产绝不能这么设,
   测试栈应该这么设。**
4. `npm install` 写十几万小文件确实慢, 但**每套栈只发生一次**。

结论: 机械盘不构成阻塞。若实测仍慢, 后备手段是把开发栈的 postgres 数据目录放 tmpfs
(数据可弃, 随时从 dump 重建), 但不作为初始方案。

## 4. 目标架构

```
Ubuntu Server 22.04.5  ·  10.10.50.64  ·  12 vCPU / 48 GB / 700 GB(机械盘阵列)
│
├── dockerd (系统服务, docker 组内所有用户可用)
│   └── /var/lib/docker 是独立的 400 G LVM 卷 —— Docker 撑爆磁盘不会拖垮整机
│       (本项目已因镜像臃肿耗尽过磁盘; 卷组另留 198 G 可在线 lvextend)
│
├── /srv/uniops/                       属主 root:uniops, setgid, 组可写
│   ├── uniops/                        主 checkout(含 .git, 全部 62 worktree 的对象库)
│   ├── uniops-prod/                   ★ 主环境 worktree, detach 钉在「生产当前发布版」
│   ├── uniops-*/                      现有 61 个 worktree(带过去备查, 不参与起栈)
│   ├── file-storage/  db-snapshots/   附件与快照
│   └── *.env                          qbo / nc65 / wms 凭据
│
├── 栈 uniops-test    5173-5179 / 8000-8011   ← 主环境, 钉生产发布版, 同事浏览器访问
├── 栈 uniops-dev1    5273-5279 / 8100-8111   ← 开发者 1, 自己的 worktree
├── 栈 uniops-dev2    5373-5379 / 8200-8211   ← 开发者 2
└── 栈 uniops-dev3    5473-5479 / 8300-8311   ← 预留(初期 2 人不起)
```

四套栈的 `LAN_HOST` **全部设成服务器 IP** —— 开发者的浏览器也在自己的笔记本上,
`localhost` 对他们同样是错的。

同一份 compose 文件, 靠 `STACK_PREFIX`(容器名前缀)+ 端口 override + `LAN_HOST` 分出 N 套栈。
每人一个 Linux 账号 + SSH key, VS Code Remote-SSH 各连各的, Claude Code 装在服务器上,
每人在自己家目录跑自己的实例。

**主环境(`uniops-test`)钉生产发布版**, 是同事测试和"出问题时的对照基准";
开发者各自的栈从主环境同一个版本起步, 各自切到自己的分支干活。

**开发者的栈也设 `LAN_HOST=<服务器IP>`** —— 因为他们的浏览器在自己的笔记本上, 不在服务器上,
`localhost` 对他们同样是错的。

## 5. 迁移范围

### 搬

在 Windows 侧打成单个 tar 包传输(避开 17 万小文件的延迟), 排除:

```
node_modules  .venv  __pycache__  .vite  dist  .pytest_cache  nchome
```

必须确认带上的 gitignored 文件:

- `uniops/.env`(主 checkout 的, 这是 compose 真正读的那份 —— §2.12)
- `qbo_conn.env`, `nc65_conn.env`, `wms_conn.env`
- `uniops/file-storage/`(3.69 GB)
- `uniops/db-snapshots/`
- **`C:\Users\<你>\.claude\`** → 服务器上你账号的 `~/.claude` —— Claude Code 的记忆库
- `.gitconfig`, SSH key

### 不搬

- Docker 镜像 / 构建缓存 / node_modules 卷(约 76 GB), 新机重建
- `nchome`(D6), `cmms-*`(D7)

## 6. 多用户与权限

- 建 Unix 组 `uniops`, 所有开发者加入; 同时加入 `docker` 组
- `/srv/uniops` 属主 `root:uniops`, 目录设 **setgid**(新建文件自动继承组), 权限 `2775`
- git 设 `core.sharedRepository=group`, 让多用户共享同一个 `.git` 不会因权限打架
- **协作纪律**: 一人一个 worktree, 不要两个人同时在同一个 worktree 上改
  (这与现有的"一会话一分支 / 一 worktree"纪律一致)
- 容器默认以 root 跑, 会在 bind mount 里创建 root 属主的文件(如 `__pycache__`、`.vite`)。
  这些是可再生的临时产物, 由 setgid + 定期清理兜底, 不作为阻塞项处理

## 7. 数据库方案

搬之前在笔记本上导出, 服务器上按已验证的执行序恢复(全程生产侧只读):

1. 从笔记本 dev 库导**全量**(含那 23 张 MRP 表) —— 不预先挑表清单, 避免漏表
2. 从生产 `10.10.50.20` 导全库(`-Fc`, 约 40 秒, 只读安全)
3. 恢复生产快照后, **先 count 确认 `mrp_*` 表在不在**(§2.9), 视结果决定是否从第 1 份里挑表灌回
4. **验证 `alembic current` 与代码一致, 但不做 `upgrade`** —— 栈钉生产版, 快照来自生产库,
   两者的 schema 天然对齐(D5)。这里跑 upgrade 反而会把测试环境推到生产之前。
   若 `alembic current` 与代码的 head 对不上, **说明栈没钉对版本**, 停下来查, 不要靠 upgrade 抹平
5. **重跑 `seed_authz`** —— 否则 7 个 `mrp.*` 授权被冲掉, MRP 前端按钮静默消失
6. **重置 admin 密码** —— 否则 `admin@epms.local` 变成生产哈希 + `must_change_password`
7. **★ 中和 `company_config` 里的 SMTP 设置** —— 否则 finance-api / vms-api / booking-api
   会拿着生产的真实 SMTP 把邮件(含发给**供应商**的汇款通知)真的发出去(§2.8)

第 5、6 步在历史上已各踩过一次; 第 7 步是本次核查新发现的。三步都写死为必做步骤。
**每套栈各恢复一份**(各自独立的 postgres 容器)。

已知限制: 快照里业务行引用的**历史附件只在生产 file 卷上** ⇒ 历史附件下载 404。
这是快照方案的固有限制, 不是故障, 需提前告知同事。

## 8. 代码改动

集中在 `docker-compose.dev.yml` 与 7 个 `vite.config.ts`, 全部采用"默认值保持原行为"的参数化:

1. **57 行** `VITE_*` / `ALLOWED_ORIGINS`: `http://localhost:` → `http://${LAN_HOST:-localhost}:`
2. **20 行** `container_name: uniops_` → `container_name: ${STACK_PREFIX:-uniops}_`(§2.3 的阻塞项)
3. 端口偏移放在**每套栈各自的 override 文件**里, 不动主文件
4. 5 树拓扑固化成 `docker-compose.devtopology.override.yml`(§2.2)
5. 7 个 `vite.config.ts` 加 `allowedHosts`(§2.7); **同时把 `usePolling` 改为默认关闭**,
   Linux 用原生 inotify —— 这是每栈省下约 1 核的那一项(§2.1)
6. 新增 MailHog 服务, `SMTP_HOST: mailhog`, UI 在 `<IP>:8025`(§2.8)

两条 sed 均已在 scratchpad 空跑验证: 57 行 / 20 行精确命中, healthcheck 里的 18 处 `localhost`
(容器自检, 改了会让全部容器 unhealthy)零误伤, 不设变量时解析结果与改动前逐字一致。

## 9. 服务器环境要求

- **固定 IP**(已配 `10.10.50.64/24` 静态, 非 DHCP) —— IP 一变, 各栈的 `LAN_HOST` 全部失效
- Ubuntu Server 22.04.5 LTS, 无桌面
- Docker Engine + compose plugin(不是 Docker Desktop)
- 防火墙(ufw)放行: `22`, `5173-5179`, `5273-5279`, `5373-5379`,
  `8000-8011`, `8100-8111`, `8200-8211`, `8025`/`8125`/`8225`
  **★ 但 ufw 管不住 Docker 发布的端口** —— Docker 直接往 iptables 的 `DOCKER` 链插规则, 优先于 ufw。
  应用端口不加规则也能访问, 且 `ufw deny` 对它们无效。真正被 ufw 保护的只有 SSH。
  内网测试机可接受; 将来若要限制来源, 要写 `DOCKER-USER` 链
- 时区 `America/Toronto`(EDT, -0400)+ NTP —— 时区错会让日志与数据库时间戳全部错位
- 每个开发者: Linux 账号 + SSH key + `docker` 组 + `uniops` 组
- `inotify` 上限调高(4 套栈 × 7 个 Vite 监听大量文件): `fs.inotify.max_user_watches=524288`

## 10. 验证标准(完成定义)

1. **从另一台电脑**的浏览器打开 `http://<IP>:5174`, 登录, 进 EPMS, 打开 PO 详情
2. **F12 Network 确认请求打的是 `<IP>:8000` 而不是 `localhost:8000`** —— LAN 化唯一真正会翻车的地方
3. 在测试栈上**执行一次 PA 付款**(验证 §7 第 4 步的迁移真跑了)
4. 触发一个通知动作, MailHog 里收到
4b. **正面确认 `company_config` 的 SMTP 已被中和** —— 只看"MailHog 收到了"不够,
   那只证明走环境变量的两个服务被挡住了; 数据库那三个要单独查表确认
5. 附件: 新上传能下载; 历史附件 404 属预期
6. **跑测试套件兜底大小写敏感问题**(§2.11): epms-api 与 finance-api 各跑一次,
   失败集合与笔记本上同条件跑的结果**逐个比对**(只比数字会误判)
7. 两个开发者同时 SSH 连入, 各自起栈、各自改代码, 互不干扰
8. 开发栈重启不影响测试栈

## 11. 风险与回退

- **笔记本这套先不删**, 服务器跑通一周再清。这是主回退路径
- 拷贝完成后第一件事: 核对 §2.4 那 17+2+1 个未 push commit 还在
- 迁移期间**冻结开发**, 不要两处同时改
- 大小写敏感是本次跨平台迁移唯一的未知量, 由 §10 第 6 条兜底

## 12. 实施时必须实测、不可假设的点

1. 生产快照里 `mrp_*` 表在不在(§2.9)
2. 科目表里有没有 `1123`(§7 第 4 步)
3. `git worktree repair` 后 62 个 worktree 是否全部可用
4. 测试套件的失败集合是否与笔记本一致(§10 第 6 条) —— 这是大小写问题的唯一探针
5. `LAN_HOST` 生效后浏览器实际请求的 host —— 用 F12 正面取证, 不接受"没报错就是通过"
