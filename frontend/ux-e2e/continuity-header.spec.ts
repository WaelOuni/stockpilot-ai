import { expect, test, type BrowserContext } from '@playwright/test'
import { sessionFixture, productFixture, stockFixture } from '../src/b2b/fixtures'

// Contract-level browser tests only. All BFF requests are intercepted; no SSO,
// database or existing deployment is contacted, including mutation endpoints.
async function fixture(context: BrowserContext) {
  const state = {
    session: { ...sessionFixture, assistant_available: true, permissions: [...sessionFixture.permissions, 'conversation:own'] },
    status: 200, created: 0, reads: [] as string[], writes: [] as string[], foreign: false,
  }
  await context.route(/\/b2b\/(?:auth|api)\//, async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname
    const write = req.method() !== 'GET'
    if (write) state.writes.push(path)
    else state.reads.push(path)
    let status = 200, body: unknown
    const page = { limit: 20, offset: 0, has_more: false }
    if (path === '/b2b/auth/session') { body = state.session; status = state.status }
    else if (path === '/b2b/auth/login') body = { authorization_url: 'https://identity.invalid/authorize' }
    else if (path === '/b2b/auth/logout') { state.status = 401; body = { provider_logout_url: 'https://identity.invalid/logout' } }
    else if (path === '/b2b/api/assistant/conversations' && write) { state.created++; body = { conversation_id: 'new-conversation' } }
    else if (path === '/b2b/api/assistant/conversations') body = { ...page, items: [{ conversation_id: 'old-conversation', created_at: '2026-10-28T10:00:00Z' }, ...(state.created ? [{ conversation_id: 'new-conversation', created_at: '2026-10-29T10:00:00Z' }] : [])] }
    else if (/\/conversations\/[\w-]+$/.test(path) && !write) {
      const id = path.split('/').at(-1)!
      if (state.foreign) { status = 404; body = {} }
      else body = { ...page, conversation_id: id, items: [{ turn_id: id, kind: 'message', message: 'Question ' + id, status: 'completed', created_at: '2026-10-29T10:00:00Z', operation: null, reply: { intent: 'PRODUCT_ADVICE', text: 'Réponse ' + id, products: [], order: null, can_confirm: false } }] }
    } else if (path === '/b2b/api/products') body = { ...page, total: 1, items: [productFixture] }
    else if (path.endsWith('/stock')) body = stockFixture
    else if (path === '/b2b/api/buyers' || path === '/b2b/api/orders') body = { ...page, items: [] }
    else { status = 500; body = { code: 'UNEXPECTED_TEST_REQUEST' } }
    await route.fulfill({ status, json: body })
  })
  return state
}
for (const width of [1440, 390]) for (const language of ['fr', 'en']) for (const theme of ['light', 'dark']) {
  test(`compact header ${width}px ${language}/${theme}, keyboard, permissions and availability`, async ({ page, context }, testInfo) => {
    const state = await fixture(context)
    await page.addInitScript(p => localStorage.setItem('stockpilot.preferences.v1', JSON.stringify(p)), { language, theme })
    await page.setViewportSize({ width, height: 900 }); await page.goto('/b2b-app/')
    const header = page.locator('.workspace-header'), nav = header.getByRole('navigation')
    await expect(nav.getByRole('link')).toHaveCount(4)
    await expect(header.getByText('Entreprise A', { exact: true }).first()).toBeVisible()
    await expect(header.locator('.account-label')).toHaveText('user-a')
    expect((await header.boundingBox())!.height).toBeLessThan(width === 390 ? 150 : 85)
    const account = header.locator('summary'); await account.focus(); await page.keyboard.press('Enter')
    await expect(header.locator('details')).toHaveAttribute('open')
    await page.keyboard.press('Tab'); await expect(header.getByRole('combobox')).toBeFocused()
    await expect(header.getByText(language === 'fr' ? 'Administrateur entreprise' : 'Company administrator', { exact: true })).toBeVisible()
    await page.keyboard.press('Escape'); await expect(account).toBeFocused()
    await expect(header.locator('details')).not.toHaveAttribute('open')
    expect(await account.evaluate(el => getComputedStyle(el).outlineStyle)).toBe('solid')
    for (const link of await nav.getByRole('link').all()) {
      await link.focus(); await expect(link).toBeInViewport()
      expect((await link.boundingBox())!.height).toBeGreaterThanOrEqual(44)
    }
    const filter = page.getByRole('checkbox', { name: language === 'fr' ? 'Afficher uniquement les produits disponibles' : 'Show only available products', exact: true })
    await filter.focus(); await page.keyboard.press('Space'); await expect(filter).toBeChecked()
    const request = page.waitForRequest(req => new URL(req.url()).searchParams.get('available') === 'true')
    await page.getByRole('button', { name: language === 'fr' ? 'Rechercher' : 'Search', exact: true }).click(); await request
    await expect(page.getByRole('button', { name: language === 'fr' ? 'Rechercher' : 'Search', exact: true })).toBeEnabled()
    await expect(page.getByText(productFixture.name, { exact: true })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath(`header-after-${width}-${language}-${theme}.png`), fullPage: true })
    state.session.permissions = ['catalogue:read']; await page.reload()
    await expect(nav.getByRole('link')).toHaveCount(1)
    expect(state.writes).toEqual([])
  })
}
test('new conversation, menu, floating assistant and refresh share one active conversation without replay', async ({ page, context }) => {
  const state = await fixture(context)
  await page.goto('/b2b-app/assistant/old-conversation')
  await expect(page.getByText('Réponse old-conversation')).toBeVisible()
  await page.getByRole('button', { name: 'Nouvelle conversation', exact: true }).click()
  await expect(page).toHaveURL(/\/assistant\/new-conversation$/)
  await expect(page.getByText('Réponse new-conversation')).toBeVisible()
  await page.getByRole('navigation').getByRole('link', { name: 'Catalogue', exact: true }).click()
  await page.getByRole('button', { name: 'Assistant IA', exact: true }).click()
  await expect(page.getByRole('dialog').getByText('Réponse new-conversation')).toBeVisible()
  await page.getByRole('dialog').getByRole('combobox', { name: 'Mes conversations' }).selectOption('old-conversation')
  await expect(page.getByRole('dialog').getByText('Réponse old-conversation')).toBeVisible()
  await page.keyboard.press('Escape')
  await page.getByRole('navigation').getByRole('link', { name: 'Assistant', exact: true }).click()
  await expect(page).toHaveURL(/\/assistant\/old-conversation$/)
  await page.reload(); await expect(page.getByText('Réponse old-conversation')).toBeVisible()
  expect(state.created).toBe(1); expect(state.writes).toEqual(['/b2b/api/assistant/conversations'])
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual(['stockpilot.preferences.v1'])
  const stored = await page.evaluate(() => sessionStorage.getItem('stockpilot.b2b.conversation.v1'))
  expect(stored).not.toContain('Réponse'); expect(stored).not.toContain('csrf')
})
test('server refuses a stale or foreign conversation hint before any history is displayed', async ({ page, context }) => {
  const state = await fixture(context); state.foreign = true
  await page.addInitScript(() => sessionStorage.setItem('stockpilot.b2b.conversation.v1', JSON.stringify({ user: 'user-a', organization: 'org-a', value: 'foreign' })))
  await page.goto('/b2b-app/assistant')
  await expect(page.getByRole('alert')).toContainText('Ressource introuvable')
  expect(await page.locator('.assistant-turn').count()).toBe(0)
  expect(await page.evaluate(() => sessionStorage.getItem('stockpilot.b2b.conversation.v1'))).toBeNull()
  expect(state.writes).toEqual([])
})
test('a new account in another tab clears visible and remembered conversations', async ({ page, context }) => {
  const state = await fixture(context)
  await page.goto('/b2b-app/assistant/old-conversation'); await expect(page.getByText('Réponse old-conversation')).toBeVisible()
  state.session = { ...state.session, user_id: 'other-user', organization_id: 'org-b', organization_name: 'Other organization' }
  const other = await context.newPage(); await other.goto('/b2b-app/')
  await expect(page.getByText('Le compte a changé dans un autre onglet. Reconnectez-vous avec le compte souhaité.')).toBeVisible()
  await expect(page.getByText('Réponse old-conversation')).toHaveCount(0)
  expect(await page.evaluate(() => sessionStorage.getItem('stockpilot.b2b.conversation.v1'))).toBeNull()
  expect(state.writes).toEqual([])
})
test('logout in a second tab prevents restoration and removes private context', async ({ page, context }) => {
  const state = await fixture(context)
  await page.goto('/b2b-app/assistant/old-conversation'); await expect(page.getByText('Réponse old-conversation')).toBeVisible()
  const other = await context.newPage(); await other.goto('/b2b-app/')
  await other.locator('.workspace-account summary').click()
  await other.getByRole('button', { name: 'Se déconnecter', exact: true }).click()
  await expect(page.getByText('La session a été fermée dans un autre onglet.')).toBeVisible()
  expect(await page.evaluate(() => sessionStorage.getItem('stockpilot.b2b.conversation.v1'))).toBeNull()
  await page.reload(); await expect(page.getByRole('button', { name: 'Continuer avec Keycloak' })).toBeVisible()
  expect(state.writes).toEqual(['/b2b/auth/logout'])
})

