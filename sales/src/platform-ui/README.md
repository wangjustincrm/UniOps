# `src/platform-ui` — UI library

Components for the Sales module's pages, built on `@uniops/shell` (design `docs/superpowers/specs/2026-10-23-order-bridge-design.md` §13.15). The Sales module uses it first; whether it moves into a shared package is decided after go-live (§14.7).

## Layers (STEP 1, 2026-10-29)

```
primitives/  visual only — Dialog, Sheet, Popover, Combobox, Tabs, Toast, StatusBadge,
             States (EmptyState, ErrorState, ErrorLine), DataTable (the view), and
             shell.ts: thin re-exports of Button, Input, Label, Badge, Card, FormField,
             Pagination, Skeleton (+ prop types) from @uniops/shell
             → may import primitives, lib and @uniops/shell; no API, permission or query code
resource/    the API side — ResourceClient, useCrudResource, querySpec, businessError
             → may import resource and lib
patterns/    composes primitives and resource — ActionButton, ConfirmAction, SchemaForm,
             QueryTable (DataTable ↔ QuerySpec), CrudPage, PermissionGate / useCan, ErrorMessage
             → may import patterns, primitives, resource, lib
lib/         cn
```

Features (`src/features/<area>/`) and pages build on `@/platform-ui` only. Enforced by ESLint (`eslint.config.js`) and by `importRule.test.ts`, each with a planted-violation proof in `tools/check-scope.sh`.

**One source for every component.** Sales imports `Button`, `Input`, `Label`, `Badge`, `Card`, `FormField`, `Pagination` and `Skeleton` from `@/platform-ui`; only `primitives/shell.ts` imports them from `@uniops/shell` (ESLint bans `@uniops/shell` everywhere else except the app shell: `src/main.tsx`, `src/components/layout/`, `src/app/`). They are re-exported, never copied; a variant Sales needs is a wrapper in `primitives/`, never a fork.

**shadcn CLI.** `sales/components.json` points the CLI at this folder (`ui` → `@/platform-ui/primitives`, css → `src/index.css`). It has not been run. After any use of it, `tools/check-scope.sh` must still show the repository root, `packages/shell` and the root `package-lock.json` unchanged — the CLI must only add files under `sales/`.

## Rules

