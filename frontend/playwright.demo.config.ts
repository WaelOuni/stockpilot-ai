import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './demo-e2e',
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: 'list',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: 'http://127.0.0.1:5175',
    trace: 'off',
    screenshot: 'off',
    video: 'off',
  },
})
