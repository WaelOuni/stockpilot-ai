// Real OIDC composition with the analytics backend. Only the already-provisioned
// disposable harness is accepted by b2b-browser.mjs and its parent Python guard.
import { expect } from '@playwright/test'
import process from 'node:process'

export async function verifyIntelligence(page, context, origin, check) {
  const session = await (await context.request.get(origin + '/b2b/auth/session')).json()
  expect(session.role).toBe('ORGANIZATION_ADMIN')
  expect(session.permissions).toContain('analytics:read')
  const reads = [], writes = []
  const observe = request => {
    if (new globalThis.URL(request.url()).pathname.startsWith('/b2b/api/')) {
      if (request.method() === 'GET') reads.push(request.url())
      else writes.push(request.url())
    }
  }
  page.on('request', observe)
  const response = page.waitForResponse(r => new globalThis.URL(r.url()).pathname === '/b2b/api/intelligence')
  await page.getByRole('link', { name: 'Intelligence', exact: true }).click()
  const result = await response
  expect(result.status()).toBe(200)
  const data = await result.json()
  expect(data.organization_id).toBe('fixture-org')
  expect(data.summary.products).toBe(1)
  expect(data.page_series).toEqual([])
  expect(data.items[0].forecast.quantity).toBeNull()
  await expect(page.getByRole('heading', { name: 'StockPilot Intelligence', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Tableau analytique' })).toContainText('Aucune vente qualifiée')
  await page.setViewportSize({ width: 390, height: 900 })
  expect(await page.evaluate(() => globalThis.document.documentElement.scrollWidth <= globalThis.innerWidth)).toBe(true)
  await page.screenshot({ path: process.env.B2B_BROWSER_DIRECTORY + '/intelligence-real-oidc.png', fullPage: true })
  const trigger = page.getByRole('button', { name: 'Analyser DRILL-001', exact: true })
  await trigger.focus(); await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog', { name: 'Analyse du produit' })).toBeVisible()
  await page.keyboard.press('Escape'); await expect(trigger).toBeFocused()
  expect(reads.some(url => url.includes('/intelligence'))).toBe(true)
  expect(reads.every(url => !url.includes('organization_id'))).toBe(true)
  expect(writes).toEqual([])
  page.off('request', observe)
  await page.goto(origin + '/b2b-app/')
  await expect(page.getByRole('heading', { name: 'Catalogue', exact: true })).toBeVisible()
  check('Intelligence with real OIDC and analytics API: scoped read-only dashboard, unavailable sales history, mobile and keyboard detail')
}
