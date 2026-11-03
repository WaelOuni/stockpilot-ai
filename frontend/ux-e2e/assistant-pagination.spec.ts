import { expect, test } from '@playwright/test'
import { sessionFixture, productFixture, stockFixture } from '../src/b2b/fixtures'

for (const language of ['fr', 'en']) {
  test(language + ': assistant current results stay separate from original mobile cards', async ({ page }) => {
    await page.addInitScript(language => localStorage.setItem('stockpilot.preferences.v1', JSON.stringify({ language, theme: 'dark' })), language)
    const observed = '2026-11-04T10:00:00Z'
    const card = { ...productFixture, ...stockFixture, observed_at: observed, name: 'Historical five', available: 5 }
    const requests: string[] = []
    await page.route(/\/b2b\/(?:auth|api)\//, async route => {
      const request = route.request(), url = new URL(request.url()), path = url.pathname
      expect(request.method()).toBe('GET')
      let body: unknown
      if (path === '/b2b/auth/session') body = { ...sessionFixture, assistant_available: true }
      else if (path === '/b2b/api/buyers') body = { items: [], limit: 100, offset: 0 }
      else if (path === '/b2b/api/assistant/conversations') body = { items: [], limit: 5, offset: 0 }
      else if (path.endsWith('/turns/turn/products')) {
        requests.push(url.search)
        const offset = Number(url.searchParams.get('offset'))
        body = { items: [{ ...card, name: 'Current ' + offset }], total: 126, has_more: offset < 120,
          limit: 20, offset, observed_at: observed,
          filters: { minimum_available: 5, maximum_available: 5, exclude_sold_out: false, sort: 'available' } }
      } else body = { conversation_id: 'conv', items: [{ turn_id: 'turn', kind: 'message', message: 'Stock exactement 5',
        status: 'completed', created_at: observed, operation: null,
        reply: { intent: 'LOW_STOCK', text: '126 produits', products: [card], order: null, can_confirm: false, can_browse_products: true } }],
        has_more: false, limit: 20, offset: 0 }
      await route.fulfill({ json: body })
    })
    await page.goto('/b2b-app/assistant/conv')
    const open = page.getByRole('button', { name: language === 'fr' ? 'Voir plus de produits' : 'See more products' })
    await open.focus(); await page.keyboard.press('Enter')
    await expect(page.getByText('Current 0', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: language === 'fr' ? 'Dernière' : 'Last', exact: true }).click()
    await expect(page.getByText('Current 120', { exact: true })).toBeVisible()
    await expect(page.getByText('Historical five', { exact: true })).toBeVisible()
    expect(requests.at(-1)).toContain('offset=120')
    await expect(page.locator('[aria-current=page]')).toHaveText('7')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}
