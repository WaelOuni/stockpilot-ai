import { expect, test } from '@playwright/test'
import sample from './fixtures/intelligence.json' with { type: 'json' }
import { sessionFixture } from '../src/b2b/fixtures'

for (const language of ['fr', 'en'] as const) for (const theme of ['light', 'dark'] as const) for (const width of [390, 1280]) {
  test(`Intelligence ${language}/${theme}/${width}: scope, figures and keyboard`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(p => localStorage.setItem('stockpilot.preferences.v1', JSON.stringify(p)), { language, theme })
    const methods: string[] = [], queries: string[] = []
    await page.route(/\/b2b\/(?:auth|api)\//, async route => {
      const url = new URL(route.request().url()); methods.push(route.request().method())
      const body = url.pathname.endsWith('/session') ? { ...sessionFixture, permissions: [...sessionFixture.permissions, 'analytics:read'] }
        : url.pathname.endsWith('/categories') ? { items: ['drill', 'saw'] }
          : url.pathname.endsWith('/intelligence') ? structuredClone(sample) : null
      if (url.pathname.endsWith('/intelligence')) { queries.push(url.search); expect(route.request().headers()['x-bff-csrf']).toBe('test-csrf') }
      await route.fulfill({ status: body ? 200 : 500, contentType: 'application/json', body: JSON.stringify(body) })
    })
    await page.goto('/b2b-app/intelligence')
    if (process.env.STOCKPILOT_INTELLIGENCE_CAPTURE === 'before') await page.screenshot({ path: testInfo.outputPath('intelligence-before.png'), fullPage: true })
    await expect(page.getByRole('heading', { name: 'StockPilot Intelligence', exact: true })).toBeVisible()
    await expect(page.getByText(language === 'fr' ? /Historique synthétique de test/ : /Synthetic test history/)).toBeVisible()
    await expect(page.getByRole('region', { name: language === 'fr' ? 'Tableau analytique' : 'Analytics table' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`intelligence-${language}-${theme}-${width}.png`), fullPage: true })
    const trigger = page.getByRole('button', { name: (language === 'fr' ? 'Analyser ' : 'Analyze ') + 'DRILL-001', exact: true })
    await trigger.focus(); await page.keyboard.press('Enter')
    const dialog = page.getByRole('dialog', { name: language === 'fr' ? 'Analyse du produit' : 'Product analysis' })
    await expect(dialog).toBeVisible()
    await expect(dialog.getByText(language === 'fr' ? /14 unités estimées sur 7 jours/ : /14 estimated units over 7 days/)).toBeVisible()
    await expect(dialog.getByRole('region', { name: language === 'fr' ? 'Simulations de remise' : 'Discount simulations' })).toContainText('10 %')
    await page.keyboard.press('Shift+Tab')
    expect(await dialog.evaluate(el => el.contains(document.activeElement))).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`intelligence-detail-${language}-${theme}-${width}.png`) })
    await page.keyboard.press('Escape'); await expect(trigger).toBeFocused()
    const download = page.waitForEvent('download')
    await page.getByRole('button', { name: language === 'fr' ? 'Exporter cette page (JSON)' : 'Export this page (JSON)' }).click()
    expect((await download).suggestedFilename()).toBe('stockpilot-intelligence-page.json')
    expect(queries.every(query => !query.includes('organization'))).toBe(true)
    expect(methods.every(method => method === 'GET')).toBe(true)
  })
}

test('unauthorized role cannot request analytics, including a direct URL', async ({ page }) => {
  let reads = 0
  await page.route(/\/b2b\/(?:auth|api)\//, async route => {
    if (route.request().url().includes('/intelligence')) reads++
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...sessionFixture, role: 'USER', permissions: [...sessionFixture.permissions, 'analytics:read'] }) })
  })
  await page.goto('/b2b-app/intelligence')
  await expect(page.getByRole('alert')).toContainText('réservé')
  await expect(page.getByRole('link', { name: 'Intelligence', exact: true })).toHaveCount(0)
  expect(reads).toBe(0)
})

test('empty history, error and retry never display invented charts', async ({ page }) => {
  let fail = true
  const empty = { ...sample, items: [], total: 0, has_more: false, summary: { ...sample.summary, products: 0 }, page_summary: { products: 0, dormant_products: 0, forecastable_products: 0, mean_coverage_days: null }, page_series: [] }
  await page.route(/\/b2b\/(?:auth|api)\//, async route => {
    const path = new URL(route.request().url()).pathname
    const analytics = path.endsWith('/intelligence')
    await route.fulfill({ status: analytics && fail ? 503 : 200, contentType: 'application/json', body: JSON.stringify(path.endsWith('/session') ? { ...sessionFixture, permissions: [...sessionFixture.permissions, 'analytics:read'] } : analytics ? empty : { items: [] }) })
  })
  await page.goto('/b2b-app/intelligence')
  await expect(page.getByRole('alert')).toContainText('indisponibles')
  expect(await page.locator('svg[role=img]').count()).toBe(0)
  fail = false
  await page.getByRole('button', { name: 'Réessayer', exact: true }).click()
  await expect(page.getByText('Aucun produit ne correspond à ces filtres.')).toBeVisible()
  expect(await page.locator('svg[role=img]').count()).toBe(0)
})
