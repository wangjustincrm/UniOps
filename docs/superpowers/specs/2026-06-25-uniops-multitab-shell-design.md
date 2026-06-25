# UniOps 多页签 UI 架构设计

- **日期**: 2026-06-25
- **状态**: 已确认设计，待实现计划
- **作者**: Justin Wang + Claude

## 1. 背景与目标

### 1.1 现状

UniOps 当前由 **4 个独立部署的 Vite/React app** 组成：

| App | 角色 | 页面数 | sidebar |
|---|---|---|---|
| epms | 采购模块（PR/PO/GR/Invoice/PA/预算入口） | ~50 | 自有 |
| oa | 费用报销模块（Expense/PA/Invoice/Task） | ~16 | 自有 |
| vms | 访客管理模块 | ~17 | 自有 |
| portal | 跨系统 launcher **+ Finance 模块**（AP/AR/GL/CoA/Tax/Bank/PaymentBatch/Budget） | ~19 | 自有 |

跨 app 导航通过 URL 握手（`#__session=<base64json>`）做整页跳转；portal 已用 iframe 内嵌 EPMS 预算页（`EpmsEmbed.tsx`，子 app 检测 `window.self !== window.top` 时隐藏自身 chrome）。

### 1.2 两个问题

1. **缺少多页签工作区**：用户每次点 sidebar 菜单都整页替换，无法同时打开多个单据并在它们之间来回切换（ERP 用户的核心诉求）。
2. **Portal 职责混淆**：portal 同时是「launcher」和「Finance 模块宿主」。Finance 本质是与 EPMS/OA/VMS 平级的功能模块，却被建在 launcher 内部，导致 Finance 里的钻取动作横跨 epms/oa 两个系统，体验割裂。

### 1.3 目标

- 在每个模块的内容区引入 **keep-alive 多页签（MDI）**：点 sidebar 菜单在 tab 条新开/聚焦一个 tab，tab 内加载页面；切走再切回状态完整保留。
- 把 **Finance 提升为独立部署的第五个模块**，portal 收成纯 launcher + 平台管理。
- 把多页签外壳 **实现一次、四模块复用**，顺带消除审计发现的 token / 组件 / chrome 重复。

### 1.4 非目标（YAGNI）

- 不把 4 个 app 合并成单一 SPA / Module Federation。
- 不做跨模块的统一全局 tab 条（每模块各自一套 tab，符合"各模块独立 sidebar"的现状）。
- 第一版不做 iframe ↔ 父窗口的 postMessage 双向通道（列入 Phase 2）。

## 2. 顶层架构

```
Portal（launcher / SSO / Home / 平台管理）
   │  切换入口（整页跳转 + #__session 握手）
   ├─→ EPMS    （采购模块）
   ├─→ OA      （费用模块）
   ├─→ VMS     （访客模块）
   └─→ Finance （财务模块，新独立部署）

每个模块 = AppShell( Sidebar + Header + TabBar + TabHost )
           + 自己的 navConfig + 路由表（数据形式）
```

### 2.1 Workspace 与共享包

引入 npm/pnpm workspace（根 `package.json` + `packages/`）。新增三个共享包：

| 包 | 内容 | 消除的审计问题 |
|---|---|---|
| `@uniops/tokens` | 单一 `tokens.css`（`@theme` 全阶色 / 字体 / focus / 阴影） | P0：token 复制 4 份且漂移 |
| `@uniops/ui` | 基础组件（Button / Card / Input / Badge / Label / Skeleton / Pagination），以 EPMS 现有 `components/ui/` 为蓝本 | P1：仅 EPMS 有组件库 |
| `@uniops/shell` | AppShell / Sidebar / Header / TabBar / **TabHost** / tab store / navConfig 与路由表类型 | 多页签 + chrome 统一 |

依赖方向：`tokens ← ui ← shell ← 各模块 app`。各模块 app（epms/oa/vms/finance）仅向 `@uniops/shell` 提供自己的 `navConfig` 和路由表。

### 2.2 Finance 独立部署

- 新建 `finance/` 前端 app：独立 Vite 构建、独立 tsconfig、独立 docker 服务、独立 origin（建议 `:5178`）。
- 消费 `#__session` 握手登录（与 oa/vms 同机制）。
- 迁移 portal 现有 `src/pages/finance/*` 与 `src/pages/budget/*` 进入 finance app；**Budget 归入 Finance 模块侧栏**；原先 iframe 内嵌 EPMS 的 budget 页改为 Finance 内的 iframe-as-tab。
- 后端不变：Finance 前端仍调用 finance-api / epms-api / oa-api / budget-api / mdm-api（纯 API 调用，前端拆分不影响）。
- 环境变量：finance app 需 `VITE_API_URL`(finance-api)、`VITE_EPMS_URL`、`VITE_OA_URL`、`VITE_BUDGET_URL`、`VITE_PORTAL_URL`。

### 2.3 Portal 瘦身

