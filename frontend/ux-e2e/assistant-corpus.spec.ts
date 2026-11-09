// Exact G20 prompts, real Vue/Chromium, synthetic BFF responses only.
// This proves rendering/interaction contracts, never backend interpretation.
import { expect, test, type BrowserContext, type Route } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { sessionFixture, productFixture, stockFixture, orderFixture } from '../src/b2b/fixtures'

const observed = '2026-11-09T10:00:00Z'
const corpus = JSON.parse(readFileSync(new URL('../../evals/assistant_v025/corpus.json', import.meta.url), 'utf8')) as { id: string; message: string; expected: string }[]
const cases = corpus.filter(c => c.id.startsWith('G20-'))
const products = Array.from({ length: 126 }, (_, i) => ({ ...productFixture,
  product_id: `p-${i}`, sku: `TEST-A-${String(i).padStart(3, '0')}`,
  name: `Perceuse synthétique ${i}`, price_minor: '9999', category: 'perceuses' }))
const card = (i: number) => ({ ...products[i], ...stockFixture, product_id: `p-${i}`,
  quantity: 8, reserved: 3, available: 5, observed_at: observed })

for (const item of cases) test(`${item.id}: original prompt, ${item.expected} UI contract`, async ({ page }, info) => {
  const number = Number(item.id.slice(-2))
  const empty = [4, 8, 9, 10].includes(number)
  const count = empty ? 0 : number === 3 ? 2 : 5
  const writes: string[] = [], reads: string[] = []
  await page.setViewportSize({ width: 390, height: 900 })
  await page.addInitScript(() => localStorage.setItem('stockpilot.preferences.v1', JSON.stringify({ language: 'fr', theme: 'dark' })))
  const reply = {
    intent: number === 4 ? 'ORDER_PREPARE' : number === 10 ? 'CLARIFICATION_REQUIRED' : number === 3 ? 'COMPARISON' : 'PRODUCT_ADVICE',
    text: number === 4 ? 'Préparation enregistrée. Aucun stock réservé.'
      : number === 9 ? 'Aucun produit ne correspond aux critères.'
      : number === 10 ? 'Produit introuvable : précisez son nom complet ou son SKU exact.'
      : number === 8 ? 'Consultez votre historique de conversations.'
      : `126 produits actifs ; prix < 100 EUR ; disponible ${number === 5 ? '= 5' : '> 0'}.`,
    products: Array.from({ length: count }, (_, i) => card(i)),
    order: number === 4 ? { ...orderFixture, lines: [{ ...orderFixture.lines[0], sku: 'TEST-A-DRILL-001', quantity: 2 }] } : null,
    can_confirm: number === 4, can_browse_products: !empty,
  }
  await page.route(/\/b2b\/(?:auth|api)\//, async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (req.method() !== 'GET') writes.push(path)
    else reads.push(path + url.search)
    if (path.endsWith('/messages')) {
      const input = req.postDataJSON()
      expect(input.message).toBe(item.message)
      return json({ turn_id: input.message_id, kind: 'message', message: input.message,
        status: 'completed', created_at: observed, operation: null, reply })
    }
    if (path === '/b2b/auth/session') return json({ ...sessionFixture, assistant_available: true })
    if (path === '/b2b/api/buyers') return json({ items: [], total: 0, has_more: false, limit: 100, offset: 0 })
    if (path === '/b2b/api/assistant/conversations') {
      const offset = Number(url.searchParams.get('offset') ?? 0), limit = Number(url.searchParams.get('limit') ?? 5)
      return json({ items: Array.from({ length: limit }, (_, i) => ({ conversation_id: `conv-${offset + i}`, created_at: observed })), limit, offset })
    }
    if (path === '/b2b/api/assistant/conversations/conv') return json({ conversation_id: 'conv', items: [], has_more: false, limit: 20, offset: 0 })
    if (path.endsWith('/products') && path.includes('/turns/')) {
      const offset = Number(url.searchParams.get('offset') ?? 0)
      return json({ items: Array.from({ length: Math.min(20, 126 - offset) }, (_, i) => card(offset + i)),
        total: 126, has_more: offset + 20 < 126, limit: 20, offset, observed_at: observed,
        filters: { minimum_available: number === 5 ? 5 : 0, maximum_available: number === 5 ? 5 : 14, exclude_sold_out: false, sort: 'available' } })
    }
    if (path.endsWith('/stock')) return json({ ...stockFixture, product_id: path.split('/')[4], quantity: 8, reserved: 3, available: 5 })
    if (path.startsWith('/b2b/api/products/')) return json(products.find(p => p.product_id === path.split('/')[4]))
    return json({ code: 'UNEXPECTED_REQUEST' }, 500)
  })
  await page.goto('/b2b-app/assistant/conv')
  const input = page.getByRole('textbox', { name: 'Votre message' })
  await input.fill(item.message)
  await page.getByRole('button', { name: 'Envoyer le message', exact: true }).click()
  await expect(page.locator('.assistant-user')).toContainText(item.message)
  await expect(page.locator('.assistant-answer')).toContainText(reply.text)
  await expect(page.locator('.assistant-product')).toHaveCount(count)
  if ([1, 5, 6].includes(number)) {
    const more = page.getByRole('button', { name: 'Voir plus de produits' })
    await more.focus(); await page.keyboard.press('Enter')
    await expect(page.locator('.current-results .b2b-grid article')).toHaveCount(20)
    await expect(page.locator('.catalogue-pagination')).toContainText('126')
    if (number === 5) {
      await expect(page.getByLabel('Disponible minimum')).toHaveValue('5')
      await expect(page.getByLabel('Disponible maximum')).toHaveValue('5')
    }
    if (number === 6) {
      const next = page.getByRole('button', { name: 'Suivant', exact: true })
      await next.focus(); await page.keyboard.press('Enter')
      await expect(page.locator('[aria-current=page]')).toHaveText('2')
      expect(reads.some(path => path.includes('offset=20'))).toBe(true)
    }
    await expect(page.locator('.assistant-product')).toHaveCount(5)
  }
  if (number === 2) {
    await expect(page.locator('.assistant-product').first()).toContainText('99,99')
    await expect(page.locator('.assistant-product').first()).toContainText('Disponible 5')
  }
  if (number === 3) {
    await page.getByRole('button', { name: 'Comparer ces produits' }).click()
    await expect(page.getByRole('region', { name: 'Comparatif produits' })).toBeVisible()
    await expect(page.locator('.comparison-table thead th')).toHaveCount(3)
    expect(reads.some(path => path.endsWith('/p-0/stock'))).toBe(true)
  }
  if (number === 4) {
    const confirm = page.getByRole('button', { name: 'Confirmer et réserver' })
    await expect(confirm).toBeDisabled()
    await page.getByRole('checkbox', { name: 'Je confirme les SKU, quantités et prix du dernier récapitulatif.' }).check()
    await expect(confirm).toBeEnabled()
    await expect(page.locator('.assistant-order-summary')).toContainText('TEST-A-DRILL-001 · quantité 2')
  }
  if (number === 7) {
    expect(await page.locator('html').getAttribute('data-theme')).toBe('dark')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: info.outputPath('G20-07-mobile-dark.png') })
  }
  if (number === 8) {
    await page.getByRole('link', { name: 'Historique des conversations' }).click()
    await expect(page.getByRole('heading', { name: 'Historique des conversations' })).toBeVisible()
    await page.getByRole('button', { name: 'Suivant', exact: true }).click()
    expect(reads.some(path => path.includes('/conversations?limit=20&offset=20'))).toBe(true)
  }
  if (number === 9 || number === 10) {
    await expect(page.getByRole('button', { name: 'Voir plus de produits' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Confirmer et réserver' })).toHaveCount(0)
  }
  expect(writes).toEqual(['/b2b/api/assistant/conversations/conv/messages'])
})

async function boundaryFixture(context: BrowserContext, identity: string) {
  const prompt = corpus.find(c => c.id === identity)!.message
  const state = { session: { ...sessionFixture, assistant_available: true },
    pending: undefined as Route | undefined, pagePending: undefined as Route | undefined,
    messageId: '', created: false, writes: [] as string[] }
  const turn = () => ({ turn_id: state.messageId, kind: 'message', message: prompt,
    status: 'completed', created_at: observed, operation: null,
    reply: { intent: 'PRODUCT_ADVICE', text: 'Réponse synthétique conservée.', products: [card(0)],
      order: null, can_confirm: false, can_browse_products: true } })
  const results = (offset: number, name: string) => ({ items: [{ ...card(offset), name }], total: 126,
    has_more: true, limit: 20, offset, observed_at: observed,
    filters: { minimum_available: 0, maximum_available: 14, exclude_sold_out: false, sort: 'available' } })
  await context.route(/\/b2b\/(?:auth|api)\//, async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname
    const json = (body: unknown) => route.fulfill({ status: 200, json: body })
    if (req.method() !== 'GET') state.writes.push(path)
    if (path === '/b2b/auth/session') return json(state.session)
    if (path === '/b2b/api/buyers') return json({ items: [], limit: 100, offset: 0 })
    if (path === '/b2b/api/products') return json({ items: [], limit: 12, offset: 0, total: 0, has_more: false })
    if (path.endsWith('/categories')) return json({ items: [] })
    if (path.endsWith('/messages')) {
      expect(req.postDataJSON().message).toBe(prompt)
      state.messageId = req.postDataJSON().message_id
      if (identity === 'G18-16') { state.pending = route; return }
      return json(turn())
    }
    if (path === '/b2b/api/assistant/conversations') {
      if (req.method() === 'POST') { state.created = true; return json({ conversation_id: 'new' }) }
      return json({ items: [{ conversation_id: 'conv', created_at: observed }, ...(state.created ? [{ conversation_id: 'new', created_at: observed }] : [])], limit: 5, offset: 0 })
    }
    if (path.endsWith('/products') && path.includes('/turns/')) {
      const offset = Number(url.searchParams.get('offset'))
      if (offset === 20) { state.pagePending = route; return }
      return json(results(0, 'Résultat courant conservé'))
    }
    if (/\/conversations\/(conv|new)$/.test(path)) return json({ conversation_id: path.split('/').at(-1),
      items: path.endsWith('/conv') && state.messageId ? [turn()] : [], limit: 20, offset: 0, has_more: false })
    return route.fulfill({ status: 500, json: { code: 'UNEXPECTED_REQUEST' } })
  })
  return { state, prompt, turn, results }
}

