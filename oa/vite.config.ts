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
  // edits are picked up without a stale optimize cache. See vms/vite.config.ts.
  optimizeDeps: {
    exclude: ['@uniops/shell'],
  },
  server: {
    port: 5175,
    watch: {
      usePolling: true,
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8006',
        changeOrigin: true,
      },
    },
  },
})
