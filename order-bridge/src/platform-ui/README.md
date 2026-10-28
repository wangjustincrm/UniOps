# `src/platform-ui` — UI library

Components for Order Bridge pages, built on `@uniops/shell` (design `docs/superpowers/specs/2026-10-23-order-bridge-design.md` §13.15). Order Bridge uses it first; whether it moves into a shared package is decided after go-live (§14.7).

## Rules

- **One-way dependency.** Nothing here imports business code: no `@/…` outside `@/platform-ui`, and no relative path that climbs out of this folder. This is enforced twice: by the ESLint `no-restricted-imports` rule for `src/platform-ui/**` (in `eslint.config.js`), and by `importRule.test.ts`, which resolves every import and also proves that it catches a bad one.
- **No business names.** Permission keys, the status → style map, API types and paths are passed in by the pages.
- **Shell first.** `Button`, `Badge`, `Card`, `Input`, `Label`, `FormField`, `Pagination` and `Skeleton` come from `@uniops/shell`; nothing here duplicates them, and `packages/shell` is not modified.
- **Light theme only**, using the shell's tokens.

## Public interface (`@/platform-ui`)

| Export | Use |
| --- | --- |
| `createResourceClient(http, basePath)` / `ResourceClient` | `list(spec)`, `get`, `create`, `update(id, changes, version)`, `deactivate`, `exportPath`, `importCsv(file, dryRun)` against the back end's `crud_router`; `http` is the module's own API client |
| `useCrudResource(client, spec)` | TanStack Query list + create / update / deactivate mutations |
| `QuerySpec`, `toSearchParams`, `defaultSpec` | the list contract (`page`, `page_size` ≤ 200, `sort=a,-b`, `search`, `filter=f:op:v`) |
| `DataTable<T>` | TanStack Table with server-side sort / page / search through `QuerySpec`; empty, error and loading states |
| `CrudPage<T>` | list + create/edit sheet + deactivate + CSV import with dry-run preview, for one config table |
| `SchemaForm` | config forms from a zod schema and a field list; `changesOnly` sends only changed fields, and a cleared field as `null` |
| `StatusBadge` | status shown through a map supplied by the page |
| `ActionButton` | disabled while its action is pending; checks a permission key passed in |
| `PermissionProvider`, `PermissionGate`, `useCan` | the module passes in one `can(key)` function |
| `Dialog`, `Sheet`, `Popover`, `ConfirmDialog`, `Tabs`, `Combobox`, `ToastProvider` / `useToast` | Radix-based overlays and inputs, rendered in portals |
| `EmptyState`, `ErrorState`, `ErrorMessage`, `businessErrorOf`, `errorText` | error display by `BusinessError` code (`{"error": {code, message, details}}`) |

## Third-party dependencies

Declared in `order-bridge/package.json` only: `@radix-ui/react-dialog`, `@radix-ui/react-popover`, `@radix-ui/react-tabs`, `@radix-ui/react-toast`, `@tanstack/react-table`, `@tanstack/react-query`, `react-hook-form`, `zod` (with a small resolver of its own in `forms/zodResolver.ts` — `@hookform/resolvers` breaks `npm ci`), `lucide-react`, `clsx`, `tailwind-merge`, and `@uniops/shell`.

## Build facts

The folder sits inside `src/`, so `tsconfig.app.json` (typecheck), Vite (build, `@/platform-ui` alias) and Tailwind's source scanning cover it with no extra configuration. `tools/check-scope.sh` confirms three things:

- a deliberate type error placed here is reported by `tsc`, which guards against the TS5101 "diagnostics skipped" trap;
- the library's classes appear in the built CSS;
- the import rule passes.

## Tests

`*.test.ts(x)` next to the code: `npm test` (vitest, jsdom).
