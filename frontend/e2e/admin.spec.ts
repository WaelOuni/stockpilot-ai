import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { resolve } from 'node:path'
import type { APIRequestContext } from '@playwright/test'
import type { AssistantResponse, OrderEvent } from '../src/types/api'

const root = resolve('..')
const python = process.env.E2E_PYTHON || resolve(root, '.venv/bin/python')
function token(customer: string) {
  try {
    return execFileSync(python, [resolve(root, 'scripts/create_customer_token.py'), customer, '--expires-in', '1800'],
      { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
  } catch { throw new Error('Isolated customer token generation failed; no credentials logged.') }
}
function persisted(customer: string, orderId: string): { status: string; events: OrderEvent[]; remaining_stock: number } {
  try {
    return JSON.parse(execFileSync(python, [resolve(root, 'frontend/e2e/support/backend_probe.py'), 'order', customer, orderId],
      { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }))
  } catch { throw new Error('Isolated read-only Mongo verification failed.') }
}
async function createOrder(request: APIRequestContext, customer: string, jwt: string) {
  const thread_id = randomUUID()
  const headers = { Authorization: `Bearer ${jwt}` }
  const prepared = await request.post('/api/assistant', {
    headers, data: { thread_id, message: 'Commande-moi 1 Bosch X100' }, timeout: 120_000,
  })
  expect(prepared.status()).toBe(200)
  const preparation = await prepared.json() as AssistantResponse
  expect(preparation.order_status).toBe('PENDING_CONFIRMATION')
  expect(preparation.pending_order).toMatchObject({ customer_id: customer, product_name: 'Bosch X100', quantity: 1 })
  const confirmed = await request.post('/api/assistant', {
    headers, data: { thread_id, message: 'Oui je confirme' }, timeout: 120_000,
  })
  expect(confirmed.status()).toBe(200)
  const confirmation = await confirmed.json() as AssistantResponse
  expect(confirmation.order_status).toBe('CONFIRMED')
  const orderId = confirmation.created_order_id!
  expect(orderId).toMatch(/^[0-9a-f-]{36}$/)
  expect(persisted(customer, orderId).status).toBe('CREATED')
  return orderId
}

test('isolated admin lifecycle: session guards, real transitions, cancellation and persisted stock', async ({ page, request }) => {
  const username = process.env.E2E_ADMIN_USERNAME
  const password = process.env.E2E_ADMIN_PASSWORD
  if (!username || !password || process.env.MONGO_DATABASE !== 'stockpilot_e2e') {
    throw new Error('Isolated admin launcher configuration is missing.')
  }
  // No credential-bearing assertions, traces, screenshots, video or console capture.
  await page.goto('/admin/orders')
  await expect(page.getByRole('heading', { name: 'Connexion administrateur' })).toBeVisible()
  await expect(page).toHaveURL(/\/admin\/login/)
  await page.getByLabel('Identifiant administrateur').fill(username)
  await page.getByLabel('Mot de passe administrateur').fill(password)
  await page.getByRole('button', { name: 'Se connecter', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Commandes administrateur', exact: true })).toBeVisible()
  const listTotal = await page.evaluate(async () => (await (await fetch('/api/backoffice/orders?include_total=true')).json()).total as number)
  if (listTotal <= 20) await expect(page.getByRole('button', { name: 'Suivant', exact: true })).toBeDisabled()
  else await expect(page.getByRole('button', { name: 'Suivant', exact: true })).toBeEnabled()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Commandes administrateur', exact: true })).toBeVisible()

  const access = await page.evaluate(async () => {
    const me = await fetch('/api/backoffice/session/me', { credentials: 'include' })
    const session = await me.json() as { csrf_token: string }
    const list = await fetch('/api/backoffice/orders', { credentials: 'include' })
    const badLogout = await fetch('/api/backoffice/session/logout', {
      method: 'POST', credentials: 'include', headers: { 'X-Admin-CSRF': 'wrong' },
    })
    const after = await fetch('/api/backoffice/session/me', { credentials: 'include' })
    const mutation = await fetch('/api/orders/nonexistent/cancel', { method: 'POST', credentials: 'include' })
    const customer = await fetch('/api/orders', { credentials: 'include' })
    const cookiesInvisible = !document.cookie.includes('stockpilot_admin')
    const csrfPresent = typeof session.csrf_token === 'string' && session.csrf_token.length > 20
    return { me: me.status, list: list.status, badLogout: badLogout.status, after: after.status,
      mutation: mutation.status, customer: customer.status, cookiesInvisible, csrfPresent,
      cache: list.headers.get('cache-control') }
  })
  expect(access).toEqual({ me: 200, list: 200, badLogout: 403, after: 200,
    mutation: 401, customer: 401, cookiesInvisible: true, csrfPresent: true, cache: 'no-store' })
  const unauthenticated = await request.get('/api/backoffice/orders')
  expect(unauthenticated.status()).toBe(401)
  const evil = await request.post('/api/backoffice/session/login', {
    headers: { Origin: 'https://evil.test', 'X-Admin-CSRF': 'login' },
    data: { username: 'not-a-secret', password: 'not-a-secret-password' },
  })
  expect(evil.status()).toBe(403)
  const technical = await request.get('/api/backoffice/orders', {
    headers: { Authorization: `Bearer ${process.env.E2E_BACKOFFICE_API_TOKEN}` },
  })
  expect(technical.status()).toBe(200)
  const originalOrders = (await technical.json()).items as Array<{ order_id: string }>
  expect(originalOrders.length).toBeLessThanOrEqual(20)
  if (originalOrders.length === 0) {
    await expect(page.getByText('Aucune commande sur cette page.', { exact: true })).toBeVisible()
  } else {
    for (const order of originalOrders) await expect(page.getByText(order.order_id, { exact: true })).toBeVisible()
  }
  await page.goto(`/admin/orders?offset=${listTotal + 20}`)
  const finalOffset = Math.max(0, Math.floor((listTotal - 1) / 20) * 20)
  await expect(page).toHaveURL(url => Number(url.searchParams.get('offset') || 0) === finalOffset)
  await page.goto('/admin/orders')
  // A stale offset is corrected to the final existing page automatically.
  await page.goto('/admin/orders/nonexistent')
  await expect(page.getByRole('alert')).toContainText('Commande introuvable')
  await expect(page.getByRole('button', { name: /Annuler|Confirmer/ })).toHaveCount(0)
  await page.getByRole('link', { name: 'Retour aux commandes administrateur' }).click()

  const customer = `e2e-admin-customer-${randomUUID()}`
  const jwt = token(customer)
  const initialStock = (await (await request.get('/api/products/E2E-BOSCH-X100')).json()).stock as number
  expect(initialStock).toBeGreaterThanOrEqual(1)
  const cancelId = await createOrder(request, customer, jwt)
  expect(persisted(customer, cancelId).remaining_stock).toBe(initialStock - 1)
  await page.getByRole('button', { name: 'Actualiser', exact: true }).click()
  const created = page.locator('tbody tr').filter({ has: page.getByText(cancelId, { exact: true }) })
  await expect(created).toContainText(customer)
  await created.getByRole('link', { name: cancelId, exact: true }).click()
  await expect(page.getByRole('button', { name: 'Passer à PROCESSING', exact: true })).toBeVisible()
  const transitionResponse = page.waitForResponse(response => response.url().endsWith(`/backoffice/orders/${cancelId}/status`)
    && response.request().method() === 'PATCH')
  await page.getByRole('button', { name: 'Passer à PROCESSING', exact: true }).click()
  expect((await transitionResponse).status()).toBe(200)
  await expect(page.getByText('Statut mis à jour.', { exact: true })).toBeVisible()
  expect(persisted(customer, cancelId).events.map(event => [event.status, event.source])).toEqual([
    ['CREATED', 'SYSTEM'], ['PROCESSING', 'BACKOFFICE'],
  ])
  await page.getByRole('button', { name: 'Annuler la commande', exact: true }).click()
  await page.getByRole('button', { name: 'Conserver la commande', exact: true }).click()
  expect(persisted(customer, cancelId)).toMatchObject({ status: 'PROCESSING', remaining_stock: initialStock - 1 })
  await page.getByRole('button', { name: 'Annuler la commande', exact: true }).click()
  const cancelResponse = page.waitForResponse(response => response.url().endsWith(`/backoffice/orders/${cancelId}/cancel`)
    && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Confirmer l’annulation', exact: true }).click()
  expect((await cancelResponse).status()).toBe(200)
  await expect(page.getByRole('status').filter({ hasText: 'Commande annulée.' })).toBeVisible()
  const cancelled = persisted(customer, cancelId)
  expect(cancelled.remaining_stock).toBe(initialStock)
  expect(cancelled.events.map(event => [event.status, event.source])).toEqual([
    ['CREATED', 'SYSTEM'], ['PROCESSING', 'BACKOFFICE'], ['CANCELLED', 'BACKOFFICE'],
  ])
  await expect(page.getByRole('region', { name: 'Progression de la commande' }).locator('li')).toHaveCount(3)
  await expect(page.getByRole('button', { name: 'Annuler la commande', exact: true })).toHaveCount(0)
  const securityAndReplay = await page.evaluate(async id => {
    const session = await (await fetch('/api/backoffice/session/me')).json() as { csrf_token: string }
    const wrong = await fetch(`/api/backoffice/orders/${id}/cancel`, {
      method: 'POST', credentials: 'include', headers: { 'X-Admin-CSRF': 'wrong' },
    })
    const replay = await fetch(`/api/backoffice/orders/${id}/cancel`, {
      method: 'POST', credentials: 'include', headers: { 'X-Admin-CSRF': session.csrf_token },
    })
    return { wrong: wrong.status, replay: replay.status }
  }, cancelId)
  expect(securityAndReplay).toEqual({ wrong: 403, replay: 200 })
  expect(persisted(customer, cancelId)).toEqual(cancelled)
  const rejectedBearer = await request.patch(`/api/backoffice/orders/${cancelId}/status`, {
    headers: { Authorization: `Bearer ${process.env.E2E_BACKOFFICE_API_TOKEN}` }, data: { status: 'PROCESSING' },
  })
  expect(rejectedBearer.status()).toBe(401)
  const rejectedCustomer = await request.post(`/api/backoffice/orders/${cancelId}/cancel`, {
    headers: { Authorization: `Bearer ${jwt}` },
  })
  expect(rejectedCustomer.status()).toBe(401)
  const evilMutation = await page.request.post(`/api/backoffice/orders/${cancelId}/cancel`, {
    headers: { Origin: 'https://evil.test' },
  })
  expect(evilMutation.status()).toBe(403)

  const deliveredId = await createOrder(request, customer, jwt)
  await page.goto(`/admin/orders/${deliveredId}`)
  for (const status of ['PROCESSING', 'SHIPPED', 'DELIVERED']) {
    await page.getByRole('button', { name: `Passer à ${status}`, exact: true }).click()
    await expect(page.getByText('Statut mis à jour.', { exact: true })).toBeVisible()
    expect(persisted(customer, deliveredId).status).toBe(status)
  }
  const delivered = persisted(customer, deliveredId)
  expect(delivered.remaining_stock).toBe(initialStock - 1)
  expect(delivered.events.map(event => [event.status, event.source])).toEqual([
    ['CREATED', 'SYSTEM'], ['PROCESSING', 'BACKOFFICE'], ['SHIPPED', 'BACKOFFICE'], ['DELIVERED', 'BACKOFFICE'],
  ])
  await expect(page.getByRole('button', { name: /Passer à|Annuler la commande/ })).toHaveCount(0)
  await page.reload()
  await expect(page.getByRole('region', { name: 'Progression de la commande' }).locator('li')).toHaveCount(4)
  const terminal = await page.evaluate(async id => {
    const session = await (await fetch('/api/backoffice/session/me')).json() as { csrf_token: string }
    return (await fetch(`/api/backoffice/orders/${id}/cancel`, {
      method: 'POST', credentials: 'include', headers: { 'X-Admin-CSRF': session.csrf_token },
    })).status
  }, deliveredId)
  expect(terminal).toBe(409)
  expect(persisted(customer, deliveredId)).toEqual(delivered)
  await page.getByRole('button', { name: 'Se déconnecter', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Connexion administrateur' })).toBeVisible()
  const loggedOut = await page.evaluate(async () =>
    (await fetch('/api/backoffice/session/me', { credentials: 'include' })).status)
  expect(loggedOut).toBe(401)
  await page.goto('/admin/orders')
  await expect(page.getByRole('heading', { name: 'Connexion administrateur' })).toBeVisible()
  // Delivery consumes exactly one real reservation; cancellation restored once.
  const products = await request.get('/api/products')
  const rows = (await products.json()).items as Array<{ sku: string; stock: number }>
  expect(rows.filter(row => ['E2E-BOSCH-S500', 'E2E-BOSCH-X100', 'E2E-MAKITA-D200'].includes(row.sku)).map(({ sku, stock }) => ({ sku, stock }))).toEqual([
    { sku: 'E2E-BOSCH-S500', stock: 5 },
    { sku: 'E2E-BOSCH-X100', stock: initialStock - 1 },
    { sku: 'E2E-MAKITA-D200', stock: 0 },
  ])
})
