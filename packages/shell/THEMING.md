# UniOps 主题设计方法

一套主题 = `packages/shell/themes/<id>.css` 里的一组 token 值 + `src/theme/theme.ts` 里登记一行。
8 个模块(EPMS、OA、Finance、Portal、VMS、Booking、MRP、Inventory)一行都不用改。

---

## 已有主题

| id | 显示名 | 说明 |
|---|---|---|
| `uniops` | UniOps | 默认主题(青绿主色),定义在 `tokens.css` 的 `@theme` |
| `crm` | CRM | Canada Royal Milk:牛奶白底色、Logo 海军蓝(`#001B72`)侧栏和主色、枫叶红(`#C20009` 提亮为 `#E8323B`)只作侧栏当前项指示条;信息色改青蓝以区别于海军蓝。`themes/crm.css` |

---

## 1. 原理

**两层 token**(全部在 `tokens.css` 的 `@theme` 里，这就是默认主题 `uniops`):

| 层 | 例子 | 谁用 |
|---|---|---|
| 色阶(原始值) | `--color-primary-50 … 900`、`--color-neutral-50 … 900`、`success/warning/danger/info-50 … 900` | 只给语义层引用 |
| 语义角色 | `fg`、`fg-muted`、`surface`、`canvas`、`line`、`accent`、`accent-soft`、`success-fg/soft/line/solid`、`cat1…8`、`nav` … | Shell 组件和页面 |

页面里的 `bg-surface`、`text-fg-muted`、`rounded-lg` 打包后都是 `var(--color-surface)` 这类变量，
所以只要在 `<html data-theme="<id>">` 下重新给变量赋值，全站跟着变。

**切换**:用户菜单里选(登记的主题 ≥ 2 套时才出现)。选择存在 `.canadaroyalmilk.com` 的 cookie
`uniops-theme` 里，所有模块共用。每个模块的 `index.html` 由 Shell 的 Vite 插件在 `<head>` 最前面
注入一段启动脚本，页面第一次绘制前就用上主题，不会闪默认色。

---

## 2. 一套主题要定的东西

只需要写**和默认不同的行**。语义角色大多指向色阶(`--color-accent: var(--color-primary-600)`),
改了色阶，角色自动跟着变;只有要指向别处时才改角色。

### 2.1 主色(品牌)色阶 `primary-50 … 900`

| 档 | 用途 | 要求 |
|---|---|---|
| 50 | `accent-soft`:选中行、主色徽章底 | `accent-fg` 在它上面 ≥ 4.5:1 |
| 200 | `accent-line`:主色细线 | — |
| 600 | `accent`:主按钮底、复选框、焦点环 | 白字在上面 ≥ 4.5:1;在白底上 ≥ 3:1 |
| 700 | `accent-fg`(链接、主色文字)、`accent-hover`、侧栏底 `nav` | 白底上 ≥ 4.5:1;侧栏文字在上面 ≥ 4.5:1 |
| 800 | `accent-active` | — |

做法：先定 600(品牌色本身，白字够对比),再向两端各铺 4~5 档，亮度均匀递进。

### 2.2 中性灰 `neutral-50 … 900`

决定整站的"冷暖"。文字三档必须达标：

| 角色 | 默认指向 | 要求 |
|---|---|---|
| `fg` 正文 | 900 | 白底、canvas 上 ≥ 4.5:1 |
| `fg-secondary` 次要文字 | 600 | 白底、浅灰底(surface-subtle)上 ≥ 4.5:1 |
| `fg-muted` 弱化文字 | 500 | **白底和浅灰底上都 ≥ 4.5:1**(最容易不达标的一档) |
| `fg-faint` 图标、占位 | 400 | 白底上 ≥ 3:1;不能用于正文 |
| `line-subtle / line / line-strong` | 100 / 200 / 300 | 只用于线和框 |
| `surface-subtle / sunken / strong` | 50 / 100 / 200 | 悬停行、内嵌底、轨道 |

### 2.3 状态色(含义固定，只能调色值)

| 状态 | 含义(不能改) | 四个角色 |
|---|---|---|
| success | 完成、通过、可用 | `-fg` 文字 / `-soft` 浅底 / `-line` 细线 / `-solid` 圆点、图标 |
| warning | 待处理、需要注意 | 同上 |
| danger | 驳回、失败、逾期、错误 | 同上 |
| info | 蓝色信息(徽章的 `blue`) | 同上 |

