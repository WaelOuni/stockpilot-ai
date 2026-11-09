import { expect, test, type Page, type Route } from '@playwright/test'
import { sessionFixture, productFixture, stockFixture } from '../src/b2b/fixtures'

const created = '2026-11-09T10:00:00Z'
const card = { ...productFixture, price_minor: '9999', ...stockFixture, observed_at: created }
function completed(id: string, message = 'Question', intent = 'PRODUCT_ADVICE') {
  return { turn_id: id, kind: 'message', message, status: 'completed', operation: null, created_at: created,
    reply: { intent, text: 'Produits vérifiés. Aucune réservation.', products: intent === 'PRODUCT_ADVICE' ? [card] : [], order: null, can_confirm: false, can_browse_products: false } }
}

async function setup(page: Page, language = 'fr', theme = 'light', historical = 0) {
  let pending: Route | undefined, sent = 0, result = completed('new'), resumePending = false
  const writes: string[] = []
  await page.addInitScript(p => {
    if (!localStorage.getItem('stockpilot.preferences.v1')) localStorage.setItem('stockpilot.preferences.v1', JSON.stringify(p))
    sessionStorage.setItem('stockpilot.b2b.conversation.v1', JSON.stringify({ user: 'user-a', organization: 'org-a', value: 'conv' }))
    const state = window as unknown as { audioCalls: number }
    state.audioCalls = 0
    // Observable API calls, not a claim of audible output in headless Chromium.
    Object.defineProperty(window, 'AudioContext', { value: class {
      state = 'running'; currentTime = 0; destination = {}
      resume() { return Promise.resolve() } close() { return Promise.resolve() }
      createOscillator() { return { frequency: { setValueAtTime() {} }, connect() {}, disconnect() {}, start() { state.audioCalls++ }, stop() {}, onended: null } }
      createGain() { return { gain: { setValueAtTime() {}, linearRampToValueAtTime() {} }, connect() {}, disconnect() {} } }
    } })
  }, { language, theme })
  await page.clock.install()
  await page.route(/\/b2b\/(?:auth|api)\//, async route => {
    const req = route.request(), path = new URL(req.url()).pathname
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (req.method() !== 'GET') writes.push(path)
    if (path.endsWith('/messages')) {
      sent++; pending = route
      const body = req.postDataJSON(); result = completed(body.message_id, body.message)
      return
    }
    if (path === '/b2b/auth/session') return json({ ...sessionFixture, assistant_available: true, permissions: [...sessionFixture.permissions, 'conversation:own'] })
    if (path === '/b2b/api/assistant/conversations') return json({ items: [{ conversation_id: 'conv', created_at: created }], limit: 5, offset: 0 })
    if (path === '/b2b/api/assistant/conversations/conv' || path.endsWith('/resume')) {
      const items = path.endsWith('/resume') ? [resumePending ? { ...result, status: 'pending', reply: null } : result] : Array.from({ length: historical }, (_, i) => completed('old-' + i, 'Ancien message ' + i))
      return json({ conversation_id: 'conv', items, limit: 20, offset: 0, has_more: false })
    }
    if (path === '/b2b/api/buyers') return json({ items: [], total: 0, has_more: false, limit: 100, offset: 0 })
    if (path.endsWith('/categories')) return json({ items: ['perceuses'] })
    if (path === '/b2b/api/products') return json({ items: [productFixture], total: 1, has_more: false, limit: 12, offset: 0 })
    if (path.endsWith('/stock')) return json(stockFixture)
    if (path.startsWith('/b2b/api/products/')) return json(productFixture)
    if (path.endsWith('/logout')) return json({ logout_url: null })
    return json({ code: 'UNEXPECTED_FIXTURE_REQUEST' }, 500)
  })
  return {
    count: () => sent, writes,
    async finish(intent = 'PRODUCT_ADVICE', status = 'completed') {
      expect(pending).toBeDefined()
      await pending!.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...result, status, reply: status === 'pending' ? null : completed(result.turn_id, result.message, intent).reply }) })
      pending = undefined
    },
    async fail() { await pending!.abort('failed'); pending = undefined },
    async reject(status: number) { await pending!.fulfill({ status, contentType: 'application/json', body: '{}' }); pending = undefined },
    pendingResume(value: boolean) { resumePending = value },
  }
}

