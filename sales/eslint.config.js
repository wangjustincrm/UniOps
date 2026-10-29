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
// The app shell may import @uniops/shell directly (tabs, routes).
const SHELL_LAYER = ['src/main.tsx', 'src/components/layout/**', 'src/app/**']

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
])
