# NC 凭证手工同步按钮(全量/增量) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 凭证中心页加「NC Sync」按钮(仅 system_admin),UI 触发 NC65 凭证的全量(清空重灌,须输入 FULL RELOAD)或增量(按 nc_source_pk 只补新 + creationtime 水位)导入,后台任务执行、`nc_sync_runs` 表记录审计/水位/进度。

**Architecture:** 把已对账 0 差异的 `scripts/nc_migration/voucher_import.py` 抽取/转换逻辑移植进 `app/services/nc_sync.py`(同步函数:oracledb 读 NC + psycopg2 写本库),API 层用 `run_in_executor` 后台跑;NC 连接走 settings(env 缺失=功能隐藏);前端凭证中心 headerActions 按钮 + 弹窗轮询进度。原脚本保留。

**Tech Stack:** FastAPI + SQLAlchemy async(状态读)/psycopg2(worker 写) + oracledb;React 19 + react-query;pytest(NC 侧注入 fake extract,不连真 Oracle)。

## Global Constraints

- **分支** `feature/finance-jv-subsystem`(主目录 c:/Project/uniops);开工前 `git branch --show-current` 确认。
- **⚠️ 工作树有用户未提交 WIP**(epms/、epms-api/、expense-api/、portal/):**只 `git add <指定文件>`,禁止 `-A`/`.`/`stash`/`reset`/`checkout --`**。
- **UI 文案纯英文**;金额/计数由后端来的字符串须 `Number()` 强转再格式化。
- **后端测试命令**:`cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest <file> -q`;**单进程串行,禁止后台/并发 pytest**。
- **前端 typecheck**:`cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`,必须显式确认 exit 0。
- **新迁移前必查 `alembic heads`**(当前唯一 head=`0017_journal_vouchers`,Task 1 内含验证步)。
- **改 finance-api 代码要 `docker restart uniops_finance_api`**;改 requirements 要 `--build` 重建(旧镜像缺依赖的坑已踩过)。
- 增量水位字段 = NC `GL_VOUCHER.creationtime`(char(19) `YYYY-MM-DD HH:MM:SS`);**GL_VOUCHER 没有 ts 列**。查询 `creationtime >= :wm` + pk 跳重。
- NC 五项配置:`nc_host / nc_port(默认1521) / nc_service / nc_user / nc_password`;`configured` = host+service+user+password 全有值。
- 确认词常量:`FULL RELOAD`(全大写含空格,前后端一致)。

---

## 现状(实现者需知)

- 被移植脚本:`finance-api/scripts/nc_migration/voucher_import.py`(保留不删)。其中常量(PK_BOOK/AUX_*/CC_BY_CODE/CC_BY_DEPT)、`_net_side`、`resolve_dims`、`load_aux`、读写 SQL 都要照搬进服务(Task 2/3 给出完整移植代码)。
- finance-api settings:`app/core/config.py` pydantic BaseSettings(env_file=.env)。
- alembic 用独立 `alembic_version_finance` 表;测试 conftest 对 finance_test 库跑真迁移(nc_sync_runs 由新迁移自动建,conftest 不用改)。
- 路由聚合:`app/api/v1/__init__.py` 的 `api_router`;模型 metadata 注册:`app/main.py` 第 8 行 import 列表。
- JWT:测试里 `jwt.encode({'sub','role','exp'}, settings.jwt_secret_key)`(参照 tests/test_jv_api.py 的 `_token/_h`)。
- 前端:凭证中心页 `finance/src/pages/finance/JournalVouchersPage.tsx` 用 `PortalChromeLayout`(支持 `headerActions` prop,当前未传);API client `financeApi`(`@/lib/api`,自动加 `/finance/v1` 前缀)。

---

### Task 1: `nc_sync_runs` 迁移 + `NcSyncRun` 模型

**Files:**
- Create: `finance-api/alembic/versions/0018_nc_sync_runs.py`
- Create: `finance-api/app/models/nc_sync.py`
- Modify: `finance-api/app/main.py`(第 8 行模型 import 列表加 `nc_sync`)
- Test: `finance-api/tests/test_nc_sync.py`(新文件)

**Interfaces:**
- Produces: 模型 `app.models.nc_sync.NcSyncRun`(表 `nc_sync_runs`),字段:`id/created_at/updated_at`(UUIDPrimaryKey+TimestampMixin)、`mode:str`、`status:str`、`started_by:uuid|None`、`started_at:datetime`、`finished_at:datetime|None`、`watermark_from:str|None`、`watermark_to:str|None`、`vouchers_deleted:int`、`vouchers_inserted:int`、`lines_inserted:int`、`dims_inserted:int`、`unmapped_cc_count:int`、`error:str|None`。常量 `RUNNING="running"`,`SUCCESS="success"`,`FAILED="failed"`。

- [ ] **Step 1: 验证 alembic 链尾**

Run: `cd c:/Project/uniops/finance-api && ./.venv/Scripts/python -m alembic heads`
Expected: 恰好一行 `0017_journal_vouchers (head)`。若不是,STOP 上报,不要猜链。

- [ ] **Step 2: 写失败测试**(新建 `tests/test_nc_sync.py`)

```python
"""NC sync runs — model/migration + service + API tests."""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.nc_sync import RUNNING, SUCCESS, NcSyncRun


async def test_nc_sync_run_roundtrip(db_session):
    run = NcSyncRun(id=uuid.uuid4(), mode="incremental", status=RUNNING,
                    started_by=uuid.uuid4(),
                    started_at=datetime.now(timezone.utc))
    db_session.add(run)
    await db_session.flush()
    got = (await db_session.execute(
        select(NcSyncRun).where(NcSyncRun.id == run.id))).scalar_one()
    assert got.status == RUNNING
    assert got.vouchers_inserted == 0          # server default
    assert got.watermark_to is None
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_sync.py -q`
Expected: FAIL(`ModuleNotFoundError: app.models.nc_sync`)

- [ ] **Step 4: 写模型**(`app/models/nc_sync.py`,完整文件)

