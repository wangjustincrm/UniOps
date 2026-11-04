import { defineConfig } from 'vitest/config'
import path from 'path'

// No @vitejs/plugin-react here: vitest 4.0 runs on its own nested vite 7
// (vitest 4.1, the first with vite 8, crashes npm 10 — see tools/new-module),
// and the vite 8 React plugin's types do not fit it. Tests need no fast
// refresh — only the automatic JSX runtime.
export default defineConfig({
  esbuild: { jsx: 'automatic' },
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    // A cap, not the default of one worker per core: on a busy machine workers fight for CPU and tests time out
    // (measured 2026-11-04 at 2 CPUs: default 79 failed, 6 workers 18, 3 workers 2, in the same time).
    maxWorkers: 3,
  },
})
