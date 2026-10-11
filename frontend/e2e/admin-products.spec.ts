import { expect, test } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { resolve } from 'node:path'
import type { AssistantResponse, ProductResponse } from '../src/types/api'

const root = resolve('..')
const python = process.env.E2E_PYTHON || resolve(root, '.venv/bin/python')
function token(customer: string) {
  try {
    return execFileSync(python, [resolve(root, 'scripts/create_customer_token.py'), customer, '--expires-in', '1800'],
      { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }).trim()
  } catch { throw new Error('Isolated customer token generation failed; no credentials logged.') }
}
function persisted(customer: string, sku: string): {
  identity: string; stock: number; name: string; price: number; category: string;
  unique_sku_index: boolean; customer_orders: number
} {
  try {
    return JSON.parse(execFileSync(python, [resolve(root, 'frontend/e2e/support/backend_probe.py'), 'product', customer, sku],
      { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }))
  } catch { throw new Error('Isolated read-only product Mongo verification failed.') }
}

test('isolated products: create, metadata edit, exact uniqueness, public compatibility and out-of-stock order', async ({ page, request }) => {
  if (!process.env.E2E_ADMIN_USERNAME || !process.env.E2E_ADMIN_PASSWORD
    || process.env.MONGO_DATABASE !== 'stockpilot_e2e') throw new Error('Isolated product launcher configuration missing.')
  const customer = `e2e-products-${randomUUID()}`
  const sku = `E2E-V41-DRILL-${process.env.E2E_FIXTURE_SUFFIX}`
  const originalName = `Bosch V410${process.env.E2E_FIXTURE_SUFFIX}`
  const editedName = `Bosch V411${process.env.E2E_FIXTURE_SUFFIX}`
  await page.goto('/admin/products')
  await expect(page.getByRole('heading', { name: 'Connexion administrateur' })).toBeVisible()
  await page.getByLabel('Identifiant administrateur').fill(process.env.E2E_ADMIN_USERNAME)
  await page.getByLabel('Mot de passe administrateur').fill(process.env.E2E_ADMIN_PASSWORD)
  await page.getByRole('button', { name: 'Se connecter', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Commandes administrateur', exact: true })).toBeVisible()
  await page.getByRole('navigation', { name: 'Navigation back-office' }).getByRole('link', { name: 'Produits admin' }).click()
  await expect(page.getByRole('heading', { name: 'Produits administrateur', exact: true })).toBeVisible()
  // The retained no-reset catalogue can span several pages; filter the new fixture after creation.
  await page.getByRole('link', { name: 'Créer un produit', exact: true }).click()
  await page.getByLabel('SKU (immuable après création)').fill(sku)
  await page.getByLabel('Nom', { exact: true }).fill(originalName)
  await page.getByLabel('Catégorie', { exact: true }).selectOption('DRILL')
  await page.getByLabel('Prix', { exact: true }).fill('40.25')
  await page.getByRole('button', { name: 'Créer', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('Produit créé avec un stock initial de 0.')
  await expect(page.getByRole('button', { name: 'Créer', exact: true })).toBeDisabled()
  const original = persisted(customer, sku)
  expect(original).toMatchObject({ name: originalName, price: 40.25, stock: 0, unique_sku_index: true, customer_orders: 0 })
  const publicCreated = await request.get(`/api/products/${sku}`)
  expect(publicCreated.status()).toBe(200)
  expect(await publicCreated.json()).toEqual({
    sku, product_name: originalName, category: 'DRILL', price: 40.25, stock: 0,
  })
  await page.getByRole('link', { name: `Consulter / modifier ${originalName}` }).click()
  await expect(page.getByLabel('SKU (immuable après création)')).toHaveAttribute('readonly', '')
  await expect(page.getByText(/Une confirmation déjà préparée/)).toBeVisible()
  await page.getByLabel('Nom', { exact: true }).fill(editedName)
  await page.getByLabel('Prix', { exact: true }).fill('41.5')
  await page.getByRole('button', { name: 'Enregistrer', exact: true }).click()
  await expect(page.getByRole('status')).toHaveText('Métadonnées enregistrées.')
  const edited = persisted(customer, sku)
  expect(edited).toMatchObject({ identity: original.identity, name: editedName, price: 41.5, stock: 0 })
  await page.reload()
  await expect(page.getByLabel('Nom', { exact: true })).toHaveValue(editedName)
  const publicEdited = await request.get(`/api/products/${sku}`)
  expect(await publicEdited.json()).toEqual({
    sku, product_name: editedName, category: 'DRILL', price: 41.5, stock: 0,
  })

  const guarded = await page.evaluate(async (newSku) => {
    const session = await (await fetch('/api/backoffice/session/me', { credentials: 'include' })).json()
    const headers = { 'Content-Type': 'application/json', 'X-Admin-CSRF': session.csrf_token }
    const stock = await fetch(`/api/backoffice/products/${newSku}`, {
      method: 'PATCH', credentials: 'include', headers, body: JSON.stringify({ stock: 5 }),
    })
    const changeSku = await fetch(`/api/backoffice/products/${newSku}`, {
      method: 'PATCH', credentials: 'include', headers, body: JSON.stringify({ sku: 'CHANGED' }),
    })
    const remove = await fetch(`/api/backoffice/products/${newSku}`, { method: 'DELETE', credentials: 'include', headers })
    const noCsrf = await fetch('/api/backoffice/products', {
      method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sku: 'FORBIDDEN', name: 'Forbidden', category: 'OTHER', price: 1 }),
    })
    const duplicate = await fetch('/api/backoffice/products', {
      method: 'POST', credentials: 'include', headers,
      body: JSON.stringify({ sku: newSku, name: 'Duplicate', category: 'OTHER', price: 1 }),
    })
    const race = await Promise.all([1, 2].map(() => fetch('/api/backoffice/products', {
      method: 'POST', credentials: 'include', headers,
      body: JSON.stringify({ sku: newSku + '-RACE', name: 'Race test', category: 'OTHER', price: 1 }),
    })))
    return { stock: stock.status, changeSku: changeSku.status, remove: remove.status,
      noCsrf: noCsrf.status, duplicate: duplicate.status, race: race.map(response => response.status).sort() }
  }, sku)
  expect(guarded).toEqual({ stock: 422, changeSku: 422, remove: 405, noCsrf: 403, duplicate: 409, race: [201, 409] })
  expect(persisted(customer, sku)).toEqual(edited)
  const publicPage = await request.get(`/api/products?limit=2&q=${process.env.E2E_FIXTURE_SUFFIX}`)
  const catalogue = await publicPage.json() as { items: ProductResponse[]; has_more: boolean }
  expect(publicPage.status()).toBe(200)
  expect(catalogue.items.map(item => item.sku)).toContain(sku)
  expect(catalogue.has_more).toBe(false)

  // New metadata-only products cannot be stocked here. Exercise the unchanged
  // customer ORDER path: zero stock must refuse preparation and create no order.
  const jwt = token(customer)
  const response = await request.post('/api/assistant', {
    headers: { Authorization: `Bearer ${jwt}` },
    data: { thread_id: randomUUID(), message: `Commande-moi 1 ${editedName}` }, timeout: 120_000,
  })
  expect(response.status()).toBe(200)
  const order = await response.json() as AssistantResponse
  expect(order.order_status).toBe('OUT_OF_STOCK')
  expect(order.created_order_id).toBeFalsy()
  expect(persisted(customer, sku)).toMatchObject({ identity: original.identity, stock: 0, customer_orders: 0 })
  const orders = await request.get('/api/orders', { headers: { Authorization: `Bearer ${jwt}` } })
  expect(orders.status()).toBe(200)
  expect((await orders.json()).items).toEqual([])
  await page.getByRole('link', { name: 'Retour aux produits' }).click()
  await expect(page.getByRole('heading', { name: 'Produits administrateur', exact: true })).toBeVisible()
  await page.getByLabel('Recherche', { exact: true }).fill(sku)
  await page.getByRole('button', { name: 'Rechercher', exact: true }).click()
  await expect(page.getByRole('link', { name: `Modifier ${editedName}`, exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Se déconnecter', exact: true }).click()
  await page.goto('/admin/products')
  await expect(page.getByRole('heading', { name: 'Connexion administrateur' })).toBeVisible()
})
