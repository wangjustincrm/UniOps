import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    globals: true,
    // 本仓库 epms 的测试目前全是纯函数(金额口径/差异计算),不碰 DOM。
    // 需要组件测试时再引 jsdom + @testing-library,照 packages/shell 的配置。
    environment: 'node',
  },
})
