import { defineConfig, devices } from '@playwright/test'
import { randomBytes } from 'node:crypto'
import { resolve } from 'node:path'

// No init_e2e_env, MongoDB/PostgreSQL, dotenv or existing-server reuse.
process.env.ADVICE_SYNTHETIC_JWT_SECRET ||= randomBytes(48).toString('base64url')
process.env.CUSTOMER_JWT_SECRET = process.env.ADVICE_SYNTHETIC_JWT_SECRET
process.env.ADVICE_SYNTHETIC_E2E = '1'
process.env.API_PROXY_TARGET = 'http://127.0.0.1:8002'
process.env.VITE_API_BASE_URL = '/api'
process.env.STOCKPILOT_DEMO_ENV_DIR = resolve('./advice-synthetic-e2e')
const python = resolve('../.venv/bin/python')

export default defineConfig({
  testDir: './advice-synthetic-e2e', workers: 1, retries: 0, timeout: 30_000,
  reporter: 'list',
  use: { ...devices['Desktop Chrome'], baseURL: 'http://127.0.0.1:5174', trace: 'off', screenshot: 'off', video: 'off' },
  webServer: [
    { command: `"${python}" -m scripts.serve_advice_fixture`, cwd: '..', url: 'http://127.0.0.1:8002/health', reuseExistingServer: false, timeout: 30_000 },
    { command: 'npm run dev -- --host 127.0.0.1 --port 5174 --strictPort', url: 'http://127.0.0.1:5174', reuseExistingServer: false, timeout: 30_000 },
  ],
})
