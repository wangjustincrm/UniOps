import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// @uniops/shell is consumed as a workspace package (file: dep). On the host the
// monorepo root node_modules resolves its transitive deps; in the Docker dev
// container the command installs it with `--install-links` so it lands as a real
// dir under /app/node_modules and its bare imports (zustand, clsx…) resolve from
// the app's own node_modules. No Vite alias needed in either environment.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  // Serve @uniops/shell as live source instead of a pre-bundled dep, so edits to
  // the shared package are picked up without a stale optimize cache.
  optimizeDeps: {
    exclude: ['@uniops/shell'],
  },
  server: {
    port: 5176,
    watch: {
      usePolling: true,
    },
    proxy: {
      '/api': {
        target: 'http://localhost:8008',
        changeOrigin: true,
      },
    },
  },
})
