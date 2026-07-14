# 总账凭证(JV)子系统设计

> 状态:设计稿(brainstorm 产出,逐段经用户确认;待写实现计划)。日期:2026-07-07。
> 关联:[[project_uniops_finance_jv_nc65]]、`2026-07-04-nc65-finance-migration-mapping-design.md`
> (JV 是 NC 历史凭证的落地容器,本设计已吃进其约束)、`2026-06-20-finance-ap-invoice-*`。
> 归属:Finance 重构子项目②(① Claim Accrual、③ Document Chain 为独立后续 spec)。

## 1. 目标与定位

把现有 posting 脊柱(`posting_events`/`posting_lines`,业务事件级)之上,建一层**正式总账凭证(JV,
记账凭证)**:凭证号、摘要、借贷分录、**制单→审核→过账**生命周期、红冲。**JV 成为 GL 真相源**——试算
/明细账/科目余额改读「已过账」JV。posting_event 退为业务事件/触发层 + Document Chain 用。

同时 JV 是 **NC65 历史凭证的落地容器**(全历史导入)与**并行期 JV 复核**的一端,故数据模型须容纳 NC
的双币金额、数量核算、辅助核算(见映射 spec)。

## 2. 数据模型

### 2.1 `journal_vouchers`(凭证头)
| 字段 | 说明 |
|---|---|
| id / jv_number / voucher_word | go-forward `JV-YYYYMM-0001`;**导入历史同用 `JV-YYYYMM-NNNN`(NC 原号补零 4 位,用户 2026-07-13 推翻原「保留记-」决定)**;voucher_word 统一 `JV`;next_jv_number 取 max-suffix+1(共享命名空间防撞号) |
| voucher_date / fiscal_period | 凭证日期 / `YYYY-MM` |
| summary | 摘要(按 event_type 模板自动生成,可手改) |
| status | `draft`(已制单)→`reviewed`(已审核)→`posted`(已过账);`reversed`(已红冲) |
| posting_event_id | 1:1 唯一**可空**(业务事件填;手工/NC 导入为空) |
| source_service / source_doc_type / source_doc_id / source_doc_number | 来源单据(Document Chain) |
| prepared_by/at · reviewed_by/at · posted_by/at | 制单/审核/过账三岗留痕 |
| reverses_jv_id / reversed_by_jv_id | 红冲关联(本单冲销谁 / 被谁冲销) |
| total_debit / total_credit | 借贷合计(**原币**) |
| total_local_debit / total_local_credit | **本位币(CAD)合计**(多币种需要) |
| entity_id | 账簿/主体(NC 单账簿 → 未来多主体) |
| nc_source_pk | 导入溯源(经 nc_id_map 回溯 NC 原始 PK) |

约束:`UNIQUE(posting_event_id)`;`UNIQUE(jv_number, entity_id)`;`status` CHECK 枚举。

### 2.2 `journal_voucher_lines`(凭证分录行)
| 字段 | 说明 |
|---|---|
| id / jv_id(FK,CASCADE)/ line_no / account_code / summary | 行标识 + 科目(NC 中式码)+ 行摘要 |
| orig_debit / orig_credit | 原币借贷(一行只一边,沿用 posting `CHECK NOT(debit>0 AND credit>0)`)|
| local_debit / local_credit | **本位币(CAD)借贷**——GL 按本币汇总 |
| currency / fx_rate | 币种 + 汇率(原币↔本币) |
| quantity / unit / price | **数量核算**(存货类科目;非数量行留空) |
| cost_center_id / department_id / partner_id(+partner_name) / tax_code / project_id / item_id … | 高频维度列 |

### 2.3 `jv_line_dimensions`(长尾维度侧表)
`jv_line_id`(FK,CASCADE)/ `dim_code` / `value_id` / `value_text` —— 镜像 NC 辅助核算长尾维度。

> 双币口径:业务事件生成的 JV,本币=原币(CAD 本位),`fx_rate=1`;外币交易与 NC 历史才有原币≠本币。

## 3. 生成机制

**插入点:包住 `emit_event`(finance-api,7 个调用点全在此;approval/expense 的 posting.py 无调用)。**
写完 `posting_event`+`posting_lines`(+长尾维度)后,**同一事务内**生成 JV:

```
emit_event(db, ..., prepared_by)
  └─ 若 event 新建成功(非幂等跳过):
       journal_voucher.generate_from_event(db, event_id, prepared_by)
         ├─ 建 journal_vouchers 头:status=draft, jv_number=JV-{period}-{流水},
         │    prepared_by/at=触发人/now, summary=模板, 拷 source_doc_*
         ├─ posting_lines → journal_voucher_lines(科目/借贷/双币/数量/维度列)
         ├─ posting_line_dimensions → jv_line_dimensions
         └─ 校验 Σ借=Σ贷(原币+本币各平), 写合计
```

- **幂等**:posting_event 命中 `ON CONFLICT DO NOTHING` 返回 None 时不生成;JV 侧 `posting_event_id`
  唯一双保险。
- **制单人**:`emit_event` 加 `prepared_by` 参数,各调用点传当前操作人(AP accrual 为 EPMS/OA 同步携带
  的终端用户)。
- **同事务原子**:JV 与 posting_event 同生共死,不会有「有事件没凭证」。
- **摘要模板**(可手改):AP accrual→`应付计提 · {ap_no} · {vendor}`、payment→`付款 · {pa_no} · {vendor}`、
  expense_paid→`报销付款 · {claim_no} · {employee}`、AR/开账/结转各自模板。
