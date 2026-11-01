import { test, expect } from '@playwright/test'
import { productFixture, stockFixture, sessionFixture } from '../src/b2b/fixtures'
for (const width of [1440, 1024, 768, 390]) for (const language of ['fr', 'en'] as const) for (const theme of ['light', 'dark'] as const) {
  test(`${language}/${theme}/${width}: catalogue pagination, keyboard discovery and account menu`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 })
    const role = width === 1440 ? 'ORGANIZATION_ADMIN' : width === 1024 ? 'MANAGER' : width === 768 ? 'USER' : 'READ_ONLY'
    const canAdjust = role === 'MANAGER' || role === 'ORGANIZATION_ADMIN'
    const canImport = role === 'ORGANIZATION_ADMIN'
    const permissions = sessionFixture.permissions.filter(p => (p !== 'stock:adjust' || canAdjust) && (p !== 'import:execute' || canImport))
    await page.addInitScript(p => localStorage.setItem('stockpilot.preferences.v1', JSON.stringify(p)), { language, theme })
    const calls: string[] = []
    const products = Array.from({ length: 225 }, (_, i) => ({ ...productFixture, product_id: `p-${i}`, sku: `BDEMO-${String(i).padStart(4, '0')}`, name: `Perceuse Norex ${i}`, category: 'perceuses', price_minor: '15500' }))
    await page.route(/\/b2b\/(auth|api)\//, async route => {
      const url = new URL(route.request().url()); const path = url.pathname; calls.push(path + url.search)
      let body: unknown
      if (path.endsWith('/session')) body = { ...sessionFixture, role, permissions }
      else if (path === '/b2b/api/stock/movements' || path === '/b2b/api/imports') body = { items: [], limit: 10, offset: 0 }
      else if (path.endsWith('/categories')) body = { items: ['perceuses', 'Éclairage'] }
      else if (path.endsWith('/suggestions')) body = { items: products.slice(0, 3) }
      else if (path.endsWith('/by-sku')) body = products.find(p => p.sku === url.searchParams.get('sku'))
      else if (path === '/b2b/api/products') {
        const filtered = url.searchParams.get('category') === 'unknown' ? [] : url.searchParams.get('available') ? products.slice(1) : products
        const offset = Number(url.searchParams.get('offset') ?? 0)
        body = { items: filtered.slice(offset, offset + 12), total: filtered.length, limit: 12, offset, has_more: offset + 12 < filtered.length }
      } else if (path.endsWith('/stock')) {
        const id = path.split('/')[4]
        body = { ...stockFixture, product_id: id, quantity: id === 'p-0' ? 0 : 10, reserved: 0, available: id === 'p-0' ? 0 : 10 }
      }
      await route.fulfill({ status: body ? 200 : 404, contentType: 'application/json', body: JSON.stringify(body ?? { code: 'NOT_FOUND' }) })
    })
    await page.goto('/b2b-app/')
    await expect(page.locator('article')).toHaveCount(12)
    const sku = page.locator('#catalogue-sku'), category = page.locator('#catalogue-category')
    const search = page.getByRole('button', { name: language === 'fr' ? 'Rechercher' : 'Search', exact: true })
    const bounds = await Promise.all([sku, category, page.locator('.availability-filter'), search].map(e => e.boundingBox()))
    const [a, b, check, button] = bounds.map(b => { expect(b).not.toBeNull(); return b! })
    if (width >= 768) {
      expect(Math.abs(a!.y - b!.y)).toBeLessThanOrEqual(1)
      expect(Math.abs(a!.height - b!.height)).toBeLessThanOrEqual(1)
    } else expect(b!.y).toBeGreaterThan(a!.y + a!.height)
    if (width >= 1024) {
      expect(Math.abs(button!.y - a!.y)).toBeLessThanOrEqual(1)
      expect(Math.abs(check!.y - a!.y)).toBeLessThanOrEqual(1)
    } else expect(check!.y).toBeGreaterThan(b!.y + b!.height)
    expect(button!.height).toBeGreaterThanOrEqual(44)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await sku.focus(); await page.keyboard.press('Tab'); await expect(category).toBeFocused()
    await page.keyboard.press('Tab'); await expect(page.locator('.availability-filter input')).toBeFocused()
    await page.keyboard.press('Tab'); await expect(search).toBeFocused()
    await expect(sku).toHaveAttribute('aria-describedby', 'catalogue-sku-hint')
    await page.screenshot({ path: testInfo.outputPath('filters.png') })
    await expect(page.locator('.catalogue-pagination')).toContainText('225')
    await expect(page.locator('article').first()).toContainText(language === 'fr' ? 'Rupture de stock' : 'Out of stock')
    await expect(page.locator('article').first().getByRole('link')).toHaveCount(0)
    await page.getByRole('button', { name: language === 'fr' ? 'Dernière' : 'Last', exact: true }).click()
    await expect(page.locator('article')).toHaveCount(9)
    await expect(page.locator('.catalogue-pagination [aria-current=page]')).toHaveText('19')
    await page.screenshot({ path: testInfo.outputPath(`last-page-${language}-${theme}.png`) })
    await page.locator('#catalogue-category').fill('per')
    await page.locator('#catalogue-category').press('ArrowDown'); await page.locator('#catalogue-category').press('ArrowDown'); await page.locator('#catalogue-category').press('Enter')
    await expect(page.locator('article')).toHaveCount(12)
    await expect(page.locator('.catalogue-pagination [aria-current=page]')).toHaveText('1')
    await page.locator('.availability-filter input').check()
    await expect(page.locator('.catalogue-pagination')).toContainText('224')
    await page.locator('#catalogue-sku').fill('BDEMO')
    await expect(page.locator('#catalogue-sku-options [role=option]')).toHaveCount(3)
    await page.locator('#catalogue-sku').press('ArrowDown'); await page.locator('#catalogue-sku').press('Enter')
    await expect(page.locator('article')).toHaveCount(1)
    expect(calls.some(c => c.includes('/by-sku?sku=BDEMO-0000'))).toBe(true)
    await page.getByRole('button', { name: language === 'fr' ? 'Effacer les filtres' : 'Clear filters' }).click()
    await page.locator('#catalogue-category').fill('unknown'); await page.locator('#catalogue-category').press('Escape'); await page.locator('#catalogue-category').press('Enter')
    await expect(page.locator('article')).toHaveCount(0)
    await expect(page.locator('.catalogue-pagination')).toContainText(language === 'fr' ? '0 sur 0' : '0 of 0')
    const account = page.locator('.workspace-account summary')
    await account.focus(); await page.keyboard.press('Enter')
    await expect(page.locator('.workspace-account')).toHaveAttribute('open')
    await page.keyboard.press('Tab'); await expect(page.locator('.workspace-account select')).toBeFocused()
    await page.keyboard.press('Escape'); await expect(account).toBeFocused()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`discovery-${language}-${theme}.png`) })
    await page.getByRole('link', { name: language === 'fr' ? 'Stocks' : 'Stock', exact: true }).click()
    await expect(page.getByRole('heading', { level: 1 })).toHaveText(canAdjust
      ? (language === 'fr' ? 'Gestion des stocks' : 'Stock management')
      : (language === 'fr' ? 'Consultation des stocks' : 'Stock overview'))
    await page.getByLabel(language === 'fr' ? 'SKU du stock' : 'Stock SKU').fill('BDEMO-0001')
    await page.getByRole('button', { name: language === 'fr' ? 'Consulter le stock' : 'Look up stock', exact: true }).click()
    await expect(page.getByRole('heading', { level: 3 })).toContainText('BDEMO-0001')
    await expect(page.getByRole('button', { name: language === 'fr' ? 'Ajuster le stock physique' : 'Adjust physical stock', exact: true })).toHaveCount(canAdjust ? 1 : 0)
    await expect(page.getByRole('button', { name: language === 'fr' ? 'Importer un CSV' : 'Import CSV', exact: true })).toHaveCount(canImport ? 1 : 0)
    expect(calls.some(c => c.startsWith('/b2b/api/imports?'))).toBe(canImport)
  })
}
