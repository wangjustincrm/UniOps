import { defineConfig } from 'vitest/config'
import path from 'path'

export default defineConfig({
  // Mirrors vite.config.ts's '@' alias — previously unneeded here because
  // every existing test-covered module only reached other src/ files through
  // `import type` (erased at compile time, no runtime resolution). Task 8's
  // matchVariance.ts is the first to add a REAL runtime import across an
  // '@/...' path (`centsEqual` from '@/lib/money'), which surfaced the gap:
  // without this, vitest fails to resolve the bare specifier at all.
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    globals: true,
    // 本仓库 epms 的测试目前全是纯函数(金额口径/差异计算),不碰 DOM。
    // 需要组件测试时再引 jsdom + @testing-library,照 packages/shell 的配置。
    environment: 'node',
  },
})
