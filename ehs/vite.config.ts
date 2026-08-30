import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  // Serve @uniops/shell as live source (not a pre-bundled dep) so shared-package
  // edits are picked up without a stale optimize cache.
  optimizeDeps: {
    exclude: ['@uniops/shell'],
  },
  server: {
    // 内网 dev/test 栈: 允许用 IP 或主机名访问(Vite 默认会挡主机名)。不要用于生产。
    allowedHosts: true,
    port: 5180,
    watch: {
      usePolling: process.env.VITE_POLL === '1',
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8012',
        changeOrigin: true,
      },
    },
  },
})
