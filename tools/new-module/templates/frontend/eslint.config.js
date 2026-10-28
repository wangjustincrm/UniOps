import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

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
%%#platform%%
  // One-way dependency (design §13): the UI library never imports business
  // code through the `@/` alias. Relative paths that climb out of the folder
  // are caught by the vitest scan (src/platform-ui/importRule.test.ts), which
  // resolves every import.
  {
    files: ['src/platform-ui/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': ['error', {
        patterns: [
          { regex: '^@/(?!platform-ui(/|$))', message: 'src/platform-ui must not import business code.' },
        ],
      }],
    },
  },
%%/platform%%
])