for (const language of ['fr', 'en']) for (const theme of ['light', 'dark']) {
  test(`UXP-01/02/04/05/17/21/22 ${language}/${theme}: honest waiting and completion without sound`, async ({ page }, info) => {
    const h = await setup(page, language, theme)
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/b2b-app/assistant/conv')
    const input = page.getByRole('textbox', { name: language === 'fr' ? 'Votre message' : 'Your message' })
    await expect(input).toBeVisible()
    await expect(page.locator('.response-dots')).toHaveCount(0)
    await input.fill('Je cherche une perceuse disponible avec un prix moins de 100 euros')
    const send = page.getByRole('button', { name: language === 'fr' ? 'Envoyer le message' : 'Send message', exact: true })
    await send.click()
    await expect.poll(h.count).toBe(1)
    await expect(page.locator('.assistant-response-status')).toContainText(language === 'fr' ? 'StockPilot prépare' : 'StockPilot is preparing')
    await expect(input).toHaveValue('Je cherche une perceuse disponible avec un prix moins de 100 euros')
    await expect(send).toBeDisabled()
    expect(await page.locator('.response-dots').evaluate(el => getComputedStyle(el).animationName)).toBe('none')
    await expect(page.locator('.assistant-transcript')).not.toHaveAttribute('aria-live')
    await expect(page.locator('.assistant-response-status')).toHaveAttribute('aria-live', 'polite')
    await page.screenshot({ path: info.outputPath(`waiting-${language}-${theme}.png`) })
    await page.clock.fastForward(12000)
    await expect(page.locator('.assistant-response-status')).toContainText(language === 'fr' ? 'Ne renvoyez pas' : 'do not resend')
    await input.focus()
    await h.finish()
    await expect(page.locator('.assistant-product')).toHaveCount(1)
    await expect(page.locator('.assistant-response-status')).toHaveText(language === 'fr' ? 'Réponse prête' : 'Response ready')
    await expect(input).toBeFocused()
    expect(await page.evaluate(() => (window as unknown as { audioCalls: number }).audioCalls)).toBe(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    expect(h.writes.filter(p => !p.endsWith('/messages'))).toEqual([])
    await page.screenshot({ path: info.outputPath(`completed-${language}-${theme}.png`) })
    await page.clock.fastForward(4000)
    await expect(page.locator('.assistant-response-status')).toHaveText('')
  })
}

test('UXP-03/18/20/24: fast reply, optional sound once, device preference survives reload', async ({ page }) => {
  const h = await setup(page)
  await page.goto('/b2b-app/assistant/conv')
  await page.getByLabel('Compte et préférences').click()
  const option = page.getByRole('checkbox', { name: 'Son à la fin des réponses de l’assistant' })
  await expect(option).not.toBeChecked(); await option.check()
  await page.getByLabel('Compte et préférences').press('Escape')
  await page.getByRole('textbox', { name: 'Votre message' }).fill('Des perceuses disponibles')
  await page.getByRole('button', { name: 'Envoyer le message', exact: true }).click()
  await expect.poll(h.count).toBe(1)
  await page.clock.fastForward(100)
  await h.finish()
  await expect(page.locator('.assistant-response-status')).toHaveText('Réponse prête')
  expect(await page.evaluate(() => (window as unknown as { audioCalls: number }).audioCalls)).toBe(1)
  await page.getByRole('button', { name: 'Reprendre et vérifier' }).click()
  await expect(page.locator('.assistant-turn')).toHaveCount(1)
  expect(await page.evaluate(() => (window as unknown as { audioCalls: number }).audioCalls)).toBe(1)
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('stockpilot.preferences.v1')!))).toEqual({ language: 'fr', theme: 'light', assistantSound: true })
  await page.reload()
  await page.getByLabel('Compte et préférences').click()
  await expect(option).toBeChecked()
  expect(await page.evaluate(() => (window as unknown as { audioCalls: number }).audioCalls)).toBe(0)
  await option.uncheck()
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('stockpilot.preferences.v1')!).assistantSound)).toBe(false)
})

test('UXP-09/10/11: uncertain network response retains text; manual reconciliation never repeats POST', async ({ page }) => {
  const h = await setup(page)
  await page.goto('/b2b-app/assistant/conv')
  const input = page.getByRole('textbox', { name: 'Votre message' })
  await input.fill('Une recherche sans commande')
  await page.getByRole('button', { name: 'Envoyer le message', exact: true }).click()
  await expect.poll(h.count).toBe(1); await h.fail()
  await expect(page.locator('.response-dots')).toHaveCount(0)
  await expect(input).toHaveValue('Une recherche sans commande')
  await expect(page.getByText('Une réponse reste à vérifier.', { exact: false })).toBeVisible()
  h.pendingResume(true)
  await page.getByRole('button', { name: 'Reprendre et vérifier' }).click()
  await expect(page.getByText('Demande enregistrée, résultat à vérifier.')).toBeVisible()
  h.pendingResume(false)
  await page.getByRole('button', { name: 'Reprendre et vérifier' }).click()
  await expect(page.locator('.assistant-response-status')).toHaveText('Réponse prête')
  expect(h.count()).toBe(1)
})

