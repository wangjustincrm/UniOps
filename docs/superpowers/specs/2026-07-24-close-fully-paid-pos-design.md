# 一次性回填:按实际已付关闭僵尸 PO

**日期:** 2026-07-24
**分支:** `feature/close-fully-paid-pos` @ worktree `C:/Project/uniops-po-close`
**类型:** 一次性数据回填(脚本),**不改任何业务逻辑**

## 背景 / 问题

PMS Data Import 导入的历史 PO,因老系统 GR(收货)不严谨,大量实际已付清关闭的
PO 在 UniOps 里仍停留在开放态(`issued` 等)。做发票 Match PO 时,这些僵尸 PO
被列进候选,成为干扰项。

现有脚本 `scripts/import_pms/close_paid_pos.py` 只按**老系统 SharePoint 的
`PaymentStatus=PAID` 标记**(读 `data/po.json`)收口。但该标记本身不严谨/有缺失,
漏网的僵尸 PO 依然存在。

## 目标

以 **UniOps 库内真实付款数据**为准:一个 PO 关联的 **processed PA 的已付金额合计
≥ PO 金额**,就把该 PO 状态直接置为 `closed`,不管它是否录了收货。关闭后它自动从
Match PO 候选中消失(`_MATCHABLE_PO_STATUSES` 本就不含 `closed`,前端同步不含),
无需改任何代码或前端。

## 口径决策(2026-07-24,已与用户确认)

本地生产快照实测:开放态待收口 PO 全部 `tax_amount=0`(PMS 导入的金额是**税前**),
而 PA 的 `payment_amount` **含税**。故:

- **规则 A 含税**(`SUM(PA.payment_amount) >= PO.total`):命中 89。
- **规则 B 税前**(`SUM(PA.subtotal) >= PO.subtotal`):命中 66。
- 两者差 23 个:税前并没付满(最低仅 74%,如 `PO-089-2509-10` 付 54.95 / PO 73.88),
  只是被税额顶过线。

**用户明确选规则 A(含税)**:这批是 PMS 历史老 PO,只要有 processed 付款覆盖了记录
金额即视为已了结(不会再来新发票),优先清干净候选列表。已知代价=上述 23 个税前未
付满的 PO 也会被关闭。**脚本即按规则 A 实现,不改。**

## 判定规则

- **已付金额** = `SUM(payment_applications.payment_amount)`,条件
  `po_id = po.id AND status = 'processed' AND currency = po.currency`。
  - 只算 `processed`(真正出账付款),与 Dashboard「已付」口径一致。
  - `payment_amount` 已是净付额(settlement PA 已扣 `prepayment_applied`),
    多条 PA(预付+结算)累加即等于该 PO 的真实现金流出。
  - 币种必须匹配 PO(`payment_applications.currency` NOT NULL,默认 CAD;
    无币种的 PA 视为 BUG,不在本脚本处理范围)。
- **PO 金额** = `purchase_orders.total`。
- **命中** = 已付金额 ≥ PO 金额,则 `status -> 'closed'`。

## 安全边界

- 只处理现状态 ∈ `{issued, approved, partially_received, fully_received}` 的 PO
  (`draft / submitted / in_review / returned / rejected / cancelled / closed` 一律不碰)。
- `po.total > 0` 守卫:避免把 0 金额 PO(已付 0 ≥ 0)误关。
- 默认 dry-run,只读预览;`--commit` 才写库;整事务出错回滚。
- 幂等:再跑一次不会新增关闭(命中的都已是 closed,不在开放态集合内)。

## 核心查询(单聚合,无逐 PO 循环)

```sql
SELECT po.id, po.number, po.status, po.total,
       COALESCE(SUM(pa.payment_amount), 0) AS paid
FROM purchase_orders po
LEFT JOIN payment_applications pa
       ON pa.po_id = po.id
      AND pa.status = 'processed'
      AND pa.currency = po.currency
WHERE po.status IN ('issued','approved','partially_received','fully_received')
  AND po.total > 0
GROUP BY po.id, po.number, po.status, po.total
HAVING COALESCE(SUM(pa.payment_amount), 0) >= po.total
```

然后对命中的 id 集合 `UPDATE ... SET status='closed'`(仍带开放态 WHERE 兜底)。

## 交付物

- 新脚本 `epms-api/scripts/import_pms/close_fully_paid_pos.py`,风格对齐现有
  `close_paid_pos.py`(argparse `--commit`、dry-run 默认、异步会话、整事务)。
- dry-run 预览打印:命中数 + 前若干条 `number (status -> closed) total=.. paid=..`。

## 运行方式(打共享生产库 10.10.50.20)

脚本属 `scripts/` 一次性工具,不在服务路径,**无需重建镜像/部署**。运行时需脚本文件
在能连生产库的环境(epms-api 容器内 / 带生产 env 的 checkout)。交付时给用户命令清单:
先 dry-run 看数,再 `--commit`。

## 不做(YAGNI)

- 不改 `_MATCHABLE_PO_STATUSES` 或前端(关闭状态已足够)。
- 不做运行时自动规则(用户明确选一次性回填)。
- 不动 `close_paid_pos.py`(两者互补,各自按不同来源收口)。
