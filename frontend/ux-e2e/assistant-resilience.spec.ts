import { expect, test } from '@playwright/test'
import { sessionFixture } from '../src/b2b/fixtures'

for (const language of ['fr', 'en']) {
  test(`${language}: failed consultation keeps identity, no automatic retry or completion feedback`, async ({ page }) => {
    let sent = 0, retries = 0, attempt = 'a'.repeat(32), original = ''
    await page.addInitScript(lang => {
      localStorage.setItem('stockpilot.preferences.v1', JSON.stringify({ language: lang, theme: 'light', assistantSound: false }))
    }, language)
    const created = '2026-11-15T20:00:00Z'
    await page.route(/\/b2b\/(?:auth|api)\//, async route => {
      const req = route.request(), path = new URL(req.url()).pathname
      const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
      if (path === '/b2b/auth/session') return json({ ...sessionFixture, assistant_available: true, permissions: [...sessionFixture.permissions, 'conversation:own'] })
      if (path === '/b2b/api/assistant/conversations') return json({ items: [{ conversation_id: 'conv', created_at: created }], limit: 5, offset: 0 })
      if (path.endsWith('/messages')) {
        sent++; expect(req.postDataJSON().read_only).toBe(true)
        original = req.postDataJSON().message_id
        return json({ code: 'UNAVAILABLE' }, 503)
      }
      if (path.endsWith('/retry')) {
        expect(path).toContain('/turns/' + original + '/retry')
        expect(req.postDataJSON().expected_attempt).toBe(attempt)
        retries++; attempt = (retries === 1 ? 'b' : 'c').repeat(32)
        return json({ code: 'UNAVAILABLE' }, 503)
      }
      if (path === '/b2b/api/assistant/conversations/conv') return json({ conversation_id: 'conv', items: sent ? [{
        turn_id: original, kind: 'message', message: 'Question synthétique', status: 'pending', operation: null, created_at: created,
        execution: { state: 'failed', attempt_id: attempt, code: 'provider_http_500', retryable: true },
        reply: { intent: 'ASSISTANT_UNAVAILABLE', text: 'Consultation non aboutie.', products: [], order: null, can_confirm: false },
      }] : [], has_more: false, limit: 20, offset: 0 })
      if (path === '/b2b/api/buyers') return json({ items: [], total: 0, has_more: false, limit: 100, offset: 0 })
      expect(req.method()).toBe('GET') // no hidden resume, order or second send
      return json({ code: 'UNEXPECTED' }, 404)
    })
    await page.goto('/b2b-app/assistant/conv')
    await page.getByRole('checkbox', { name: language === 'fr' ? 'Consultation uniquement (aucune commande)' : 'Read-only consultation (no orders)' }).check()
    await page.getByRole('textbox', { name: language === 'fr' ? 'Votre message' : 'Your message' }).fill('Question synthétique')
    const send = page.getByRole('button', { name: language === 'fr' ? 'Envoyer le message' : 'Send message', exact: true })
    await send.click()
    await expect(page.getByText(language === 'fr' ? 'La consultation a échoué.' : 'The consultation failed.', { exact: false })).toBeVisible()
    await expect(send).toBeDisabled()
    await expect(page.locator('.response-dots')).toHaveCount(0)
    await expect(page.locator('.assistant-response-status')).not.toHaveText(language === 'fr' ? 'Réponse prête' : 'Response ready')
    expect(sent).toBe(1); expect(retries).toBe(0)
    const retry = page.getByRole('button', { name: language === 'fr' ? 'Rejouer explicitement la même demande' : 'Explicitly retry the same request' })
    await retry.click(); await expect.poll(() => retries).toBe(1)
    await expect(retry).toBeEnabled()
    await retry.click(); await expect.poll(() => retries).toBe(2)
    expect(sent).toBe(1)
  })
}
