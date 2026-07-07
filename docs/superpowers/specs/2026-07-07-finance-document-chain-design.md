# Document Chain(单据链/凭证联查)设计 —— 占位稿

> 状态:**占位稿**(概念与选项已记,实现前需完整 brainstorm)。日期:2026-07-07。
> 归属:Finance 重构子项目③。关联:`2026-07-07-finance-jv-subsystem-design.md`(JV 是链路下游节点)、
> `2026-06-20-finance-ap-invoice-*`、[[project_uniops_finance_jv_nc65]]。

## 1. 背景与目标
AP 模块过于单薄,缺「上下文」。目标:从一条业务记录能看到**完整单据链**——
```
PR → PO → GR → Invoice(EPMS) → AP Record(ap_invoices) → posting_event → JV(总账凭证)
                                          PA → payment event → JV
```
例:Invoice posted 触发一条 AP Record,AP 再推式生成 JV;用户希望在 AP/JV 上**看到关联的上下游节点**并
可钻取。

## 2. 现状(链路只是隐式)
- 唯一关联是 `posting_events.source_doc_type/id/number` 与 `ap_invoices.source_invoice_id`;
- **无显式链路表、无 JV 联查、无跨服务上溯**(PR→PO→GR→Invoice 在 EPMS)。

## 3. 设计选项(待定)
- **存储**:(a) **按需推导**——读时沿 source 引用跨服务遍历;(b) **显式 `document_links` 表**——建单时
  写边。权衡:推导零维护但慢/依赖各服务查询;显式表快/可视但要在各写点埋点。
- **链路节点**:PR→PO→GR→Invoice→AP→JV,PA→付款 JV;跨服务(EPMS/OA 拥有上游)。
- **交付面**:链路查询 API(chain-for-doc)+ AP/JV 详情的**上下文/血缘面板**(可点钻取,带 session
  handoff 跳源系统)。JV 子系统详情已预留「来源单据单跳」作基础钩子。

## 4. 待解决(实现前 brainstorm)
- 链路深度与方向(仅 AP↔Invoice↔JV,还是全链 PR..JV + 付款侧)。
- 存储模型(推导 vs 显式 document_links)。
- 跨服务解析方式(各服务查询接口 / 统一血缘服务)。
- UI 形态(时间线 / 图 / 节点卡列表)。
- 与 NC 历史导入凭证的链路(NC 凭证的 `PK_SOURCEPK` 来源单据是否可追)。

## 5. 依赖与范围
- **依赖 ② JV 子系统**(JV 作为链路下游节点)。建议在 JV 落地后做。
- 不含:完整血缘图谱/分析;先满足 AP/JV 的上下文追溯即可。
