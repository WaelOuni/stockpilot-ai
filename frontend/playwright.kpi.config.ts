import { defineConfig, devices } from '@playwright/test'

// Pure frontend validation: no API launcher, database connection or initialization.
export default defineConfig({
  testDir: './kpi-e2e',
  testMatch: 'dashboard-kpi.spec.ts',
  workers: 1,
  use: { ...devices['Desktop Chrome'], baseURL: 'http://127.0.0.1:5176', trace: 'off', screenshot: 'off', video: 'off' },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5176 --strictPort',
    url: 'http://127.0.0.1:5176',
    reuseExistingServer: false,
    env: { API_PROXY_TARGET: 'http://127.0.0.1:9', VITE_API_BASE_URL: '/api' },
  },
})
