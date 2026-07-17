# Finance JV 列表排序 + System 列 + 科目余额维度汇总 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Finance 加三项界面完善:JV 列表列头服务端排序、导入并展示 NC 的 System 列(GL/AP/AR…)、科目余额按辅助核算展开显示期初/发生/期末四列汇总(对齐 NC 报表、子行加总=父级)。

**Architecture:** 三项独立。#1 纯接口/前端(加 sort/dir 参数 + 可点列头)。#2 加一列 `source_subsystem`(迁移 0024)+ 凭证同步取 `PK_SYSTEM` + 列表展示全名。#3 把 `expand_by_dims` 从单期 movement 改为 opening/period_debit/period_credit/closing 四列(与父级科目行同构)。

**Tech Stack:** FastAPI + SQLAlchemy(async) + alembic / oracledb(读 NC) + psycopg2(写库) / pytest + pytest-asyncio / React + TanStack Query(TS 6.0.3)

**Spec:** `docs/superpowers/specs/2026-07-17-finance-jv-ab-polish-design.md` —— 每个决策的依据在 spec,有疑问回去查,别自行发挥。

**Worktree:** `c:/Project/uniops/.worktrees/nc-coa-sync`,分支 `feature/finance-nc-coa-sync`(接在 NC COA/凭证同步之后)。

## Global Constraints

- **CHAR 列必须 strip**:NC 的 `PK_SYSTEM`(CHAR)会空格补位,`'~'`+空格是补位空值。**比较/存储前一律 `.strip()`**。这是本大分支两踩的坑(TALLYDATE、BD_ACCTYPE)。
- **枚举 NC 取值不得截断**:`PK_SYSTEM` 有 **9** 个值(AP/GL/IA/FA/CM/AR/EGL/OT/PLCF);首次查漏了 PLCF。
- **不猜映射**:未登记的 subsystem 码 → 展示回退原码,不编全名。
- **列名白名单是安全边界**:`sort` 参数直接进 `order_by`,只接受白名单列,其余 422。
- **UI 文案全英文**,注释可中文。
- **测试连本地 docker 库,不得打生产**。命令统一:
  ```bash
  cd c:/Project/uniops/.worktrees/nc-coa-sync/finance-api
  TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
    DATABASE_URL=postgresql+asyncpg://x:x@localhost/x JWT_SECRET_KEY=x \
    /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/<file> -q
  ```
  (worktree 无自带 venv,用主仓库 `finance-api/.venv`;宿主 `.env` 指向生产,故显式传 dummy `DATABASE_URL`。)
- **每个 Task 结束必须 commit**,commit message 英文。
- **前端 typecheck**:`cd .../finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`(TS 6.0.3,`tsc -b`/`npm run build` 会因 baseUrl 报错)。**先测基线再对比,不要「无输出=通过」**。

## File Structure

| 文件 | 改动 | Task |
|---|---|---|
| `finance-api/app/models/journal_voucher.py` | `JournalVoucher` 加 `source_subsystem` 列 | 2 |
| `finance-api/alembic/versions/0024_jv_source_subsystem.py` **(新)** | 加列迁移 | 2 |
| `finance-api/app/services/nc_sync.py` | `NcExtract` + fetch + transform + insert 取 `PK_SYSTEM` | 2 |
| `finance-api/app/api/v1/journal_voucher.py` | `_hdr` 加 subsystem+label;`list_vouchers` 加 sort/dir/subsystem 过滤 | 1+2 |
| `finance-api/app/crud/account_balance.py` | `expand_by_dims` 改四列 | 3 |
| `finance/src/pages/finance/JournalVouchersPage.tsx` | 可点列头 + System 列 + System 过滤下拉 | 1+2 |
| `finance/src/pages/finance/AccountBalancePage.tsx` | 展开子行改四列 | 3 |
| `finance/src/pages/finance/JvDetailModal.tsx` | `JvHeader` 接口加 `source_subsystem*` | 1/2 |
| 各 `tests/test_*.py` | 对应测试 | 1/2/3 |

三项无相互依赖,可按 Task 1→2→3 顺序;#1 与 #2 都动 `list_vouchers`/前端列表,故 **Task 1 先做纯排序,Task 2 叠加 System 列**(Task 2 会用到 Task 1 的白名单机制)。

---

### Task 1: JV 列表服务端排序(#1)