test('G14-11: exact request followed by explicit new-conversation UI preserves old history', async ({ page, context }) => {
  const h = await boundaryFixture(context, 'G14-11')
  await page.goto('/b2b-app/assistant/conv')
  await page.getByRole('textbox', { name: 'Votre message' }).fill(h.prompt)
  await page.getByRole('button', { name: 'Envoyer le message', exact: true }).click()
  await expect(page.locator('.assistant-user')).toContainText(h.prompt)
  await page.getByRole('button', { name: 'Nouvelle conversation', exact: true }).click()
  await expect(page).toHaveURL(/\/assistant\/new$/)
  await expect(page.locator('.assistant-turn')).toHaveCount(0)
  await page.getByRole('combobox', { name: 'Mes conversations' }).selectOption('conv')
  await expect(page.locator('.assistant-user')).toContainText(h.prompt)
  await expect(page.locator('.assistant-answer')).toContainText('Réponse synthétique conservée.')
  expect(h.state.writes).toEqual(['/b2b/api/assistant/conversations/conv/messages', '/b2b/api/assistant/conversations'])
})

test('G18-16: exact hostile request cannot retain a late response after cross-tab identity change', async ({ page, context }) => {
  const h = await boundaryFixture(context, 'G18-16')
  await page.goto('/b2b-app/assistant/conv')
  await page.getByRole('textbox', { name: 'Votre message' }).fill(h.prompt)
  await page.getByRole('button', { name: 'Envoyer le message', exact: true }).click()
  await expect.poll(() => !!h.state.pending).toBe(true)
  h.state.session = { ...h.state.session, user_id: 'user-b', organization_id: 'org-b', organization_name: 'Other organization' }
  const other = await context.newPage(); await other.goto('/b2b-app/')
  await expect(page.getByText('Le compte a changé dans un autre onglet. Reconnectez-vous avec le compte souhaité.')).toBeVisible()
  await h.state.pending!.fulfill({ status: 200, json: h.turn() })
  await expect(page.locator('.assistant-turn')).toHaveCount(0)
  await expect(page.getByText('Réponse synthétique conservée.')).toHaveCount(0)
  expect(await page.evaluate(() => sessionStorage.getItem('stockpilot.b2b.conversation.v1'))).toBeNull()
  expect(h.state.writes).toEqual(['/b2b/api/assistant/conversations/conv/messages'])
})

