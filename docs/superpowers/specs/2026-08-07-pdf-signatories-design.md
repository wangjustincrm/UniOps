# EPMS 单据 PDF 增加申请人 / 审批人姓名

- 日期：2026-08-07
- 分支：`feature/pdf-signatories`（基 `main` = `d57578d`）
- 工作区：`c:/Project/uniops-pdf-names`
- 范围：epms-api（PR / PA / GR 三种 PDF）。**PO PDF 不在范围内**。

## 1. 目标

系统自动生成的 PR、PA、GR PDF 上要能看到经手人姓名：

- PR：申请人（Requested By）+ 全审批链审批人
- PA：申请人（Applied By）+ 全审批链审批人
- GR：创建人（Created By）；GR 无审批链，沿用现有 Signatures 小节

## 2. 现状与约束

### 2.1 渲染函数是同步的，不能查库

`generate_pr_pdf` / `generate_pa_pdf` / `generate_gr_pdf` 均为同步函数，通过
`loop.run_in_executor(None, ...)` 调用，只接收 ORM 对象。它们**不能**发起异步 DB
查询。因此所有姓名必须由**调用方**（async 上下文）查好后作为参数传入。

### 2.2 数据来源

| 内容 | 来源 |
|---|---|
| PR / PA 申请人 | `purchase_requests.created_by` / `payment_applications.created_by` → `users.full_name` |
| PR / PA 审批人 | `approval_events`（`document_type` = `pr`/`pa`，`action='approve'`）→ `actor_id` join `users.full_name` |
| 审批角色标签 | `app/crud/current_step.py` 的 `ROLE_LABELS` / `_label()` |
| GR 创建人 | `goods_receipts.created_by` → `users.full_name` |
| GR 收货人 / 确认人 | `goods_receipts.received_by` / `acknowledged_by`（String 字段） |

### 2.3 approval_events 里的两类自动事件

approval-api 的审批引擎会写入两类不代表"真人在这一步点了批准"的事件，必须区分：

- `comment` 含 `Auto-skipped` —— 该步骤被跳过（部门无 Director、配置的 Supervisor
  失效等），`actor_id` 是提交人而非审批人。**必须排除**，否则申请人会被误列为审批人。
- `comment` 为 `Auto-approved (same approver holds both roles)` —— 真人一人兼两个角色，
  引擎自动带过后续步骤。**必须保留**，该人会以两个不同角色各出现一行。

判定方式与前端审批时间轴一致（`PrDetailPage.tsx` 用 `comment.includes('Auto-skipped')`）。

### 2.4 GR 的 UUID 兜底缺陷

`app/crud/gr.py` 有 6 处形如 `payload.received_by or str(created_by)` /
`req.acknowledged_by or str(actor_id)` 的兜底。浏览器路径会传 `user?.name`（真实姓名），
但非浏览器路径（NC 导入、PMS 导入、Teams 审批、直接调 API）不传，于是**把 UUID 字符串
存进了姓名字段**，PDF 上就会印出一串 UUID。

### 2.5 各文档 PDF 的生成时机

| 单据 | 生成时机 | 可重生成 |
|---|---|---|
| PR | 审批通过后台任务 `app/api/v1/pr.py::_generate_pr_pdf_background` | 有 `POST /pr/{id}/attachments/regenerate-pdf` |
| PA | 审批通过内联生成 `app/api/v1/pa.py`（`new_status == "approved"` 分支） | 有 `POST /pa/{id}/attachments/regenerate-pdf` |
| GR | acknowledge 步骤 `app/crud/gr.py::_attach_gr_pdf`（全仓库唯一调用点） | 无 |

因为 GR PDF 在 acknowledge 时就定版，而 `collected_by` 是之后的领用步骤才写入的，
**Collected By 不可能出现在 GR PDF 上**。本次决定不改 GR 生成时序，因此 GR PDF
不含 Collected By。

### 2.6 遗留死代码

`app/crud/pr.py::action` / `_attach_pr_pdf` 与 `app/crud/pa.py::_attach_pa_pdf` 已无调用方
（审批已全量委派给 approval-api）。本次不删除、不修改，仅要求新增参数全部可选以免破坏其编译。

## 3. 方案

### 3.1 新增共享取数模块 `app/crud/signatories.py`

```python
async def resolve_user_names(db, ids: Iterable[UUID]) -> dict[UUID, str]
    """批量查 users.full_name，一次查询，避免 N+1。"""

async def approval_signatories(db, doc_type: str, doc_id: UUID, created_by: UUID)
        -> tuple[str | None, list[dict]]
    """返回 (申请人姓名, 审批人列表)。审批人 dict 形如
    {"role": "Dept Manager", "name": "Alice Wang", "at": datetime}。
    按 (step_idx, created_at) 排序；排除 Auto-skipped 事件。"""

async def gr_signatories(db, gr) -> dict
    """返回 {"created_by_name", "received_by", "acknowledged_by"}。
    received_by / acknowledged_by 若存的是 UUID 形状的字符串，反查 users.full_name
    替换（修存量脏数据的显示）；查不到则原样返回。"""
```

配套：把 `app/crud/current_step.py` 的私有 `_label()` 改名为公开的 `role_label()`
（仅 2 处内部引用），供本模块复用，避免第二份 ROLE_LABELS。

### 3.2 渲染层改动

