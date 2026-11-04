// Disposable internal network only. No screenshots, traces, cookies or raw errors.
const { chromium } = require('playwright')
const { readFileSync, mkdirSync } = require('node:fs')
const { execFileSync } = require('node:child_process')
const assert = require('node:assert/strict')
const config = JSON.parse(readFileSync('/inputs/config.json', 'utf8'))
const stored = JSON.parse(readFileSync('/state/assistant-turn.json', 'utf8'))
const origin = 'https://bff.example:8443'
let stage = 'certificate-trust'
const checks = []
function ok(value, label) { assert.ok(value); checks.push(label) }
async function visible(page, name, level = 1) {
  await page.getByRole('heading', { name, exact: true, level }).waitFor()
}
async function main() {
  mkdirSync('/root/.pki/nssdb', { recursive: true })
  execFileSync('certutil', ['-N', '--empty-password', '-d', 'sql:/root/.pki/nssdb'], { stdio: 'pipe' })
  execFileSync('certutil', ['-A', '-d', 'sql:/root/.pki/nssdb', '-n', 'Synthetic qualification CA',
    '-t', 'C,,', '-i', '/inputs/tls.crt'], { stdio: 'pipe' })
  const browser = await chromium.launch({ headless: true })
  try {
    for (const actor of ['a1', 'b1']) {
      stage = actor + '-native-login'
      const context = await browser.newContext({ locale: 'fr-FR' })
      const page = await context.newPage()
      page.setDefaultTimeout(20000)
      let scriptErrors = 0
      page.on('pageerror', () => scriptErrors++)
      let ready = false
      for (let attempt = 0; attempt < 30; attempt++) {
        const response = await page.goto(origin + '/b2b/auth/session')
        if (response.status() === 401) { ready = true; break }
        await new Promise(resolve => setTimeout(resolve, 1000))
      }
      ok(ready, 'HTTPS BFF readiness reached without bypassing certificate validation')
      const initial = await page.goto(origin + '/b2b-app/')
      ok(initial.status() === 200, 'historical Web served through verified TLS')
      await page.getByLabel('Code d’entreprise', { exact: true }).fill(config.actors[actor].org)
      await page.getByRole('button', { name: 'Continuer avec Keycloak', exact: true }).click()
      await page.locator('#username').fill(actor)
      await page.locator('#password').fill(config.password)
      await page.locator('#kc-login').click()
      await page.waitForURL(origin + '/b2b-app/')
      await visible(page, 'Catalogue')
      const session = await page.evaluate(async () => (await fetch('/b2b/auth/session')).json())
      ok(session.user_id === actor && session.organization_id === config.actors[actor].org
        && session.role === 'USER', 'native OIDC callback/session/tenant verified')
      stage = actor + '-catalogue'
      await page.getByLabel('SKU exact', { exact: true }).fill('DRILL-001')
      await page.getByLabel('SKU exact', { exact: true }).press('Enter')
      await page.locator('[aria-label="Résultats du catalogue"]').getByText('DRILL-001', { exact: true }).waitFor()
      ok(true, 'historical catalogue renders the exact synthetic SKU')
      stage = actor + '-stocks'
      await page.goto(origin + '/b2b-app/stock')
      await visible(page, 'Consultation des stocks')
      await page.getByLabel('SKU du stock', { exact: true }).fill('DRILL-001')
      await page.getByRole('button', { name: 'Consulter le stock', exact: true }).click()
      await page.getByText('Votre rôle autorise la consultation uniquement.', { exact: true }).waitFor()
      ok(await page.getByRole('button', { name: 'Ajuster le stock physique', exact: true }).count() === 0,
        'USER reads stock without adjustment controls')
      stage = actor + '-orders'
      await page.goto(origin + '/b2b-app/orders')
      await visible(page, 'Commandes')
      await visible(page, 'Mon historique', 2)
      if (actor === 'a1') await page.locator('.b2b-table tbody tr').first().waitFor()
      ok(true, 'historical orders navigation and history render')
      stage = actor + '-assistant'
      await page.goto(origin + '/b2b-app/assistant/history')
      await visible(page, 'Historique des conversations')
      if (actor === 'a1') {
        await page.locator('a[href="/b2b-app/assistant/' + stored.conversation_id + '"]').click()
        await visible(page, 'Assistant StockPilot')
        await page.locator('[aria-label="Produits vérifiés"]').first().waitFor()
        ok(true, 'historical assistant renders a persisted v0.2.4 enriched turn')
      } else {
        const status = await page.evaluate(async id =>
          (await fetch('/b2b/api/assistant/conversations/' + id)).status, stored.conversation_id)
        ok(status === 404, 'cross-tenant conversation refused by the candidate API')
      }
      stage = actor + '-unsupported-route'
      await page.goto(origin + '/b2b-app/contact')
      await visible(page, 'Catalogue')
      ok(new URL(page.url()).pathname === '/b2b-app/', 'new Contact route safely absent from historical UI')
      ok(scriptErrors === 0, 'no uncaught browser application exception')
      await context.close()
    }
  } finally {
    await browser.close()
  }
  process.stdout.write(JSON.stringify({ success: true, checks, browser: 'Chromium',
    scope: 'historical Web + schema6-compatible API + real Keycloak; synthetic read-only business recipe' }) + '\n')
}
main().catch(error => {
  process.stdout.write(JSON.stringify({ success: false, stage, type: error.constructor.name }) + '\n')
  process.exitCode = 1
})
