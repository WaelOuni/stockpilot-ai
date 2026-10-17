import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect, type Page } from '@playwright/test'
import type { AssistantResponse, CustomerOrdersResponse, OrderResponse, OrderEvent } from '../src/types/api'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const python = process.env.E2E_PYTHON || resolve(root, '.venv/bin/python')

function generateToken(customerId: string): string {
  try {
    return execFileSync(python, [
      resolve(root, 'scripts/create_customer_token.py'), customerId, '--expires-in', '1800',
    ], { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
  } catch {
    throw new Error('E2E JWT generation failed. Configure E2E_PYTHON and CUSTOMER_JWT_SECRET to match FastAPI. No token was logged.')
  }
}

interface MongoOrder {
  order_id: string
  customer_id: string
  product_name: string
  quantity: number
  status: string
  operation_id: string
  remaining_stock: number
  events: OrderEvent[]
}

function probe<T>(action: 'stock' | 'order', customerId: string, orderId?: string): T {
  let output: string
  try {
    output = execFileSync(python, [
      resolve(root, 'frontend/e2e/support/backend_probe.py'), action, customerId,
      ...(orderId ? [orderId] : []),
    ], { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
  } catch {
    throw new Error(action === 'stock'
      ? 'E2E prerequisite failed: Mongo must be reachable, Bosch X100 must match one product with stock >= 1, and the dedicated customer must have no orders. No data was changed.'
      : 'E2E Mongo verification failed: the created order must belong to the dedicated customer in the configured database.')
  }
  return JSON.parse(output) as T
}

async function send(page: Page, message: string, expectedThread?: string, confirmButton = false) {
  const received = page.waitForResponse((response) =>
    response.url().endsWith('/api/assistant') && response.request().method() === 'POST',
  { timeout: 120_000 })
  if (confirmButton) {
    await page.getByRole('button', { name: 'Confirmer', exact: true }).click()
  } else {
    await page.getByLabel('Votre message', { exact: true }).fill(message)
    await page.getByRole('button', { name: 'Envoyer', exact: true }).click()
  }
  const response = await received
  expect(response.status(), 'Assistant must respond successfully; verify JWT and backend services.').toBe(200)
  const payload = response.request().postDataJSON() as { message: string; thread_id: string }
  expect(payload.message).toBe(message)
  expect(payload.thread_id).toMatch(/^[0-9a-f-]{36}$/)
  if (expectedThread) expect(payload.thread_id).toBe(expectedThread)
  const result = await response.json() as AssistantResponse
  expect(typeof result.response).toBe('string')
  await expect(page.getByRole('log').getByText(message, { exact: true })).toBeVisible()
  await expect(page.getByRole('log').getByText(result.response!, { exact: true })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
  return { result, threadId: payload.thread_id }
}

test('creates, tracks polling updates and cancels its own order', async ({ page, request }) => {
  const customerId = `e2e-customer-${randomUUID()}`
  const token = generateToken(customerId)
  const initialStock = probe<{ stock: number }>('stock', customerId).stock
  expect(initialStock).toBeGreaterThanOrEqual(1)
  const browserErrors: string[] = []
  // Record only categories, never console text or headers that could contain a JWT.
  page.on('pageerror', () => { browserErrors.push('uncaught browser exception') })
  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push('console error')
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Assistant', exact: true })).toBeVisible()
  await page.getByLabel('JWT client', { exact: true }).fill(token)
  await page.getByRole('button', { name: 'Connecter', exact: true }).click()
  await expect(page.getByLabel('JWT client', { exact: true })).toHaveValue('')
  await page.getByRole('navigation').getByRole('link', { name: 'Assistant', exact: true }).click()

  const catalogueCount = (await (await request.get('/api/products?limit=20', {
    headers: { Authorization: `Bearer ${token}` },
  })).json()).count
  await page.getByRole('navigation').getByRole('link', { name: 'Catalogue', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Catalogue', exact: true })).toBeVisible()
  await page.getByLabel('Catégorie', { exact: true }).selectOption('DRILL')
  await page.getByLabel('Recherche', { exact: true }).fill('X100')
  await page.getByRole('button', { name: 'Rechercher', exact: true }).click()
  await expect(page).toHaveURL(/q=X100.*category=DRILL/)
  await expect(page.getByRole('article')).toHaveCount(1)
  await expect(page.getByRole('article').getByRole('heading')).toHaveText('Bosch X100')
  await page.getByLabel('Disponibilité', { exact: true }).selectOption('true')
  await page.getByRole('button', { name: 'Rechercher', exact: true }).click()
  await expect(page).toHaveURL(/available=true/)
  await expect(page.getByRole('article')).toHaveCount(1)
  await page.getByRole('button', { name: 'Réinitialiser les filtres', exact: true }).click()
  await expect(page.getByRole('article')).toHaveCount(catalogueCount)
  await page.getByLabel('Recherche', { exact: true }).fill('Makita')
  await page.getByRole('button', { name: 'Rechercher', exact: true }).click()
  await expect(page.getByRole('article')).toHaveCount(1)
  await expect(page.getByRole('article').getByRole('heading')).toHaveText('Makita D200')
  await page.getByLabel('Disponibilité', { exact: true }).selectOption('true')
  await page.getByRole('button', { name: 'Rechercher', exact: true }).click()
  await expect(page).toHaveURL(/q=Makita.*available=true/)
  await expect(page.getByText('Aucun produit ne correspond à vos critères.', { exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('Recherche', { exact: true })).toHaveValue('Makita')
  await expect(page.getByLabel('Disponibilité', { exact: true })).toHaveValue('true')
  await page.getByLabel('JWT client', { exact: true }).fill(token)
  await page.getByRole('button', { name: 'Connecter', exact: true }).click()
  await expect(page.getByText('Aucun produit ne correspond à vos critères.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Réinitialiser les filtres', exact: true }).click()
  await expect(page.getByRole('article')).toHaveCount(catalogueCount)
  const productCard = page.getByRole('article').filter({ has: page.getByRole('heading', { name: 'Bosch X100', exact: true }) })
  await productCard.getByRole('link', { name: 'Voir le produit Bosch X100', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Fiche produit', exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Commander', exact: true }).click()
  await expect(page.getByLabel('Votre message', { exact: true })).toHaveValue('Commande-moi 1 Bosch X100')
  const preparation = await send(page, 'Commande-moi 1 Bosch X100')
  expect(preparation.result.intent, 'The real classifier must identify ORDER.').toBe('ORDER')
  expect(preparation.result.order_status, 'Expected confirmation; check product matching and available stock.').toBe('PENDING_CONFIRMATION')
  expect(preparation.result.pending_order).toMatchObject({ product_name: 'Bosch X100', quantity: 1, customer_id: customerId })
  await expect(page.getByText('Une commande attend votre decision.', { exact: false })).toBeVisible()
  expect(preparation.result.response).toMatch(/Confirmez-vous/)

  await expect(page.getByRole('region', { name: 'Commande à confirmer' })).toBeVisible()
  const confirmation = await send(page, 'Oui je confirme', preparation.threadId, true)
  expect(confirmation.result.order_status).toBe('CONFIRMED')
  expect(confirmation.result.confirmation_handled).toBe(true)
  expect(confirmation.result.created_order_id).toMatch(/^[0-9a-f-]{36}$/)
  const orderId = confirmation.result.created_order_id!
  expect(confirmation.result.response).toContain(orderId)
  expect(confirmation.result.response).toContain('Bosch X100')
  await expect(page.getByRole('region', { name: 'Commande créée' })).toBeVisible()
  const stored = probe<MongoOrder>('order', customerId, orderId)
  expect(stored).toMatchObject({
    order_id: orderId, customer_id: customerId, product_name: 'Bosch X100',
    quantity: 1, status: 'CREATED',
    operation_id: preparation.result.pending_order?.operation_id,
    remaining_stock: initialStock - 1,
  })
  expect(stored.events).toHaveLength(1)
  expect(stored.events[0]).toMatchObject({ status: 'CREATED', source: 'SYSTEM' })
  expect(Number.isFinite(Date.parse(stored.events[0]!.occurred_at))).toBe(true)

  const listing = page.waitForResponse((response) =>
    new URL(response.url()).pathname === '/api/orders' && response.request().method() === 'GET')
  await page.getByRole('region', { name: 'Commande créée' }).getByRole('link', { name: 'Voir mes commandes' }).click()
  const listingResponse = await listing
  expect(listingResponse.status()).toBe(200)
  const history = await listingResponse.json() as CustomerOrdersResponse
  expect(history.count).toBe(1)
  expect(history.items[0]).toMatchObject({
    order_id: orderId, product_name: stored.product_name, quantity: stored.quantity, status: stored.status,
  })
  await expect(page.getByRole('heading', { name: 'Mes commandes', exact: true })).toBeVisible()
  const card = page.getByRole('article').filter({ has: page.getByText(orderId, { exact: true }) })
  await expect(card.getByRole('heading', { name: 'Bosch X100', exact: true })).toBeVisible()
  await expect(card.getByText('Creee', { exact: false })).toBeVisible()

  const detailRequest = page.waitForResponse((response) =>
    new URL(response.url()).pathname === `/api/orders/${orderId}` && response.request().method() === 'GET')
  await card.getByRole('link', { name: 'Voir la commande', exact: true }).click()
  const detailResponse = await detailRequest
  expect(detailResponse.status()).toBe(200)
  const detail = await detailResponse.json() as OrderResponse
  expect(detail.events).toHaveLength(1)
  expect(detail.events?.[0]).toMatchObject({ status: 'CREATED', source: 'SYSTEM' })
  await expect(page.getByRole('heading', { name: 'Détail de commande' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Détail de la commande' }).getByText('(CREATED)', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Progression de la commande' }).locator('[aria-current="step"]')).toContainText('CREATED')
  const refreshRequest = page.waitForResponse((response) =>
    new URL(response.url()).pathname === `/api/orders/${orderId}` && response.request().method() === 'GET')
  await page.getByRole('button', { name: 'Actualiser', exact: true }).click()
  expect((await refreshRequest).status()).toBe(200)
  await expect(page.getByRole('button', { name: 'Fermer la notification de statut' })).toHaveCount(0)
  const polledUpdate = page.waitForResponse((response) =>
    new URL(response.url()).pathname === `/api/orders/${orderId}` && response.request().method() === 'GET',
  { timeout: 25_000 })
  const transition = await request.patch(`http://127.0.0.1:8002/orders/${orderId}/status`, {
    headers: { Authorization: `Bearer ${process.env.E2E_BACKOFFICE_API_TOKEN}` },
    data: { status: 'PROCESSING' },
  })
  expect(transition.status()).toBe(200)
  expect((await polledUpdate).status()).toBe(200)
  await expect(page.getByRole('region', { name: 'Détail de la commande' }).getByText('(PROCESSING)', { exact: true })).toBeVisible()
  await expect(page.getByRole('status').filter({ hasText: 'Votre commande est en préparation.' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Progression de la commande' }).locator('[aria-current="step"]')).toContainText('PROCESSING')

  await page.getByRole('navigation').getByRole('link', { name: 'Assistant', exact: true }).click()
  await expect(page.getByRole('log').getByText('Oui je confirme', { exact: true })).toBeVisible()
  const status = await send(page, 'Quel est le statut de ma dernière commande ?', preparation.threadId)
  expect(status.result.intent).toBe('ORDER_STATUS')
  expect(status.result.order_status_result).toBe('FOUND')
  const latest = probe<MongoOrder>('order', customerId, orderId)
  expect(status.result.queried_order_id).toBe(latest.order_id)
  expect(status.result.queried_order_status).toBe(latest.status)
  expect(status.result.response).toContain(latest.status)
  await expect(page.getByRole('region', { name: 'Statut de la commande' }).getByText('(PROCESSING)', { exact: true })).toBeVisible()

  await page.getByRole('region', { name: 'Commande créée' }).getByRole('link', { name: 'Voir la commande', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Annuler la commande', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Annuler la commande', exact: true }).click()
  const cancellationRequest = page.waitForResponse((response) =>
    new URL(response.url()).pathname === `/api/customer/orders/${orderId}/cancel` &&
    response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Confirmer l’annulation', exact: true }).click()
  const cancellationResponse = await cancellationRequest
  expect(cancellationResponse.status()).toBe(200)
  const cancelled = await cancellationResponse.json() as OrderResponse
  expect(cancelled.status).toBe('CANCELLED')
  expect(cancelled.events?.map((event) => [event.status, event.source])).toEqual([
    ['CREATED', 'SYSTEM'], ['PROCESSING', 'BACKOFFICE'], ['CANCELLED', 'CUSTOMER'],
  ])
  const timestamps = cancelled.events!.map((event) => Date.parse(event.occurred_at))
  expect(timestamps.every(Number.isFinite)).toBe(true)
  expect(timestamps[1]).toBeGreaterThanOrEqual(timestamps[0]!)
  expect(timestamps[2]).toBeGreaterThanOrEqual(timestamps[1]!)
  await expect(page.getByRole('region', { name: 'Détail de la commande' }).getByText('(CANCELLED)', { exact: true })).toBeVisible()
  expect(probe<MongoOrder>('order', customerId, orderId)).toMatchObject({ status: 'CANCELLED', remaining_stock: initialStock })
  const timeline = page.getByRole('region', { name: 'Progression de la commande' })
  await expect(timeline.locator('li')).toHaveCount(3)
  expect(await timeline.locator('li small:first-of-type').allTextContents()).toEqual(['CREATED', 'PROCESSING', 'CANCELLED'])
  await expect(timeline.locator('time')).toHaveCount(3)
  for (const time of await timeline.locator('time').all()) {
    expect(await time.textContent()).toBeTruthy()
    expect(Number.isFinite(Date.parse((await time.getAttribute('datetime'))!))).toBe(true)
  }
  await page.reload()
  await page.getByLabel('JWT client', { exact: true }).fill(token)
  await page.getByRole('button', { name: 'Connecter', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Détail de la commande' }).getByText('(CANCELLED)', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Annuler la commande', exact: true })).toHaveCount(0)
  expect(probe<MongoOrder>('order', customerId, orderId)).toMatchObject({ status: 'CANCELLED', remaining_stock: initialStock })
  const persistedEvents = probe<MongoOrder>('order', customerId, orderId).events
  expect(persistedEvents.map((event) => [event.status, event.source])).toEqual([
    ['CREATED', 'SYSTEM'], ['PROCESSING', 'BACKOFFICE'], ['CANCELLED', 'CUSTOMER'],
  ])
  expect(persistedEvents.map((event) => Date.parse(event.occurred_at))).toEqual(timestamps)
  await expect(page.getByRole('region', { name: 'Progression de la commande' }).locator('li')).toHaveCount(3)
  expect(browserErrors, 'No console errors or uncaught browser exceptions expected.').toEqual([])
})
