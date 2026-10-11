import original from './playwright.config'
import { defineConfig, devices } from '@playwright/test'

// Importing the base config only generates private ephemeral auth configuration;
// replace its launcher command before Playwright starts any process.
process.env.E2E_NO_RESET = '1'
const servers = original.webServer ? (Array.isArray(original.webServer) ? original.webServer : [original.webServer]) : []
export default defineConfig({
  ...original,
  testDir: './visual-e2e',
  testMatch: process.env.VISUAL_STAGE === 'before' ? 'screens.spec.ts' : '*.spec.ts',
  projects: [{ name: 'visual', use: { ...devices['Desktop Chrome'] } }],
  webServer: servers.map(server => ({
    ...server,
    command: server.command.includes('init_e2e_env.py') && !server.command.includes('--no-reset')
      ? server.command + ' --no-reset' : server.command,
  })),
})
