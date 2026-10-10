import { test, expect } from '@playwright/test'
import type { ProductsResponse, ProductResponse } from '../src/types/api'

test('preserves catalogue context through detail, reload and browser back', async ({ page, request }) => {
  const cases = [
    { query: 'limit=3', limit: 3, offset: 0, skus: ['E2E-BOSCH-S500', 'E2E-BOSCH-X100', 'E2E-MAKITA-D200'], hasMore: false },
    { query: 'limit=2', limit: 2, offset: 0, skus: ['E2E-BOSCH-S500', 'E2E-BOSCH-X100'], hasMore: true },
    { query: 'limit=2&offset=2', limit: 2, offset: 2, skus: ['E2E-MAKITA-D200'], hasMore: false },
    { query: 'limit=1&category=DRILL', limit: 1, offset: 0, skus: ['E2E-BOSCH-X100'], hasMore: true },
    { query: 'limit=1&category=DRILL&available=true', limit: 1, offset: 0, skus: ['E2E-BOSCH-X100'], hasMore: false },
    { query: 'limit=1&category=DRILL&available=false', limit: 1, offset: 0, skus: ['E2E-MAKITA-D200'], hasMore: false },
  ]
  for (const { query, limit, offset, skus, hasMore } of cases) {
    const response = await request.get(`/api/products?${query}`)
    expect(response.status(), query).toBe(200)
    const result = await response.json() as ProductsResponse
    expect(Object.keys(result).sort()).toEqual(['count', 'has_more', 'items', 'limit', 'offset'])
    expect(result).toMatchObject({ limit, offset, count: skus.length, has_more: hasMore })
    expect(result.items.map((item) => item.sku)).toEqual(skus)
    expect(result.items.length).toBeLessThanOrEqual(limit)
    expect(result.count).toBe(result.items.length)
  }
  const detailResponse = await request.get('/api/products/E2E-BOSCH-X100')
  expect(detailResponse.status()).toBe(200)
  const detail = await detailResponse.json() as ProductResponse
  expect(detail).toEqual({
    sku: 'E2E-BOSCH-X100', product_name: 'Bosch X100', category: 'DRILL', price: 99, stock: 10,
  })

  const browserErrors: string[] = []
  page.on('pageerror', () => { browserErrors.push('uncaught browser exception') })
  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push('console error')
  })
  await page.goto('/products')
  await expect(page.getByRole('article')).toHaveCount(3)
  await expect(page.getByRole('button', { name: 'Suivant', exact: true })).toBeDisabled()
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
  await expect(page.getByRole('heading', { name: 'Bosch X100', exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Retour au catalogue', exact: true }).click()
  await expect(page).toHaveURL((url) =>
    url.pathname === '/products' && url.searchParams.get('offset') === '20' &&
    url.searchParams.get('q') === 'Bosch' && url.searchParams.get('category') === 'DRILL' &&
    url.searchParams.get('available') === 'true')
  await expect(page.getByText('Page 2', { exact: true })).toBeVisible()
  await expect(page.getByText('Aucun produit ne correspond à vos critères.', { exact: true })).toBeVisible()
  await expect(page.getByRole('article')).toHaveCount(0)

  await page.goto('/products/E2E-BOSCH-X100')
  await expect(page.getByRole('heading', { name: 'Bosch X100', exact: true })).toBeVisible()
  await page.getByRole('link', { name: 'Retour au catalogue', exact: true }).click()
  await expect(page).toHaveURL((url) => url.pathname === '/products' && url.search === '')
  await expect(page.getByRole('article')).toHaveCount(3)
  expect(browserErrors).toEqual([])
})
