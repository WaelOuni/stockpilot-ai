import { createHmac } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'
import { redactedRequest } from './redactedRequest'

const root = '/home/waelko/deployments/stockpilot-candidate-v133'
const privateValues = JSON.parse(readFileSync(`${root}/credentials.json`, 'utf8'))
const manifest = JSON.parse(readFileSync(`${root}/candidate.json`, 'utf8'))
if (manifest.url !== 'https://localhost:5193' || manifest.project !== 'stockpilot-v133-candidate') throw new Error('Wrong candidate')
function token(customer: string, lifetime = 3600) {
  const encoded = (value: unknown) => Buffer.from(JSON.stringify(value)).toString('base64url')
  const body = `${encoded({ alg: 'HS256', typ: 'JWT' })}.${encoded({ customer_id: customer, exp: Math.floor(Date.now() / 1000) + lifetime })}`
  return `${body}.${createHmac('sha256', privateValues.customer_jwt_secret).update(body).digest('base64url')}`
}

test('real Ollama candidate: Advice, context, order cards, browser refresh and identity isolation', async ({ page }) => {
  const errors: string[] = []
  const timings: Array<{ message: string; total_ms: number; request_id: string | undefined }> = []
  page.on('pageerror', error => errors.push(error.name))
  await page.goto('/assistant')
  const connect = async (customer = 'candidate-browser-a', lifetime = 3600) => {
    const field = page.getByLabel('JWT client', { exact: true })
    if (!await field.isVisible()) await page.locator('details.token-panel > summary').click()
    try { await field.fill(token(customer, lifetime)) } catch { throw new Error('Private token input failed; value withheld') }
    await page.getByRole('button', { name: 'Connecter', exact: true }).click()
  }
  const send = async (message: string) => {
    const received = page.waitForResponse(r => r.url() === manifest.url + '/api/assistant' && r.request().method() === 'POST')
    await page.getByLabel('Votre message', { exact: true }).fill(message)
    const started = performance.now()
    await page.getByRole('button', { name: 'Envoyer', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Envoyer', exact: true })).toBeDisabled()
    const response = await received
    expect(response.status()).toBe(200)
    const result = await response.json()
    await expect(page.getByRole('log')).toContainText(result.response)
    timings.push({ message, total_ms: performance.now() - started, request_id: response.headers()['x-request-id'] })
    return result
  }
  await expect(page.getByLabel('Votre message', { exact: true })).toBeDisabled()
  await connect()
  const general = await send('Je cherche une perceuse disponible à moins de 100 euros.')
  expect(general.recommendations.map((p: { sku: string }) => p.sku)).toEqual(['D-A', 'D-B', 'D-C'])
  await expect(page.getByRole('article', { name: 'Produit recommandé', exact: true })).toHaveCount(3)
  for (const product of general.recommendations) {
    const actual = await (await redactedRequest(() => page.request.get(`/api/products/${product.sku}`, { headers: { Authorization: 'Bearer ' + token('candidate-browser-a') } }))).json()
    expect(product.price).toBe(actual.price)
    expect(product.stock).toBe(actual.stock)
    expect(product.product_name).toBe(actual.product_name)
  }
  expect((await send('Et si je monte le budget à 150 euros ?')).budget).toBe(150)
  expect((await send('Montre-moi seulement deux produits.')).recommendations).toHaveLength(2)
  expect((await send('Et parmi ceux-là, lequel est le moins cher ?')).recommendations[0].sku).toBe('D-A')
  const target = await send('Peux-tu me conseiller sur la Perceuse Concurrence Synthétique V017 ?')
  expect(target.recommendations).toHaveLength(1)
  const comparison = await send('Compare cette perceuse avec deux autres.')
  expect(comparison.recommendations.map((p: { sku: string }) => p.sku)).toEqual(['D-C', 'D-A', 'D-B'])
  await expect(page.getByRole('article', { name: 'Produit de référence', exact: true })).toHaveCount(1)
  await expect(page.getByRole('article', { name: 'Alternative', exact: true })).toHaveCount(2)
  expect((await send('Compare cette perceuse avec deux autres.')).advice_status).toBe('CLARIFICATION_REQUIRED')
  await expect(page.locator('article.recommendation')).toHaveCount(0)
  expect((await send('Conseille-moi sur la Perceuse Inconnue V017.')).advice_status).toBe('NO_RESULTS')
  await page.getByRole('button', { name: 'Nouvelle conversation', exact: true }).click()
  expect((await send('Compare cette perceuse avec deux autres.')).advice_status).toBe('CLARIFICATION_REQUIRED')
  await send('Conseille-moi sur le SKU D-C')
  const stock = await send('Le stock du SKU D-C ?')
  expect(stock.intent).toBe('STOCK_CHECK')
  await expect(page.locator('article.recommendation')).toHaveCount(0)
  const prepared = await send('Commande-moi 1 SKU D-C')
  expect(prepared.order_status).toBe('PENDING_CONFIRMATION')
  await expect(page.getByRole('region', { name: 'Commande à confirmer' })).toBeVisible()
  const response = page.waitForResponse(r => r.url() === manifest.url + '/api/assistant' && r.request().method() === 'POST')
  await page.getByRole('button', { name: 'Confirmer', exact: true }).click()
  const confirmed = await (await response).json()
  expect(confirmed.order_status).toBe('CONFIRMED')
  await expect(page.getByRole('region', { name: 'Commande créée' })).toBeVisible()
  const cancelled = await redactedRequest(() => page.request.post(`/api/customer/orders/${confirmed.created_order_id}/cancel`, { headers: { Authorization: 'Bearer ' + token('candidate-browser-a'), Origin: manifest.url } }))
  expect(cancelled.status()).toBe(200)
  expect((await cancelled.json()).status).toBe('CANCELLED')
  await page.reload()
  await expect(page.getByLabel('Votre message', { exact: true })).toBeDisabled()
  await expect(page.locator('article.recommendation')).toHaveCount(0)
  await connect('candidate-browser-b')
  expect((await send('Compare cette perceuse avec deux autres.')).advice_status).toBe('CLARIFICATION_REQUIRED')
  await connect('candidate-browser-expired', -60)
  const expired = page.waitForResponse(r => r.url() === manifest.url + '/api/assistant')
  await page.getByLabel('Votre message', { exact: true }).fill('Je cherche une perceuse')
  await page.getByRole('button', { name: 'Envoyer', exact: true }).click()
  expect((await expired).status()).toBe(401)
  await expect(page.getByRole('alert')).toBeVisible()
  expect(errors).toEqual([])
  writeFileSync(`${root}/browser-timings.json`, JSON.stringify(timings, null, 2), { mode: 0o600 })
})

test('candidate backoffice cookie session and dashboard remain usable across browser refresh', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.name))
  await page.goto('/admin/login')
  try {
    await page.getByLabel('Identifiant administrateur', { exact: true }).fill(privateValues.admin_username)
    await page.getByLabel('Mot de passe administrateur', { exact: true }).fill(privateValues.admin_password)
  } catch { throw new Error('Private administrator input failed; values withheld') }
  await page.getByRole('button', { name: 'Se connecter', exact: true }).click()
  await expect(page).toHaveURL(/\/admin\/orders$/)
  await page.goto('/admin/dashboard')
  await expect(page.getByRole('heading', { name: 'Tableau de bord', exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Tableau de bord', exact: true })).toBeVisible()
  expect(errors).toEqual([])
  await page.getByRole('button', { name: 'Se déconnecter', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Connexion administrateur', exact: true })).toBeVisible()
})
