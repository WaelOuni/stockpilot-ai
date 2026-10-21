// Real Windows Chrome or Linux Chromium against the guarded disposable lifecycle.
import process from 'node:process'
import { createRequire } from 'node:module'
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { createHash, X509Certificate } from 'node:crypto'
import assert from 'node:assert/strict'
const require = createRequire(import.meta.url)
const { chromium } = require('playwright')
const state = process.env.SP_DEMO_TEST_STATE
const phase = process.env.SP_DEMO_TEST_PHASE
assert(state && ['before', 'after'].includes(phase))
const credentials = JSON.parse(readFileSync(join(state, 'credentials.json'), 'utf8'))
const leaf = new X509Certificate(readFileSync(join(state, 'tls.crt')))
const spki = createHash('sha256').update(leaf.publicKey.export({ type: 'spki', format: 'der' })).digest('base64')
const reportPath = join(state, 'browser-report.json')
const report = phase === 'after' ? JSON.parse(readFileSync(reportPath, 'utf8')) : { checks: [], platform: process.platform }
let stage = 'launch'
const browser = await chromium.launch({
  ...(process.platform === 'win32' ? { executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' } : {}),
  headless: true,
  args: ['--host-resolver-rules=MAP auth.stockpilot.test 127.0.0.1', '--ignore-certificate-errors-spki-list=' + spki],
})
const context = await browser.newContext()
const page = await context.newPage()
page.setDefaultTimeout(90000)
const origin = 'https://127.0.0.1:57914'
async function login(username) {
  stage = 'login ' + username
  const account = credentials.accounts.find(a => a.username === username)
  assert(account)
  await context.clearCookies()
  await page.goto(origin + '/b2b-app/')
  await page.getByLabel('Code d’entreprise').fill(account.organization)
  await page.getByRole('button', { name: 'Continuer avec Keycloak' }).click()
  await page.waitForURL('https://auth.stockpilot.test:57915/**')
  await page.locator('#username').fill(account.username)
  await page.locator('#password').fill(account.password)
  await page.locator('#kc-login').click()
  await page.waitForURL(origin + '/b2b-app/')
  await page.getByRole('heading', { name: /Perceuse Northline/ }).waitFor()
}
async function api(path, body) {
  return await page.evaluate(async ({ path, body }) => {
    const session = await (await globalThis.fetch('/b2b/auth/session', { credentials: 'include' })).json()
    const response = await globalThis.fetch(path, {
      method: body ? 'POST' : 'GET', credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'X-BFF-CSRF': session.csrf_token },
      ...(body ? { body: JSON.stringify(body) } : {}),
    })
    return { status: response.status, json: await response.json() }
  }, { path, body })
}
async function send(message) {
  stage = 'assistant message'
  await page.getByLabel('Votre message', { exact: true }).fill(message)
  const pending = page.waitForResponse(r => /\/messages$/.test(new globalThis.URL(r.url()).pathname))
  await page.getByRole('button', { name: 'Envoyer le message', exact: true }).click()
  const response = await pending
  assert.equal(response.status(), 200)
  return await response.json()
}
const check = name => report.checks.push(name)
try {
  await login('demo-a-manager')
  let catalogue = await api('/b2b/api/products?limit=10&offset=0')
  assert.equal(catalogue.status, 200)
  assert(catalogue.json.items.every(p => p.organization_id === 'org-a'))
  assert.equal(catalogue.json.items[0].price_minor, '4900')
  const cookie = (await context.cookies(origin)).find(c => c.name === '__Host-stockpilot_bff')
  assert(cookie.secure && cookie.httpOnly && cookie.sameSite === 'Lax')
  assert.deepEqual(await page.evaluate(() => [globalThis.localStorage.length, globalThis.sessionStorage.length]), [0, 0])
  check(phase + ': real Keycloak Windows/Chromium login and scoped catalogue/cookies, no browser tokens')
  if (phase === 'before') {
    stage = 'create conversation'
    await page.getByRole('link', { name: 'Assistant', exact: true }).click()
    await page.getByRole('button', { name: 'Nouvelle conversation' }).click()
    await page.getByLabel('Votre message', { exact: true }).waitFor()
    report.conversation_id = page.url().split('/').at(-1)
    let turn = await send('Des perceuses disponibles à moins de 50 euros')
    assert(turn.reply.products.length > 0)
    assert(turn.reply.products.every(p => p.organization_id === 'org-a' && globalThis.BigInt(p.price_minor) < 5000n))
    turn = await send('Quelle est la garantie documentaire ?')
    assert.equal(turn.reply.intent, 'RAG')
    assert(!turn.reply.text.includes('BETA-ONLY'))
    check('real Ollama catalogue and tenant RAG before stop')
    turn = await send('Je veux commander 1 unité du SKU DRILL-001')
    assert.equal(turn.reply.order.status, 'prepared')
    assert.equal(turn.reply.order.lines[0].quantity, 1)
    report.order_id = turn.reply.order.order_id
    report.product_id = catalogue.json.items[0].product_id
    let stock = await api('/b2b/api/products/' + report.product_id + '/stock')
    assert.equal(stock.json.reserved, 0)
    assert(await page.getByRole('button', { name: 'Confirmer et réserver' }).isDisabled())
    check('prepare leaves durable stock unreserved and requires explicit confirmation')
    page.on('request', request => {
      const path = new globalThis.URL(request.url()).pathname
      if (request.method() === 'POST' && path.endsWith('/confirm')) report.confirm = { path, body: request.postDataJSON() }
    })
    await page.getByLabel('Je confirme les SKU', { exact: false }).check()
    await page.getByRole('button', { name: 'Confirmer et réserver' }).click()
    await page.getByText(/État observé : Réservée/).waitFor()
    stock = await api('/b2b/api/products/' + report.product_id + '/stock')
    assert.equal(stock.json.reserved, 1)
    assert(report.confirm)
    check('explicit confirmation persists one reservation before stop')
    await page.reload()
    await page.getByText(/État observé : Réservée/).waitFor()
    check('browser reload retains private transcript and reserved order')
  } else {
    stage = 'resume private conversation'
    await page.goto(origin + '/b2b-app/assistant/' + report.conversation_id)
    await page.getByText(/État observé : Réservée/).waitFor()
    assert.equal((await api('/b2b/api/products/' + report.product_id + '/stock')).json.reserved, 1)
    check('after service stop/resume: same account, conversation/checkpoint, physical stock and reservation')
    assert.equal((await api(report.confirm.path, report.confirm.body)).status, 200)
    assert.equal((await api('/b2b/api/products/' + report.product_id + '/stock')).json.reserved, 1)
    check('original confirmation replay after resume never reserves twice')
    await page.getByRole('button', { name: 'Reprendre et vérifier', exact: true }).click()
    await page.getByLabel('Je demande l’annulation', { exact: false }).check()
    await page.getByRole('button', { name: 'Confirmer l’annulation', exact: true }).click()
    await page.getByText(/État observé : Annulée/).waitFor()
    assert.equal((await api('/b2b/api/products/' + report.product_id + '/stock')).json.reserved, 0)
    check('cancellation after resume releases once')
    stage = 'stock administration'
    await page.getByRole('link', { name: 'Stock et administration' }).click()
    await page.getByLabel('SKU du stock').fill('DRILL-001')
    await page.getByRole('button', { name: 'Consulter le stock', exact: true }).click()
    await page.getByLabel('Nouveau stock physique').fill('8')
    await page.getByLabel('Motif de l’ajustement').fill('Inventaire synthétique après reprise')
    await page.getByRole('button', { name: 'Enregistrer l’ajustement' }).click()
    await page.getByText(/Opération enregistrée et confirmée/).waitFor()
    assert.equal((await api('/b2b/api/products/' + report.product_id + '/stock')).json.quantity, 8)
    assert.equal(await page.getByLabel('Fichier CSV UTF-8').count(), 0)
    check('MANAGER cannot import CSV without import permission')
    await page.getByRole('button', { name: 'Se déconnecter', exact: true }).click()
    await page.getByLabel('Code d’entreprise').waitFor()
    await login('demo-a-admin')
    await page.getByRole('link', { name: 'Stock et administration' }).click()
    await page.getByLabel('SKU du stock').fill('DRILL-001')
    await page.getByRole('button', { name: 'Consulter le stock', exact: true }).click()
    await page.getByLabel('Fichier CSV UTF-8').setInputFiles({ name: 'stock.csv', mimeType: 'text/csv', buffer: globalThis.Buffer.from('sku,quantity\nDRILL-001,9\n') })
    await page.getByRole('button', { name: 'Prévisualiser le CSV' }).click()
    await page.getByRole('heading', { name: /Aperçu prêt/ }).waitFor()
    assert.equal((await api('/b2b/api/products/' + report.product_id + '/stock')).json.quantity, 8)
    await page.getByLabel('Je confirme l’application', { exact: false }).check()
    await page.getByRole('button', { name: 'Appliquer l’import' }).click()
    await page.getByRole('heading', { name: /Aperçu prêt/ }).waitFor({ state: 'hidden' })
    assert.equal((await api('/b2b/api/products/' + report.product_id + '/stock')).json.quantity, 9)
    check('manager adjustment and explicit organization-admin CSV preview/apply work after resume')
    await page.getByRole('button', { name: 'Se déconnecter', exact: true }).click()
    await page.getByLabel('Code d’entreprise').waitFor()
    check('logout clears tenant UI after resume')
    await login('demo-a-user')
    assert.equal((await api('/b2b/api/orders/' + report.order_id)).status, 404)
    assert.equal((await api('/b2b/api/assistant/conversations/' + report.conversation_id)).status, 404)
    check('same-organization USER cannot read another creator order/conversation')
  }
  await login('demo-b-user')
  catalogue = await api('/b2b/api/products?limit=10&offset=0')
  assert(catalogue.json.items.every(p => p.organization_id === 'org-b'))
  assert.equal(catalogue.json.items[0].price_minor, '9900')
  assert.equal((await api('/b2b/api/orders/' + report.order_id)).status, 404)
  assert.equal((await api('/b2b/api/assistant/conversations/' + report.conversation_id)).status, 404)
  await page.getByRole('link', { name: 'Stock et administration' }).click()
  assert.equal(await page.getByRole('button', { name: 'Enregistrer l’ajustement' }).count(), 0)
  check(phase + ': B distinct catalogue, no admin controls, foreign order/conversation hidden')
  await page.screenshot({ path: join(state, 'browser-' + phase + '.png'), fullPage: true })
  report[phase + '_passed'] = true
} catch (error) {
  report[phase + '_passed'] = false
  report.error_stage = stage
  report.error_type = error.name
  let message = String(error.message).replace(/https?:\/\/\S+/g, '<URL>')
  for (const account of credentials.accounts) message = message.replaceAll(account.password, '<private>')
  report.error = message.slice(0, 1500)
  process.exitCode = 1
} finally {
  await browser.close()
  writeFileSync(reportPath, JSON.stringify(report, null, 2), { mode: 0o600 })
}