三个渲染函数新增**全部可选**参数，默认值保持现有行为：

| 文件 | 新增参数 | 渲染 |
|---|---|---|
| `app/services/pdf_pr.py` | `requester_name=None`, `approvals=None` | 元信息格新增第 6 行 `Requested By \| Submitted`（后者取 `pr.submitted_at`）；Total 表之后、Notes 之前插入 `Approvals` 小节 |
| `app/services/pdf_pa.py` | `requester_name=None`, `approvals=None` | 元信息格新增第 5 行 `Applied By \| Date Approved`（后者取 `pa.approved_at`）；同款 `Approvals` 小节 |
| `app/services/pdf_gr.py` | `created_by_name=None`, `received_by=None`, `acknowledged_by=None` | 现有 Signatures 小节新增 `Created By` 行；`received_by` / `acknowledged_by` 参数为 None 时回落到 ORM 字段 |

`Approvals` 小节规格：
- 三列表格 `Step / Approved By / Date`，列宽 `[W*0.30, W*0.45, W*0.25]`
- 沿用 Line Items 的既有样式：主色表头 + 白/浅灰隔行底色 + 同样的 padding
- `approvals` 为空或 None 时整节不渲染
- 姓名为 None（用户被删）渲染 `—`；日期格式 `%Y-%m-%d`

GR Signatures 小节：`Created By` 始终渲染（它是 DB 外键回查、最权威的一个），
排在现有 `Received By` / `Acknowledged By` 之前。小节本身的渲染条件从现有的
`if gr.received_by or gr.acknowledged_by` 放宽为 `if created_by_name or received_by
or acknowledged_by`，否则一张两个字符串字段都为空的 GR 会连 Created By 一起丢掉。

### 3.3 调用点接线（5 处活代码）

| 位置 | 改动 |
|---|---|
| `app/api/v1/pr.py::_generate_pr_pdf_background` | 用 `fresh_db` 调 `approval_signatories("pr", ...)`，结果传入 |
| `app/api/v1/pr_attachments.py::regenerate_pdf` | 用请求 `db` 同上 |
| `app/api/v1/pa.py` 审批通过分支 | 用请求 `db` 调 `approval_signatories("pa", ...)` |
| `app/api/v1/pa_attachments.py::regenerate_pdf` | 同上 |
| `app/crud/gr.py::_attach_gr_pdf` | 函数本身已带 `db`，内部调 `gr_signatories` |

### 3.4 GR UUID 兜底修复

`app/crud/gr.py` 6 处兜底改为先查 `users.full_name`，查不到才回落 UUID 字符串：

```python
gr.acknowledged_by = req.acknowledged_by or await _user_name(db, actor_id) or str(actor_id)
```

其中 `_user_name` 是 `app/crud/gr.py` 内的单人薄封装，底层复用 3.1 的
`resolve_user_names`，不再另写一份查询。

存量已存成 UUID 的数据由 3.1 的 `gr_signatories` 在渲染侧兜底反查。

## 4. 测试

新增 `epms-api/tests/test_pdf_signatories.py`，复用
`tests/test_pr_pdf_department.py` 已有的 PDF 内容流解压断言法
（ASCII85 + Flate 解压页内容流后做字节子串断言，姓名全部用 ASCII）。

| 用例 | 断言 |
|---|---|
| PR 审批人过滤 | 播种 3 条 approve 事件（正常 / `Auto-skipped` / 正常），`approval_signatories` 只返回 2 人，且顺序按 step_idx |
| PR PDF 内容 | PDF 含申请人姓名 + 2 位审批人姓名；**不含**被 Auto-skipped 步骤的角色标签 |
| PR 兼容性 | 不传新参数调用 `generate_pr_pdf` 仍产出合法 PDF（保护遗留死代码路径） |
| PA PDF 内容 | `Applied By` 姓名与审批人姓名入 PDF |
| GR 创建人 | `created_by` 对应姓名入 PDF |
| GR UUID 存量 | `acknowledged_by` 存成 UUID 字符串时，PDF 印出真实姓名而非该 UUID |

跑测环境（见记忆 `reference` / `feedback`）：覆盖 `POSTGRES_*` 指向本地 docker
`uniops_postgres`、库 `epms_test`；worktree 内还须传 `JWT_SECRET_KEY`。
epms-api 套件同一时刻只能跑一个（测试库不支持并发）。失败数须对基线，不能只看"无输出"。

## 5. 范围外

- PO PDF（`app/services/pdf_po.py`）
- GR 的 Collected By、GR PDF 重生成时序、GR Regenerate PDF 端点
- `app/crud/pr.py` / `app/crud/pa.py` 中的遗留审批引擎死代码
- 前端改动（PR/PA Detail 页已有 Regenerate PDF 按钮；GR 无，本次不加）

## 6. 上线影响

- 无数据库迁移。
- 只改 epms-api，发布时只需重建 epms-api 镜像。
- 存量已生成的 PDF 不会自动刷新。PR / PA 可用 Detail 页现有 Regenerate PDF 按钮逐张重生成；
  GR 没有该按钮，只有新单会带 Created By。
- GR UUID 兜底修复即时生效于新数据；存量脏数据的显示由渲染侧兜底覆盖（但存量 GR PDF 已定版，
  除非该 GR 尚未 acknowledge）。
