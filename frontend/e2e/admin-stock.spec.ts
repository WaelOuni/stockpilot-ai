import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { resolve } from 'node:path'
import type { AssistantResponse, StockMovement } from '../src/types/api'

const root = resolve('..')
const python = process.env.E2E_PYTHON || resolve(root, '.venv/bin/python')
const sku = 'E2E-BOSCH-X100'
function probe<T>(action: 'movements' | 'order', customer: string, target: string): T {
  try {
    return JSON.parse(execFileSync(python, [
      resolve(root, 'frontend/e2e/support/backend_probe.py'), action, customer, target,
    ], { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }))
  } catch { throw new Error('Isolated read-only stock/order Mongo verification failed.') }
}
interface StoredStock {
  stock: number; movements: StockMovement[]; customer_orders: number; internal_product_matches: boolean
}

test('isolated stock adjustments: signed deltas, replay, insufficient stock and real order reservation/cancellation', async ({ page, request }) => {
  if (!process.env.E2E_ADMIN_USERNAME || !process.env.E2E_ADMIN_PASSWORD
    || process.env.MONGO_DATABASE !== 'stockpilot_e2e') throw new Error('Isolated stock launcher configuration missing.')
  const customer = `e2e-stock-${randomUUID()}`
  const persisted = () => probe<StoredStock>('movements', customer, sku)
  const initial = persisted()
  expect(initial.customer_orders).toBe(0)
  await page.goto(`/admin/products/${sku}/stock`)
  await expect(page.getByRole('heading', { name: 'Connexion administrateur' })).toBeVisible()
  await page.getByLabel('Identifiant administrateur').fill(process.env.E2E_ADMIN_USERNAME)
  await page.getByLabel('Mot de passe administrateur').fill(process.env.E2E_ADMIN_PASSWORD)
  await page.getByRole('button', { name: 'Se connecter', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Commandes administrateur', exact: true })).toBeVisible()
  await page.goto('/admin/products')
  await page.getByRole('link', { name: 'Ajuster le stock de Bosch X100', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Ajuster le stock', exact: true })).toBeVisible()
  await expect(page.getByText(`stock serveur : ${initial.stock}`)).toBeVisible()
  await page.getByLabel('Variation de stock signée').fill('2')
  await page.getByLabel('Motif (obligatoire)').fill('  Reception control  ')
  await page.getByRole('button', { name: 'Prévisualiser', exact: true }).click()
  await expect(page.getByText(`Stock estimé : ${initial.stock} → ${initial.stock + 2}`)).toBeVisible()
  expect(persisted()).toEqual(initial)
  const responsePromise = page.waitForResponse(response =>
    response.url().endsWith(`/api/backoffice/products/${sku}/stock-adjustments`)
      && response.request().method() === 'POST')
  await page.getByLabel('Je confirme cet ajustement de stock.').check()
  await page.getByRole('button', { name: 'Confirmer l’ajustement', exact: true }).click()
  const response = await responsePromise
  expect(response.status()).toBe(200)
  const payload = response.request().postDataJSON() as { operation_id: string; delta: number; reason: string }
  const movement = await response.json() as StockMovement
  expect(payload).toMatchObject({ delta: 2, reason: 'Reception control' })
  expect(movement).toMatchObject({
    operation_id: payload.operation_id, product_sku: sku, delta: 2,
    stock_before: initial.stock, stock_after: initial.stock + 2, reason: 'Reception control', source: 'BACKOFFICE',
  })
  const positive = persisted()
  expect(positive.stock).toBe(initial.stock + 2)
  expect(positive.movements).toHaveLength(initial.movements.length + 1)
  expect(positive.movements[0]!.movement_id).toBe(movement.movement_id)
  expect(positive.internal_product_matches).toBe(true)

  const guards = await page.evaluate(async ({ sku, payload }) => {
    const session = await (await fetch('/api/backoffice/session/me', { credentials: 'include' })).json()
    const headers = { 'Content-Type': 'application/json', 'X-Admin-CSRF': session.csrf_token }
    const endpoint = `/api/backoffice/products/${sku}/stock-adjustments`
    const post = (body: unknown, requestHeaders = headers) => fetch(endpoint, {
      method: 'POST', credentials: 'include', headers: requestHeaders, body: JSON.stringify(body),
    })
    const replay = await post(payload)
    const found = await fetch(`/api/backoffice/stock-adjustments/${payload.operation_id}`, { credentials: 'include' })
    const conflict = await post({ ...payload, delta: 3 })
    const noCsrf = await post({ ...payload, operation_id: crypto.randomUUID() }, { ...headers, 'X-Admin-CSRF': '' })
    return {
      replayStatus: replay.status, replay: await replay.json(),
      foundStatus: found.status, found: await found.json(), conflict: conflict.status, noCsrf: noCsrf.status,
    }
  }, { sku, payload })
  expect(guards).toEqual({ replayStatus: 200, replay: movement, foundStatus: 200, found: movement, conflict: 409, noCsrf: 403 })
  expect(persisted()).toEqual(positive)

  await expect(page.getByText(`stock serveur : ${initial.stock + 2}`)).toBeVisible()
  await page.getByLabel('Variation de stock signée').fill('-1')
  await page.getByLabel('Motif (obligatoire)').fill('Damaged unit')
  await page.getByRole('button', { name: 'Prévisualiser', exact: true }).click()
  await expect(page.getByText(`Stock estimé : ${initial.stock + 2} → ${initial.stock + 1}`)).toBeVisible()
  await page.getByLabel('Je confirme cet ajustement de stock.').check()
  await page.getByRole('button', { name: 'Confirmer l’ajustement', exact: true }).click()
  await expect(page.getByText(`stock serveur : ${initial.stock + 1}`)).toBeVisible()
  const negative = persisted()
  expect(negative.stock).toBe(initial.stock + 1)
  expect(negative.movements).toHaveLength(initial.movements.length + 2)
  expect(negative.movements[0]!.delta).toBe(-1)
  await page.reload()
  await expect(page.getByText(`stock serveur : ${initial.stock + 1}`)).toBeVisible()
  await expect(page.getByRole('region', { name: 'Historique des mouvements' })).toContainText('Damaged unit')
  await page.getByLabel('Variation de stock signée').fill(String(-(initial.stock + 2)))
  await page.getByLabel('Motif (obligatoire)').fill('Must be rejected')
  await page.getByRole('button', { name: 'Prévisualiser', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('stock final estimé positif ou nul requis')
  const insufficient = await page.evaluate(async ({ sku, delta }) => {
    const session = await (await fetch('/api/backoffice/session/me', { credentials: 'include' })).json()
    return (await fetch(`/api/backoffice/products/${sku}/stock-adjustments`, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'X-Admin-CSRF': session.csrf_token },
      body: JSON.stringify({ operation_id: crypto.randomUUID(), delta, reason: 'Server stock guard' }),
    })).status
  }, { sku, delta: -(initial.stock + 2) })
  expect(insufficient).toBe(409)
  expect(persisted()).toEqual(negative)

  let jwt: string
  try {
    jwt = execFileSync(python, [resolve(root, 'scripts/create_customer_token.py'), customer, '--expires-in', '1800'],
      { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
  } catch { throw new Error('Isolated customer token generation failed; no credentials logged.') }
  const headers = { Authorization: 'Bearer ' + jwt }
  const thread = randomUUID()
  const prepared = await request.post('/api/assistant', {
    headers, data: { thread_id: thread, message: 'Commande-moi 1 Bosch X100' }, timeout: 120_000,
  })
  expect(prepared.status()).toBe(200)
  expect((await prepared.json() as AssistantResponse).order_status).toBe('PENDING_CONFIRMATION')
  const confirmed = await request.post('/api/assistant', {
    headers, data: { thread_id: thread, message: 'Oui je confirme' }, timeout: 120_000,
  })
  expect(confirmed.status()).toBe(200)
  const created = await confirmed.json() as AssistantResponse
  expect(created.order_status).toBe('CONFIRMED')
  const orderId = created.created_order_id!
  expect(persisted().stock).toBe(initial.stock)
  const cancelled = await request.post(`/api/customer/orders/${orderId}/cancel`, { headers })
  expect(cancelled.status()).toBe(200)
  expect((await cancelled.json()).status).toBe('CANCELLED')
  const final = persisted()
  expect(final.stock).toBe(initial.stock + 1)
  expect(final.movements).toEqual(negative.movements)
  expect(final.customer_orders).toBe(1)
  const stored = probe<{
    status: string; remaining_stock: number; reserved_product_matches: boolean;
    events: { status: string; source: string }[]
  }>('order', customer, orderId)
  expect(stored).toMatchObject({
    status: 'CANCELLED', remaining_stock: initial.stock + 1, reserved_product_matches: true,
  })
  expect(stored.events.map(event => [event.status, event.source])).toEqual([
    ['CREATED', 'SYSTEM'], ['CANCELLED', 'CUSTOMER'],
  ])
  await page.getByRole('button', { name: 'Se déconnecter', exact: true }).click()
  await page.goto(`/admin/products/${sku}/stock`)
  await expect(page.getByRole('heading', { name: 'Connexion administrateur' })).toBeVisible()
})
