// Captured from the guarded real Mongo fixture. UI transport is intercepted;
// interpretation and tenant isolation are separately asserted in backend tests.
import { expect, test } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { sessionFixture } from '../src/b2b/fixtures'

const cases = JSON.parse(readFileSync(process.env.STOCKPILOT_UAT_FIXTURE || new URL('./fixtures/uat-language.json', import.meta.url), 'utf8')) as { message: string; reply: { text: string; products: unknown[] } }[]
const counts = [2, 1, 1, 5, 3, 1]
for (const [index, item] of cases.entries()) test(`UAT ${index + 1}: exact phrase and qualified read-only answer`, async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 900 })
  await page.addInitScript(() => localStorage.setItem('stockpilot.preferences.v1', JSON.stringify({ language: 'fr', theme: 'dark' })))
  const writes: string[] = []
  await page.route(/\/b2b\/(?:auth|api)\//, async route => {
    const req = route.request(), path = new URL(req.url()).pathname
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (req.method() !== 'GET') writes.push(path)
    if (path.endsWith('/session')) return json({ ...sessionFixture, organization_id: 'org-a', assistant_available: true })
    if (path.endsWith('/messages')) {
      const input = req.postDataJSON(); expect(input.message).toBe(item.message)
      return json({ turn_id: input.message_id, kind: 'message', message: input.message, status: 'completed', created_at: '2026-11-17T13:00:00Z', operation: null, reply: item.reply })
    }
    if (path.endsWith('/conversations/conv')) return json({ conversation_id: 'conv', items: [], has_more: false, limit: 20, offset: 0 })
    if (path.endsWith('/conversations')) return json({ items: [], limit: 5, offset: 0 })
    if (path.endsWith('/buyers')) return json({ items: [], total: 0, has_more: false, limit: 100, offset: 0 })
    return json({ code: 'UNEXPECTED_REQUEST' }, 500)
  })
  await page.goto('/b2b-app/assistant/conv')
  await page.getByRole('textbox', { name: 'Votre message' }).fill(item.message)
  const send = page.getByRole('button', { name: 'Envoyer le message', exact: true })
  await send.focus(); await page.keyboard.press('Enter')
  await expect(page.locator('.assistant-user')).toContainText(item.message)
  await expect(page.locator('.assistant-answer')).toContainText(item.reply.text)
  await page.locator('.assistant-transcript').evaluate(element => { element.scrollTop = 0 })
  await page.screenshot({ path: info.outputPath(`uat-${index + 1}-${process.env.STOCKPILOT_UAT_FIXTURE ? 'before' : 'after'}.png`), fullPage: true })
  await expect(page.locator('.assistant-product')).toHaveCount(counts[index]!)
  if (index === 1) await expect(page.locator('.assistant-answer')).toContainText('Aucune date de rupture')
  await expect(page.getByRole('button', { name: 'Confirmer et réserver' })).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  expect(writes).toEqual(['/b2b/api/assistant/conversations/conv/messages'])
})
