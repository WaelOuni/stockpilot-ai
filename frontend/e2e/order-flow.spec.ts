import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect, type Page } from '@playwright/test'
import type { AssistantResponse, CustomerOrdersResponse } from '../src/types/api'

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

test('creates its own order, lists it and reads the Mongo status in the same thread', async ({ page }) => {
  const customerId = `e2e-customer-${randomUUID()}`
  const token = generateToken(customerId)
  expect(probe<{ stock: number }>('stock', customerId).stock).toBe(10)
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

  const preparation = await send(page, 'Commande-moi une Bosch X100')
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
    remaining_stock: 9,
  })

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
  await expect(page.getByRole('heading', { name: 'My Orders', exact: true })).toBeVisible()
  const card = page.getByRole('article').filter({ has: page.getByText(orderId, { exact: true }) })
  await expect(card.getByRole('heading', { name: 'Bosch X100', exact: true })).toBeVisible()
  await expect(card.getByText('Creee', { exact: false })).toBeVisible()

  const detailRequest = page.waitForResponse((response) =>
    new URL(response.url()).pathname === `/api/orders/${orderId}` && response.request().method() === 'GET')
  await card.getByRole('link', { name: 'Voir la commande', exact: true }).click()
  const detailResponse = await detailRequest
  expect(detailResponse.status()).toBe(200)
  await expect(page.getByRole('heading', { name: 'Détail de commande' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Détail de la commande' }).getByText('(CREATED)', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Progression de la commande' }).locator('[aria-current="step"]')).toContainText('CREATED')
  const refreshRequest = page.waitForResponse((response) =>
    new URL(response.url()).pathname === `/api/orders/${orderId}` && response.request().method() === 'GET')
  await page.getByRole('button', { name: 'Actualiser', exact: true }).click()
  expect((await refreshRequest).status()).toBe(200)

  await page.getByRole('navigation').getByRole('link', { name: 'Assistant', exact: true }).click()
  await expect(page.getByRole('log').getByText('Oui je confirme', { exact: true })).toBeVisible()
  const status = await send(page, 'Quel est le statut de ma dernière commande ?', preparation.threadId)
  expect(status.result.intent).toBe('ORDER_STATUS')
  expect(status.result.order_status_result).toBe('FOUND')
  const latest = probe<MongoOrder>('order', customerId, orderId)
  expect(status.result.queried_order_id).toBe(latest.order_id)
  expect(status.result.queried_order_status).toBe(latest.status)
  expect(status.result.response).toContain(latest.status)
  await expect(page.getByRole('region', { name: 'Statut de la commande' }).getByText('(CREATED)', { exact: true })).toBeVisible()
  expect(browserErrors, 'No console errors or uncaught browser exceptions expected.').toEqual([])
})
