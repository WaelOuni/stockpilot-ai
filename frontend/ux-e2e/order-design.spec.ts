import { test, expect, type Page } from '@playwright/test'
import { sessionFixture, orderFixture, list } from '../src/b2b/fixtures'

const reserved = { ...orderFixture, status: 'reserved', reserved_at: '2026-10-21T11:00:00Z', lines: [{ ...orderFixture.lines[0], quantity: 3 }] }
async function fixture(page: Page, failures = 0, permissions = sessionFixture.permissions) {
  const writes: string[] = []
  await page.route(/\/b2b\/(auth|api)\//, async route => {
    const request = route.request(), url = new URL(request.url())
    if (request.method() === 'POST') writes.push(url.pathname)
    let body: unknown
    if (url.pathname.endsWith('/session')) body = { ...sessionFixture, permissions }
    else if (url.pathname.endsWith('/buyers')) body = { items: [], limit: 100, offset: 0 }
    else if (url.pathname.endsWith('/orders')) body = list([reserved])
    else if (url.pathname.endsWith('/orders/order-a')) {
      if (failures-- > 0) { await route.fulfill({ status: 503, json: {} }); return }
      body = reserved
    } else if (url.pathname.endsWith('/cancel')) body = { ...reserved, status: 'cancelled', cancelled_at: '2026-10-21T12:00:00Z' }
    else throw new Error('Unexpected fixture path: ' + url.pathname)
    await route.fulfill({ json: body })
  })
  return writes
}

for (const language of ['fr', 'en']) for (const theme of ['light', 'dark']) for (const width of [390, 1280]) {
  test(`order design ${language}/${theme}/${width}: details, exact totals, focus and safe actions`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    await page.addInitScript(({ language, theme }) => localStorage.setItem('stockpilot.preferences.v1', JSON.stringify({ language, theme })), { language, theme })
    const writes = await fixture(page)
    await page.goto('/b2b-app/orders')
    const view = page.getByRole('button', { name: language === 'fr' ? 'Voir la commande' : 'View order', exact: true })
    await expect(view).toBeEnabled()
    await page.screenshot({ path: `test-results/${process.env.STOCKPILOT_CAPTURE_SET ?? 'after'}-orders-${language}-${theme}-${width}.png`, fullPage: true })
    await view.focus()
    if (width > 600) {
      await expect(page.getByRole('tooltip')).toContainText(language === 'fr' ? 'Voir les détails' : 'View details')
      await page.keyboard.press('Escape')
      await expect(page.getByRole('tooltip')).toBeHidden()
    } else await expect(view).toHaveText(language === 'fr' ? 'Voir la commande' : 'View order')
    await page.keyboard.press('Enter')
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await page.screenshot({ path: `test-results/${process.env.STOCKPILOT_CAPTURE_SET ?? 'after'}-order-detail-${language}-${theme}-${width}.png`, fullPage: true })
    await expect(dialog).toContainText(language === 'fr' ? 'Total des lignes' : 'Line total')
    await expect(dialog).toContainText(language === 'fr' ? '147,00' : '147.00')
    await expect(dialog.locator('.order-events li')).toHaveCount(2)
    await expect(dialog.getByRole('button').first()).toBeFocused()
    await page.keyboard.press('Shift+Tab')
    expect(await dialog.evaluate(el => el.contains(document.activeElement))).toBe(true)
    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0); await expect(view).toBeFocused()
    expect(writes).toEqual([])
    const cancel = page.getByRole('button', { name: language === 'fr' ? 'Annuler la commande' : 'Cancel order', exact: true })
    await cancel.click()
    expect(writes).toEqual([])
    await expect(page.getByRole('dialog')).toContainText(language === 'fr' ? 'Les réservations seront libérées' : 'Reservations will be released')
    await page.keyboard.press('Escape')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  })
}

test('order detail failure keeps the dialog recoverable and does not send a mutation', async ({ page }) => {
  const writes = await fixture(page, 1)
  await page.goto('/b2b-app/orders')
  const view = page.getByRole('button', { name: 'Voir la commande', exact: true })
  await view.click()
  await expect(page.getByRole('dialog').getByRole('alert')).toContainText('Impossible de charger')
  await page.getByRole('button', { name: 'Réessayer', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('Total des lignes')
  await page.keyboard.press('Escape'); await expect(view).toBeFocused()
  expect(writes).toEqual([])
})

test('read-only order history and details expose no cancellation action', async ({ page }) => {
  const writes = await fixture(page, 0, ['order:read_own'])
  await page.goto('/b2b-app/orders')
  await page.getByRole('button', { name: 'Voir la commande', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('Total des lignes')
  await expect(page.getByRole('button', { name: 'Annuler la commande', exact: true })).toHaveCount(0)
  expect(writes).toEqual([])
})