test('G19-07: exact next-page request and late paginated HTTP response cannot replace reopened results', async ({ page, context }) => {
  const h = await boundaryFixture(context, 'G19-07')
  await page.goto('/b2b-app/assistant/conv')
  await page.getByRole('textbox', { name: 'Votre message' }).fill(h.prompt)
  await page.getByRole('button', { name: 'Envoyer le message', exact: true }).click()
  await page.getByRole('button', { name: 'Voir plus de produits' }).click()
  await expect(page.getByText('Résultat courant conservé', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Suivant', exact: true }).click()
  await expect.poll(() => !!h.state.pagePending).toBe(true)
  await page.getByRole('button', { name: 'Fermer les résultats actualisés' }).click()
  await page.getByRole('button', { name: 'Voir plus de produits' }).click()
  await expect(page.getByText('Résultat courant conservé', { exact: true })).toBeVisible()
  await h.state.pagePending!.fulfill({ status: 200, json: h.results(20, 'Résultat périmé interdit') })
  await expect(page.getByText('Résultat périmé interdit')).toHaveCount(0)
  await expect(page.getByText('Résultat courant conservé', { exact: true })).toBeVisible()
  await expect(page.locator('.assistant-product')).toHaveCount(1)
  expect(h.state.writes).toEqual(['/b2b/api/assistant/conversations/conv/messages'])
})
