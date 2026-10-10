import { defineConfig, devices } from '@playwright/test'
import { randomBytes } from 'node:crypto'
import { resolve } from 'node:path'

// Override normal development targets for the entire test process and its children.
const python = process.env.E2E_PYTHON || resolve('../.venv/bin/python')
// Playwright also evaluates this config in workers; preserve the launcher's key.
process.env.E2E_CUSTOMER_JWT_SECRET ||= randomBytes(48).toString('base64url')
process.env.CUSTOMER_JWT_SECRET = process.env.E2E_CUSTOMER_JWT_SECRET
process.env.MONGO_DATABASE = 'stockpilot_e2e'
process.env.MONGO_URI = process.env.E2E_MONGO_URI ||
  'mongodb://localhost:27017/?replicaSet=rs0&directConnection=true'
process.env.API_PROXY_TARGET = 'http://127.0.0.1:8002'
process.env.VITE_API_BASE_URL = '/api'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 240_000,
  expect: { timeout: 15_000 },
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5174',
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    trace: 'off',
    screenshot: 'off',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `"${python}" ../scripts/init_e2e_env.py --serve`,
      url: 'http://127.0.0.1:8002/health',
      reuseExistingServer: false,
      timeout: 60_000,
      gracefulShutdown: { signal: 'SIGTERM', timeout: 15_000 },
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 5174 --strictPort',
      url: 'http://127.0.0.1:5174',
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
})
