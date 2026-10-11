import { test, expect } from '@playwright/test'
import type { StockMovementsResponse } from '../src/types/api'
import { mkdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const demoHistory: StockMovementsResponse = JSON.parse(readFileSync(resolve('test-fixtures/demo-stock-history.json'), 'utf8'))

test('DEMO historical UUID v5 response loads with empty stock form and no mutation', async ({ page }) => {
  await page.goto('/admin/login')
  await page.getByLabel('Identifiant administrateur').fill(process.env.E2E_ADMIN_USERNAME!)
  await page.getByLabel('Mot de passe administrateur').fill(process.env.E2E_ADMIN_PASSWORD!)
  await page.getByRole('button', { name: 'Se connecter', exact: true }).click()
  await expect(page).toHaveURL(/admin\/orders/)
  const mutations: string[] = []
  page.on('request', request => {
    if (request.method() !== 'GET' && request.url().includes('/api/backoffice/') && !request.url().includes('/session/')) mutations.push(new URL(request.url()).pathname)
  })
  // Replay the exact read-only Mongo DEMO API response captured during diagnosis.
  // Authentication is real in E2E; no demo mutation or business POST is performed.
  await page.route('**/api/backoffice/products/DEMO-DRILL-0001/**', route => route.fulfill({ json: demoHistory }))
  await page.route('**/api/backoffice/products/DEMO-DRILL-0001', route => route.fulfill({ json: {
    sku: 'DEMO-DRILL-0001', product_name: 'Perceuse de référence DEMO', category: 'DRILL', price: 99,
    stock: demoHistory.items[0]!.stock_after,
  } }))
  const output = resolve('../artifacts/ui-ux/stock-contract')
  mkdirSync(output, { recursive: true })
  for (const width of [1440, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 })
    await page.goto('/admin/products/DEMO-DRILL-0001/stock')
    await expect(page.getByText('Synthetic demo inventory adjustment', { exact: true })).toHaveCount(6)
    await expect(page.locator('.error-notice')).toHaveCount(0)
    await expect(page.getByLabel('Variation de stock signée')).toBeEmpty()
    await expect(page.getByLabel('Motif (obligatoire)')).toBeEmpty()
    await expect(page.getByLabel('Variation de stock signée')).toBeEnabled()
    await expect(page.getByText('Page 1 sur 1', { exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: resolve(output, `history-${width}.png`), fullPage: true })
  }
  expect(mutations).toEqual([])
})
