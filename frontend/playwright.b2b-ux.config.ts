import { defineConfig } from '@playwright/test'

// UI-only contract fixtures: no application backend, database or SSO connection.
export default defineConfig({
  testDir: './ux-e2e',
  workers: 1,
  retries: 0,
  use: { baseURL: 'http://127.0.0.1:5186', viewport: { width: 390, height: 844 } },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5186 --strictPort',
    url: 'http://127.0.0.1:5186', reuseExistingServer: false,
  },
})
