import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// Shared workspace package. Resolve it directly to its TS source so Vite
// transpiles it as app source (its bare imports — react, zustand, clsx… —
// then resolve from this app's node_modules). Works on host and in the
// Docker dev container, where ../packages/shell is mounted at /packages/shell.
const shellEntry = path.resolve(__dirname, '../packages/shell/src/index.ts')

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      '@uniops/shell': shellEntry,
    },
  },
  server: {
    port: 5176,
    // Allow serving the shared package source that lives outside this app's root.
    fs: {
      allow: [path.resolve(__dirname, '..')],
    },
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
