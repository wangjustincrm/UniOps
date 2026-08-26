import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  // Serve @uniops/shell as live source (not a pre-bundled dep). See vms/vite.config.ts.
  optimizeDeps: {
    exclude: ['@uniops/shell'],
  },
  server: {
    // 内网 dev/test 栈: 允许用 IP 或主机名访问(Vite 默认会挡主机名)。不要用于生产。
    allowedHosts: true,
    port: 5177,
    // Linux 原生 inotify 够用; 只有在 Windows bind mount 下才需要轮询(VITE_POLL=1)。
    // 轮询实测让每个前端常驻占用约 12% CPU。
    watch: {
      usePolling: process.env.VITE_POLL === '1',
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8004',
        changeOrigin: true,
      },
    },
  },
})
