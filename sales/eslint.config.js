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
    rules: { 'no-restricted-imports': ['error', { patterns: [SHELL] }] },
  },
  // One block per file group for no-restricted-syntax (a later block would
  // replace, not add to, an earlier one's list).
  {
    files: ['src/**/*.tsx'],
    ignores: ['src/platform-ui/primitives/**', 'src/features/**'],
    rules: { 'no-restricted-syntax': ['error', ...RAW_CONTROLS] },
  },
  {
    files: ['src/features/**/*.{ts,tsx}'],
    ignores: ['src/features/**/*.test.{ts,tsx}'],
    rules: { 'no-restricted-syntax': ['error', ...RAW_CONTROLS, ...DATES] },
  },
])
