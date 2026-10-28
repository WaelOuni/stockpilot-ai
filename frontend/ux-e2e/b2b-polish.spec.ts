import { expect, test } from '@playwright/test'
import { productFixture, stockFixture, sessionFixture, previewFixture } from '../src/b2b/fixtures'

for (const language of ['fr', 'en'] as const) {
  for (const theme of ['light', 'dark'] as const) {
    for (const count of [2, 4]) {
      test(`${language}/${theme}/${count} products: readable mobile comparison and business import history`, async ({ page }, testInfo) => {
        const products = Array.from({ length: 4 }, (_, i) => ({ ...productFixture, product_id: `product-${i}`, sku: `DRILL-${i}`, name: `Perceuse Northline ${i}`, price_minor: '4900', description: 'Perceuse compacte pour les travaux quotidiens en atelier.' }))
        const unexpected: string[] = []
        await page.addInitScript(p => localStorage.setItem('stockpilot.preferences.v1', JSON.stringify(p)), { language, theme })
        await page.route(/\/b2b\/(?:auth|api)\//, async route => {
          const request = route.request(), path = new URL(request.url()).pathname
          let body: unknown
          if (request.method() !== 'GET') unexpected.push(request.method() + ' ' + path)
          if (path === '/b2b/auth/session') body = sessionFixture
          else if (path === '/b2b/api/products') body = { items: products, total: 4, has_more: false, limit: 12, offset: 0 }
          else if (path.startsWith('/b2b/api/products/')) {
            const id = path.split('/')[4], product = products.find(p => p.product_id === id)
            body = path.endsWith('/stock') ? { ...stockFixture, product_id: id } : product
          } else if (path === '/b2b/api/stock/movements') body = { items: [], limit: 10, offset: 0 }
          else if (path === '/b2b/api/imports') body = { items: [{ ...previewFixture, import_id: 'complete-import-reference-1234567890', file_fingerprint: 'a'.repeat(64), status: 'applied' }], limit: 10, offset: 0 }
          else unexpected.push(path)
          await route.fulfill({ status: body ? 200 : 500, contentType: 'application/json', body: JSON.stringify(body ?? {}) })
        })
        await page.goto('/b2b-app/')
        const checks = page.getByRole('checkbox', { name: language === 'fr' ? 'Comparer' : 'Compare', exact: true })
        await expect(checks).toHaveCount(4)
        for (const checkbox of (await checks.all()).slice(0, count)) await checkbox.check()
        const trigger = page.getByRole('button', { name: language === 'fr' ? 'Comparer les produits' : 'Compare products', exact: true })
        await trigger.click()
        const region = page.getByRole('region', { name: language === 'fr' ? 'Comparatif produits' : 'Product comparison' })
        await expect(region.locator('thead th')).toHaveCount(count + 1)
        await expect(region.locator('tbody th').filter({ hasText: language === 'fr' ? 'Caractéristiques' : 'Specifications' })).toBeVisible()
        await expect(page.getByText(language === 'fr' ? /Faites défiler horizontalement/ : /Scroll horizontally/)).toBeVisible()
        await page.getByRole('dialog').getByRole('button').first().focus()
        await page.keyboard.press('Tab')
        await expect(region).toBeFocused()
        expect(await region.evaluate(el => getComputedStyle(el).outlineStyle)).toBe('solid')
        const first = region.locator('tbody th').first(), initialX = (await first.boundingBox())!.x
        await page.keyboard.press('ArrowRight')
        await expect.poll(() => region.evaluate(el => el.scrollLeft)).toBeGreaterThan(0)
        await region.evaluate(el => { el.scrollLeft = el.scrollWidth })
        expect(Math.abs((await first.boundingBox())!.x - initialX)).toBeLessThan(2)
        expect((await first.boundingBox())!.width).toBeGreaterThanOrEqual(140)
        await expect(region.locator('thead th').last()).toBeInViewport()
        const lastColumn = (await region.locator('thead th').last().boundingBox())!
        const labelColumn = (await first.boundingBox())!
        expect(lastColumn.x).toBeGreaterThanOrEqual(labelColumn.x + labelColumn.width - 1)
        expect(lastColumn.x + lastColumn.width).toBeLessThanOrEqual((await region.boundingBox())!.x + (await region.boundingBox())!.width + 1)
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
        await page.screenshot({ path: testInfo.outputPath(`comparison-${language}-${theme}.png`) })
        await page.keyboard.press('Escape')
        await expect(trigger).toBeFocused()
        await page.goto('/b2b-app/stock')
        const entry = page.locator('.import-history-entry')
        await expect(entry).toHaveCount(1)
        await expect(entry.locator('p').first()).toHaveText(language === 'fr' ? 'Stock physique uniquement' : 'Physical stock only')
        await expect(entry.locator('p').nth(1)).toContainText(language === 'fr' ? 'Import appliqué' : 'Import applied')
        await expect(entry.locator('details')).not.toHaveAttribute('open')
        await entry.locator('summary').focus()
        await page.keyboard.press('Enter')
        await expect(entry.locator('details')).toHaveAttribute('open')
        await expect(entry.getByText('complete-import-reference-1234567890', { exact: true })).toBeVisible()
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
        await entry.screenshot({ path: testInfo.outputPath(`imports-${language}-${theme}.png`) })
        expect(unexpected).toEqual([])
      })
    }
  }
}
