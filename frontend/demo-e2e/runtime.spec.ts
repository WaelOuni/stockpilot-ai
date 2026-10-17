import { expect, test } from '@playwright/test'
import { connectCustomer, customerHeaders, syntheticCustomerToken } from '../e2e/support/customer_auth'

test('real demo catalogue pagination, combined filters and preserved detail context', async ({ page, request }) => {
  if (process.env.MONGO_DATABASE !== 'stockpilot_demo') throw new Error('Dedicated demo required.')
  const jwt = syntheticCustomerToken('ephemeral-demo-catalogue-reader')
  expect((await request.get('/api/products')).status()).toBe(401)
  await page.goto('/products')
  await connectCustomer(page, jwt)
  await expect(page.getByRole('heading', { name: 'Catalogue', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Suivant', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: 'Suivant', exact: true }).click()
  await expect(page).toHaveURL(/offset=20/)
  await page.getByLabel('Recherche', { exact: true }).fill('DEMO')
  await page.getByLabel('Catégorie', { exact: true }).selectOption('DRILL')
  await page.getByLabel('Disponibilité', { exact: true }).selectOption('true')
  const loaded = page.waitForResponse(response => {
    const url = new URL(response.url())
    return url.pathname === '/api/products' && url.searchParams.get('q') === 'DEMO'
      && url.searchParams.get('category') === 'DRILL' && url.searchParams.get('available') === 'true'
      && response.request().method() === 'GET'
  })
  await page.getByRole('button', { name: 'Rechercher', exact: true }).click()
  const products = await (await loaded).json()
  expect(products.items.length).toBe(20)
  expect(products.items.every((product: { category: string; stock: number }) =>
    product.category === 'DRILL' && product.stock > 0)).toBe(true)
  const sku = products.items[0].sku as string
  const link = page.locator(`a[href^="/products/${sku}"]`).first()
  await link.click()
  await expect(page).toHaveURL(new RegExp(`/products/${sku}\\?`))
  const detail = await (await request.get(`/api/products/${sku}`, { headers: customerHeaders(jwt) })).json()
  await expect(page.getByRole('heading', { name: detail.product_name, exact: true })).toBeVisible()
  const back = page.getByRole('link', { name: /catalogue/i }).filter({ hasText: /Retour/i })
  await back.click()
  await expect(page.getByLabel('Recherche', { exact: true })).toHaveValue('DEMO')
  await expect(page.getByLabel('Catégorie', { exact: true })).toHaveValue('DRILL')
  await expect(page.getByLabel('Disponibilité', { exact: true })).toHaveValue('true')
  await page.getByLabel('Disponibilité', { exact: true }).selectOption('false')
  const unavailableResponse = page.waitForResponse(response => {
    const url = new URL(response.url())
    return url.pathname === '/api/products' && url.searchParams.get('available') === 'false'
      && response.request().method() === 'GET'
  })
  await page.getByRole('button', { name: 'Rechercher', exact: true }).click()
  const unavailable = await (await unavailableResponse).json()
  expect(unavailable.items.every((product: { stock: number }) => product.stock <= 0)).toBe(true)
  if (unavailable.count === 0) {
    await expect(page.getByText('Aucun produit ne correspond à vos critères.', { exact: true })).toBeVisible()
  } else {
    await expect(page.getByRole('article')).toHaveCount(unavailable.count)
  }
})

test('real demo admin cookie dashboard global totals, lists, histories and logout', async ({ page, request }) => {
  const password = process.env.DEMO_ADMIN_PASSWORD
  const username = process.env.ADMIN_USERNAME
  if (!password || !username || !process.env.DEMO_EXPECTED_DASHBOARD) throw new Error('Ephemeral admin required.')
  expect((await request.get('/api/backoffice/dashboard')).status()).toBe(401)
  await page.goto('/admin/dashboard')
  await page.getByLabel('Identifiant administrateur').fill(username)
  await page.getByLabel('Mot de passe administrateur').fill(password)
  await page.getByRole('button', { name: 'Se connecter', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Commandes administrateur', exact: true })).toBeVisible()
  await page.goto('/admin/dashboard')
  await expect(page.getByRole('heading', { name: 'Tableau de bord', exact: true })).toBeVisible()
  const dashboard = await page.evaluate(async () => {
    const response = await fetch('/api/backoffice/dashboard', { credentials: 'include' })
    if (response.headers.get('cache-control') !== 'no-store') throw new Error('Missing cache protection')
    return response.json()
  })
  expect(dashboard).toEqual(JSON.parse(process.env.DEMO_EXPECTED_DASHBOARD))
  expect(dashboard.product_stats.total_products).toBeGreaterThan(500)
  expect(dashboard.order_stats.total_orders).toBeGreaterThan(2000)
  await page.goto('/admin/orders')
  await expect(page.getByRole('heading', { name: 'Commandes administrateur', exact: true })).toBeVisible()
  await page.goto('/admin/products')
  await expect(page.getByRole('heading', { name: 'Produits administrateur', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Suivant', exact: true })).toBeEnabled()
  const movementSku = dashboard.recent_stock_movements[0].product_sku
  await page.goto(`/admin/products/${movementSku}/stock`)
  await expect(page.getByRole('heading', { name: /stock/i })).toBeVisible()
  await page.getByRole('button', { name: 'Se déconnecter', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Connexion administrateur' })).toBeVisible()
  expect(await page.evaluate(async () => (await fetch('/api/backoffice/dashboard')).status)).toBe(401)
})
