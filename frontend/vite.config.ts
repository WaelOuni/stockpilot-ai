import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const envDir = process.env.STOCKPILOT_DEMO_ENV_DIR || process.cwd()
  const env = loadEnv(mode, envDir, '')
  return {
    envDir,
    plugins: [vue()],
    server: {
      proxy: {
        '/api': {
          target: env.API_PROXY_TARGET || 'http://127.0.0.1:8000',
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
  }
})
