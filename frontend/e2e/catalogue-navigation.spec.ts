import { test, expect } from '@playwright/test'
import type { ProductsResponse, ProductResponse } from '../src/types/api'
import { connectCustomer, customerHeaders, syntheticCustomerToken } from './support/customer_auth'

test('preserves catalogue context through detail, reload and browser back', async ({ page, request }) => {
  const token = syntheticCustomerToken(`e2e-catalogue-${process.env.E2E_FIXTURE_SUFFIX}`)
  const headers = customerHeaders(token)
  expect((await request.get('/api/products')).status()).toBe(401)
  const cases = ['limit=3', 'limit=2', 'limit=2&offset=2', 'limit=1&category=DRILL', 'limit=1&category=DRILL&available=true', 'limit=1&category=DRILL&available=false']
  for (const query of cases) {
    const legacy = await request.get(`/api/products?${query}`, { headers })
    const counted = await request.get(`/api/products?${query}&include_total=true`, { headers })
    expect(legacy.status()).toBe(200)
    const result = await legacy.json() as ProductsResponse
    const exact = await counted.json() as ProductsResponse
    expect(Object.keys(result).sort()).toEqual(['count', 'has_more', 'items', 'limit', 'offset'])
    expect(result.items).toEqual(exact.items)
    expect(result.count).toBe(result.items.length)
    expect(result.items.length).toBeLessThanOrEqual(result.limit)
    expect(result.has_more).toBe(result.offset + result.count < exact.total!)
  }
  const baseline = await (await request.get('/api/products?limit=20&include_total=true', { headers })).json() as ProductsResponse
  const detailResponse = await request.get('/api/products/E2E-BOSCH-X100', { headers })
  expect(detailResponse.status()).toBe(200)
  const detail = await detailResponse.json() as ProductResponse
  const initialStock = detail.stock
  expect(initialStock).toBeGreaterThanOrEqual(1)
  expect(detail).toEqual({
    sku: 'E2E-BOSCH-X100', product_name: 'Bosch X100', category: 'DRILL', price: 99, stock: initialStock,
  })

  const browserErrors: string[] = []
  page.on('pageerror', () => { browserErrors.push('uncaught browser exception') })
  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push('console error')
  })
  await page.goto('/products')
  await connectCustomer(page, token)
  await expect(page.getByRole('article')).toHaveCount(baseline.count)
  if (baseline.has_more) await expect(page.getByRole('button', { name: 'Suivant', exact: true })).toBeEnabled()
  else await expect(page.getByRole('button', { name: 'Suivant', exact: true })).toBeDisabled()
  await page.getByLabel('Recherche', { exact: true }).fill('Bosch')
  await page.getByLabel('Catégorie', { exact: true }).selectOption('DRILL')
  await page.getByLabel('Disponibilité', { exact: true }).selectOption('true')
  await page.getByRole('button', { name: 'Rechercher', exact: true }).click()
  await expect(page.getByRole('article')).toHaveCount(1)
  await expect(page).toHaveURL((url) =>
    url.pathname === '/products' && url.searchParams.get('q') === 'Bosch' &&
    url.searchParams.get('category') === 'DRILL' && url.searchParams.get('available') === 'true')
  const catalogueUrl = page.url()
  await page.getByRole('link', { name: 'Voir le produit Bosch X100', exact: true }).click()
  await expect(page).toHaveURL((url) =>
    url.pathname === '/products/E2E-BOSCH-X100' && url.searchParams.get('q') === 'Bosch' &&
    url.searchParams.get('category') === 'DRILL' && url.searchParams.get('available') === 'true')
  await expect(page.getByRole('heading', { name: 'Bosch X100', exact: true })).toBeVisible()
  await page.reload()
  await connectCustomer(page, token)
  await expect(page.getByRole('heading', { name: 'Bosch X100', exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Retour au catalogue', exact: true }).click()
  await expect(page).toHaveURL(catalogueUrl)
  await expect(page.getByLabel('Recherche', { exact: true })).toHaveValue('Bosch')
  await expect(page.getByLabel('Catégorie', { exact: true })).toHaveValue('DRILL')
  await expect(page.getByLabel('Disponibilité', { exact: true })).toHaveValue('true')
  await expect(page.getByRole('article')).toHaveCount(1)
  await expect(page.getByRole('article').getByRole('heading')).toHaveText('Bosch X100')

  await page.getByRole('link', { name: 'Voir le produit Bosch X100', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Fiche produit', exact: true })).toBeVisible()
  await page.goBack()
  await expect(page).toHaveURL(catalogueUrl)
  await expect(page.getByRole('article')).toHaveCount(1)

  await page.goto('/products/E2E-BOSCH-X100?q=Bosch&category=DRILL&available=true&offset=20')
  await page.reload()
  await connectCustomer(page, token)
  await expect(page.getByRole('heading', { name: 'Bosch X100', exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Retour au catalogue', exact: true }).click()
  await expect(page).toHaveURL((url) =>
    url.pathname === '/products' && !url.searchParams.has('offset') &&
    url.searchParams.get('q') === 'Bosch' && url.searchParams.get('category') === 'DRILL' &&
    url.searchParams.get('available') === 'true')
  await expect(page.getByText('Page 1 sur 1', { exact: true })).toBeVisible()
  await expect(page.getByRole('article')).toHaveCount(1)
  await expect(page.getByRole('article')).toHaveCount(1)

  await page.goto('/products/E2E-BOSCH-X100')
  await connectCustomer(page, token)
  await expect(page.getByRole('heading', { name: 'Bosch X100', exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Retour au catalogue', exact: true }).click()
  await expect(page).toHaveURL((url) => url.pathname === '/products' && url.search === '')
  await expect(page.getByRole('article')).toHaveCount(baseline.count)
  expect(browserErrors).toEqual([])
})
