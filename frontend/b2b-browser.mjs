// Actual Chromium + Keycloak + Vue + BFF + Mongo campaign. No mocked provider or services.
import process from 'node:process'
import console from 'node:console'
import { Buffer } from 'node:buffer'
import { chromium, expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
const origin = 'https://127.0.0.1:57914'
const issuerOrigin = process.env.B2B_TEST_ISSUER_ORIGIN || 'http://localhost:57915'
const dnsArgs = process.env.B2B_TEST_ISSUER_ORIGIN ? ['--host-resolver-rules=MAP auth.stockpilot.test 127.0.0.1'] : []
const checks = []
const check = name => checks.push(name)
const browser = await chromium.launch({
  headless: true,
  args: [...dnsArgs, '--ignore-certificate-errors-spki-list=' + process.env.B2B_BROWSER_SPKI],
})
const context = await browser.newContext()
const page = await context.newPage()
const failures = []
const historicalCalls = []
page.on('request', request => { if (new globalThis.URL(request.url()).pathname.startsWith('/api/')) historicalCalls.push('historical-api') })
page.on('pageerror', () => failures.push('browser-script-error'))
const probe = stage => execFileSync(process.env.B2B_TEST_PYTHON, ['scripts/tests/b2b_browser_probe.py', stage], { cwd: '..', env: process.env, stdio: 'pipe' })
async function login(org, username) {
  await page.goto(origin + '/b2b-app/')
  await page.getByLabel('Code d’entreprise').fill(org)
  await page.getByRole('button', { name: 'Continuer avec Keycloak' }).click()
  await page.waitForURL(issuerOrigin + '/**')
  await page.locator('#username').fill(username)
  await page.locator('#password').fill(process.env.BFF_TEST_USER_PASSWORD)
  await page.locator('#kc-login').click()
  await page.waitForURL(origin + '/b2b-app/')
  await expect(page.getByRole('heading', { name: 'Catalogue', exact: true })).toBeVisible()
}
try {
  await page.goto(origin + '/b2b-app/orders')
  await expect(page.getByLabel('Code d’entreprise')).toBeVisible()
  check('direct protected URL requires a real BFF session')
  await login('fixture-org', 'bff-test-user')
  await expect(page.getByText('Synthetic test company', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Perceuse A', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Perceuse B', exact: true })).toHaveCount(0)
  const cookies = await context.cookies(origin)
  const sessionCookie = cookies.find(c => c.name === '__Host-stockpilot_bff')
  expect(sessionCookie).toMatchObject({ secure: true, httpOnly: true, sameSite: 'Lax', path: '/' })
  const providerCookies = await context.cookies(issuerOrigin)
  expect(providerCookies.some(c => c.name.startsWith('__Host-stockpilot'))).toBe(false)
  expect(await page.evaluate(() => ({ local: globalThis.localStorage.length, session: globalThis.sessionStorage.length }))).toEqual({ local: 0, session: 0 })
  check('real Keycloak confidential PKCE login, HTTPS cookies, separate provider host, scoped catalogue and no browser tokens')
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Perceuse A', exact: true })).toBeVisible()
  check('reload restores server session')
  await page.screenshot({ path: process.env.B2B_BROWSER_DIRECTORY + '/catalogue-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByRole('heading', { name: 'Perceuse A', exact: true })).toBeVisible()
  expect(await page.evaluate(() => globalThis.document.documentElement.scrollWidth <= globalThis.window.innerWidth)).toBe(true)
  await page.keyboard.press('Tab')
  expect(await page.evaluate(() => globalThis.document.activeElement !== globalThis.document.body)).toBe(true)
  await page.screenshot({ path: process.env.B2B_BROWSER_DIRECTORY + '/catalogue-mobile.png', fullPage: true })
  await page.setViewportSize({ width: 1280, height: 900 })
  check('mobile viewport without horizontal page overflow and keyboard navigation')
  await page.getByRole('link', { name: 'Préparer une commande' }).first().click()
  await page.getByLabel('SKU 1', { exact: true }).fill('DRILL-001')
  await page.getByRole('button', { name: 'Préparer la commande', exact: true }).click()
  await expect(page.getByRole('heading', { name: /^Récapitulatif/ })).toBeVisible()
  probe('prepared')
  await page.screenshot({ path: process.env.B2B_BROWSER_DIRECTORY + '/order-prepared.png', fullPage: true })
  const confirmationButton = page.getByRole('button', { name: 'Confirmer et réserver', exact: true })
  await expect(confirmationButton).toBeDisabled()
  check('prepare persists without reservation; explicit confirmation required')
  let confirmRequest
  // Execute a genuine transaction, then replace only its HTTP response to simulate lost commit acknowledgement.
  await page.route('**/b2b/api/orders/*/confirm', async route => {
    confirmRequest = { url: route.request().url(), body: route.request().postDataJSON() }
    const response = await route.fetch()
    expect(response.status()).toBe(200)
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ code: 'OUTCOME_UNKNOWN', operation: { namespace: 'confirm', operation_id: confirmRequest.body.operation_id } }) })
  }, { times: 1 })
  await page.getByLabel('Je confirme explicitement', { exact: false }).check()
  await confirmationButton.click()
  await expect(page.getByRole('button', { name: 'Vérifier le résultat' })).toBeVisible()
  probe('reserved')
  await expect(confirmationButton).toBeDisabled()
  await page.reload()
  await page.getByRole('button', { name: 'Vérifier le résultat' }).click()
  await expect(page.getByText(/Commande .* : réservée/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Confirmer et réserver' })).toHaveCount(0)
  probe('reserved')
  check('real committed reservation with injected OUTCOME_UNKNOWN reconciles by the same key after reload; no new confirmation')
  const replayStatus = await page.evaluate(async ({ url, body }) => {
    const s = await (await globalThis.fetch('/b2b/auth/session', { credentials: 'include' })).json()
    const r = await globalThis.fetch(new globalThis.URL(url).pathname, { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-BFF-CSRF': s.csrf_token }, body: JSON.stringify(body) })
    return r.status
  }, confirmRequest)
  expect(replayStatus).toBe(200)
  probe('reserved')
  check('same confirmation key replays without a second reservation')
  await page.getByRole('button', { name: 'Annuler la commande', exact: true }).click()
  await page.getByRole('button', { name: 'Confirmer l’annulation', exact: true }).click()
  await expect(page.getByText('État serveur : Annulée', { exact: true })).toBeVisible()
  probe('cancelled')
  check('explicit cancellation releases stock once in durable Mongo')
  await page.getByRole('link', { name: 'Stock et administration' }).click()
  await page.getByLabel('SKU du stock').fill('DRILL-001')
  await page.getByRole('button', { name: 'Consulter le stock', exact: true }).click()
  await page.getByLabel('Nouveau stock physique').fill('8')
  await page.getByLabel('Motif de l’ajustement').fill('Inventaire synthétique navigateur')
  await page.getByRole('button', { name: 'Enregistrer l’ajustement' }).click()
  await expect(page.getByText(/Opération enregistrée et confirmée/)).toBeVisible()
  probe('adjusted')
  check('authorized physical adjustment and movement committed atomically')
  await page.getByLabel('Fichier CSV UTF-8').setInputFiles({ name: 'stock.csv', mimeType: 'text/csv', buffer: Buffer.from('sku,quantity\nDRILL-001,9\n') })
  await page.getByRole('button', { name: 'Prévisualiser le CSV' }).click()
  await expect(page.getByRole('heading', { name: /Aperçu prêt/ })).toBeVisible()
  probe('preview')
  await expect(page.getByRole('button', { name: 'Appliquer l’import' })).toBeDisabled()
  await page.getByLabel('Je confirme l’application', { exact: false }).check()
  await page.getByRole('button', { name: 'Appliquer l’import' }).click()
  await expect(page.getByRole('heading', { name: /Aperçu prêt/ })).toHaveCount(0)
  probe('imported')
  await page.screenshot({ path: process.env.B2B_BROWSER_DIRECTORY + '/stock-import.png', fullPage: true })
  check('CSV preview has no stock mutation; explicit apply and movement confirmed in Mongo')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => globalThis.document.documentElement.scrollWidth <= globalThis.window.innerWidth)).toBe(true)
  await page.screenshot({ path: process.env.B2B_BROWSER_DIRECTORY + '/stock-mobile.png', fullPage: true })
  await page.setViewportSize({ width: 1280, height: 900 })
  const csrfStatus = await page.evaluate(async () => (await globalThis.fetch('/b2b/api/imports/apply', {
    method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-BFF-CSRF': 'forged' },
    body: JSON.stringify({ operation_id: 'forged', import_id: 'forged', preview_digest: 'forged', confirmed: true }),
  })).status)
  expect(csrfStatus).toBe(403)
  probe('imported')
  check('invalid CSRF rejected by real BFF without mutation')
  const otherTab = await context.newPage()
  await otherTab.goto(origin + '/b2b-app/')
  await expect(otherTab.getByRole('heading', { name: 'Perceuse A', exact: true })).toBeVisible()
  // Hold a real authenticated catalogue response across logout. It must never repopulate the UI.
  let release
  const gate = new Promise(resolve => { release = resolve })
  let intercepted
  const reached = new Promise(resolve => { intercepted = resolve })
  await page.route('**/b2b/api/products?*', async route => {
    const response = await route.fetch()
    intercepted()
    await gate
    await route.fulfill({ response }).catch(() => {})
  }, { times: 1 })
  await page.getByRole('link', { name: 'Catalogue', exact: true }).click()
  await reached
  await page.getByRole('button', { name: 'Se déconnecter' }).click()
  await expect(page.getByLabel('Code d’entreprise')).toBeVisible()
  release()
  await expect(otherTab.getByLabel('Code d’entreprise')).toBeVisible()
  await otherTab.close()
  await expect(page.getByRole('heading', { name: 'Perceuse A', exact: true })).toHaveCount(0)
  await expect.poll(async () => await page.evaluate(async () => (await globalThis.fetch('/b2b/auth/session', { credentials: 'include' })).status)).toBe(401)
  check('logout revokes server session, clears tenant UI and rejects a delayed real response')
  // The other account uses its own Keycloak login session; local logout does not promise SSO logout.
  await context.clearCookies({ domain: new globalThis.URL(issuerOrigin).hostname })
  await login('fixture-other', 'bff-unprovisioned')
  await expect(page.getByRole('heading', { name: 'Perceuse B', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Perceuse A', exact: true })).toHaveCount(0)
  await page.getByRole('link', { name: 'Commandes', exact: true }).click()
  await expect(page.getByText('Aucune commande à afficher.', { exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Stock et administration' }).click()
  await expect(page.getByRole('button', { name: 'Enregistrer l’ajustement' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Importer un CSV' })).toHaveCount(0)
  check('real organization B USER cannot see A data or admin controls')
  const forbidden = await page.evaluate(async () => {
    const s = await (await globalThis.fetch('/b2b/auth/session', { credentials: 'include' })).json()
    return (await globalThis.fetch('/b2b/api/products/000000000000000000000001/adjustments', { method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'X-BFF-CSRF': s.csrf_token }, body: JSON.stringify({ operation_id: 'forged-user', quantity: 1, expected_revision: 1, reason: 'forged' }) })).status
  })
  expect(forbidden).toBe(403)
  probe('imported')
  check('forged USER adjustment refused server-side with Mongo unchanged')
  probe('expire')
  await page.reload()
  await expect(page.getByLabel('Code d’entreprise')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Perceuse B', exact: true })).toHaveCount(0)
  check('server-side session expiry fails closed and removes tenant content')
  expect(failures).toEqual([])
  expect(historicalCalls).toEqual([])
  writeFileSync(process.env.B2B_BROWSER_REPORT, JSON.stringify({ passed: true, checks }, null, 2))
} catch (error) {
  // Detailed diagnostics stay in the parent's private log, not the public report.
  console.error(error)
  writeFileSync(process.env.B2B_BROWSER_REPORT, JSON.stringify({ passed: false, checks, error_type: error.constructor.name }, null, 2))
  process.exitCode = 1
} finally {
  await context.close()
  await browser.close()
}
