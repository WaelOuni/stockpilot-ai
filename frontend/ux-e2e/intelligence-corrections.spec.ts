import { expect, test } from '@playwright/test'
import sample from './fixtures/intelligence.json' with { type: 'json' }
import { sessionFixture } from '../src/b2b/fixtures'

const movements = ['sold', 'returned', 'received', 'adjusted', 'reserved', 'cancelled', 'transferred'] as const
function mixedHistory() {
  const data = structuredClone(sample), item = data.items[0]!
  Object.assign(item, { dataset: 'mixed', period_complete: false, period_sales_units: null, period_returns_units: null })
  Object.assign(item.forecast, { status: 'mixed_dataset', method: null, quantity: null, daily: [], backtest: null, uncertainty: null })
  Object.assign(item.replenishment, { status: 'forecast_unavailable', suggested_quantity: null, coverage_days: null, reorder_point: null })
  data.page_summary.forecastable_products--
  data.page_series.forEach((row, index) => {
    if (index === 0) {
      row.datasets[0]!.complete_products--
      Object.assign(row.datasets[0]!, Object.fromEntries(movements.map(field => [field, null])))
    }
    row.datasets.push({ dataset: 'observed', expected_products: 1, complete_products: index === 0 ? 1 : 0,
      ...Object.fromEntries(movements.map(field => [field, index === 0 ? field === 'sold' ? 7 : field === 'reserved' ? 50 : 0 : null])) } as typeof row.datasets[number])
  })
  return data
}

for (const language of ['fr', 'en'] as const) for (const width of [390, 1280]) {
  test(`separated provenance ${language}/${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(language => localStorage.setItem('stockpilot.preferences.v1', JSON.stringify({ language, theme: 'light' })), language)
    const fixture = mixedHistory()
    await page.route(/\/b2b\/(?:auth|api)\//, route => {
      const path = new URL(route.request().url()).pathname
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(path.endsWith('/session') ? { ...sessionFixture, permissions: [...sessionFixture.permissions, 'analytics:read'] } : path.endsWith('/intelligence') ? fixture : { items: [] }) })
    })
    await page.goto('/b2b-app/intelligence')
    await expect(page.getByRole('status')).toContainText(language === 'fr' ? 'Plusieurs provenances' : 'Multiple provenance types')
    const observed = page.locator('figure').filter({ has: page.locator('figcaption', { hasText: language === 'fr' ? 'Ventes réalisées · Données observées' : 'Completed sales · Observed data' }) })
    await expect(observed).toBeVisible()
    await expect(observed.locator('circle')).toHaveCount(1)
    await expect(page.locator('figure').filter({ hasText: language === 'fr' ? 'Ventes réalisées · DEMO' : 'Completed sales · DEMO' })).toBeVisible()
    await page.getByText(language === 'fr' ? 'Consulter les valeurs et la complétude' : 'View values and completeness', { exact: true }).click()
    const table = page.getByRole('region', { name: language === 'fr' ? 'Historique des mouvements' : 'Movement history' })
    const firstObserved = table.getByRole('row').filter({ hasText: sample.page_series[0]!.date }).filter({ hasText: language === 'fr' ? 'Données observées' : 'Observed data' })
    await expect(firstObserved.getByRole('cell').nth(2)).toHaveText('7')
    await expect(firstObserved.getByRole('cell').nth(5)).toHaveText('50')
    await expect(table.getByRole('row').filter({ hasText: sample.page_series[1]!.date }).filter({ hasText: language === 'fr' ? 'Données observées' : 'Observed data' }).getByRole('cell').nth(2)).toHaveText(language === 'fr' ? 'Non disponible' : 'Unavailable')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`provenance-${language}-${width}.png`), fullPage: true })
  })

  test(`large exact facts and local visualization limit ${language}/${width}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(language => localStorage.setItem('stockpilot.preferences.v1', JSON.stringify({ language, theme: 'dark' })), language)
    const fixture = structuredClone(sample), item = fixture.items[0]!
    Object.assign(item, { quantity: '9223372036854775807', available: '9223372036854775807', reserved: '0', unit_cost_minor: '9223372036854775807', stock_cost_minor: (9223372036854775807n ** 2n).toString() })
    item.replenishment.coverage_days = fixture.page_summary.mean_coverage_days = 4.611686018427388e18
    await page.route(/\/b2b\/(?:auth|api)\//, route => {
      const path = new URL(route.request().url()).pathname
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(path.endsWith('/session') ? { ...sessionFixture, permissions: [...sessionFixture.permissions, 'analytics:read'] } : path.endsWith('/intelligence') ? fixture : { items: [] }) })
    })
    await page.goto('/b2b-app/intelligence')
    const row = page.getByRole('region', { name: language === 'fr' ? 'Tableau analytique' : 'Analytics table' }).getByRole('row').filter({ hasText: item.sku })
    await expect(row).toContainText(language === 'fr' ? 'Hors plage de visualisation' : 'Outside visualization range')
    await expect(row.locator('meter')).toHaveCount(0)
    const available = await row.getByRole('cell').first().textContent()
    expect(available!.replace(/[^0-9]/g, '')).toBe('9223372036854775807')
    await expect(page.getByRole('alert')).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.getByRole('button', { name: (language === 'fr' ? 'Analyser ' : 'Analyze ') + item.sku, exact: true }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog).toContainText(language === 'fr' ? 'Les dates de disponibilité et de correction ne sont pas enregistrées' : 'Availability and revision dates are not recorded')
    await page.screenshot({ path: testInfo.outputPath(`large-values-${language}-${width}.png`) })
    await page.keyboard.press('Escape')
  })
}

test('pagination retains filters and the corrected contract', async ({ page }) => {
  const offsets: number[] = []
  await page.route(/\/b2b\/(?:auth|api)\//, route => {
    const url = new URL(route.request().url())
    const data = structuredClone(sample)
    if (url.pathname.endsWith('/intelligence')) {
      const offset = Number(url.searchParams.get('offset'))
      offsets.push(offset)
      expect(url.searchParams.get('horizon_days')).toBe('7')
      expect(url.searchParams.has('organization_id')).toBe(false)
      if (offset === 20) {
        data.offset = offset; data.items = [data.items.at(-1)!]; data.has_more = false
        data.page_summary.products = 1; data.page_series = []
      }
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(url.pathname.endsWith('/session') ? { ...sessionFixture, permissions: [...sessionFixture.permissions, 'analytics:read'] } : url.pathname.endsWith('/intelligence') ? data : { items: [] }) })
  })
  await page.goto('/b2b-app/intelligence')
  await page.getByRole('button', { name: 'Suivant', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Suivant', exact: true })).toBeDisabled()
  await page.getByRole('button', { name: 'Précédent', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Précédent', exact: true })).toBeDisabled()
  expect(offsets).toEqual([0, 20, 0])
})
