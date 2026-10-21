// Real Chromium after independent restore: no HTTP mocks or bypassed app auth.
import process from 'node:process'
import console from 'node:console'
import { writeFileSync } from 'node:fs'
import { chromium, expect } from '@playwright/test'
const origin = 'https://127.0.0.1:57914'
const issuer = 'https://auth.stockpilot.test:57915'
const browser = await chromium.launch({ args: [
  '--host-resolver-rules=MAP auth.stockpilot.test 127.0.0.1',
  '--ignore-certificate-errors-spki-list=' + process.env.B2B_BROWSER_SPKI,
] })
const context = await browser.newContext()
const page = await context.newPage()
page.setDefaultTimeout(90000)
const report = { passed: false, checks: [] }
async function login(org, username) {
  await page.goto(origin + '/b2b-app/')
  await page.getByLabel('Code d’entreprise').fill(org)
  await page.getByRole('button', { name: 'Continuer avec Keycloak' }).click()
  await page.waitForURL(issuer + '/**')
  await page.locator('#username').fill(username)
  await page.locator('#password').fill(process.env.BFF_TEST_USER_PASSWORD)
  await page.locator('#kc-login').click()
  await page.waitForURL(origin + '/b2b-app/')
}
try {
  await login('fixture-org', 'bff-test-user')
  await expect(page.getByRole('heading', { name: 'Perceuse Northline 18V 0476', exact: true })).toBeVisible()
  await page.goto(origin + '/b2b-app/assistant/' + process.env.B2B_RECOVERY_CONVERSATION)
  await expect(page.getByText(/État observé : Annulée/)).toBeVisible()
  await page.getByLabel('Votre message', { exact: true }).fill('Quelle est la garantie documentaire ?')
  const answer = page.waitForResponse(r => /\/messages$/.test(new globalThis.URL(r.url()).pathname))
  await page.getByRole('button', { name: 'Envoyer le message', exact: true }).click()
  const response = await answer
  expect(response.status()).toBe(200)
  expect((await response.json()).reply.text).not.toContain('BETA-ONLY')
  report.checks.push('restored A login, catalogue, cancelled order transcript/checkpoint and actual scoped RAG/Ollama')
  await page.getByRole('button', { name: 'Se déconnecter', exact: true }).click()
  await context.clearCookies()
  await login('fixture-other', 'bff-unprovisioned')
  await expect(page.getByRole('heading', { name: 'Perceuse B', exact: true })).toBeVisible()
  const session = await (await context.request.get(origin + '/b2b/auth/session')).json()
  const headers = { 'X-BFF-CSRF': session.csrf_token }
  expect((await context.request.get(origin + '/b2b/api/assistant/conversations/' + process.env.B2B_RECOVERY_CONVERSATION, { headers })).status()).toBe(404)
  expect((await context.request.get(origin + '/b2b/api/orders/' + process.env.B2B_RECOVERY_ORDER, { headers })).status()).toBe(404)
  report.checks.push('restored B login and distinct catalogue; foreign A conversation/order remain hidden')
  report.passed = true
} catch (error) {
  report.error_type = error.constructor.name
  console.error(error)
} finally {
  await browser.close()
  writeFileSync(process.env.B2B_BROWSER_REPORT, JSON.stringify(report, null, 2))
}
if (!report.passed) process.exitCode = 1
