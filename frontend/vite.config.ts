import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const api = loadEnv(mode, '.', '').VITE_PERSONAL_AGENT_API ?? 'http://127.0.0.1:8000'
  return {
    plugins: [react()],
    server: { proxy: { '/api': api, '/health': api } },
  }
})
