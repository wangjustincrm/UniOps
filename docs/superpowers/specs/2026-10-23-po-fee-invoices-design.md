# 原料 PO 的运费 / 清关费发票关联（PO Fee Invoices）设计文档

- 日期：2026-10-23
- 状态：**D1–D10 已定；已实现于 feat/po-fee-invoices（见 docs/prd/EPMS.md §3.23）**
- 分支：`feat/po-fee-invoices`（基于 main `0eaf2516`）
- 归属：EPMS（Invoice 详情、PO 详情、PO Edit Details、Admin Panel）

---

## 1. 背景与目标

NC 导入的原料/包材 PO（`type=1`，不含 `nc_milk`）通常会配套产生**运费**和**清关费**发票。
这些发票由货代/报关行开具（如 Radiant），与 PO 供应商不是同一家，目前只能走 Direct PA 付款，
于是：

1. 费用与产生它的 PO **断链** → 原料成本不准（NC 里同样有 45 张无上游单据的孤儿费用发票）。
2. 看一张 PO **无法知道**它的运费/清关费发票是否已收齐。

目标：

- 费用发票可以关联到原料 PO 的**到货（GR）**；清关费可以**逐行**关联到不同 PO。
- 是否需要运费/清关费由 PO 的 **Incoterms** 决定，Incoterms 变成可配置的 Picker。
- PO 详情页能看到每次到货的运费/清关费是否已到。
- **不做金额比较，不改付款路径。**

## 2. 已定决策

| # | 决策 | 来源 |
|---|---|---|
| D1 | **不**自动建 PO、**不**往 PO 上加费用行；付款仍走 Direct PA，另加一层「费用关联」 | 用户 10-23 认可 |
| D2 | 关联**不复用** `invoices.po_id`：`ck_invoices_direct_no_po` 禁止 Direct 发票带 PO（防 PO PA 与 Direct PA 双付）；关联与付款完全正交 | 代码约束 |
| D3 | 是否需要运费/清关费由 PO 的 Incoterms 代码决定；Admin Panel 新增 **Incoterms Settings**，每个代码可勾「需要运费」「需要清关费」 | 用户 10-23 |
| D4 | 运费：**每次到货都要被至少一张运费发票覆盖**；一张运费发票可覆盖多次到货（运输方常合并开票） | 用户 10-23 |
| D5 | 清关费：一张发票常覆盖多张 PO，每个发票行对应一张 PO → **行 → PO（可到 GR）关联**，不拆金额 | 用户 10-23 |
| D6 | 关联是**可选**的：Radiant 的 Direct 发票大量是销售出库运费（客户 PO 号），不能强制挂原料 PO | 生产实测 |
| D7 | 「需要与否」**实时读配置**，不在 PO 上存快照（改配置立即全量生效） | 能派生的一律派生 |
| D8 | Incoterms 未填的 PO 显示「无法判断」，**不计入缺失**（现有 280 张中 273 张为空） | 生产实测 |
| D9 | 配置项**只停用不删除**，避免已引用的 PO 悬空 | — |
| D10 | Incoterms 在 Edit Details **必填**（非奶 NC PO），前后端都校验；**不做供应商默认值**——同一供应商因货物不同条款会不同；NC 无此字段 | 用户 10-23 |

## 3. Incoterms 配置

存于 `company_config` JSONB（与 Currency Settings 同一套）：

```json
"incoterms": [
  {"code": "FOB", "label": "Free On Board", "needs_freight": true, "needs_customs": true, "active": true},
  ...
]
```

首次上线预填 Incoterms 2020 的 11 个代码（买方视角的默认值，Admin 可改）：

| 代码 | 运费 | 清关 | 说明 |
|---|---|---|---|
| EXW / FCA / FAS / FOB | ✓ | ✓ | 主运费与进口清关都由我方承担 |
| CPT / CIP / CFR / CIF | ✗ | ✓ | 主运费卖方付，进口清关我方付 |
| DAP / DPU | ✗ | ✓ | 送到指定地点，进口清关我方付 |
| DDP | ✗ | ✗ | 卖方全包 |

## 4. PO 上的 Incoterms