- portal 保留：Login / Logout / Home（launcher 卡片网格）/ 平台管理（AdminPanel / DataMaintenance / UnitsOfMeasure）。
- portal navConfig 新增 `VITE_FINANCE_URL` 指向 finance app，移除自身 finance section。
- portal 自身可后续选配 AppShell（非本设计核心）。

## 3. 多页签引擎

### 3.1 Tab 数据模型（zustand）

```ts
type TabKind = 'page' | 'iframe'

interface TabMeta {
  key: string          // 稳定唯一，去重身份
  title: string        // 可运行时更新（如 PO 号回填）
  kind: TabKind
  path?: string        // kind:'page'  → 模块内路由，如 '/po/123'
  src?:  string         // kind:'iframe' → 带 #__session 的跨模块 URL
  icon?: string        // 图标名（字符串，可序列化）→ lucide 映射
  closable: boolean    // Dashboard = false
  pinned?: boolean     // 固定在最左、免淘汰
}

interface TabStore {
  tabs: TabMeta[]
  activeKey: string
  openTab(meta: Omit<TabMeta,'key'> & { key?: string }): void  // 按 key 去重，存在则聚焦
  closeTab(key: string): void
  closeOthers(key: string): void
  closeAll(): void          // 保留 pinned
  setActive(key: string): void
  updateTitle(key: string, title: string): void
  markDirty(key: string, dirty: boolean): void
}
```

- 持久化到 `localStorage: uniops:<module>:tabs`，**只存 meta，不存页面状态**。
- 图标存字符串名（React 节点不可序列化），渲染时按名查 lucide 映射表。

### 3.2 Tab 去重身份（deriveTabKey）

由**路由表显式声明**，不靠路径猜测：

- 列表 / 配置页 → `keyStrategy:'static'`，key = 路由 id（如 `po-list`），全模块一个 tab。
- 详情 / 编辑页 → `keyStrategy:'param'`，key = `路由id:参数值`（如 `po-detail:123`），按记录区分。
- **query 参数不分叉 tab**：列表筛选/分页状态靠 keep-alive 留在组件内，不进 key。

### 3.3 路由表（替代 JSX `<Routes>`）

每个模块导出一份数据形式的路由表，供 AppShell 同时用于 URL 匹配与 path→element 解析：

```ts
interface RouteDef {
  path: string                       // '/po/:id'
  element: React.ReactNode           // <PoDetailPage/>
  tab?: {                            // 无 tab 的路由（login/mfa）在 shell 外渲染
    title: string | ((p: Record<string,string>) => string)
    icon?: string
    keyStrategy: 'static' | 'param'
    paramName?: string               // keyStrategy:'param' 时取哪个参数
    pinned?: boolean                 // Dashboard = true
    closable?: boolean               // 默认 true
  }
}
```

### 3.4 keep-alive TabHost（路线 A：手写注册表 + 全挂载/切显隐）

把模块 AppLayout 里的 `<Outlet/>` 换成 `TabHost`：

TabHost 维护一个 **alive 集合**（被激活过至少一次的 tab key）。它只为 alive 集合内的 tab 渲染内容；从未激活的 tab（如刷新恢复但未点开的）在 TabBar 上有条目，但内容尚未挂载。

```tsx
<TabHost>
  {tabs.filter(t => alive.has(t.key)).map(t => (
    <div key={t.key} hidden={t.key !== activeKey}>   {/* 切显隐，不卸载 */}
      {t.kind === 'page'   && <RouteRenderer path={t.path!} routes={routes} />}
      {t.kind === 'iframe' && <iframe src={t.src} className="w-full h-full border-0" />}
    </div>
  ))}
</TabHost>
```

- `RouteRenderer`：用 react-router 的 `matchRoutes(routes, path)` 解析 path→element+params，渲染该 element；外包 ErrorBoundary + Suspense。
- **懒挂载**：tab 在首次激活时加入 alive 集合并挂载；挂载后保活（仅切显隐、不卸载）直到关闭或淘汰。开机/刷新恢复不会一次性挂载全部 tab。
- **淘汰策略**：活动 `page` tab 软上限 **N = 15**。超出时淘汰 LRU 的非固定、非脏 tab（直接关闭）；候选若为脏（有未保存表单）则跳到下一个；全脏则提示用户。

### 3.5 URL ↔ Tab 双向同步（URL 为准）

- 点 Sidebar / 模块内链接 → `openTab(meta)` 然后 `navigate(path)`。
- URL 变化（深链 / 刷新 / 浏览器前进后退）→ `<TabRouterSync/>` 读 `location.pathname`，派生 key，确保对应 tab 存在并设为活动。
- 活动 tab 的 path 恒等于当前 URL → 深链可用、刷新可恢复、前进后退在 tab 间走。

### 3.6 跨模块钻取（iframe-as-tab）