```python
"""NC65 sync run log — audit + incremental watermark + live progress.

One row per UI-triggered sync (full / incremental). The worker updates the
insert counters as it goes, so the row doubles as the progress feed.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"


class NcSyncRun(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "nc_sync_runs"

    mode: Mapped[str] = mapped_column(String(15), nullable=False)     # full | incremental
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=RUNNING, index=True)
    started_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    watermark_from: Mapped[str | None] = mapped_column(String(19), nullable=True)
    watermark_to: Mapped[str | None] = mapped_column(String(19), nullable=True)
    vouchers_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    vouchers_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    lines_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    dims_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    unmapped_cc_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 5: 写迁移**(`alembic/versions/0018_nc_sync_runs.py`,完整文件)

```python
"""nc_sync_runs — NC65 voucher sync audit/watermark/progress log

Revision ID: 0018_nc_sync_runs
Revises: 0017_journal_vouchers
Create Date: 2026-07-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0018_nc_sync_runs"
down_revision = "0017_journal_vouchers"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nc_sync_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("mode", sa.String(15), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("started_by", UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watermark_from", sa.String(19), nullable=True),
        sa.Column("watermark_to", sa.String(19), nullable=True),
        sa.Column("vouchers_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vouchers_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dims_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unmapped_cc_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("ix_nc_sync_runs_status", "nc_sync_runs", ["status"])


def downgrade():
    op.drop_index("ix_nc_sync_runs_status", table_name="nc_sync_runs")
    op.drop_table("nc_sync_runs")
```

- [ ] **Step 6: 注册模型 metadata** —— `app/main.py` 第 8 行 import 列表按字母序加 `nc_sync`:

```python
from app.models import admin_audit_log, ap_invoice, bank, coa, fiscal_period, journal_voucher, mirrors, nc_sync, pa, payment, payment_batch, posting  # noqa: F401 — register with metadata
```

(若该行当前没有 `journal_voucher`,保持原有项不动,只**追加** `nc_sync`。)

- [ ] **Step 7: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_sync.py -q`
Expected: 1 passed

- [ ] **Step 8: 对本地 dev 库应用迁移**(dev epms 库,非测试库)

Run: `cd c:/Project/uniops/finance-api && ./.venv/Scripts/python -m alembic upgrade head`
Expected: `Running upgrade 0017_journal_vouchers -> 0018_nc_sync_runs`

- [ ] **Step 9: Commit(surgical add)**

```bash
cd c:/Project/uniops
git add finance-api/alembic/versions/0018_nc_sync_runs.py finance-api/app/models/nc_sync.py finance-api/app/main.py finance-api/tests/test_nc_sync.py
git commit -m "feat(finance): nc_sync_runs table + model (NC sync audit/watermark/progress)"
```

---

### Task 2: 同步服务核心 —— NC 配置 + 纯转换逻辑(移植)

**Files:**
- Modify: `finance-api/app/core/config.py`(加 NC 五项 settings)
- Create: `finance-api/app/services/nc_sync.py`
- Test: `finance-api/tests/test_nc_sync.py`(追加)

**Interfaces:**
- Consumes: `app/core/config.settings`。
- Produces(Task 3/4 依赖,签名精确):
  - settings 新字段:`nc_host: str | None = None`、`nc_port: int = 1521`、`nc_service: str | None = None`、`nc_user: str | None = None`、`nc_password: str | None = None`。
  - `nc_sync.nc_configured() -> bool`(host+service+user+password 全非空)。
  - `@dataclass NcExtract`:`ccy: dict`(pk_currtype→code)、`aux: dict`(freevalueid→(dept_code, cc_code, io_code))、`vouchers: list[tuple]`(pk, year, period, num, explanation, prepareddate, creationtime)、`details: list[tuple]`(pk_voucher, detailindex, accountcode, dr, cr, ldr, lcr, pk_currtype, excrate1, explanation, assid)、`max_creationtime: str | None`。
  - `transform(extract, uni_cc, uni_dept, uni_ba, skip_pks: set) -> tuple[list, list, list, int]` → `(vouchers, lines, dims, unmapped_cc)`;vouchers 是 dict(id/jv_number/period/vdate/summary/nc_pk),lines/dims 是与脚本 load() 同结构的 tuple。
  - 常量 `FULL_CONFIRM = "FULL RELOAD"`、`PK_BOOK`。

- [ ] **Step 1: settings 加 NC 字段** —— `app/core/config.py` 在 `budget_api_url` 行后插入:

```python
    # NC65 read-only connection (sync button). All-or-nothing: nc_configured()
    # in services/nc_sync.py hides the feature when any of these is missing.
    nc_host: str | None = None
    nc_port: int = 1521
    nc_service: str | None = None
    nc_user: str | None = None
    nc_password: str | None = None
```

- [ ] **Step 2: 写失败测试**(追加到 `tests/test_nc_sync.py`;纯函数,无 DB)

```python
def test_nc_configured_all_or_nothing(monkeypatch):
    from app.core.config import settings
    from app.services import nc_sync
    for f in ("nc_host", "nc_service", "nc_user", "nc_password"):
        monkeypatch.setattr(settings, f, "x")
    assert nc_sync.nc_configured() is True
    monkeypatch.setattr(settings, "nc_password", None)
    assert nc_sync.nc_configured() is False


def _mini_extract():
    from app.services.nc_sync import NcExtract
    # one voucher, two lines (5101 with cc E01 -> MOH-0106-E01; 2202 no dims);
    # line 1 is NC "both-sided" (dr and cr both >0) -> must net to one side.
    return NcExtract(
        ccy={"CADPK": "CAD"},
        aux={"ASS1": ("0104", "E01", "CRM004")},
        vouchers=[("NCPK1", "2026", "07", 12, "test voucher",
                   "2026-07-10 09:00:00", "2026-07-11 08:00:00")],
        details=[
            ("NCPK1", 1, "5101", 150, 50, 150, 50, "CADPK", 1, "expense", "ASS1"),
            ("NCPK1", 2, "2202", 0, 100, 0, 100, "CADPK", 1, "payable", None),
        ],
        max_creationtime="2026-07-11 08:00:00",
    )


def test_transform_maps_dims_and_nets_sides():
    from decimal import Decimal
    from app.services.nc_sync import transform
    cc_id, dept_id, ba_id = object(), object(), object()
    vouchers, lines, dims, unmapped = transform(
        _mini_extract(), uni_cc={"MOH-0106-E01": cc_id},
        uni_dept={"0104": dept_id}, uni_ba={"CRM004": ba_id}, skip_pks=set())
    assert len(vouchers) == 1 and vouchers[0]["jv_number"] == "记-202607-12"
    assert vouchers[0]["nc_pk"] == "NCPK1"
    l1 = next(l for l in lines if l[2] == 1)
    assert (l1[5], l1[6]) == (Decimal("100"), Decimal("0"))   # netted to debit
    assert l1[11] is cc_id and l1[12] is dept_id
    assert len(dims) == 1 and dims[0][2] == "income_expense_item" and dims[0][3] is ba_id
    assert unmapped == 0


def test_transform_skips_existing_and_counts_unmapped():
    from app.services.nc_sync import NcExtract, transform
    ex = _mini_extract()
    # skip the only voucher -> nothing out
    v, l, d, _ = transform(ex, {}, {}, {}, skip_pks={"NCPK1"})
    assert v == [] and l == [] and d == []
    # unknown cc code -> unmapped counted (line still produced, cc_id None)
    ex2 = NcExtract(ccy=ex.ccy, aux={"ASS1": ("", "ZZZ", "")},
                    vouchers=ex.vouchers, details=ex.details[:1],
                    max_creationtime=ex.max_creationtime)
    v2, l2, _, unmapped2 = transform(ex2, {}, {}, {}, skip_pks=set())
    assert len(l2) == 1 and l2[0][11] is None and unmapped2 == 1
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_sync.py -q`
Expected: 新增 3 个 FAIL(`ModuleNotFoundError: app.services.nc_sync`),Task 1 的 1 个仍 PASS

- [ ] **Step 4: 写服务核心**(`app/services/nc_sync.py`,完整文件;常量与算法逐行移植自 scripts/nc_migration/voucher_import.py)

```python
"""NC65 voucher sync service (UI-triggered full / incremental).

