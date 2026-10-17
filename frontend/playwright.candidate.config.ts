import { defineConfig, devices } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'

// This config never invokes the historical E2E initializer or any reset/seed.
execFileSync(resolve('../.venv/bin/python'), ['-m', 'scripts.candidate.check_environment'], {
  cwd: resolve('..'), stdio: 'pipe',
})
export default defineConfig({
  testDir: './candidate-e2e', workers: 1, retries: 0, timeout: 180_000,
  outputDir: '/home/waelko/deployments/stockpilot-candidate-v133/browser-results',
  reporter: 'list',
  use: { ...devices['Desktop Chrome'], baseURL: 'https://localhost:5193',
    ignoreHTTPSErrors: true, trace: 'off', screenshot: 'off', video: 'off' },
})
