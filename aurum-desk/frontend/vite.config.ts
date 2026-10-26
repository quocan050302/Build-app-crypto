import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
// Browser tests (frontend/tests/e2e) point the dev proxy at an isolated backend via AURUM_API_TARGET.
const API_TARGET = process.env.AURUM_API_TARGET ?? 'http://127.0.0.1:8000'
const WS_TARGET = API_TARGET.replace(/^http/, 'ws')

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5174,
    proxy: {

      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        timeout: 300000,
      },
      '/health': {
        target: API_TARGET,
        changeOrigin: true,
      },
      '/ws': {
        target: WS_TARGET,
        ws: true,
      }
    }
  }
})
