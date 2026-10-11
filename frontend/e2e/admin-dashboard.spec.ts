import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { resolve } from 'node:path'
import type { APIRequestContext, Page } from '@playwright/test'
import type { AssistantResponse, BackofficeDashboard } from '../src/types/api'

const root = resolve('..')
const python = process.env.E2E_PYTHON || resolve(root, '.venv/bin/python')
const sku = 'E2E-BOSCH-X100'

function probeDashboard(): BackofficeDashboard {
  try {
    return JSON.parse(execFileSync(python, [
      resolve(root, 'frontend/e2e/support/backend_probe.py'), 'dashboard', 'dashboard-reference', '',
    ], { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }))
  } catch {
    throw new Error('Isolated read-only Mongo dashboard verification failed.')
  }
}

function customerToken(customer: string): string {
  try {
    return execFileSync(python, [
      resolve(root, 'scripts/create_customer_token.py'), customer, '--expires-in', '1800',
    ], { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
  } catch {
    throw new Error('Isolated customer token generation failed; no credentials logged.')
  }
}

async function createOrder(request: APIRequestContext, customer: string): Promise<string> {
  const headers = { Authorization: `Bearer ${customerToken(customer)}` }
  const threadId = randomUUID()
  const prepared = await request.post('/api/assistant', {
    headers, data: { thread_id: threadId, message: 'Commande-moi 1 Bosch X100' }, timeout: 120_000,
  })
  expect(prepared.status()).toBe(200)
  expect((await prepared.json() as AssistantResponse).order_status).toBe('PENDING_CONFIRMATION')
  const confirmed = await request.post('/api/assistant', {
    headers, data: { thread_id: threadId, message: 'Oui je confirme' }, timeout: 120_000,
  })
  expect(confirmed.status()).toBe(200)
  const result = await confirmed.json() as AssistantResponse
  expect(result.order_status).toBe('CONFIRMED')
  expect(result.created_order_id).toMatch(/^[0-9a-f-]{36}$/)
  return result.created_order_id!
}

function comparable(dashboard: BackofficeDashboard) {
  return {
    order_stats: dashboard.order_stats,
    product_stats: dashboard.product_stats,
    recent_orders: dashboard.recent_orders.map(order => ({
      ...order, created_at: new Date(order.created_at).toISOString(),
    })),
    recent_stock_movements: dashboard.recent_stock_movements.map(movement => ({
      ...movement, occurred_at: new Date(movement.occurred_at).toISOString(),
    })),
  }
}

async function readDashboardFromBrowser(page: Page): Promise<BackofficeDashboard> {
  return page.evaluate(async () => {
    const response = await fetch('/api/backoffice/dashboard', {
      credentials: 'include', cache: 'no-store',
    })
    if (!response.ok) throw new Error(`Dashboard read failed (${response.status}).`)
    return response.json() as Promise<BackofficeDashboard>
  })
}

async function refreshAndCompare(page: Page): Promise<BackofficeDashboard> {
  const response = page.waitForResponse(result =>
    result.url().endsWith('/api/backoffice/dashboard') && result.request().method() === 'GET')
  await page.getByRole('button', { name: 'Actualiser', exact: true }).click()
  expect((await response).status()).toBe(200)
  const browserDashboard = await readDashboardFromBrowser(page)
  expect(comparable(browserDashboard)).toEqual(comparable(probeDashboard()))
  return browserDashboard
}

test('isolated admin dashboard: Mongo stats, evolved order statuses and refreshed stock adjustment', async ({ page, request }) => {
  if (!process.env.E2E_ADMIN_USERNAME || !process.env.E2E_ADMIN_PASSWORD
    || !process.env.E2E_BACKOFFICE_API_TOKEN || process.env.MONGO_DATABASE !== 'stockpilot_e2e') {
    throw new Error('Isolated dashboard launcher configuration is missing.')
  }

  await page.goto('/admin/dashboard')
  await expect(page.getByRole('heading', { name: 'Connexion administrateur' })).toBeVisible()
  await page.getByLabel('Identifiant administrateur').fill(process.env.E2E_ADMIN_USERNAME)
  await page.getByLabel('Mot de passe administrateur').fill(process.env.E2E_ADMIN_PASSWORD)
  await page.getByRole('button', { name: 'Se connecter', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Commandes administrateur', exact: true })).toBeVisible()
  await page.goto('/admin/dashboard')
  await expect(page.getByRole('heading', { name: 'Tableau de bord', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Commandes', exact: true })).toBeVisible()

  const protectedRead = await request.get('/api/backoffice/dashboard', {
    headers: { Authorization: `Bearer ${process.env.E2E_BACKOFFICE_API_TOKEN}` },
  })
  expect(protectedRead.status()).toBe(401)
  const publicRead = await request.get('/api/backoffice/dashboard')
  expect(publicRead.status()).toBe(401)
  const initial = await readDashboardFromBrowser(page)
  expect(comparable(initial)).toEqual(comparable(probeDashboard()))
  expect(Object.keys(initial)).toEqual([
    'order_stats', 'product_stats', 'recent_orders', 'recent_stock_movements',
  ])
  expect(Object.keys(initial.recent_stock_movements[0] ?? {})).toEqual([
    'product_sku', 'delta', 'occurred_at', 'reason',
  ])
  expect(initial.product_stats.total_products).toBe(probeDashboard().product_stats.total_products)
  expect(initial.product_stats.available_products + initial.product_stats.unavailable_products)
    .toBe(initial.product_stats.total_products)
  expect(initial.recent_stock_movements.length).toBeGreaterThan(0)

  const customer = `e2e-dashboard-${randomUUID()}`
  const orderId = await createOrder(request, customer)
  await refreshAndCompare(page)
  await expect(page.getByRole('link', { name: orderId, exact: true })).toBeVisible()
  await page.goto(`/admin/orders/${orderId}`)
  await expect(page.getByRole('button', { name: 'Passer à PROCESSING', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Passer à PROCESSING', exact: true }).click()
  await expect(page.getByText('Statut mis à jour.', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Passer à SHIPPED', exact: true }).click()
  await expect(page.getByText('Statut mis à jour.', { exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Tableau de bord', exact: true }).click()
  const evolved = await refreshAndCompare(page)
  expect(evolved.order_stats.by_status.SHIPPED).toBeGreaterThan(initial.order_stats.by_status.SHIPPED)
  expect(evolved.order_stats.by_status.CREATED).toBe(initial.order_stats.by_status.CREATED)
  expect(evolved.order_stats.by_status.DELIVERED).toBeGreaterThan(0)
  expect(evolved.order_stats.by_status.CANCELLED).toBeGreaterThan(0)
  await expect(page.getByRole('link', { name: orderId, exact: true })).toBeVisible()

  const adjustment = await page.evaluate(async (productSku: string) => {
    const sessionResponse = await fetch('/api/backoffice/session/me', { credentials: 'include' })
    const session = await sessionResponse.json() as { csrf_token: string }
    const response = await fetch(`/api/backoffice/products/${encodeURIComponent(productSku)}/stock-adjustments`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'X-Admin-CSRF': session.csrf_token },
      body: JSON.stringify({
        operation_id: crypto.randomUUID(), delta: 1, reason: 'Dashboard refresh E2E adjustment',
      }),
    })
    return { status: response.status, movement: await response.json() as {
      product_sku: string; delta: number; reason: string; source: string
    } }
  }, sku)
  expect(adjustment.status).toBe(200)
  expect(adjustment.movement).toMatchObject({
    product_sku: sku, delta: 1, reason: 'Dashboard refresh E2E adjustment', source: 'BACKOFFICE',
  })
  const refreshed = await refreshAndCompare(page)
  expect(refreshed.recent_stock_movements[0]).toMatchObject({
    product_sku: sku, delta: 1, reason: 'Dashboard refresh E2E adjustment',
  })
  await expect(page.getByText('Dashboard refresh E2E adjustment', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('link', { name: 'Voir l’historique complet du stock' }).first())
    .toHaveAttribute('href', `/admin/products/${sku}/stock`)

  await page.getByRole('button', { name: 'Se déconnecter', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Connexion administrateur' })).toBeVisible()
  const expired = await page.evaluate(async () =>
    (await fetch('/api/backoffice/dashboard', { credentials: 'include' })).status)
  expect(expired).toBe(401)
})
