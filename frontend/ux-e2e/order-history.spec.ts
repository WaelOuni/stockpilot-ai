import { test, expect } from '@playwright/test'
import { sessionFixture, orderFixture } from '../src/b2b/fixtures'
for (const language of ['fr', 'en']) test(language + ': accessible mobile order pages and explicit personal archive', async ({ page }) => {
  await page.addInitScript(language => localStorage.setItem('stockpilot.preferences.v1', JSON.stringify({ language, theme: 'light' })), language)
  let archives = 0
  await page.route(/\/b2b\/(?:auth|api)\//, async route => {
    const req = route.request(), url = new URL(req.url())
    let body: unknown
    if (url.pathname === '/b2b/auth/session') body = sessionFixture
    else if (url.pathname.includes('/buyers')) body = { items: [], limit: 100, offset: 0 }
    else if (url.pathname.endsWith('/archive')) {
      archives++; expect(req.postDataJSON().confirmed).toBe(true)
      body = { operation_id: req.postDataJSON().operation_id, order_id: orderFixture.order_id, archived: true }
    } else {
      const offset = Number(url.searchParams.get('offset'))
      body = { items: [{ ...orderFixture, status: 'cancelled' }], total: 21, has_more: offset < 20, limit: 10, offset }
    }
    await route.fulfill({ json: body })
  })
  await page.goto('/b2b-app/orders')
  const last = page.getByRole('button', { name: language === 'fr' ? 'Dernière' : 'Last', exact: true })
  await expect(last).toBeEnabled()
  await last.focus(); await page.keyboard.press('Enter')
  await expect(page.locator('button[aria-current=page]')).toHaveText('3')
  await page.getByRole('button', { name: language === 'fr' ? 'Archiver pour moi' : 'Archive for me' }).click()
  expect(archives).toBe(0)
  await page.getByRole('dialog').getByRole('button', { name: language === 'fr' ? 'Confirmer l’archivage personnel' : 'Confirm personal archive' }).click()
  await expect.poll(() => archives).toBe(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})