test('absolute expiry, explicit login and fixed callback preserve navigation without replay', async ({ page, context }) => {
  const state = await fixture(context)
  state.session.expires_at = '2099-01-01T00:05:00Z'; state.session.idle_expires_at = '2099-01-01T00:05:00Z'
  await page.clock.install({ time: new Date('2099-01-01T00:04:59Z') })
  // This provider page only represents navigation. Real OIDC is covered separately.
  await page.route('https://identity.invalid/authorize', route => route.fulfill({ contentType: 'text/html', body: '<p>Synthetic identity provider</p>' }))
  await page.goto('/b2b-app/assistant/old-conversation'); await expect(page.getByText('Réponse old-conversation')).toBeVisible()
  await page.clock.fastForward(2000)
  await expect(page.getByText('Votre session a expiré. Reconnectez-vous ; aucune mutation ne sera relancée automatiquement.')).toBeVisible()
  await expect(page.getByText('Réponse old-conversation')).toHaveCount(0)
  expect(state.writes).toEqual([])
  await page.getByRole('textbox', { name: 'Code d’entreprise' }).fill('org-a')
  await page.getByRole('button', { name: 'Continuer avec Keycloak', exact: true }).click()
  await expect(page).toHaveURL('https://identity.invalid/authorize')
  state.session.expires_at = '2099-01-01T01:00:00Z'; state.session.idle_expires_at = '2099-01-01T00:15:00Z'
  await page.goto('/b2b-app/') // The unchanged BFF callback returns to this fixed path.
  await expect(page).toHaveURL(/\/assistant\/old-conversation$/)
  await expect(page.getByText('Réponse old-conversation')).toBeVisible()
  expect(state.writes).toEqual(['/b2b/auth/login'])
})

test('history navigation uses the dedicated page, not a conversation named history', async ({ page, context }) => {
  const state = await fixture(context)
  await page.goto('/b2b-app/assistant/old-conversation')
  await page.getByRole('link', { name: 'Historique des conversations', exact: true }).click()
  await expect(page).toHaveURL(/assistant\/history$/)
  await expect(page.getByRole('heading', { name: 'Historique des conversations' })).toBeVisible()
  await expect(page.locator('section li a').filter({ hasText: 'old-conversation' })).toBeVisible()
  expect(state.reads.some(path => path === '/b2b/api/assistant/conversations/history')).toBe(false)
  expect(state.writes).toEqual([])
})