**Files:**
- Modify: `finance-api/app/api/v1/journal_voucher.py`(`list_vouchers`)
- Modify: `finance/src/pages/finance/JournalVouchersPage.tsx`(可点列头)
- Test: `finance-api/tests/test_journal_voucher.py`(追加)

**Interfaces:**
- Produces: `GET /finance/v1/journal-vouchers?sort=<col>&dir=<asc|desc>`;白名单 `_SORTABLE`(供 Task 2 追加 `source_subsystem`)

- [ ] **Step 1: 写失败的测试**

追加到 `finance-api/tests/test_jv_api.py`(HTTP/list 夹具 client/_h/_draft_jv 在这;test_journal_voucher.py 无 HTTP 夹具)(先读该文件现有 list 测试的 client/JWT 夹具照用):

```python
async def test_list_sort_by_jv_number_asc(client, db_session):
    from sqlalchemy import text
    # 造两张不同 jv_number 的 posted 凭证 (照该文件既有 _pg/seed 风格填实)
    await _seed_two_vouchers(db_session, ("JV-202601-0001", "JV-202601-0002"))
    r = await client.get("/finance/v1/journal-vouchers?sort=jv_number&dir=asc", headers=_h())
    nums = [i["jv_number"] for i in r.json()["items"]]
    assert nums == sorted(nums)          # 升序

async def test_list_sort_rejects_unknown_column(client):
    r = await client.get("/finance/v1/journal-vouchers?sort=id;drop", headers=_h())
    assert r.status_code == 422          # 白名单外 -> 拒绝

async def test_list_default_sort_is_date_desc(client, db_session):
    r = await client.get("/finance/v1/journal-vouchers", headers=_h())
    assert r.status_code == 200          # 无 sort 参数仍按 voucher_date desc
```

> `_seed_two_vouchers` / `_h` 用该文件既有 helper;先 `grep -n "async def test_.*list\|def _h\|def _seed" tests/test_journal_voucher.py` 照抄一个既有 list 测试的造数方式。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  DATABASE_URL=postgresql+asyncpg://x:x@localhost/x JWT_SECRET_KEY=x \
  /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/test_jv_api.py -k sort -v
```
Expected: FAIL(`sort=id;drop` 现在被忽略而非 422)

- [ ] **Step 3: 实现**

`finance-api/app/api/v1/journal_voucher.py`,在 `list_vouchers` 之前加白名单常量:

```python
# sort 参数直接进 order_by,故只接受白名单列 (SQL 注入边界)。source_subsystem 由 Task 2 加。
_SORTABLE = {
    "voucher_date": JournalVoucher.voucher_date,
    "jv_number": JournalVoucher.jv_number,
    "summary": JournalVoucher.summary,
    "total_debit": JournalVoucher.total_debit,
    "status": JournalVoucher.status,
}
```

`list_vouchers` 签名加两个参数:

```python
                        sort: str = Query(default="voucher_date"),
                        dir: str = Query(default="desc"),
```

把末尾的 `order_by(...)` 替换为:

```python
    if sort not in _SORTABLE:
        raise HTTPException(status_code=422, detail=f"unknown sort column {sort!r}")
    col = _SORTABLE[sort]
    col = col.asc() if dir == "asc" else col.desc()
    rows = (await db.execute(
        base.order_by(col, JournalVoucher.jv_number.desc())   # jv_number 作稳定次级键
        .offset(offset).limit(limit))).scalars().all()
```

(确认文件顶部已 import `HTTPException`;没有则加 `from fastapi import HTTPException`。)

- [ ] **Step 4: 跑测试确认通过**

```bash
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  DATABASE_URL=postgresql+asyncpg://x:x@localhost/x JWT_SECRET_KEY=x \
  /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/test_jv_api.py -v
```
Expected: 全绿(既有 + 3 新)

- [ ] **Step 5: 前端可点列头**

`finance/src/pages/finance/JournalVouchersPage.tsx`:

5a. 加排序状态(与 `page` 并列):
```tsx
  const [sort, setSort] = useState<{ col: string; dir: 'asc' | 'desc' }>({ col: 'voucher_date', dir: 'desc' })
```

5b. `params` 的 `useMemo` 里加(在 `if (q)...` 之后、`return` 之前):
```tsx
    p.set('sort', sort.col); p.set('dir', sort.dir)
