import { test, expect } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

test('KPI numbers remain complete on one line at every viewport', async ({ page }) => {
  const stage = process.env.KPI_STAGE || 'after'
  const output = resolve('../artifacts/ui-ux/kpi', stage)
  mkdirSync(output, { recursive: true })
  const payload = {
    order_stats: { total_orders: 2018, by_status: { CREATED: 214, PROCESSING: 200, SHIPPED: 200, DELIVERED: 1200, CANCELLED: 204 } },
    product_stats: { total_products: 12500, available_products: 12500, unavailable_products: 0, total_stock_units: 1000000 },
    recent_orders: [], recent_stock_movements: [],
  }
  const referencePayload = structuredClone(payload)
  const unexpected: string[] = []
  page.on('pageerror', () => unexpected.push('pageerror'))
  // Entire API is fulfilled locally: no requests or writes reach any database.
  await page.route('http://127.0.0.1:5176/api/**', route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/backoffice/session/me') return route.fulfill({ json: { username: 'visual-kpi', csrf_token: 'fixture-only' } })
    if (path === '/api/backoffice/dashboard') return route.fulfill({ json: payload })
    unexpected.push(path)
    return route.fulfill({ status: 404, json: {} })
  })
  const measurements: object[] = []
  for (const width of [1440, 768, 390]) {
    Object.assign(payload, structuredClone(referencePayload))
    await page.setViewportSize({ width, height: 1000 })
    await page.goto('/admin/dashboard')
    await expect(page.locator('.dashboard-metric dd')).toHaveCount(10)
    await page.screenshot({ path: resolve(output, `dashboard-${width}.png`), fullPage: true })
    const inspect = async () => page.locator('.dashboard-metric dd').evaluateAll(elements => elements.map(element => {
      const range = document.createRange()
      range.selectNodeContents(element)
      const rect = element.getBoundingClientRect()
      const card = element.parentElement!.getBoundingClientRect()
      const style = getComputedStyle(element)
      return { text: element.textContent, lines: new Set(Array.from(range.getClientRects(), r => Math.round(r.top))).size,
        insideCard: rect.left >= card.left && rect.right <= card.right, fits: element.scrollWidth <= element.clientWidth + 1,
        maxWidth: style.maxWidth, whiteSpace: style.whiteSpace, overflowWrap: style.overflowWrap,
        wordBreak: style.wordBreak, grid: getComputedStyle(element.parentElement!).gridTemplateColumns }
    }))
    const metrics = await inspect()
    measurements.push({ width, metrics })
    writeFileSync(resolve(output, 'measurements.json'), JSON.stringify(measurements, null, 2))
    if (stage === 'after') {
      expect(metrics.filter(metric => metric.lines !== 1 || !metric.insideCard || !metric.fits)).toEqual([])
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
      for (const value of [12500, 100000, 1000000]) {
        // Populate every KPI with a long value to stress both product and order grids.
        payload.order_stats.total_orders = value * 5
        for (const status of Object.keys(payload.order_stats.by_status) as Array<keyof typeof payload.order_stats.by_status>) payload.order_stats.by_status[status] = value
        payload.product_stats = { total_products: value, available_products: value, unavailable_products: 0, total_stock_units: value }
        await page.getByRole('button', { name: 'Actualiser', exact: true }).click()
        await expect(page.locator('.dashboard-metric dd').nth(1)).toHaveText(value.toLocaleString('fr-FR'))
        expect((await inspect()).filter(metric => metric.lines !== 1 || !metric.insideCard || !metric.fits)).toEqual([])
        await page.screenshot({ path: resolve(output, `long-${value}-${width}.png`), fullPage: true })
      }
    }
  }
  writeFileSync(resolve(output, 'measurements.json'), JSON.stringify(measurements, null, 2))
  expect(unexpected).toEqual([])
})
