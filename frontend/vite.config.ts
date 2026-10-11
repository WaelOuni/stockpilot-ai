import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { devAutoLogin } from './devAutoLogin'

export default defineConfig(({ mode, command }) => {
  const envDir = process.env.STOCKPILOT_DEMO_ENV_DIR || process.cwd()
  const env = loadEnv(mode, envDir, '')
  return {
    envDir,
    plugins: [vue(), ...devAutoLogin(env, command, mode)],
    server: {
      ...(env.VITE_DEV_AUTO_LOGIN === 'true' ? { host: '127.0.0.1', port: 5173, strictPort: true, cors: false } : {}),
      proxy: {
        '/api': {
          target: env.API_PROXY_TARGET || 'http://127.0.0.1:8000',
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
  }
})
