import { defineConfig, devices } from '@playwright/test'
import { pbkdf2Sync, randomBytes } from 'node:crypto'
import { resolve } from 'node:path'

process.env.E2E_FIXTURE_SUFFIX ||= String(randomBytes(4).readUInt32BE())

// Override normal development targets for the entire test process and its children.
const python = process.env.E2E_PYTHON || resolve('../.venv/bin/python')
// Playwright also evaluates this config in workers; preserve the launcher's key.
process.env.E2E_CUSTOMER_JWT_SECRET ||= randomBytes(48).toString('base64url')
process.env.CUSTOMER_JWT_SECRET = process.env.E2E_CUSTOMER_JWT_SECRET
process.env.E2E_BACKOFFICE_API_TOKEN ||= randomBytes(48).toString('base64url')
process.env.BACKOFFICE_API_TOKEN = process.env.E2E_BACKOFFICE_API_TOKEN
process.env.E2E_ADMIN_USERNAME ||= `e2e-admin-${randomBytes(16).toString('hex')}`
process.env.E2E_ADMIN_PASSWORD ||= randomBytes(48).toString('base64url')
const adminSalt = randomBytes(16).toString('hex')
process.env.ADMIN_USERNAME = process.env.E2E_ADMIN_USERNAME
process.env.ADMIN_PASSWORD_VERIFIER = `pbkdf2_sha256$600000$${adminSalt}$${pbkdf2Sync(
  process.env.E2E_ADMIN_PASSWORD, Buffer.from(adminSalt, 'hex'), 600000, 32, 'sha256',
).toString('hex')}`
process.env.ADMIN_TRUSTED_ORIGIN = 'http://127.0.0.1:5174'
process.env.ADMIN_ALLOW_LOCAL_HTTP = 'true'
process.env.DEPLOYMENT_ENVIRONMENT = 'development'
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
  projects: [
    { name: 'chromium', testIgnore: [
      'admin.spec.ts', 'admin-products.spec.ts', 'admin-stock.spec.ts', 'admin-dashboard.spec.ts', 'admin-import.spec.ts',
    ], use: { ...devices['Desktop Chrome'] } },
    // Delivery permanently consumes seed stock; run admin operations last.
    { name: 'chromium-admin', testMatch: 'admin.spec.ts', dependencies: ['chromium'],
      use: { ...devices['Desktop Chrome'] } },
    // Product creation changes catalogue size; preserve historical seed-dependent scenarios first.
    { name: 'chromium-products', testMatch: 'admin-products.spec.ts', dependencies: ['chromium-admin'],
      use: { ...devices['Desktop Chrome'] } },
    { name: 'chromium-stock', testMatch: 'admin-stock.spec.ts', dependencies: ['chromium-products'],
      use: { ...devices['Desktop Chrome'] } },
    { name: 'chromium-dashboard', testMatch: 'admin-dashboard.spec.ts', dependencies: ['chromium-stock'],
      use: { ...devices['Desktop Chrome'] } },
    { name: 'chromium-import', testMatch: 'admin-import.spec.ts', dependencies: ['chromium-dashboard'],
      use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: [
    {
      command: `"${python}" ../scripts/init_e2e_env.py --serve${process.env.E2E_NO_RESET === '1' ? ' --no-reset' : ''}`,
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