```ts
openCrossModuleTab({ module: 'epms', path: '/po/123', title: 'PO 123' })
// → src = `${EPMS_URL}/po/123#__session=${encodeSession(...)}`，kind:'iframe'
```

- 子 app 被 iframe 时已隐藏自身 sidebar/header（现有 `window.self !== window.top` 检测）。
- Finance 的 AP / Payment Batch / Aging 钻取 PO / PA / Invoice 时走此通道，用户始终留在 Finance 的 tab 条内。
- Phase 2 再加 postMessage（子→父：改 tab 标题 / 请求自关 / 冒泡登录失效）；第一版只做 session 透传 + 展示。

### 3.7 固化的交互行为

| 行为 | 决策 |
|---|---|
| 首 tab | 每模块固定 Dashboard，`pinned`、不可关、最左 |
| 刷新恢复 | 重建 tab 列表（页面状态重置，keep-alive 不跨整页重载） |
| 重复打开 | 聚焦已有 tab；详情按 id 分 |
| 关闭活动 tab | 激活右邻 → 否则左邻 → 否则 Dashboard |
| 脏检查 | 页面调 `useTabDirty(isDirty)` 标记；关闭脏 tab 弹确认；淘汰跳过脏 tab |
| TabBar 右键 | 关闭 / 关闭其他 / 关闭全部（保留 pinned） |
| 溢出 | tab 数超出宽度横向滚动（不用下拉折叠，第一版） |

## 4. 组件分层

```
模块 App.tsx
  └─ <AppShell navConfig={...} routes={...}>          （来自 @uniops/shell）
       ├─ <Sidebar/>        navConfig 驱动；点击项 → openTab + navigate
       ├─ <Header/>         面包屑 / 用户菜单；模块可配置 slot（铃铛/语言等）
       ├─ <TabBar/>         渲染 tab store；点=激活，×=关闭，右键=上下文菜单
       └─ <TabHost/>        keep-alive 渲染器（见 3.4）
            └─ <RouteRenderer/>  path→element 解析（ErrorBoundary + Suspense）
       + <TabRouterSync/>   URL↔tab 同步副作用（见 3.5）
```

每个单元职责单一、接口清晰：`AppShell` 只接 `navConfig`+`routes`；`TabHost` 只认 tab store；`RouteRenderer` 只认 routes+path。模块之间通过这两份数据解耦。

## 5. 分阶段落地（每阶段独立可上线）

| Phase | 内容 | 风险 |
|---|---|---|
| 0 | workspace + `@uniops/tokens`：抽单一 token 源，4 个 app 改 `@import`；统一 focus 色 / 背景色 | 极低（零视觉风险） |
| 1 | `@uniops/ui`：抽 EPMS `components/ui/` 为共享包，先在 oa 试点替换 | 低 |
| 2 | `@uniops/shell` + tab 引擎，**先在 VMS 试点**（最小，17 页）：路由改数据表、挂 AppShell、验证保活/持久化/淘汰/URL 同步 | 中（核心验证） |
| 3 | 推广 AppShell + tab 到 OA、EPMS | 中 |
| 4 | 脚手 finance app（新部署）→ 迁移 finance+budget 页 → 挂 AppShell → 接 iframe 钻取 → portal 收成 launcher（navConfig 加 `VITE_FINANCE_URL`） | 中 |

## 6. 测试策略

- **单元**：tab store（openTab 去重 / closeTab / closeOthers / closeAll 保留 pinned / 淘汰 LRU / 脏跳过）；deriveTabKey（static / param / query 不分叉）。
- **集成**：
  - 保活：tab A 填表单 → 切 B → 切回 A，断言表单值 + 滚动位置完整。
  - 持久化：刷新后断言 tab 列表重建、Dashboard 在最左、活动 tab 挂载、页面状态重置。
  - URL 同步：深链到未开详情 → 自动开 tab 并激活；前进后退在 tab 间切换。
  - iframe 钻取：Finance 开 PO tab，断言 src 带 session、子 app chrome 隐藏。
- **回归**：每模块迁移后跑现有页面冒烟（成功路径必须覆盖）。

## 7. 已识别的边界情况

| 情况 | 处理 |
|---|---|
| 深链到未打开的详情页 | 自动新建并激活 tab |
| iframe 内登录过期 | 子 app 自显登录，父窗口不受影响（Phase 2 用 postMessage 冒泡） |
| 上限淘汰命中脏表单 | 跳过该 tab，淘汰下一个 LRU；全脏则提示 |
| 同列表不同筛选 | 不分叉（同 key），筛选状态靠 keep-alive 保留 |
| 关闭最后一个非固定 tab | 回落到 Dashboard |
| 图标序列化 | tab meta 存图标名字符串，渲染时查 lucide 映射 |
| 持久化体积 | 仅存 meta；恢复后懒挂载，避免开机挂载全部页面 |

## 8. 与现有约定的衔接

- UI 文案全英文（中文仅注释）——延续现状（审计确认四 app 已合规）。
- 每个页面包在模块 chrome 内——本设计把 chrome 收敛为唯一的 `@uniops/shell` AppShell。
- 颜色 / 状态色用语义 token（danger/success/info/neutral）——随 `@uniops/tokens` + `@uniops/ui` 落地，消除硬编码 hex 与裸 Tailwind 色。
- 严格版本管理——每个 Phase 按逻辑单元正式 commit。