test('UXP-13/14: minimized dock shows one private badge, cleared when read', async ({ page }, info) => {
  const h = await setup(page)
  await page.goto('/b2b-app/')
  const launcher = page.getByRole('button', { name: 'Assistant IA', exact: true })
  await launcher.click()
  await page.getByRole('textbox', { name: 'Votre message' }).fill('Des perceuses disponibles')
  await page.getByRole('button', { name: 'Envoyer le message', exact: true }).click()
  await expect.poll(h.count).toBe(1)
  await page.getByRole('button', { name: 'Réduire l’assistant' }).click()
  await page.clock.fastForward(1000); await h.finish()
  await expect(page.locator('.assistant-unread')).toHaveText('Réponse disponible')
  await page.screenshot({ path: info.outputPath('dock-unread.png') })
  await page.locator('.assistant-launcher').click()
  await expect(page.locator('.assistant-unread')).toHaveCount(0)
  await expect(page.locator('.assistant-product')).toBeVisible()
  await page.getByRole('button', { name: 'Réduire l’assistant' }).click()
  await page.getByRole('link', { name: 'Assistant', exact: true }).click()
  await expect(page.locator('.assistant-response-status')).toHaveText('')
})

test('UXP-12/23: reading older text preserves scroll and focus; history is silent', async ({ page }) => {
  const h = await setup(page, 'fr', 'dark', 20)
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto('/b2b-app/assistant/conv')
  await expect(page.locator('.assistant-turn')).toHaveCount(20)
  await expect(page.locator('.assistant-response-status')).toHaveText('')
  await page.getByRole('textbox', { name: 'Votre message' }).fill('Des perceuses disponibles')
  await page.getByRole('button', { name: 'Envoyer le message', exact: true }).click()
  await expect.poll(h.count).toBe(1)
  const transcript = page.locator('.assistant-transcript')
  await transcript.evaluate(el => { el.scrollTop = 0 })
  await transcript.focus(); await h.finish()
  await expect(transcript).toBeFocused()
  await expect(transcript.getByText('Ancien message 0', { exact: false })).toBeVisible()
  expect(await transcript.evaluate(el => el.scrollTop)).toBe(0)
  await page.getByRole('button', { name: 'Nouvelle réponse', exact: true }).click()
  expect(await transcript.evaluate(el => el.scrollTop)).toBeGreaterThan(0)
})

test('UXP-09: actual browser fetch timeout at 90 seconds retains the draft without completion', async ({ page }) => {
  const h = await setup(page)
  await page.goto('/b2b-app/assistant/conv')
  const input = page.getByRole('textbox', { name: 'Votre message' })
  await input.fill('Question longue')
  await page.getByRole('button', { name: 'Envoyer le message', exact: true }).click()
  await expect.poll(h.count).toBe(1)
  await page.clock.fastForward(90001)
  await expect(page.locator('.response-dots')).toHaveCount(0)
  await expect(input).toHaveValue('Question longue')
  await expect(page.locator('.assistant-response-status')).toHaveText('')
  expect(await page.evaluate(() => (window as unknown as { audioCalls: number }).audioCalls)).toBe(0)
})

for (const status of [401, 403]) test(`UXP-15/16: HTTP ${status} invalidates waiting and old private data`, async ({ page }) => {
  const h = await setup(page)
  await page.goto('/b2b-app/assistant/conv')
  await page.getByRole('textbox', { name: 'Votre message' }).fill('Question privée')
  await page.getByRole('button', { name: 'Envoyer le message', exact: true }).click()
  await expect.poll(h.count).toBe(1); await h.reject(status)
  await expect(page.locator('.response-dots')).toHaveCount(0)
  await expect(page.locator('.assistant-turn')).toHaveCount(0)
  await expect(page.getByRole('textbox', { name: 'Code d’entreprise' })).toBeVisible()
  expect(await page.evaluate(() => (window as unknown as { audioCalls: number }).audioCalls)).toBe(0)
})

test('UXP-21: desktop at 200 percent CSS zoom retains readable nonblocking status and controls', async ({ page }, info) => {
  const h = await setup(page)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/b2b-app/assistant/conv')
  await page.evaluate(() => { document.documentElement.style.zoom = '2' })
  await page.getByRole('textbox', { name: 'Votre message' }).fill('Des perceuses disponibles')
  await page.getByRole('button', { name: 'Envoyer le message', exact: true }).click()
  await expect.poll(h.count).toBe(1); await h.finish()
  await expect(page.locator('.assistant-response-status')).toHaveText('Réponse prête')
  await expect(page.getByRole('button', { name: 'Reprendre et vérifier' })).toBeEnabled()
  const geometry = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth,
    overflowing: [...document.querySelectorAll('body *')].filter(el => el.getBoundingClientRect().right > innerWidth + 1).slice(0, 12).map(el => ({ tag: el.tagName, class: el.className, right: el.getBoundingClientRect().right })) }))
  expect(geometry.scroll <= geometry.width, JSON.stringify(geometry)).toBe(true)
  await page.screenshot({ path: info.outputPath('assistant-zoom-200.png') })
})