- 逻辑集中在新 `services/journal_voucher.py`,`emit_event` 末尾调 `generate_from_event`。

## 4. 生命周期状态机

```
制单 draft ──审核──▶ reviewed ──过账──▶ posted ──红冲──▶ 原单 reversed + 红字冲销 JV
   ▲(系统自动/手工)   │弃审◀┘              │反过账(期间未关)
   └ NC 历史导入:直接 posted(跳工作流)     └──────────▶ 回 reviewed/draft 改正
```

### 三种入口
1. **系统自动制单**(业务事件):生成 `draft`,`prepared_by`=触发人。
2. **NC 历史导入**:直接 `posted`(已记账),跳过 draft/reviewed;三岗人从 NC 制单/审核/记账人经
   `nc_id_map` 还原(缺失用迁移哨兵)。
3. **手工凭证**(future):财务建 `draft`(`posting_event_id` 空)。

### 流转 + 门禁
| 动作 | 流转 | 门禁 |
|---|---|---|
| 审核 | draft→reviewed | **SoD:审核人 ≠ 制单人**(新 sod_rule `jv_self_review`);可批量 |
| 弃审 | reviewed→draft | 仅未过账 |
| 过账 | reviewed→posted | **仅 posted 进 GL**;受会计期间开关阻断(复用 `_check_period_open`);可批量 |
| 反过账 | posted→reviewed/draft | 仅期间未关;当期改正 |
| 红冲 | posted→(原单 reversed) | 生成红字冲销 JV(金额取负、自动过账),原单留痕;用于已关期/审计。**解决旧 void 不冲销 accrual 的 gap** |

### 角色(复用 role_management 指派)
- 制单人 = 触发操作人/系统/导入器(无 SoD)
- 审核人 = `finance_manager`/`finance_bp` 指派(或新增「凭证审核」键)
- 过账人 = `finance_manager`(或新增「凭证过账」键)
- SoD:`制单≠审核` 强制;`审核≠过账` 可配(默认允许同人)

### 批量化(必须)
系统自动制单产生大量 `draft` → 凭证中心须**批量审核 + 批量过账**(按期间/来源/科目筛选)。
开账/结转/红冲/历史导入 → 直接 `posted`(系统权威,不经工作流)。

## 5. GL 读取层迁移

`gl.py` 的 trial_balance / account_ledger / journal / income_statement / balance_sheet:
- **从** join `posting_events`+`posting_lines` 聚合 debit/credit
- **改为** 读 `journal_vouchers`(`status='posted'`)+ `journal_voucher_lines`,聚合
  **`local_debit/local_credit`(本位币 CAD)**,按 `fiscal_period`/`account_code`/维度。
- `posting_events`/`posting_lines` 保留(业务脊柱 + Chain)。
- **切换零差异前提**:先把现网存量 posting_events 回填成 posted JV,再切查询源。

## 6. 与导入/回填的关系

`journal_vouchers` 两来源同表共存:
- 业务事件:`emit_event` → `draft` JV(幂等键 posting_event_id)。
- NC 历史导入:按映射 spec 直接写 `posted` JV(幂等键 nc_source_pk)——批量历史底座(~3.9 万张)。
- 存量回填:现网已有少量 posting_events → posted JV,保证 §5 切换前后 GL 一致。

## 7. API / UI(凭证中心)

**后端**:
- 查询:凭证列表(期间/状态/来源/科目筛选)、详情(头+借贷行+维度)。
- 动作:`审核`/`批量审核`、`过账`/`批量过账`、`弃审`、`反过账`、`红冲`(、手工建单 future)。
- GL 端点(已存在)切数据源到 posted JV。

**前端**(Portal Finance 区,参照 AccountsPayable/AR 页):
- 凭证中心:列表 + 详情(借贷分录 + 维度 + 双币)+ 批量审核/过账工具条 + 红冲按钮。
- 详情挂**来源单据链接**(Document Chain 子项目③的基础钩子,先做单跳)。

## 8. 错误处理 + 测试

- **校验**:借贷平衡(原币+本币各平)、幂等(posting_event_id / nc_source_pk 唯一)、SoD(制单≠审核)、
  期间开关(过账/反过账受关期阻断)。
- **测试**:生成/审核/过账/弃审/红冲/双币/数量核算 单元;SoD 与关期 fail 场景;**GL 读取层切换回归**
  (切换前后逐科目试算表一致);批量操作;导入 posted JV 路径;红冲后 GL 净影响归零。

## 9. 与其他子项目的接口
- **① Claim Accrual**:Claim 审批通过 accrual(借费用/税 贷员工报销应付)也经 `emit_event` → 自动生成
  draft JV,无需特殊处理。
- **③ Document Chain**:JV 的 `source_doc_*` + posting_event 提供上下游追溯锚点;本设计仅做详情单跳,
  完整链路查询留③。

## 10. 待解决 / 不在范围
- 待解决:开账/结转是否需人工过账(暂定系统直接 posted);「凭证审核/过账」是否新增独立权限键还是复用
  finance_manager;红冲对已被下游引用凭证的约束。
- 不在范围:Claim Accrual、Document Chain 完整实现、NC 迁移实现计划(各自 spec/plan)。
