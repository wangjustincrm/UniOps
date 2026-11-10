import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

// One-way dependency (design §13): the UI library never imports business
// code through the `@/` alias (relative climbs out of the folder are caught by
// the vitest scan).
const BUSINESS = { regex: '^@/(?!platform-ui(/|$))', message: 'src/platform-ui must not import business code.' }
const SHELL = {
  group: ['@uniops/shell', '@uniops/shell/*'],
  message: "Import components from '@/platform-ui' (platform-ui/primitives re-exports @uniops/shell).",
}
// Pages show dates, times and amounts through the platform's cells and
// formatters (DateTimeCell, DateCell, MoneyCell, formatDateTime…), never by
// hand: one format everywhere, and a date-only value never goes through
// `new Date("YYYY-MM-DD")`, which loses a day west of Greenwich.
const DATES = [
  { selector: "CallExpression[callee.property.name=/^toLocale(String|DateString|TimeString)$/]",
    message: 'Use DateTimeCell / DateCell / MoneyCell or formatDateTime / formatDate / formatTime / formatMoney from @/platform-ui.' },
  { selector: "NewExpression[callee.name='Date']",
    message: 'Use the date cells and formatters from @/platform-ui (Date.now() for "now").' },
]
// The app shell may import @uniops/shell directly (tabs, routes).
const SHELL_LAYER = ['src/main.tsx', 'src/components/layout/**', 'src/app/**']
// Form controls come from the primitives (Select, Checkbox, CheckboxList,
// NumberInput, DateInput, DateTimeInput, Textarea, Autocomplete…): pages and
// patterns never render a raw one (the user's rule, 2026-10-30).
const TYPES = '/^(checkbox|radio|date|datetime-local|number|file)$/'
const RAW_CONTROLS = [
  { selector: "JSXOpeningElement[name.name='select']", message: 'Use Select (or CheckboxList) from @/platform-ui, not a raw <select>.' },
  { selector: "JSXOpeningElement[name.name='textarea']", message: 'Use Textarea from @/platform-ui, not a raw <textarea>.' },
  {
    selector: `JSXOpeningElement[name.name=/^(input|Input)$/] > JSXAttribute[name.name='type'][value.value=${TYPES}]`,
    message: 'Use Checkbox, NumberInput, DateInput, DateTimeInput or FilePicker from @/platform-ui for this input type.',
  },
  {
    selector: "JSXOpeningElement[name.name=/^(input|Input)$/] > JSXAttribute[name.name='type'][value.type='JSXExpressionContainer']",
    message: 'A computed input type hides which control this is: use the matching primitive from @/platform-ui.',
  },
]

// Tables in features and pages come from the library: LocalTable (rows in
// memory), EditableTable (rows edited in place), DiffTable (side by side),
// ListPage / QueryTable (server lists) — never hand-made (the user's rule,
// 2026-11-02). Only platform-ui/primitives renders the elements.
const RAW_TABLES = [
  {
    selector: "JSXOpeningElement[name.name=/^(table|thead|tbody|tfoot|tr|td|th)$/]",
    message: 'Use LocalTable, EditableTable or DiffTable from @/platform-ui (ListPage for a server list), not a raw table element.',
  },
]

// Forms come from the library — SchemaForm (a record's fields) or
// MasterDetailForm (fields + lines), always in a Sheet or a Dialog; a page
// never carries a form of its own and "New" is ListPage's create slot (the
// user's rule, 2026-11-02). The placement half cannot be seen by ESLint across
// components; it is in platform-ui/README.md.
const RAW_FORMS = [
  { selector: "JSXOpeningElement[name.name='form']", message: 'Use SchemaForm or MasterDetailForm from @/platform-ui (in a Sheet or Dialog), not a raw <form>.' },
]