```
并把 `sort` 加进该 `useMemo` 的依赖数组;list 查询的 `queryKey` 也要含 `sort`(否则不重取)。

5c. 加一个点击处理(点同列切方向,点新列重置 desc,并回第 0 页):
```tsx
  const toggleSort = (col: string) => {
    setPage(0)
    setSort((s) => s.col === col ? { col, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { col, dir: 'desc' })
  }
```

5d. 把可排序的 `<th>`(第 173-178 行:Voucher No./Date/Summary/Debit/Status)改成可点、带箭头。示例(Voucher No.,其余同构,列名对应 `jv_number`/`voucher_date`/`summary`/`total_debit`/`status`):
```tsx
                    <th className="px-3 py-2 w-36 cursor-pointer select-none" onClick={() => toggleSort('jv_number')}>
                      Voucher No.{sort.col === 'jv_number' ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                    </th>
```
`Source` 列(第 176 行)不排序,保持原样。

- [ ] **Step 6: typecheck(先基线后对比)**

```bash
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance
npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
```
Expected: 0 errors(与改动前一致)。

- [ ] **Step 7: Commit**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync
git add finance-api/app/api/v1/journal_voucher.py finance-api/tests/test_journal_voucher.py \
        finance/src/pages/finance/JournalVouchersPage.tsx
git commit -m "feat(finance): server-side sortable JV list columns

Column headers sort the whole result set, not just the visible page, via a
sort/dir param on a whitelist that doubles as the injection boundary; jv_number
stays a stable secondary key so pagination doesn't jump."
```

---

### Task 2: 导入并展示 NC System 列(#2)

**Files:**
- Modify: `finance-api/app/models/journal_voucher.py`(加列)
- Create: `finance-api/alembic/versions/0024_jv_source_subsystem.py`
- Modify: `finance-api/app/services/nc_sync.py`(取 PK_SYSTEM)
- Modify: `finance-api/app/api/v1/journal_voucher.py`(`_hdr` + `_SORTABLE` + 过滤)
- Modify: `finance/src/pages/finance/JvDetailModal.tsx`(`JvHeader` 接口)
- Modify: `finance/src/pages/finance/JournalVouchersPage.tsx`(System 列 + 过滤下拉)
- Test: `finance-api/tests/test_nc_sync.py`、`tests/test_journal_voucher.py`

**Interfaces:**
- Consumes: Task 1 的 `_SORTABLE`
- Produces: `journal_vouchers.source_subsystem`;`_hdr` 返回 `source_subsystem` + `source_subsystem_label`

> ⚠️ 迁移 `down_revision = "0023_coa_sync_runs"`(2026-07-17 实测链尾)。动手前
> `grep -H "^revision\|^down_revision" alembic/versions/00*.py | tail -3` 核对。

- [ ] **Step 1: 写失败的测试**

追加到 `finance-api/tests/test_nc_sync.py`:

```python
def test_transform_maps_and_strips_subsystem():
    # PK_SYSTEM is CHAR(padded); store the stripped raw code, empty -> None
    e = _mini_extract(pk_system="GL                  ")   # 空格补位
    vs, _, _, _ = transform(e, {}, {}, {}, {}, {}, set())
    assert vs[0]["source_subsystem"] == "GL"
    e2 = _mini_extract(pk_system="~")
    vs2, _, _, _ = transform(e2, {}, {}, {}, {}, {}, set())
    assert vs2[0]["source_subsystem"] is None
```

追加到 `finance-api/tests/test_jv_api.py`(纯函数测试放这也可,与 API 测试同文件):

```python
def test_subsystem_label_maps_known_and_falls_back():
    from app.api.v1.journal_voucher import _subsystem_label
    assert _subsystem_label("GL") == "General Ledger"
    assert _subsystem_label("PLCF") == "Gain/Loss Carry-Forward"
    assert _subsystem_label("OT") == "OT"        # 未登记 -> 回退原码,不猜
    assert _subsystem_label(None) is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  DATABASE_URL=postgresql+asyncpg://x:x@localhost/x JWT_SECRET_KEY=x \
  /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/test_nc_sync.py::test_transform_maps_and_strips_subsystem tests/test_jv_api.py::test_subsystem_label_maps_known_and_falls_back -v
```
Expected: FAIL(`_mini_extract` 无 `pk_system` 参数 / 无 `_subsystem_label`)

- [ ] **Step 3: 加列 + 迁移**

`app/models/journal_voucher.py` 的 `JournalVoucher` 类,`nc_source_pk` 之后加:
```python
    # NC GL_VOUCHER.PK_SYSTEM (GL/AP/AR/FA/CM/IA/EGL/OT/PLCF); null for go-forward JVs.
    source_subsystem: Mapped[str | None] = mapped_column(String(10), nullable=True)
```

新建 `alembic/versions/0024_jv_source_subsystem.py`:
```python
"""journal_vouchers.source_subsystem — NC GL_VOUCHER.PK_SYSTEM (GL/AP/AR/...)

Revision ID: 0024_jv_source_subsystem
Revises: 0023_coa_sync_runs
Create Date: 2026-07-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0024_jv_source_subsystem"
down_revision = "0023_coa_sync_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("journal_vouchers",
                  sa.Column("source_subsystem", sa.String(10), nullable=True))


def downgrade():
    op.drop_column("journal_vouchers", "source_subsystem")
```

- [ ] **Step 4: 同步取 PK_SYSTEM**

`app/services/nc_sync.py`:

4a. `NcExtract.vouchers` 注释的元组末尾加 `pk_system`(在 tallydate 之后)。

4b. `transform` 的 voucher 循环:解包多一个 `pk_system`,并写入 dict。把
```python
    for pk, year, period, num, expl, pdate, _ctime, tallydate in extract.vouchers:
```
改为
```python
    for pk, year, period, num, expl, pdate, _ctime, tallydate, pk_system in extract.vouchers:
```
并在 `vouchers.append({...})` 里加(与 `"status"` 并列):
```python
            # NC PK_SYSTEM is CHAR (space-padded); store the stripped raw code.
            "source_subsystem": (pk_system or "").strip() or None,
```

4c. `fetch_from_nc` 的两处 voucher 查询里,主查询 select 末尾加 `pk_system`(tallydate 之后):
```python
        vq = ("select pk_voucher, year, period, num, explanation, prepareddate, "
              "creationtime, tallydate, pk_system from NCSC.GL_VOUCHER "
              "where pk_accountingbook = :b "
              "  and (discardflag is null or discardflag <> 'Y')")
```
(status 回填那条 `(pk_voucher, tallydate)` 查询**不用改** —— 它只喂 tallied 集。)

4d. `_run_worker` 的 `v_rows` INSERT 加该列。把 tuple 与 SQL/template 同步加一列
`v["source_subsystem"]`(放在 nc_source_pk 之后、总额之前),INSERT 列清单和 `template` 的 `%s` 各加一个。
> **列数三处必须同步**:tuple、INSERT 列名、template。漏一处会静默错位或报错。先读 `_run_worker` 的完整 v_rows/INSERT 再改。

- [ ] **Step 5: `_hdr` 加 subsystem + label**

`app/api/v1/journal_voucher.py`:

5a. 加映射常量与函数(在 `_hdr` 之前):
```python
_SUBSYSTEM_LABELS = {
    "GL": "General Ledger", "AP": "Accounts Payable", "AR": "Accounts Receivable",
    "FA": "Fixed Assets", "CM": "Cash Management", "IA": "Inventory Accounting",
    "EGL": "Exchange Gain/Loss", "PLCF": "Gain/Loss Carry-Forward",
    # OT and any future NC code fall back to the raw code — a human names the map,
    # code doesn't guess (spec §2.4).
}

def _subsystem_label(code: str | None) -> str | None:
    if code is None:
        return None
    return _SUBSYSTEM_LABELS.get(code, code)
```

5b. `_hdr` 的返回 dict 里加(与 `status` 并列):
```python
        "source_subsystem": jv.source_subsystem,
        "source_subsystem_label": _subsystem_label(jv.source_subsystem),
```

5c. `_SORTABLE`(Task 1 加的)追加一项:
```python
    "source_subsystem": JournalVoucher.source_subsystem,
```

5d. `list_vouchers` 加过滤参数与 where:
```python
                        source_subsystem: str | None = Query(default=None),
```
在既有 `if source_doc_type:` 之后加:
```python
    if source_subsystem:
        base = base.where(JournalVoucher.source_subsystem == source_subsystem)
```

- [ ] **Step 6: 跑后端测试**

```bash
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  DATABASE_URL=postgresql+asyncpg://x:x@localhost/x JWT_SECRET_KEY=x \
  /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/test_nc_sync.py tests/test_jv_api.py -q
```
Expected: 全绿(含 2 新)

- [ ] **Step 7: 前端 System 列 + 过滤 + 接口字段**

7a. `finance/src/pages/finance/JvDetailModal.tsx` 的 `JvHeader` 接口,`status` 之后加:
```tsx
  source_subsystem?: string | null; source_subsystem_label?: string | null
```

7b. `JournalVouchersPage.tsx`:把 `Source` 列(第 176 行 `<th>Source</th>`)之后**新增一列** System(可排,列名 `source_subsystem`):
```tsx
                    <th className="px-3 py-2 w-32 cursor-pointer select-none" onClick={() => toggleSort('source_subsystem')}>
                      System{sort.col === 'source_subsystem' ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                    </th>
```
对应 `<tbody>` 每行的 Source `<td>` 之后加:
```tsx
                    <td className="px-3 py-2 text-neutral-600">{v.source_subsystem_label ?? '—'}</td>
```
(找到渲染 Source 的那个 `<td>` 照着加相邻列;`colSpan` 若有「No vouchers」空行要同步 +1。)

7c. 顶部过滤区加 System 下拉(与既有 status 过滤并列),选项 = 9 码全名:
```tsx
  const [subsystem, setSubsystem] = useState('')
  const SUBSYSTEMS = [
    ['GL','General Ledger'],['AP','Accounts Payable'],['AR','Accounts Receivable'],
    ['FA','Fixed Assets'],['CM','Cash Management'],['IA','Inventory Accounting'],
    ['EGL','Exchange Gain/Loss'],['PLCF','Gain/Loss Carry-Forward'],['OT','OT'],
  ] as const
```
`params` 的 useMemo 里加 `if (subsystem) p.set('source_subsystem', subsystem)`(并进依赖 + queryKey);过滤区渲染一个 `<select>`:
```tsx
          <select value={subsystem} onChange={(e) => { setPage(0); setSubsystem(e.target.value) }}
                  className={inputCls}>
            <option value="">All systems</option>
            {SUBSYSTEMS.map(([c, n]) => <option key={c} value={c}>{n}</option>)}
          </select>
```

- [ ] **Step 8: typecheck**

```bash
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance
npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
```
Expected: 0 errors。

- [ ] **Step 9: live-NC 冒烟(照 §14 教训 —— 绿测试不构成 NC 类型证据)**

驱动已提交的 `fetch_from_nc` 打真库(只读 Oracle,可与 pytest 并行):
```bash
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance-api
# 复用 §14 冒烟脚本的连接方式(读根 .env 的 NC65_*,注入 NC_*),调 fetch_from_nc(None),
# 断言 vouchers 元组长度=9、set(strip(pk_system)) 覆盖 GL/AP/AR/FA/CM/IA/EGL/OT/PLCF 共 9 码。
```
> 具体脚本照 scratchpad 里 §14 的 verify_vfetch.py 改;关键断言:9 个 subsystem 都出现、且都是 strip 过的短码(无尾随空格)。

- [ ] **Step 10: Commit**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync
git add finance-api/app/models/journal_voucher.py finance-api/alembic/versions/0024_jv_source_subsystem.py \
        finance-api/app/services/nc_sync.py finance-api/app/api/v1/journal_voucher.py \
        finance-api/tests/test_nc_sync.py finance-api/tests/test_jv_api.py \
        finance/src/pages/finance/JvDetailModal.tsx finance/src/pages/finance/JournalVouchersPage.tsx
git commit -m "feat(finance): import and show NC's System (GL/AP/AR/...) column

GL_VOUCHER.PK_SYSTEM is CHAR, so it needs the same strip TALLYDATE taught. Stores
the raw code, maps to a full name for display with a raw-code fallback for OT and
any future NC code. Nine subsystems, not eight — PLCF (Gain/Loss Carry-Forward)
was the one an earlier [:8] slice hid. Existing rows fill on the next full reload."
```

---

### Task 3: 科目余额维度展开四列汇总(#3)

**Files:**
- Modify: `finance-api/app/crud/account_balance.py`(`expand_by_dims`)
- Modify: `finance/src/pages/finance/AccountBalancePage.tsx`(展开子行)
- Test: `finance-api/tests/test_account_balance.py`

**Interfaces:**
- Produces: `expand_by_dims` 返回 `rows: [{keys, opening, period_debit, period_credit, closing}]`(取代原 `amount`)

- [ ] **Step 1: 写失败的测试**

追加到 `finance-api/tests/test_account_balance.py`(照该文件既有 expand 测试的造数 helper):

```python
async def test_expand_returns_four_columns_reconciling_with_parent(db_session):
    # 造:某科目某维度,前期(opening)+ 本期借贷。断言四列正确且子行合计=父级。
    # 用该文件既有的 JV/line seed helper (先 grep 一个既有 expand 测试照抄 setup)。
    #   dept A: 前期 debit 100 (opening=+100); 本期 debit 30, credit 50
    #   dept B: 本期 debit 0, credit 0 但前期 credit 40 (opening=-40, 本期冲平)
    out = await expand_by_dims(db_session, account_code=ACCT, period=PERIOD, dims=["department"])
    by = {r["keys"][0]["code"]: r for r in out["rows"]}
    assert by["A"]["opening"] == "100.00"
    assert by["A"]["period_debit"] == "30.00" and by["A"]["period_credit"] == "50.00"
    assert by["A"]["closing"] == "80.00"          # 100 + 30 - 50
    # dept B: 本期冲平但有期初 -> 仍出现,closing 非零
    assert "B" in by and by["B"]["closing"] == "-40.00"
    # 子行逐列合计 == 父级 (从主报表取 ACCT 那行对比)
    ab = await account_balance(db_session, PERIOD)   # 主报表函数名以实际为准
    parent = next(r for r in ab["rows"] if r["account_code"] == ACCT)
    from decimal import Decimal
    assert sum(Decimal(r["closing"]) for r in out["rows"]) == Decimal(parent["closing"])
```

> `ACCT`/`PERIOD`/seed helper 用该文件既有的;先 `grep -n "async def test_expand\|def _seed\|def _line\|account_balance(" tests/test_account_balance.py` 找一个既有 expand 用例照抄。主报表函数名(`account_balance`?)以文件实际为准。

- [ ] **Step 2: 跑测试确认失败**

```bash
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  DATABASE_URL=postgresql+asyncpg://x:x@localhost/x JWT_SECRET_KEY=x \
  /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/test_account_balance.py -k four_columns -v
```
Expected: FAIL(`KeyError: 'opening'` —— 现在只有 `amount`)

- [ ] **Step 3: 改 `expand_by_dims`**

`app/crud/account_balance.py`,把 `expand_by_dims` 的单期查询+组装整体替换为 opening/movement 双查(与父级 `sums()` 同构):

```python
async def expand_by_dims(db: AsyncSession, account_code: str, period: str,
                         dims: list[str]) -> dict:
    """② dynamic expansion, per NC's aux-item balance report: opening (cumulative
    before `period`) + this-period gross debit/credit + closing, grouped by the
    chosen dimension columns. _net is d-c and linear, so children reconcile with
    the parent account row column for column (spec §3)."""
    reg = _check_dims(dims)
    cols = [reg[d][0] for d in dims]

    async def grouped(where):
        q = (select(*cols,
                    func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                    func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
             .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
             .where(JournalVoucher.status == POSTED,
                    JournalVoucherLine.account_code == account_code, where)
             .group_by(*cols))
        # key = the dimension-id tuple; value = (debit, credit)
        return {tuple(r[:len(dims)]): (r[len(dims)], r[len(dims) + 1])
                for r in (await db.execute(q)).all()}

    opening = await grouped(JournalVoucher.fiscal_period < period)
    movement = await grouped(JournalVoucher.fiscal_period == period)
    all_keys = set(opening) | set(movement)     # 本期冲平但有期初的组也要出现

    # batch-load id -> (code, name) per dimension over the union of keys
    lookups: dict[str, dict] = {}
    for i, d in enumerate(dims):
        ids = {k[i] for k in all_keys if k[i] is not None}
        lookups[d] = await _resolve_dim(db, d, ids, reg)

    rows = []
    for key in all_keys:
        od, oc = opening.get(key, (0, 0))
        md, mc = movement.get(key, (0, 0))
        keys = []
        for i, d in enumerate(dims):
            vid = key[i]
            code, name = lookups[d].get(vid, (None, None))
            keys.append({"dim_code": d, "id": str(vid) if vid else None,
                         "code": code, "name": name})
        rows.append({"keys": keys,
                     "opening": _s(_net(od, oc)),
                     "period_debit": _s(md), "period_credit": _s(mc),
                     "closing": _s(_net(od, oc) + _net(md, mc))})
    rows.sort(key=lambda r: tuple(k["code"] or "￿" for k in r["keys"]))
    return {"account_code": account_code, "period": period, "dims": dims, "rows": rows}
```

> 保留原有 import(`_s`/`_net`/`_resolve_dim`/`_check_dims`/`func`/`select` 都已在文件里)。删掉原来那段单期 `q`+`raw`+组装。

- [ ] **Step 4: 跑测试确认通过**

```bash
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  DATABASE_URL=postgresql+asyncpg://x:x@localhost/x JWT_SECRET_KEY=x \
  /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/test_account_balance.py -v
```
Expected: 全绿(既有 expand 用例可能断言 `amount` 键 —— 若有,同步改为四列;既有用例数不得减少)。

> ⚠️ **既有测试有 4 处断言 `row["amount"]`,现在会 KeyError,必须改**(已实测:约在
> line 130、131、221、283,以实际为准 `grep -n '\["amount"\]' tests/test_account_balance.py`)。
> 转换规则:旧 `amount` = 本期发生净额 = `_net(period_debit, period_credit)`。这些既有用例的
> seed 只有本期数据、无前期(opening=0),故**旧 `amount` 值 == 新 `closing`**。逐处把
> `["amount"]` 改成 `["closing"]`、期望值不变即可(如 line 130 `by["MOH-01"]["closing"] == "100.00"`)。
> **改前先确认该 seed 确无前期数据**(若有 opening,closing≠旧 amount,需重算)。这是合理连带改动,不是新功能。

- [ ] **Step 5: 前端展开子行改四列**

`finance/src/pages/finance/AccountBalancePage.tsx`:

5a. `ExpandResp` 接口的 row 类型:`{ keys: ExpandKey[]; amount: string }` →
```tsx
{ keys: ExpandKey[]; opening: string; period_debit: string; period_credit: string; closing: string }
```

5b. 展开子行渲染(现在 `<td colSpan={4} ...>{money(row.amount)}</td>` 那处):改为与父级科目行对齐的多列。父级行有 opening/period_debit/period_credit/closing 四列 —— 子行照同样的列位渲染:
```tsx
            <td colSpan={2} className="px-3 py-1.5 pl-8 text-neutral-600">{label}</td>
            <td className="px-3 py-1.5 text-right font-mono">{money(row.opening)}</td>
            <td className="px-3 py-1.5 text-right font-mono">{money(row.period_debit)}</td>
            <td className="px-3 py-1.5 text-right font-mono">{money(row.period_credit)}</td>
            <td className="px-3 py-1.5 text-right font-mono">
              {Number(row.closing) === 0 ? <span className="text-neutral-400">Balanced</span> : money(row.closing)}
            </td>
```
> 具体列位/`colSpan` 要对齐父级科目行的表头列(先看父级行怎么排 opening/借/贷/期末四列,子行照抄列结构)。`money()` 是该文件既有格式化函数。

- [ ] **Step 6: typecheck**

```bash
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance
npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
```
Expected: 0 errors。

- [ ] **Step 7: Commit**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync
git add finance-api/app/crud/account_balance.py finance-api/tests/test_account_balance.py \
        finance/src/pages/finance/AccountBalancePage.tsx
git commit -m "feat(finance): account-balance dimension expansion shows opening/period/closing

The expansion summed only this period's movement while the account row shows the
cumulative closing balance, so an account whose period nets to zero (gross debit
== credit) expanded to all 0.00. Now it returns opening / period debit / period
credit / closing per dimension, mirroring NC's aux-item balance report; _net is
linear so the children reconcile with the parent column for column."
```

---

## 收尾:dev 验证(非 Task,但必做)

三项都做完,在 dev 上真跑(用户正在此测):
```bash
# 迁移 0024
cd /c/Project/uniops && docker compose -f docker-compose.dev.yml exec finance-api alembic upgrade head
# System 列补齐存量:再跑一次全量重灌 (§14.5 / spec §4)
#   dev 前端点 NC Sync (voucher) full,或跑 scratchpad 的 dev_reload.py
# 核对:
docker exec uniops_postgres psql -U epms -d epms -c "
select source_subsystem, count(*) from journal_vouchers
where nc_source_pk is not null group by source_subsystem order by 2 desc;"
# 预期 9 个 subsystem 分布 (AP 最多, GL 次之, ... PLCF 最少), 无 NULL
```
- #1:JV 列表点各列头 → 排序生效、箭头正确、翻页稳定。
- #3:Account Balance 展开 660101 → 四列有数、子行合计 = 父级、本期冲平的维度显 "Balanced"。