- **One-way dependency.** Nothing here imports business code: no `@/…` outside `@/platform-ui`, and no relative path that climbs out of this folder. This is enforced twice: by the ESLint `no-restricted-imports` rule for `src/platform-ui/**` (in `eslint.config.js`), and by `importRule.test.ts`, which resolves every import and also proves that it catches a bad one.
- **No business names.** Permission keys, the status → style map, API types and paths are passed in by the pages.
- **Shell first.** `Button`, `Badge`, `Card`, `Input`, `Label`, `FormField`, `Pagination` and `Skeleton` come from `@uniops/shell` through `primitives/shell.ts`; nothing here duplicates them, and `packages/shell` is not modified.
- **Light theme only**, using the shell's tokens (the shell has no dark tokens yet). Colours are never hard-coded: neutral / primary / warning / danger / success tokens only, so a dark set in the shell would apply as is. Numbers are `tabular-nums`; money right-aligned through `MoneyCell`.
- **One detail frame (the user's ask, 2026-11-03).** A record's detail is a `RecordSheet`, opened by its list (`ListPage` / `CrudPage` `renderDetail`) — never a hand-made sheet. A settings form that is not a record's detail is a `FormSheet`. ESLint refuses `<Sheet>` and `import { Sheet }` in features and pages (planted proofs in `tools/check-scope.sh`).
- **Making and editing records (the user's rule, 2026-11-02).** A page never carries a form. "New" is ListPage's `create` slot — the standard New button at the end of the toolbar, the form in the standard Sheet; on save the sheet closes, the list refreshes and the new record's detail opens (CrudPage uses the same slot). A record's own form is `SchemaForm`; a record with lines (an order, a transfer, an invoice) is `MasterDetailForm` (header fields + `EditableTable` lines + an optional preview, one submit). Editing opens the same form inside the record's detail Sheet; a step with its own fields (a return, a bulk confirmation) opens a Dialog. An action that needs a reason is `ConfirmAction` with `reason` — never an ad-hoc bordered section. ESLint refuses a raw `<form>` in features and pages; where a form is rendered is reviewed, not linted.

## Public interface (`@/platform-ui`)

`index.ts` exports only what features, pages and the app use (trimmed 2026-11-04); a name a feature needs for the first time is added there. The table below describes the library: some of what it names is reached by its direct path (the tests do), not through the barrel.

| Export | Use |
| --- | --- |
| `createResourceClient(http, basePath)` / `ResourceClient` | `list(spec)`, `get`, `create`, `update(id, changes, version)`, `deactivate`, `exportPath`, `importCsv(file, dryRun)` against the back end's `crud_router`; `http` is the module's own API client |
| `useCrudResource(client, spec)` | TanStack Query list + create / update / deactivate mutations |
| `QuerySpec`, `toSearchParams`, `defaultSpec` | the list contract (`page`, `page_size` ≤ 200, `sort=a,-b`, `search`, `filter=f:op:v`) |
| `DataTable<T>` (primitive) | the table view: sortable header, controlled search, filter slot, paging, empty / error / loading states — no API knowledge |
| `QueryTable<T>` (pattern) | `DataTable` adapted to `QuerySpec`: sort / page (clamped to 200) / search / filters (`FilterControl`) / error text; `setFilter`, `getFilter`, `clearFilter`, `clampPageSize` |
| `CrudPage<T>` | list + create sheet + the record's `RecordSheet` (its form as the "Details" tab, read-only without the write permission with a "Read only" badge; Deactivate under More) + CSV import with dry-run preview, for one config table; `recordTitle` / `recordSubtitle` / `recordFacts` fill the sheet's header; `detailTabs(row, details)` orders the tabs (e.g. `[details, historyTab(...)]`); `singular` names one record in titles |
| `RecordSheet` | one record's detail (pattern): a sticky header — title with a copy icon, subtitle, badges; Refresh with "updated Xs ago"; previous / next record (J / K, Alt+↑ / Alt+↓, from the list's current rows, with "3 of 50"); Full width (remembered in localStorage, guarded); close — then `facts` (4–6 tiles, `tone` for warnings), the action bar (`actions`: ActionButtons with their permission; `more`: a "More" menu, each item with an optional `permission`), and `tabs` (`count`, `tone` → a dot, `hidden`, `empty` → an empty state; only the shown tab renders). `loading` → skeleton, `error` → its text + Try again (`onRefresh`). `size` md / lg / xl (default lg; the list's `detailSize`). Opened by a ListPage (its host, `RecordSheetHostContext`), the chosen tab is kept from record to record and a linked tab (`requestOpen(basePath, id, tab)`, e.g. from a Task Inbox link) opens first; alone, `open` / `onOpenChange` |
| `Section` | a titled block (a region named by its title, or by `label`): a card inside a tab, or `plain` with no card for a page's panel or part of a sheet; `level` 2 for a page's panels (pattern) |
| `Muted`, `Hint` | secondary text: at the size around it, or as a small line of explanation; `as` for p or dt (primitive) |
| `TrackingLink` | a tracking number: a copy chip, and the carrier's page in a new tab when there is a link (primitive) |
| `FormSheet` | a settings form in the standard sheet (not a record's detail) (pattern) |
| `Timeline` | a record's events as a vertical timeline: when, who, what, a before → after (primitive) |
| `CopyButton`, `CopyChip` | copy a value (a title; a tracking number or lot as a chip); a check shows it worked; no clipboard → nothing, no error (primitives) |
| `useRefresh(query, { queryKey, compare, auto, setAuto })` | **THE one way to refresh what a user sees** (pattern). Every arrival of the query's data — a click, an auto-refresh tick, a record sheet's refresh, the incidental fetches (window focus, an action's invalidation) — is compared with the last one in one place: one busy state (a click and a tick: the bar and the spin at once, ≥ 600 ms), one message ("Up to date: no changes" / "1 new · 2 updated · 1 gone" / "Refresh failed: try again"; an incidental fetch speaks only when something changed), one highlight (new and changed rows, 2 s). `compareRows(key)` for a list page, `compareRecord` for one record. `auto`: seconds (its own ticker; paused while the tab is hidden, a tick at once when it is shown again) or `'follow'` (a record sheet ticks with the list that opened it). Features never call `refetch()` (ESLint, proved in `tools/check-scope.sh`) |
| `useNotices()` / `createNoticeStore()` | **THE one way a list or a record sheet says what happened** (pattern): `notify({ tone, text, action?, sticky?, key? })` into the scope it is in (each ListPage and each RecordSheet has its own; ListPage `notices` lets the page around it share one, as CrudPage does for import / export). A key replaces the earlier notice with that key (a refresh that works clears 'refresh' failures). Info and success fade after ~4 s; a warning or an error stays until closed or cleared; every notice has ×; the session's last 20 stay in the history (also when closed). useRefresh, ActionButton (a failure always, a success with `done`), the view picker, CSV import / export, Re-check, Send 940, runs and bulk confirmations report here. Features never render their own `role="status"` / `role="alert"` / `aria-live` elements (ESLint, proved in check-scope); a standing fact ("on hold", "automatic runs off", "login refused") is a `Callout` |
| `TableStatusBar` | **THE status row** (pattern): one reserved row (h-9) that always takes its space — the current notice as a chip tinted by its tone (icon, text, its action, ×, "+N" for more open sticky ones; read aloud politely, an error at once), a bell with the unseen count opening the history (Clear), and the row count and pages on the right (wrapping below under ~480 px). ListPage pins it to the bottom of the table card; RecordSheet pins it under its tabs |
| `NoticeScope` | a notice scope with its status row for what is neither a list nor a sheet (a dashboard's section) (pattern) |
| `DataTable` `fit` / `statusBar` | the card fills what is left of the view (`fit: 'viewport'`, ListPage's default; a number caps it): the rows scroll inside it — sideways too — under a sticky header and above sticky totals; the status row is outside the scroll, pinned at the card's bottom (sticky to the page's bottom when the page itself scrolls, on a phone); a short list shrinks to its rows; columns that do not fit: a fade, and "More columns →" in a thin strip pinned with the status row (it covers no heading; at the last column "← First columns"); a refresh's new rows are brought into view only for a user still at the top |
| `Callout` | a standing fact shown where it applies, in a tone (primitive); `label` names it as a region |
| `RefreshControl` | **THE refresh control** (pattern): a split button — the icon refreshes and spins while a refresh runs (click and tick alike), the chevron chooses Off / 30s / 1m / 5m (lists; a record sheet follows its list); with auto on, the interval sits on the icon ("30", "1m", "5m") and the button is tinted; the tooltip says "Refresh · auto every 30s · updated 2:05:31 p.m."; the last refresh's message beside it, briefly. ListPage's toolbar and RecordSheet's header use it; the auto choice is kept in the saved view, else per list in localStorage |
| `ToolbarPopover` | **THE toolbar control that opens something** (pattern): an icon (aria-label, tooltip, focus ring, optional count badge) opening a small popover with a title, a body and its actions — Save as new, the advanced Search. Plain toolbar actions are `IconButton`s with tooltips (Save view, Reset, Export / Import CSV); the record's main action stays a labelled button (New) |
| `SchemaForm` | a record's form from a zod schema and a field list (types: text, email, number, textarea, checkbox, select, date, reference, multiselect) (its fields are `SchemaFields`, shared with MasterDetailForm); `changesOnly` sends only changed fields, and a cleared field as `null`; a server refusal is shown on the field it names (`details.field`, or each of `details.errors`); `readOnly` disables every input and drops the save button |
| `FieldSpec` `section` / `advanced` / `showWhen` | titled groups; a collapsed Advanced part of a group (it opens on an error); a field that applies only to some values: when not, it is neither shown nor validated, and its value is not sent. Types `segmented` (`Segmented`: a few short choices) and `days` (`DayChips`) |
| `FieldSpec` `alwaysSent`, types `presets` and `note` | with `showWhen`: not shown but still sent, at its starting value (a value the server requires); `PresetNumber` (a few usual numbers, or Custom); a line computed from the values, sending nothing |
| `StatusBadge` | status shown through a map supplied by the page |
| `ActionButton` | disabled while its action is pending; checks a permission key passed in |
| `PermissionProvider`, `PermissionGate`, `useCan` | the module passes in one `can(key)` function |
| `Dialog`, `Sheet`, `Popover`, `Tabs`, `Combobox`, `ToastProvider` | Radix-based overlays and inputs, rendered in portals (primitives); `Sheet` takes `size` (md / lg / xl / full) and a custom `header` (with `SheetTitle` / `SheetClose`) — features use RecordSheet / FormSheet, not Sheet; `Tabs` items take `count` and `attention` |
| `ConfirmAction` | confirm dialog that runs an action once (in-flight guard), shows its error; `reason` asks for a required text first and passes it to `onConfirm` (pattern) |
| `MasterDetailForm` | a record with lines: SchemaForm-style header fields + optional `addresses` (AddressFields sections) + `EditableTable` lines + optional `preview` / `blocked`; one submit, field and line errors (a server error on `ship_to.zip` lands on that input), a ref-guarded submit; in a Sheet or Dialog only (pattern) |
| `AddressFields` / `addressSchema()` | an address under one form key, in a fieldset: name, attention, phone, address, line 2, city, province (the Canadian provinces as a Select when the country is Canada, else text), postal code (A1A 1A1 in Canada), country (default CA); `addressSchema({ phone })` holds the same rules as the server — in Canada the postal code must be in the province (`postalMismatch`, first letter); a PO box (`isPoBox`) shows a warning with a confirm box (`po_box_ok`, never sent), not an error; `phoneRequired` marks the phone; `cleanAddress()` drops empty parts before sending (pattern) |
| `AddressView` | an address shown as its lines, with an optional note under it (where it came from); a dash when empty (primitive) |
| `StatCard` | a count with its label (dashboard / health); with `onOpen` the whole card is a button that opens the list behind the count (primitive) |
| `IconPopover`, `TipButton` | primitives under ToolbarPopover and RefreshControl: an icon button that opens a Radix popover (tooltip, badge), and a button whose name is its tooltip |
| `requestOpen(basePath, id, tab?)` | asks the list at `basePath` to open record `id` in its sheet, on `tab` when given (fetched by `get` when the client has it) — a Task Inbox link; `requestView(resource, { name, state })` opens a list with a view made on the spot (a dashboard card's filters, under its name) (resource) |
| `ListPage` `create` | the standard New: toolbar button (permission-gated), the form in the standard Sheet, `done(created)` closes, refreshes and opens the record (pattern) |
| `Button`, `Input`, `Label`, `Badge`, `Card…`, `FormField`, `Pagination`, `Skeleton…` | `@uniops/shell`, re-exported (primitives) |
| `EmptyState`, `ErrorState(message)`, `ErrorLine` | primitives; `ErrorMessage(error)`, `ErrorView(error)` turn an error into text by `BusinessError` code (patterns); `businessErrorOf`, `errorText` (resource) |

## Third-party dependencies

Declared in `sales/package.json` only: `@radix-ui/react-dialog`, `@radix-ui/react-popover`, `@radix-ui/react-tabs`, `@radix-ui/react-toast`, `@tanstack/react-table`, `@tanstack/react-query`, `react-hook-form`, `zod` (with a small resolver of its own in `patterns/zodResolver.ts` — `@hookform/resolvers` breaks `npm ci`), `lucide-react`, `clsx`, `tailwind-merge`, and `@uniops/shell`.

## Build facts

The folder sits inside `src/`, so `tsconfig.app.json` (typecheck), Vite (build, `@/platform-ui` alias) and Tailwind's source scanning cover it with no extra configuration. `tools/check-scope.sh` confirms three things:

- a deliberate type error placed here is reported by `tsc`, which guards against the TS5101 "diagnostics skipped" trap;
- the library's classes appear in the built CSS;
- the import rule passes.

## Tests

`*.test.ts(x)` next to the code: `npm test` (vitest, jsdom).

## List columns

A list column says what it holds: `const col = columnsOf<OrderRead>()`, then `col.date('order_date', 'Ordered')`, `col.money('total', 'Total', { currency: 'currency' })`, `col.countdown(...)`, `col.relative(...)`, `col.status(...)`, `col.number(...)`, `col.bool(...)`, `col.tags(...)`, `col.reference(...)`, `col.tracking(...)`, `col.duration(...)`, `col.file(...)`, `col.text(...)`. A kind of value then looks the same in every list; a column with a look of its own stays a plain `ColumnDef`. ESLint refuses a hand-made date cell in a feature. Countdown and relative cells share one clock (`useNow`).

## Choosing and searching

`MultiSelect` shows what is chosen as tags in the box, and the box is the search for the list below it, which offers what is not chosen yet; the search panel always uses it. `MultiChoice` is for forms: checkboxes up to six choices, else `MultiSelect`; `display` overrides it. A search form puts `submitOnEnter` on `onKeyDownCapture`: while a list is open, or text waits to be added, Enter chooses; after that Enter confirms and searches. An input that owns its Enter carries `CHOOSING`.
