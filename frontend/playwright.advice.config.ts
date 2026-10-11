import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './advice-e2e',
  workers: 1,
  retries: 0,
  timeout: 240_000,
  reporter: 'list',
  use: { ...devices['Desktop Chrome'], baseURL: 'http://127.0.0.1:5174', trace: 'off', screenshot: 'off', video: 'off' },
})