Extraction + transform ported verbatim from scripts/nc_migration/voucher_import.py
(reconciled 0-diff against NC per-account nets, 2026-07-11..13). The whole run is
synchronous (oracledb reads NC, psycopg2 writes our Postgres) and is executed on a
worker thread by the API layer. The CLI script stays for cut-over/emergency use.
"""
import uuid
from dataclasses import dataclass
from decimal import Decimal

from app.core.config import settings

PK_BOOK = "1001A1100000003CGCBX"            # Canada Royal Milk accounting book
FULL_CONFIRM = "FULL RELOAD"

# NC 辅助核算 global type pks (first 20 chars of a GL_FREEVALUE.typevalueN):
AUX_DEPT = "0001Z0100000000005CS"           # 部门 -> ORG_DEPT
AUX_COSTCENTER = "1003Z31000000000SP6J"     # 成本中心 -> RESA_COSTCENTER
AUX_IOITEM = "0001Z0100000000005CZ"         # 收支项目 -> BD_INOUTBUSICLASS

# Curated NC -> EPMS cost-center map (user, 2026-07-12): code decides when present,
# else classify by department.
CC_BY_CODE = {
    "E01": "MOH-0106-E01", "E02": "MOH-0106-E01", "E03": "MOH-0106-E01",
    "E04": "MOH-0106-E01", "E05": "MOH-0106-E01", "E06": "MOH-0106-E01",
    "E07": "MOH-0106-E01", "ENG": "MOH-0106-E01",
    "P01": "MOH-0104-P01", "P02": "MOH-0104-P02", "P03": "MOH-0104-P03",
    "PD": "MOH-0104-P01",
    "Q01": "MOH-0105-LAB", "Q02": "MOH-0105-LAB", "QA": "GA-0105",
    "S02": "MOH-0107-S02", "S03": "SELL-0107-S03", "SC": "GA-0107",
    "H01": "MOH-0101", "HR": "GA-0101",
}
CC_BY_DEPT = {
    "0100": "GA-0100", "0101": "GA-0101", "0103": "GA-0103",
    "0105": "GA-0105", "0107": "GA-0107", "0109": "RD-0109",
    "0110": "SELL-0110", "0111": "SELL-0111", "0112": "SELL-0112", "0113": "SELL-0113",
}


def nc_configured() -> bool:
    return all([settings.nc_host, settings.nc_service, settings.nc_user, settings.nc_password])


@dataclass
class NcExtract:
    """Raw NC reads, pre-transform. Tests inject a fake one."""
    ccy: dict           # pk_currtype -> currency code
    aux: dict           # freevalueid -> (dept_code, cc_code, io_code)
    vouchers: list      # (pk, year, period, num, explanation, prepareddate, creationtime)
    details: list       # (pk_voucher, detailindex, accountcode, dr, cr, ldr, lcr,
                        #  pk_currtype, excrate1, explanation, assid)
    max_creationtime: str | None


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def _net_side(dr: Decimal, cr: Decimal) -> tuple[Decimal, Decimal]:
    n = dr - cr
    return (n, Decimal("0")) if n >= 0 else (Decimal("0"), -n)


def _resolve_dims(assid, aux, uni_cc, uni_dept, uni_ba):
    """-> (cost_center_id, department_id, io_code, budget_account_id, had_cc_hint)."""
    d, c, io = aux.get(assid, ("", "", ""))
    epms = CC_BY_CODE.get(c) if c else CC_BY_DEPT.get(d)
    return (uni_cc.get(epms) if epms else None,
            uni_dept.get(d) if d else None,
            io or None,
            uni_ba.get(io) if io else None,
            bool(c or d))


def transform(extract: NcExtract, uni_cc: dict, uni_dept: dict, uni_ba: dict,
              skip_pks: set) -> tuple[list, list, list, int]:
    """NC rows -> (voucher dicts, line tuples, dim tuples, unmapped_cc count).
    Skips vouchers whose pk is in skip_pks (incremental pk-dedup)."""
    pk2id, vouchers = {}, []
    for pk, year, period, num, expl, pdate, _ctime in extract.vouchers:
        if pk in skip_pks:
            continue
        jid = uuid.uuid4()
        pk2id[pk] = jid
        vdate = (pdate[:10] if pdate and len(pdate) >= 10 else f"{year}-{period}-01")
        num_s = str(int(num)) if num is not None else "0"
        vouchers.append({
            "id": jid, "jv_number": f"记-{year}{period}-{num_s}",
            "period": f"{year}-{period}", "vdate": vdate,
            "summary": (expl or "")[:255], "nc_pk": pk,
        })

    lines, dims, unmapped = [], [], 0
    for pk, idx, acct, dr, cr, ldr, lcr, curr, rate, expl, assid in extract.details:
        jid = pk2id.get(pk)
        if jid is None:
            continue
        odr, ocr = _net_side(_d(dr), _d(cr))
        ldr_, lcr_ = _net_side(_d(ldr), _d(lcr))
        cc_id, dept_id, io_code, ba_id, had_hint = _resolve_dims(
            assid, extract.aux, uni_cc, uni_dept, uni_ba)
        if had_hint and cc_id is None:
            unmapped += 1
        lid = uuid.uuid4()
        lines.append((
            lid, jid, int(idx or 0), (acct or "").strip() or None,
            (expl or "")[:255], odr, ocr, ldr_, lcr_,
            extract.ccy.get(curr, "CAD"), _d(rate) if rate else Decimal("1"),
            cc_id, dept_id))
        if io_code:
            dims.append((uuid.uuid4(), lid, "income_expense_item", ba_id, io_code))
    return vouchers, lines, dims, unmapped


def fetch_from_nc(watermark: str | None) -> NcExtract:
    """Live NC read (oracledb, read-only). watermark: only vouchers with
    creationtime >= watermark (pk-dedup upstream makes >= safe)."""
    import oracledb
    oracledb.defaults.fetch_decimals = True
    dsn = oracledb.makedsn(settings.nc_host, settings.nc_port,
                           service_name=settings.nc_service)
    con = oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)
    try:
        cur = con.cursor()
        cur.execute("select pk_currtype, code from NCSC.BD_CURRTYPE")
        ccy = {pk: code for pk, code in cur.fetchall()}

        cur.execute("select pk_dept, code from NCSC.ORG_DEPT")
        dept = {pk: code for pk, code in cur.fetchall()}
        cur.execute("select pk_costcenter, cccode from NCSC.RESA_COSTCENTER")
        cc = {pk: code for pk, code in cur.fetchall()}
        cur.execute("select pk_inoutbusiclass, code from NCSC.BD_INOUTBUSICLASS")
        io = {pk: code for pk, code in cur.fetchall()}
        cur.execute("select freevalueid, typevalue1, typevalue2, typevalue3, typevalue4, "
                    "typevalue5, typevalue6, typevalue7, typevalue8, typevalue9 "
                    "from NCSC.GL_FREEVALUE")
        aux = {}
        for row in cur:
            fid, tvs = row[0], row[1:]
            dcode = ccode = iocode = ""
            for tv in tvs:
                if not tv or len(tv) < 40:
                    continue
                tpk, vpk = tv[:20], tv[20:40]
                if tpk == AUX_DEPT:
                    dcode = dept.get(vpk, "")
                elif tpk == AUX_COSTCENTER:
                    ccode = cc.get(vpk, "")
                elif tpk == AUX_IOITEM:
                    iocode = io.get(vpk, "")
            aux[fid] = (dcode, ccode, iocode)

        vq = ("select pk_voucher, year, period, num, explanation, prepareddate, "
              "creationtime from NCSC.GL_VOUCHER where pk_accountingbook = :b")
        if watermark:
            cur.execute(vq + " and creationtime >= :wm", b=PK_BOOK, wm=watermark)
        else:
            cur.execute(vq, b=PK_BOOK)
        vouchers = list(cur.fetchall())
        max_ct = max((v[6] for v in vouchers if v[6]), default=None)

        # details: fetch the whole book; transform() filters by pk2id membership.
        cur.execute(
            "select pk_voucher, detailindex, accountcode, debitamount, creditamount, "
            "localdebitamount, localcreditamount, pk_currtype, excrate1, explanation, assid "
            "from NCSC.GL_DETAIL where pk_accountingbook = :b", b=PK_BOOK)
        details = list(cur.fetchall())
    finally:
        con.close()
    return NcExtract(ccy=ccy, aux=aux, vouchers=vouchers, details=details,
                     max_creationtime=max_ct)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_sync.py -q`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
cd c:/Project/uniops
git add finance-api/app/core/config.py finance-api/app/services/nc_sync.py finance-api/tests/test_nc_sync.py
git commit -m "feat(finance): NC sync service core — settings, extract dataclass, ported transform"
```

---

### Task 3: run 工作器(水位/跳重/清空重灌/进度/单飞)

**Files:**
- Modify: `finance-api/app/services/nc_sync.py`(追加 worker 部分)
- Test: `finance-api/tests/test_nc_sync.py`(追加)

**Interfaces:**
- Consumes: Task 2 的 `NcExtract/transform/fetch_from_nc/nc_configured`、Task 1 的 `nc_sync_runs` 表。
- Produces(Task 4 依赖):
  - `class SyncAlreadyRunning(Exception)`。
  - `start_run(mode: str, started_by: uuid.UUID, *, fetch=fetch_from_nc, pg_dsn: str | None = None, run_worker: bool = True) -> uuid.UUID`:清陈旧 running(>30min→failed 'abandoned')→有 running 抛 `SyncAlreadyRunning`→插入 running 行→`run_worker=True` 时同步执行 `_run_worker`(**注意:start_run 本身是同步函数,线程化由 API 层做**);返回 run_id。`pg_dsn=None` 时用 `_pg_dsn()`(从 settings.database_url 推导 libpq DSN)。
  - `_run_worker(run_id, mode, fetch, dsn) -> None`:成功标 success+计数+水位;异常标 failed+error,主事务回滚。

- [ ] **Step 1: 写失败测试**(追加;用 psycopg2 直连 finance_test,同 conftest 的连接参数)

```python
import os
import psycopg2

_TEST_DSN = (f"host={os.getenv('TEST_PG_HOST', 'localhost')} "
             f"port={os.getenv('TEST_PG_PORT', '5432')} "
             f"dbname={os.getenv('TEST_FINANCE_DB', 'finance_test')} "
             f"user={os.getenv('TEST_PG_USER', 'epms')} "
             f"password={os.getenv('TEST_PG_PASSWORD', 'epms_dev')}")


def _pg(sql, params=()):
    con = psycopg2.connect(_TEST_DSN); con.autocommit = True
    cur = con.cursor(); cur.execute(sql, params)
    out = cur.fetchall() if cur.description else None
    con.close(); return out


async def test_start_run_incremental_inserts_and_sets_watermark(db_session):
    # db_session fixture has migrated finance_test; worker writes via its own psycopg2 conn
    from app.services import nc_sync
    run_id = nc_sync.start_run("incremental", uuid.uuid4(),
                               fetch=lambda wm: _mini_extract(), pg_dsn=_TEST_DSN)
    rows = _pg("select status, vouchers_inserted, lines_inserted, dims_inserted, "
               "watermark_to from nc_sync_runs where id = %s", (run_id,))
    assert rows[0] == ("success", 1, 2, 1, "2026-07-11 08:00:00")
    assert _pg("select count(*) from journal_vouchers where nc_source_pk = 'NCPK1'")[0][0] == 1
    # second incremental: same extract -> pk skipped, 0 inserted, watermark kept
    run2 = nc_sync.start_run("incremental", uuid.uuid4(),
                             fetch=lambda wm: _mini_extract(), pg_dsn=_TEST_DSN)
    rows2 = _pg("select status, vouchers_inserted, watermark_from, watermark_to "
                "from nc_sync_runs where id = %s", (run2,))
    assert rows2[0] == ("success", 0, "2026-07-11 08:00:00", "2026-07-11 08:00:00")


async def test_full_clears_and_reloads(db_session):
    from app.services import nc_sync
    nc_sync.start_run("incremental", uuid.uuid4(),
                      fetch=lambda wm: _mini_extract(), pg_dsn=_TEST_DSN)
    run_id = nc_sync.start_run("full", uuid.uuid4(),
                               fetch=lambda wm: _mini_extract(), pg_dsn=_TEST_DSN)
    rows = _pg("select status, vouchers_deleted, vouchers_inserted "
               "from nc_sync_runs where id = %s", (run_id,))
    assert rows[0] == ("success", 1, 1)
    assert _pg("select count(*) from journal_vouchers where nc_source_pk is not null")[0][0] == 1


async def test_concurrent_run_blocked_and_stale_recovered(db_session):
    from datetime import timedelta
    from app.services import nc_sync
    from app.services.nc_sync import SyncAlreadyRunning
    # plant a fresh 'running' row -> new run must be refused
    _pg("insert into nc_sync_runs (id, mode, status, started_at, created_at, updated_at) "
        "values (%s, 'incremental', 'running', now(), now(), now())", (uuid.uuid4(),))
    with pytest.raises(SyncAlreadyRunning):
        nc_sync.start_run("incremental", uuid.uuid4(),
                          fetch=lambda wm: _mini_extract(), pg_dsn=_TEST_DSN)
    # make it stale (>30 min) -> auto-failed, new run proceeds
    _pg("update nc_sync_runs set started_at = now() - interval '31 minutes' "
        "where status = 'running'")
    run_id = nc_sync.start_run("incremental", uuid.uuid4(),
                               fetch=lambda wm: _mini_extract(), pg_dsn=_TEST_DSN)
    assert _pg("select status from nc_sync_runs where id = %s", (run_id,))[0][0] == "success"
    assert _pg("select count(*) from nc_sync_runs where status='failed' "
               "and error='abandoned'")[0][0] == 1


async def test_worker_failure_marks_failed_and_rolls_back(db_session):
    from app.services import nc_sync

    def boom(wm):
        raise RuntimeError("NC unreachable")

    run_id = nc_sync.start_run("incremental", uuid.uuid4(), fetch=boom, pg_dsn=_TEST_DSN)
    rows = _pg("select status, error from nc_sync_runs where id = %s", (run_id,))
    assert rows[0][0] == "failed" and "NC unreachable" in rows[0][1]
    assert _pg("select count(*) from journal_vouchers where nc_source_pk is not null")[0][0] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_sync.py -q`
Expected: 新增 4 个 FAIL(`AttributeError: ... start_run`),原 4 个 PASS

- [ ] **Step 3: 实现 worker**(追加到 `app/services/nc_sync.py` 末尾)

```python
# ── run lifecycle (worker) ─────────────────────────────────────────────────────────
import threading
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import execute_values, register_uuid
from sqlalchemy.engine.url import make_url

register_uuid()

_start_lock = threading.Lock()
STALE_AFTER = timedelta(minutes=30)
_CHUNK = 5000


class SyncAlreadyRunning(Exception):
    pass


def _pg_dsn() -> str:
    u = make_url(settings.database_url)
    return (f"host={u.host} port={u.port or 5432} dbname={u.database} "
            f"user={u.username} password={u.password}")


def _mark(dsn, run_id, **fields):
    """Small autocommit update on the run row (progress + terminal states)."""
    con = psycopg2.connect(dsn); con.autocommit = True
    cur = con.cursor()
    sets = ", ".join(f"{k} = %s" for k in fields)
    cur.execute(f"update nc_sync_runs set {sets}, updated_at = now() where id = %s",
                (*fields.values(), run_id))
    con.close()


def start_run(mode: str, started_by, *, fetch=fetch_from_nc,
              pg_dsn: str | None = None, run_worker: bool = True):
    """Single-flight gate + run-row insert. Synchronous — the API layer threads it.
    Returns the new run id. Raises SyncAlreadyRunning if a live run exists."""
    dsn = pg_dsn or _pg_dsn()
    with _start_lock:
        con = psycopg2.connect(dsn); con.autocommit = True
        cur = con.cursor()
        # auto-fail stale 'running' rows (crashed container), then check liveness
        cur.execute("update nc_sync_runs set status = 'failed', error = 'abandoned', "
                    "finished_at = now(), updated_at = now() "
                    "where status = 'running' and started_at < %s",
                    (datetime.now(timezone.utc) - STALE_AFTER,))
        cur.execute("select id from nc_sync_runs where status = 'running'")
        if cur.fetchone():
            con.close()
            raise SyncAlreadyRunning("an NC sync is already running")
        run_id = uuid.uuid4()
        cur.execute("insert into nc_sync_runs (id, mode, status, started_by, started_at, "
                    "created_at, updated_at) values (%s, %s, 'running', %s, now(), now(), now())",
                    (run_id, mode, started_by))
        con.close()
    if run_worker:
        _run_worker(run_id, mode, fetch, dsn)
    return run_id


def _run_worker(run_id, mode: str, fetch, dsn: str) -> None:
    try:
        con = psycopg2.connect(dsn); con.autocommit = False
        cur = con.cursor()
        cur.execute("select nc_source_pk from journal_vouchers where nc_source_pk is not null")
        existing = {r[0] for r in cur.fetchall()}
        cur.execute("select watermark_to from nc_sync_runs where status = 'success' "
                    "and watermark_to is not null order by started_at desc limit 1")
        row = cur.fetchone()
        prev_wm = row[0] if row else None

        extract = fetch(prev_wm if mode == "incremental" else None)

        cur.execute("select code, id from cost_centers")
        uni_cc = dict(cur.fetchall())
        cur.execute("select code, id from departments")
        uni_dept = dict(cur.fetchall())
        cur.execute("select code, id from budget_accounts")
        uni_ba = dict(cur.fetchall())

        skip = existing if mode == "incremental" else set()
        vouchers, lines, dims, unmapped = transform(extract, uni_cc, uni_dept, uni_ba, skip)

        deleted = 0
        if mode == "full":
            cur.execute("delete from journal_vouchers where nc_source_pk is not null")
            deleted = cur.rowcount

        tot: dict = {}
        for _, jid, _, _, _, dr, crr, ldr, lcr, _, _, _, _ in lines:
            t = tot.setdefault(jid, [Decimal("0")] * 4)
            t[0] += dr; t[1] += crr; t[2] += ldr; t[3] += lcr

        v_rows = [(v["id"], v["jv_number"], "记", v["vdate"], v["period"], v["summary"],
                   "posted", "nc", "nc_voucher", v["jv_number"], v["nc_pk"],
                   *(tot.get(v["id"], [Decimal("0")] * 4))) for v in vouchers]
        for i in range(0, len(v_rows), _CHUNK):
            execute_values(cur,
                "insert into journal_vouchers "
                "(id, jv_number, voucher_word, voucher_date, fiscal_period, summary, status, "
                " source_service, source_doc_type, source_doc_number, nc_source_pk, "
                " total_debit, total_credit, total_local_debit, total_local_credit, "
                " created_at, updated_at) values %s",
                v_rows[i:i + _CHUNK],
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), now())")
            _mark(dsn, run_id, vouchers_inserted=min(i + _CHUNK, len(v_rows)))
        for i in range(0, len(lines), _CHUNK):
            execute_values(cur,
                "insert into journal_voucher_lines "
                "(id, jv_id, line_no, account_code, summary, orig_debit, orig_credit, "
                " local_debit, local_credit, currency, fx_rate, cost_center_id, department_id, "
                " created_at, updated_at) values %s",
                lines[i:i + _CHUNK],
                template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), now())")
            _mark(dsn, run_id, lines_inserted=min(i + _CHUNK, len(lines)))
        for i in range(0, len(dims), _CHUNK):
            execute_values(cur,
                "insert into jv_line_dimensions "
                "(id, jv_line_id, dim_code, value_id, value_text, created_at, updated_at) "
                "values %s",
                dims[i:i + _CHUNK],
                template="(%s,%s,%s,%s,%s, now(), now())")
            _mark(dsn, run_id, dims_inserted=min(i + _CHUNK, len(dims)))

        con.commit(); con.close()
        _mark(dsn, run_id, status="success", finished_at=datetime.now(timezone.utc),
              vouchers_deleted=deleted, vouchers_inserted=len(vouchers),
              lines_inserted=len(lines), dims_inserted=len(dims),
              unmapped_cc_count=unmapped, watermark_from=prev_wm,
              watermark_to=extract.max_creationtime or prev_wm)
    except Exception as e:  # noqa: BLE001 — terminal state must always be written
        try:
            con.rollback(); con.close()
        except Exception:
            pass
        _mark(dsn, run_id, status="failed", error=str(e)[:2000],
              finished_at=datetime.now(timezone.utc))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_sync.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
cd c:/Project/uniops
git add finance-api/app/services/nc_sync.py finance-api/tests/test_nc_sync.py
git commit -m "feat(finance): NC sync worker — watermark/pk-dedup incremental, full clear+reload, single-flight, progress"
```

---

### Task 4: API 端点 `/nc-sync`(status + trigger)

**Files:**
- Create: `finance-api/app/api/v1/nc_sync.py`
- Modify: `finance-api/app/api/v1/__init__.py`(注册 router)
- Test: `finance-api/tests/test_nc_sync.py`(追加)

**Interfaces:**
- Consumes: Task 3 的 `start_run/SyncAlreadyRunning/nc_configured/FULL_CONFIRM`、Task 1 的 `NcSyncRun`。
- Produces(前端 Task 5 依赖):
  - `GET /finance/v1/nc-sync/status`(任何登录用户)→ `{can_sync, configured, current_run, last_run}`;run 序列化字段:`id/mode/status/started_at/finished_at/watermark_from/watermark_to/vouchers_deleted/vouchers_inserted/lines_inserted/dims_inserted/unmapped_cc_count/error`。
  - `POST /finance/v1/nc-sync` body `{mode, confirm?}`:非 admin 403;未配置 503;full 且 confirm≠"FULL RELOAD" 422;并发 409;成功 202 `{run_id}`。

- [ ] **Step 1: 写失败测试**(追加;`client` fixture 与 `_h` 复制自 test_jv_api.py 模式)

```python
import pytest_asyncio
from datetime import timedelta
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings as app_settings
from app.db.base import get_db
from app.main import app


def _token(role="system_admin", sub=None):
    return jwt.encode({"sub": str(sub or uuid.uuid4()), "role": role,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      app_settings.jwt_secret_key, algorithm=app_settings.jwt_algorithm)


def _h(role="system_admin"):
    return {"Authorization": f"Bearer {_token(role)}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _configure_nc(monkeypatch):
    for f in ("nc_host", "nc_service", "nc_user", "nc_password"):
        monkeypatch.setattr(app_settings, f, "x")


async def test_status_shape_and_can_sync(client, monkeypatch):
    _configure_nc(monkeypatch)
    r = await client.get("/finance/v1/nc-sync/status", headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["can_sync"] is True and body["configured"] is True
    assert body["current_run"] is None and body["last_run"] is None
    r2 = await client.get("/finance/v1/nc-sync/status", headers=_h(role="finance_manager"))
    assert r2.json()["can_sync"] is False


async def test_post_guards(client, monkeypatch):
    # not configured -> 503
    monkeypatch.setattr(app_settings, "nc_host", None)
    r = await client.post("/finance/v1/nc-sync", json={"mode": "incremental"}, headers=_h())
    assert r.status_code == 503
    _configure_nc(monkeypatch)
    # non-admin -> 403
    r = await client.post("/finance/v1/nc-sync", json={"mode": "incremental"},
                          headers=_h(role="finance_manager"))
    assert r.status_code == 403
    # full without confirm -> 422
    r = await client.post("/finance/v1/nc-sync", json={"mode": "full"}, headers=_h())
    assert r.status_code == 422
    # bad mode -> 422 (pydantic Literal)
    r = await client.post("/finance/v1/nc-sync", json={"mode": "bananas"}, headers=_h())
    assert r.status_code == 422


async def test_post_triggers_run_and_status_reports_it(client, monkeypatch, db_session):
    from app.api.v1 import nc_sync as api_mod
    _configure_nc(monkeypatch)
    monkeypatch.setattr(api_mod, "_worker_dsn", lambda: _TEST_DSN)
    monkeypatch.setattr(api_mod, "_fetch", lambda wm: _mini_extract())
    r = await client.post("/finance/v1/nc-sync", json={"mode": "incremental"}, headers=_h())
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    # endpoint runs the worker synchronously in tests? No — it schedules a thread;
    # poll the DB (worker writes via psycopg2, visible outside the async session)
    import time
    for _ in range(50):
        rows = _pg("select status from nc_sync_runs where id = %s", (run_id,))
        if rows and rows[0][0] != "running":
            break
        time.sleep(0.2)
    assert rows[0][0] == "success"
    r2 = await client.get("/finance/v1/nc-sync/status", headers=_h())
    assert r2.json()["last_run"]["vouchers_inserted"] == 1


async def test_post_conflict_when_running(client, monkeypatch):
    _configure_nc(monkeypatch)
    from app.api.v1 import nc_sync as api_mod
    monkeypatch.setattr(api_mod, "_worker_dsn", lambda: _TEST_DSN)
    _pg("insert into nc_sync_runs (id, mode, status, started_at, created_at, updated_at) "
        "values (%s, 'incremental', 'running', now(), now(), now())", (uuid.uuid4(),))
    r = await client.post("/finance/v1/nc-sync", json={"mode": "incremental"}, headers=_h())
    assert r.status_code == 409
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_sync.py -q`
Expected: 新增 4 个 FAIL(404 — 路由不存在),原 8 个 PASS

- [ ] **Step 3: 写端点**(`app/api/v1/nc_sync.py`,完整文件)

```python
"""NC65 voucher sync — status + trigger (system_admin only for writes)."""
import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.nc_sync import RUNNING, NcSyncRun
from app.services import nc_sync as svc

router = APIRouter(prefix="/nc-sync", tags=["nc-sync"])

# test seams — monkeypatched in tests to point at finance_test / a fake extract
_worker_dsn = svc._pg_dsn
_fetch = svc.fetch_from_nc


class SyncIn(BaseModel):
    mode: Literal["full", "incremental"]
    confirm: str | None = None


def _run_out(r: NcSyncRun | None) -> dict | None:
    if r is None:
        return None
    return {
        "id": str(r.id), "mode": r.mode, "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "watermark_from": r.watermark_from, "watermark_to": r.watermark_to,
        "vouchers_deleted": r.vouchers_deleted, "vouchers_inserted": r.vouchers_inserted,
        "lines_inserted": r.lines_inserted, "dims_inserted": r.dims_inserted,
        "unmapped_cc_count": r.unmapped_cc_count, "error": r.error,
    }


@router.get("/status")
async def status(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    current = (await db.execute(
        select(NcSyncRun).where(NcSyncRun.status == RUNNING)
        .order_by(NcSyncRun.started_at.desc()).limit(1))).scalars().first()
    last = (await db.execute(
        select(NcSyncRun).where(NcSyncRun.status != RUNNING)
        .order_by(NcSyncRun.started_at.desc()).limit(1))).scalars().first()
    return {
        "can_sync": user.get("role") == "system_admin",
        "configured": svc.nc_configured(),
        "current_run": _run_out(current),
        "last_run": _run_out(last),
    }


@router.post("", status_code=202)
async def trigger(body: SyncIn, user: CurrentUser):
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin only")
    if not svc.nc_configured():
        raise HTTPException(status_code=503, detail="NC connection is not configured")
    if body.mode == "full" and body.confirm != svc.FULL_CONFIRM:
        raise HTTPException(status_code=422,
                            detail=f'full reload requires confirm="{svc.FULL_CONFIRM}"')
    dsn = _worker_dsn()
    try:
        # gate + insert the running row synchronously (fast, DB-only)…
        run_id = svc.start_run(body.mode, uuid.UUID(user["sub"]),
                               fetch=_fetch, pg_dsn=dsn, run_worker=False)
    except svc.SyncAlreadyRunning as e:
        raise HTTPException(status_code=409, detail=str(e))
    # …then do the actual NC read + load on a worker thread.
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, svc._run_worker, run_id, body.mode, _fetch, dsn)
    return {"run_id": str(run_id)}
```

- [ ] **Step 4: 注册路由** —— `app/api/v1/__init__.py`:

import 区加(admin_router 行后):

```python
from app.api.v1.nc_sync import router as nc_sync_router
```

include 区加(admin_router include 行后):

```python
api_router.include_router(nc_sync_router)
```

- [ ] **Step 5: 跑测试确认通过 + JV 回归**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_sync.py tests/test_jv_api.py -q`
Expected: 12 + 7 全 PASS

- [ ] **Step 6: Commit**

```bash
cd c:/Project/uniops
git add finance-api/app/api/v1/nc_sync.py finance-api/app/api/v1/__init__.py finance-api/tests/test_nc_sync.py
git commit -m "feat(finance): /nc-sync API — status + trigger with role/confirm/conflict guards"
```

---

### Task 5: 前端 —— NcSyncModal + 凭证中心按钮

**Files:**
- Create: `finance/src/pages/finance/NcSyncModal.tsx`
- Modify: `finance/src/pages/finance/JournalVouchersPage.tsx`(headerActions + 状态查询)

**Interfaces:**
- Consumes: Task 4 的 `GET /nc-sync/status`、`POST /nc-sync`;`financeApi`;`PortalChromeLayout` 的 `headerActions` prop。
- Produces: `NcSyncModal({ onClose, onSynced }: { onClose: () => void; onSynced: () => void })`。

- [ ] **Step 1: 写 `NcSyncModal.tsx`**(完整文件)

```tsx
/**
 * NC Sync modal — trigger a full / incremental NC65 voucher import and watch
 * its progress (polls /nc-sync/status while a run is live). system_admin only
 * (the launcher button is gated by status.can_sync). English-only copy.
 */
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, DatabaseZap, Loader2, X } from 'lucide-react'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'

const FULL_CONFIRM = 'FULL RELOAD'

export interface NcSyncRun {
  id: string; mode: string; status: string
  started_at: string | null; finished_at: string | null
  watermark_from: string | null; watermark_to: string | null
  vouchers_deleted: number; vouchers_inserted: number
  lines_inserted: number; dims_inserted: number
  unmapped_cc_count: number; error: string | null
}
export interface NcSyncStatus {
  can_sync: boolean; configured: boolean
  current_run: NcSyncRun | null; last_run: NcSyncRun | null
}

function RunSummary({ run, label }: { run: NcSyncRun; label: string }) {
  return (
    <div className="rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
      <span className="font-medium text-neutral-700">{label}:</span>{' '}
      {run.mode} · {run.status}
      {run.started_at && ` · ${run.started_at.slice(0, 16).replace('T', ' ')}`}
      {run.status !== 'running' && (
        <> · inserted {Number(run.vouchers_inserted).toLocaleString()} vouchers
          {run.vouchers_deleted > 0 && `, deleted ${Number(run.vouchers_deleted).toLocaleString()}`}
          {run.unmapped_cc_count > 0 && `, ${Number(run.unmapped_cc_count).toLocaleString()} unmapped CC lines`}
        </>
      )}
      {run.watermark_to && <> · watermark {run.watermark_to}</>}
      {run.error && <div className="mt-1 text-red-600">{run.error}</div>}
    </div>
  )
}

export function NcSyncModal({ onClose, onSynced }: { onClose: () => void; onSynced: () => void }) {
  const qc = useQueryClient()
  const [mode, setMode] = useState<'incremental' | 'full'>('incremental')
  const [confirm, setConfirm] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [startedRunId, setStartedRunId] = useState<string | null>(null)

  const { data: status } = useQuery({
    queryKey: ['nc-sync-status'],
    queryFn: () => financeApi.get<NcSyncStatus>('/nc-sync/status'),
    refetchInterval: (q) => (q.state.data?.current_run ? 2000 : false),
  })
  const running = status?.current_run ?? null
  const finishedOurRun = startedRunId !== null && !running &&
    status?.last_run?.id === startedRunId

  // one-shot: when the run we started finishes, tell the page to refresh.
  // Must be an effect — calling the parent callback during render is illegal.
  useEffect(() => {
    if (finishedOurRun) {
      setStartedRunId(null)
      onSynced()
    }
  }, [finishedOurRun])  // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setErr(null); setStarting(true)
    try {
      const r = await financeApi.post<{ run_id: string }>('/nc-sync', {
        mode, confirm: mode === 'full' ? confirm : undefined,
      })
      setStartedRunId(r.run_id)
      await qc.invalidateQueries({ queryKey: ['nc-sync-status'] })
    } catch (e) { setErr((e as Error).message) } finally { setStarting(false) }
  }

  const canStart = !running && !starting && (mode === 'incremental' || confirm === FULL_CONFIRM)

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="w-full max-w-lg rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-base font-semibold text-neutral-800">
            <DatabaseZap className="h-4 w-4 text-neutral-400" /> NC Voucher Sync
          </h2>
          <button onClick={onClose} className="rounded p-1 text-neutral-400 hover:text-neutral-700">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-3">
          {status?.last_run && !running && <RunSummary run={status.last_run} label="Last sync" />}

          {running ? (
            <div className="rounded-lg border border-neutral-200 px-3 py-3 text-sm">
              <div className="flex items-center gap-2 text-neutral-700">
                <Loader2 className="h-4 w-4 animate-spin" />
                Sync in progress ({running.mode})…
              </div>
              <div className="mt-2 font-mono text-xs text-neutral-500">
                {Number(running.vouchers_inserted).toLocaleString()} vouchers ·{' '}
                {Number(running.lines_inserted).toLocaleString()} lines ·{' '}
                {Number(running.dims_inserted).toLocaleString()} dims
              </div>
            </div>
          ) : (
            <>
              <div className="space-y-2 text-sm">
                <label className="flex items-start gap-2">
                  <input type="radio" checked={mode === 'incremental'}
                         onChange={() => setMode('incremental')} className="mt-0.5" />
                  <span>
                    <span className="font-medium">Incremental</span>
                    <span className="block text-xs text-neutral-500">
                      Import only vouchers that are new in NC (by source pk, creationtime watermark).
                      Existing vouchers are never touched.
                    </span>
                  </span>
                </label>
                <label className="flex items-start gap-2">
                  <input type="radio" checked={mode === 'full'}
                         onChange={() => setMode('full')} className="mt-0.5" />
                  <span>
                    <span className="font-medium">Full reload</span>
                    <span className="block text-xs text-neutral-500">
                      Delete ALL NC-sourced vouchers (~39.8k) and re-import everything.
                    </span>
                  </span>
                </label>
              </div>

              {mode === 'full' && (
                <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2">
                  <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-red-700">
                    <AlertTriangle className="h-3.5 w-3.5" />
                    Destructive: deletes every NC-sourced voucher before re-importing.
                    Type {FULL_CONFIRM} to enable.
                  </div>
                  <input value={confirm} onChange={(e) => setConfirm(e.target.value)}
                         placeholder={FULL_CONFIRM} className={cn(inputCls, 'w-full font-mono')} />
                </div>
              )}

              {err && <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}

              <div className="flex justify-end gap-2 border-t border-neutral-100 pt-3">
                <button onClick={onClose} className={secondaryBtn}>Close</button>
                <button onClick={start} disabled={!canStart} className={primaryBtn}>
                  {starting ? <Loader2 className="h-4 w-4 animate-spin" /> : <DatabaseZap className="h-4 w-4" />}
                  Start Sync
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 接进凭证中心页** —— `finance/src/pages/finance/JournalVouchersPage.tsx` 三处修改:

(a) import 区加:

```tsx
import { DatabaseZap } from 'lucide-react'
import { NcSyncModal, type NcSyncStatus } from './NcSyncModal'
```

(lucide 的 `DatabaseZap` 并入现有 lucide-react import 大括号,不要重复 import 语句。)

(b) 组件 state 区(`const [banner, ...]` 行后)加:

```tsx
  const [showNcSync, setShowNcSync] = useState(false)
  const { data: ncStatus } = useQuery({
    queryKey: ['nc-sync-status'],
    queryFn: () => financeApi.get<NcSyncStatus>('/nc-sync/status'),
  })
```

(c) `PortalChromeLayout` 加 `headerActions`(在 `subtitle=...` 行后):

```tsx
      headerActions={ncStatus?.configured && ncStatus?.can_sync ? (
        <button onClick={() => setShowNcSync(true)}
                className="flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50">
          <DatabaseZap className="h-4 w-4" /> NC Sync
        </button>
      ) : undefined}
```

(d) 页尾 modal 渲染区(`{detailId && ...}` 旁)加:

```tsx
      {showNcSync && (
        <NcSyncModal onClose={() => setShowNcSync(false)} onSynced={refresh} />
      )}
```

- [ ] **Step 3: Typecheck**

Run: `cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: exit 0(显式确认退出码)

- [ ] **Step 4: Commit**

```bash
cd c:/Project/uniops
git add finance/src/pages/finance/NcSyncModal.tsx finance/src/pages/finance/JournalVouchersPage.tsx
git commit -m "feat(finance-ui): NC Sync button + modal (incremental/full with typed confirm, live progress)"
```

---

### Task 6: 部署接线 + 容器重建 + 实测冒烟

**Files:**
- Modify: `finance-api/requirements.txt`(加 oracledb、psycopg2-binary)
- Modify: `docker-compose.dev.yml`(finance-api 服务 environment 加 NC_* 透传)
- 本地 `.env`(gitignored,**不提交**):加 NC65_* 值

**Interfaces:**
- Consumes: Task 2 的 settings 字段名(NC_HOST/NC_PORT/NC_SERVICE/NC_USER/NC_PASSWORD —— pydantic 大小写不敏感)。

- [ ] **Step 1: requirements.txt 末尾加两行**

```
oracledb==2.5.1
psycopg2-binary==2.9.10
```

- [ ] **Step 2: compose 透传** —— `docker-compose.dev.yml` 的 `finance-api:` 服务 `environment:` 块(`BUDGET_API_URL` 行后)加:

```yaml
      # NC65 read-only connection for the NC Sync button (values live in the
      # gitignored root .env as NC65_*; missing values = feature hidden).
      NC_HOST: ${NC65_HOST:-}
      NC_PORT: ${NC65_PORT:-1521}
      NC_SERVICE: ${NC65_SERVICE:-}
      NC_USER: ${NC65_USER:-}
      NC_PASSWORD: ${NC65_PASSWORD:-}
```

- [ ] **Step 3: 本地 .env 配值(不提交)**

先确认 `.env` 被忽略:`cd c:/Project/uniops && git check-ignore .env`(应输出 `.env`;若不被忽略,STOP 上报)。
然后把 `c:/Project/nc65_conn.env` 里的 NC65_HOST/NC65_PORT/NC65_SERVICE/NC65_USER/NC65_PASSWORD 五行追加到 `c:/Project/uniops/.env`(键名保持 NC65_* 与 compose 对应)。⚠️ 空 NC_PORT 会让 pydantic int 解析失败,compose 已给 `:-1521` 默认。

- [ ] **Step 4: 重建 finance-api 容器**(requirements 变了必须 --build)

Run: `cd c:/Project/uniops && docker compose -f docker-compose.dev.yml up -d --build finance-api`
Expected: 构建成功,`docker ps --filter name=uniops_finance_api` 显示 Up。

- [ ] **Step 5: 冒烟 status(应 configured=true)**

用 finance-api venv 铸 system_admin token(参照 tests/test_jv_api.py `_token`,role='system_admin'),然后:

Run: `curl -s -H "Authorization: Bearer <TOK>" http://localhost:8004/finance/v1/nc-sync/status`
Expected: `{"can_sync":true,"configured":true,"current_run":null,"last_run":null}`

- [ ] **Step 6: 实测增量同步**(⚠️ 会真实只读连 NC 生产 Oracle 10.10.95.67——本地 dev 库已含全部 39,839 张,预期 0 插入 + 建立水位;若环境权限拦截,把这步的命令清单交给用户执行)

Run: `curl -s -X POST -H "Authorization: Bearer <TOK>" -H "Content-Type: application/json" -d '{"mode":"incremental"}' http://localhost:8004/finance/v1/nc-sync`
然后轮询 status 直到 `current_run` 变 null:
Expected: `last_run.status="success"`、`vouchers_inserted=0`、`watermark_to` 为非空 char(19) 时间串。

- [ ] **Step 7: 全套回归 + Commit**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_sync.py tests/test_jv_api.py tests/test_account_balance.py -q`
Expected: 全 PASS

```bash
cd c:/Project/uniops
git add finance-api/requirements.txt docker-compose.dev.yml
git commit -m "feat(finance): NC sync deps + dev compose NC connection passthrough"
```

---

## Self-Review 记录

- **Spec 覆盖**:§2 表(T1)、§3 服务两模式/单飞/陈旧/进度(T2/T3)、§4 API 与 4 个门(T4)、§5 按钮+弹窗+轮询+缓存失效(T5,onSynced→refresh 失效 jv-list;报表缓存由既有 onJvActed 机制外的场景——同步后用户在凭证中心,refresh 足够;报表页下次查询自然重取,可接受)、§6 配置部署(T6)、§7 测试矩阵(T2/3/4 共 12 测)。§3「全量同一事务」/「失败回滚」在 T3 worker 内实现并有测试。
- **Placeholder 扫描**:无 TBD;所有代码块完整。
- **类型一致性**:`start_run(mode, started_by, *, fetch, pg_dsn, run_worker)` 在 T3 定义、T4 以 `run_worker=False` 消费;`_run_worker(run_id, mode, fetch, dsn)` 两处签名一致;`NcSyncStatus/NcSyncRun` TS 接口与 `_run_out` 字段一致;`FULL_CONFIRM` 前后端字面量一致("FULL RELOAD")。
- **已知取舍**:测试 `test_post_triggers_run_and_status_reports_it` 里 worker 在默认 executor 线程真实跑(fake fetch),用 DB 轮询等结果——因 worker 用独立 psycopg2 连接,不依赖 async session 事务。