要求：`-fg` 在 `-soft` 上、在白底上都 ≥ 4.5:1;`-solid` 在白底上 ≥ 3:1。
注意：Badge 的 `info` 变体是**主色**(accent),蓝色是 `blue` 变体。

### 2.4 分类色 `cat1 … cat8`

只用于"类别"(PA 类型、工序、区域、图表系列),不用于状态。每个有 `catN`(实色)、`-soft`、`-fg`、`-line`,
`-fg` 在 `-soft` 上 ≥ 4.5:1。相邻编号要容易区分(图表按顺序取色)。

### 2.5 其他

- `canvas`(页面底)、`surface`(卡片、表格、输入框底)
- 侧栏：`nav`、`nav-fg`、`nav-fg-active`、`nav-item-hover`、`nav-item-active`、`nav-line`、`nav-indicator`(当前项左侧的竖条，可放品牌点缀色)
- 反色区(PDF 查看器、提示框):`inverse*`、`fg-on-inverse*`
- 阴影 `shadow-xs … lg`、`shadow-focus`(焦点环颜色跟主色走)
- 字体 `font-sans`、`font-mono`(换字体要确认已加载)
- `color-scheme: light | dark`(原生滚动条、日期选择器跟着变)

---

## 3. 制作步骤

1. `cp packages/shell/themes/_template.css packages/shell/themes/<id>.css`
   把选择器改成 `:root[data-theme="<id>"]`,删掉不变的行，改需要变的值。
2. `packages/shell/themes/index.css` 加一行 `@import "./<id>.css";`
3. `packages/shell/src/theme/theme.ts` 的 `THEMES` 加一项:`{ id: '<id>', label: '显示名', scheme: 'light' }`
4. 验收(全部要过):
   - `node scripts/theme-contrast.mjs <id>` —— 所有文字/底色组合 ≥ WCAG AA,输出全 `ok`
   - `cd packages/shell && npx vitest run`
   - `node scripts/ui-guard.mjs`
   - 本地打开各模块，用户菜单切到新主题，至少看：登录页、各模块首页/看板、一个长列表(状态徽章)、
     一个详情页(卡片、提示框)、一个表单(输入框报错/警示状态)、一个弹窗、侧栏选中项、打印预览(Ctrl+P)
5. 单独一个 `feat(shell): theme <id>` 提交，发布(8 个前端都要重建)。

---

## 4. 不能做的事

- 不改状态色的含义，不在主题里互换 success/warning/danger。
- 不为某个模块单独定颜色;模块代码里不判断当前主题。
- 两种灰的规则不变：同一张表里浅的那种灰用 outline,唯一的灰用 neutral,Paid 等已结束状态是深底白字(`dark`)。
- 打印的 PO/PR 单据、VMS 胸卡印刷色和签名墨色、后端邮件模板、上传的 Logo **不随主题**(按设计)。

---

## 5. 深色主题的前提

浅色主题(换主色、换灰的冷暖、换字体)现在就能做。

**深色主题还不行**:各模块里约 1,190 处直接写了色阶(如 `bg-primary-50`、`text-primary-700`),
深色主题下 `primary-50` 依然是浅色，会出现亮块。做深色前需要先一轮转换：把这些改成语义 token
(`accent-soft`、`accent-fg` …),并加守卫指标禁止页面直接用色阶。完成后深色主题才只需改 Shell。

深色主题的额外规则:`surface` 比 `canvas` 亮一档(卡片要浮起来),阴影几乎不可见，层次靠
`surface-subtle/sunken/strong` 和细线;状态 `-soft` 用低饱和深色，`-fg` 用浅色。

---

## 6. 让 Claude 设计一套主题时，请提供

- 主题名(显示名)和 id(小写英文)
- 浅色还是深色
- 品牌主色(色值，或"偏蓝/偏绿/公司 VI 色"等描述;有 VI 手册最好)
- 中性灰偏冷还是偏暖
- 想要的整体感觉(例如"更柔和"、"高对比"、"适合车间大屏")
- 可选：参考截图、字体偏好、侧栏要主色底还是浅色底

Claude 会产出 `themes/<id>.css`,跑对比度检查并修到全部达标，截取关键页面的前后对比图，然后按上面的步骤提交。