// A record's detail is a RecordSheet (the user's ask, 2026-11-03: one detail
// frame — header, facts, actions, tabs); a settings form is a FormSheet.
// ListPage / CrudPage open the record's sheet; nothing in features or pages
// draws a raw Sheet.
const RAW_SHEETS = [
  { selector: "JSXOpeningElement[name.name='Sheet']", message: 'A record detail is a RecordSheet (a form: FormSheet) from @/platform-ui, not a raw Sheet.' },
]
// A refresh the user sees goes through useRefresh (the user, 2026-11-03: one
// reusable refresh — the busy time, what changed, the highlight): features
// never call a query's refetch() themselves.
// A table opens a row only for a click not meant for something in the cell (DataTable's
// clickOpensRow), and a question is asked in the app's dialog (ask / ConfirmAction), 2026-11-10.
const ROW_AND_PROMPT = [
  { selector: "CallExpression[callee.property.name='stopPropagation']", message: 'A table never opens the row for a click on a button, link or popover in a cell (clickOpensRow): no stopPropagation needed.' },
  { selector: "CallExpression[callee.object.name='window'][callee.property.name=/^(confirm|alert|prompt)$/]", message: 'Ask in the app dialog: ask() or ConfirmAction from @/platform-ui, not window.confirm / alert / prompt.' },
  { selector: "CallExpression[callee.type='Identifier'][callee.name=/^(confirm|alert|prompt)$/]", message: 'Ask in the app dialog: ask() or ConfirmAction from @/platform-ui.' },
]
const DIRECT_REFETCH = [
  { selector: "CallExpression[callee.property.name='refetch']", message: 'Refresh through useRefresh (and RefreshControl) from @/platform-ui, not refetch().' },
]
// What happened is said through useNotices (the list's or the sheet's status
// row, the user's ask 2026-11-03); a standing fact is a Callout. Features never
// make their own live / status / alert elements (Health, a dashboard, aside).
const AD_HOC_MESSAGES = [
  { selector: "JSXAttribute[name.name='aria-live']", message: 'Say it with useNotices (the status row) from @/platform-ui, not an aria-live element.' },
  { selector: "JSXAttribute[name.name='role'][value.value=/^(status|alert)$/]", message: 'Say it with useNotices (the status row), or show a standing fact with Callout, from @/platform-ui — not role="status" / role="alert".' },
]
// A titled block is a Section (a card, or `plain`): features write no h2 / h3
// of their own, so the look and the region it makes stay one.
const RAW_HEADINGS = [
  { selector: "JSXOpeningElement[name.name=/^h[23]$/]", message: 'Use Section from @/platform-ui, not a raw heading.' },
]
// A list column names what it holds (columnsOf from @/platform-ui), so a date looks the same in every list.
const LIST_CELLS = [
  { selector: "Property[key.name='cell'] > ArrowFunctionExpression > JSXElement > JSXOpeningElement[name.name=/^(DateCell|DateTimeCell)$/]",
    message: 'Declare the column with col.date / col.datetime from columnsOf() in @/platform-ui.' },
]
// Toasts are gone from the features for the same reason: a result is said in
// the status row, where it stays in the session's history (the bell).
const NO_TOAST_IMPORT = {
  name: '@/platform-ui', importNames: ['useToast'],
  message: 'Say it with useNotices (the status row) from @/platform-ui, not a toast.',
}
// Going to another record or list (2026-11-10): useOpenRecord / useOpenView / useGoTo close the sheet
// they are called from and ask about unsaved input; requestOpen + navigate by hand did neither.
const NO_RAW_OPEN_IMPORT = {
  name: '@/platform-ui', importNames: ['requestOpen', 'requestView'],
  message: 'Use useOpenRecord / useOpenView / useGoTo from @/platform-ui (they close this sheet and ask about unsaved input).',
}
const NO_RAW_SHEET_IMPORT = {
  name: '@/platform-ui', importNames: ['Sheet'],
  message: 'A record detail is a RecordSheet (a form: FormSheet) from @/platform-ui, not a raw Sheet.',
}

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      globals: globals.browser,
    },
  },
  // Layer rules (design §13 / §13.15). The vitest scan
  // (src/platform-ui/importRule.test.ts) resolves relative imports as well;
  // these rules give the same answer in the editor. Each file group below has
  // ONE complete list: in flat config a later block replaces the rule's
  // options instead of merging them, so the groups never overlap.
  {
    files: ['src/platform-ui/primitives/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: [BUSINESS, {
          regex: '^(\\.\\./(patterns|resource)|@/platform-ui/(patterns|resource))(/|$)',
          message: 'primitives are visual only: they import primitives, lib and @uniops/shell — never patterns or resource.',
        }],
        paths: [{ name: '@tanstack/react-query', message: 'primitives are visual only: no API or query code.' }],
      }],
    },
  },
  {
    files: ['src/platform-ui/resource/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: [BUSINESS, SHELL, {
          regex: '^(\\.\\./(patterns|primitives)|@/platform-ui/(patterns|primitives))(/|$)',
          message: 'resource is the API layer: it imports resource and lib only.',
        }],
      }],
    },
  },
  {
    files: ['src/platform-ui/patterns/**/*.{ts,tsx}'],
    rules: { 'no-restricted-imports': ['error', { patterns: [BUSINESS, SHELL] }] },
  },
  {
    files: ['src/platform-ui/*.{ts,tsx}', 'src/platform-ui/lib/**/*.{ts,tsx}'],
    rules: { 'no-restricted-imports': ['error', { patterns: [BUSINESS] }] },
  },
  // Every Sales component comes from '@/platform-ui' (its primitives
  // re-export @uniops/shell). Exempt: platform-ui itself (handled above) and
  // the app shell, which wires the shell's tabs and routes.
  {
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['src/platform-ui/**', ...SHELL_LAYER],
    rules: { 'no-restricted-imports': ['error', {
      patterns: [SHELL, { group: ['@/platform-ui/primitives/toastContext'], message: NO_TOAST_IMPORT.message }],
      paths: [NO_RAW_SHEET_IMPORT, NO_TOAST_IMPORT, NO_RAW_OPEN_IMPORT],
    }] },
  },
  // One block per file group for no-restricted-syntax (a later block would
  // replace, not add to, an earlier one's list).
  {
    files: ['src/**/*.tsx'],
    ignores: ['src/platform-ui/primitives/**', 'src/features/**', 'src/pages/**'],
    rules: { 'no-restricted-syntax': ['error', ...RAW_CONTROLS] },
  },
  {
    files: ['src/pages/**/*.tsx'],
    rules: { 'no-restricted-syntax': ['error', ...RAW_CONTROLS, ...RAW_TABLES, ...RAW_FORMS, ...RAW_SHEETS, ...DIRECT_REFETCH, ...ROW_AND_PROMPT] },
  },
  {
    files: ['src/features/**/*.{ts,tsx}'],
    ignores: ['src/features/**/*.test.{ts,tsx}'],
    rules: { 'no-restricted-syntax': ['error', ...RAW_CONTROLS, ...DATES, ...LIST_CELLS, ...RAW_TABLES, ...RAW_FORMS, ...RAW_SHEETS, ...DIRECT_REFETCH, ...AD_HOC_MESSAGES, ...RAW_HEADINGS, ...ROW_AND_PROMPT] },
  },
  {
    // Health is a dashboard (no list or sheet scope): its own result text may still be read aloud.
    files: ['src/features/health/**/*.{ts,tsx}'],
    ignores: ['src/features/**/*.test.{ts,tsx}'],
    rules: { 'no-restricted-syntax': ['error', ...RAW_CONTROLS, ...DATES, ...LIST_CELLS, ...RAW_TABLES, ...RAW_FORMS, ...RAW_SHEETS, ...DIRECT_REFETCH, ...RAW_HEADINGS, ...ROW_AND_PROMPT] },
  },
])