- 新列 `purchase_orders.incoterms_code`（String(10)，nullable）；原 `incoterms` 列保留为**地点**文本。
- Edit Details：代码 Picker（只列 active）**必填** + 可选「Named place」输入框。必填只对非奶 NC PO（`status<>'nc_milk'`）；奶单不涉及费用关联，保持可空。
- 后端 `PUT` 同样校验：非奶 NC PO 提交时 `incoterms_code` 为空 → 422。副作用：编辑任何一张未填的老单（含已关闭）都要先选 Incoterms。
- PDF / 详情显示 `code + place`。
- 迁移回填：现有 `incoterms` 开头的 token 若命中配置代码 → 拆成 code + 剩余作 place（`DAP CRM` → `DAP` + `CRM`；`FOB, Arbuckle, CA` → `FOB` + `Arbuckle, CA`）；命不中则 code 为空、原文不动。
- NC 同步写入器不碰这两列（已是买方字段）。

## 5. 数据模型：`invoice_fee_links`

| 列 | 说明 |
|---|---|
| id | |
| invoice_id | FK invoices，ON DELETE CASCADE |
| invoice_line_id | 发票 `line_items[].id`；NULL = 整张发票（运费常用） |
| fee_type | `freight` \| `customs` \| `other` |
| po_id | FK purchase_orders，必填；仅限 `type=1 AND source='nc' AND status<>'nc_milk'` |
| gr_id | FK goods_receipts，可空；须属于 po_id |
| created_by / created_at | |

唯一约束：`(invoice_id, coalesce(invoice_line_id,''), po_id, coalesce(gr_id,…))`。

- 一张运费发票覆盖 3 次到货 = 3 行（同一 invoice、不同 gr_id）。
- 一张清关费发票 4 行对应 4 张 PO = 4 行（不同 invoice_line_id / po_id）。
- 只挂到 PO、未选到货的关联（PO 尚无 GR 时）在面板上显示为「未指定到货」，**不算覆盖任何到货**。

可关联的发票：`match_route` 为 `direct` 或尚未定路由的发票；`po` / `agreement` / `freight` 路由的发票不提供此功能（它们已有自己的 PO 关系或属于快递模块）。
关联不改变发票的 status / match_route / 付款。

## 6. 界面

**Invoice 详情页 → 「Link to raw-material PO」面板**
- 选费用类型（默认整张发票一个类型，可逐行改）。
- 整张发票模式（运费）：搜原料 PO → 勾选它的到货（可多选、可跨 PO）。
- 逐行模式（清关费）：每个发票行选 PO（+可选到货）。
- 随时可增删，留审计记录。

**PO 详情页 → 「Freight & Customs」面板**（仅 type 1 非奶）
- 顶部：Incoterms 及其要求（如 `FOB — freight ✓ customs ✓`）；未填则提示去 Edit Details。
- 每次到货一行：到货号、日期、运费 ✓/✗（链接到发票）、清关费 ✓/✗（不需要的显示 —）。
- 下方：已关联但未指定到货的发票。

**PO 列表**：加一列/筛选「Fees」：Complete / Missing / N/A（不需要或 Incoterms 未填）。

## 7. 不做 / 二期

- 不做金额比较、不做成本分摊计算。
- 二期：导出「PO 费用分摊表」给财务做 NC 价值调整；AI 识别发票上的 PO/提单号自动建议关联。
- 不做「缺费用发票」的任务提醒（先看面板和列表，有需要再加）。

## 8. 存量数据

- B00006151（Radiant 清关费 179.25）10-22 被人用表头级分摊挂到 Independent Can 的 PO-057-2608-01（跨供应商，variance -99.5%，尚无 PA）。上线后建议：Unmatch → Mark Direct → 用本功能关联。**生产写操作需另行获准。**
- 现有表头级分摊允许跨供应商挂 PO，这是付错人的口子——**本设计不处理**，另记一条待议。

## 9. 同步更新

- `docs/prd/EPMS.md`：Incoterms、费用关联、PO 面板。
- 向导层：`knowledge/po_types.yaml` / `rules/po.yaml` / `rules/invoice.yaml` 加问答；本体加 `incoterms_code`。
- 权限：关联操作复用发票匹配权限（`epms.invoice.match`，与 Mark Direct 同一道闸），Incoterms 配置限 system_admin。
