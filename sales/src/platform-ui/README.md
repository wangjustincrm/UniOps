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
- **Light theme only**, using the shell's tokens.

## Public interface (`@/platform-ui`)

| Export | Use |
| --- | --- |
| `createResourceClient(http, basePath)` / `ResourceClient` | `list(spec)`, `get`, `create`, `update(id, changes, version)`, `deactivate`, `exportPath`, `importCsv(file, dryRun)` against the back end's `crud_router`; `http` is the module's own API client |
| `useCrudResource(client, spec)` | TanStack Query list + create / update / deactivate mutations |
| `QuerySpec`, `toSearchParams`, `defaultSpec` | the list contract (`page`, `page_size` ≤ 200, `sort=a,-b`, `search`, `filter=f:op:v`) |
| `DataTable<T>` (primitive) | the table view: sortable header, controlled search, filter slot, paging, empty / error / loading states — no API knowledge |
| `QueryTable<T>` (pattern) | `DataTable` adapted to `QuerySpec`: sort / page (clamped to 200) / search / filters (`FilterControl`) / error text; `setFilter`, `getFilter`, `clearFilter`, `clampPageSize` |
| `CrudPage<T>` | list + create/edit sheet + deactivate + CSV import with dry-run preview, for one config table |
| `SchemaForm` | config forms from a zod schema and a field list; `changesOnly` sends only changed fields, and a cleared field as `null` |
| `StatusBadge` | status shown through a map supplied by the page |
| `ActionButton` | disabled while its action is pending; checks a permission key passed in |
| `PermissionProvider`, `PermissionGate`, `useCan` | the module passes in one `can(key)` function |
| `Dialog`, `Sheet`, `Popover`, `Tabs`, `Combobox`, `ToastProvider` / `useToast` | Radix-based overlays and inputs, rendered in portals (primitives) |
| `ConfirmAction` | confirm dialog that runs an action once (in-flight guard), shows its error (pattern) |
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
