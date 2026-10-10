import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import AdminProductStockPage from './AdminProductStockPage.vue'
import AdminLoginPage from './AdminLoginPage.vue'
import { protectAdminRoutes } from '../router/adminGuard'
import type { StockMovement } from '../types/api'
import { readPendingStockAdjustment, savePendingStockAdjustment } from '../utils/pendingStockAdjustment'

const fetchMock = vi.fn<typeof fetch>()
const operationId = '4d22787c-a342-4c9c-b2b6-4fcd03027df4'
let product: { sku: string; product_name: string; category: 'DRILL'; price: number; stock: number }
let history: StockMovement[]
let movementByOperation: Map<string, StockMovement>
let postMode: 'commit' | 'uncertain-commit' | 'uncertain-abort' | 'reject-409' | 'unauthorized' | 'forbidden'
let wrapper: VueWrapper | undefined
let router: ReturnType<typeof createRouter>

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
function movement(operation: string, delta: number, reason: string, before = product.stock): StockMovement {
  return {
    movement_id: `movement-${movementByOperation.size + 1}`,
    operation_id: operation,
    product_sku: product.sku,
    delta,
    stock_before: before,
    stock_after: before + delta,
    reason,
    occurred_at: '2026-10-10T10:00:00Z',
    source: 'BACKOFFICE',
  }
}

beforeEach(() => {
  product = { sku: 'SKU-1', product_name: 'Drill', category: 'DRILL', price: 12.5, stock: 5 }
  history = []
  movementByOperation = new Map()
  postMode = 'commit'
  sessionStorage.clear()
  fetchMock.mockReset()
  vi.stubGlobal('crypto', { randomUUID: () => operationId })
  fetchMock.mockImplementation(async (input, options) => {
    const url = new URL(String(input), 'http://localhost')
    const path = url.pathname
    if (path.endsWith('/session/me')) return json({ username: 'admin', csrf_token: 'csrf' })
    if (path.endsWith('/session/logout')) return new Response(null, { status: 204 })
    if (path.endsWith('/stock-movements')) {
      const offset = Number(url.searchParams.get('offset') ?? 0)
      const items = history.slice(offset, offset + 20)
      return json({ items, limit: 20, offset, count: items.length, has_more: history.length > offset + 20 })
    }
    if (path.startsWith('/api/backoffice/stock-adjustments/')) {
      const result = movementByOperation.get(decodeURIComponent(path.split('/').at(-1)!))
      return result ? json(result) : json({ detail: 'private' }, 404)
    }
    if (path.endsWith('/stock-adjustments') && options?.method === 'POST') {
      if (postMode === 'unauthorized') return json({}, 401)
      if (postMode === 'forbidden') return json({}, 403)
      if (postMode === 'reject-409') return json({}, 409)
      const body = JSON.parse(String(options.body)) as { operation_id: string; delta: number; reason: string }
      const result = movement(body.operation_id, body.delta, body.reason)
      if (postMode === 'commit' || postMode === 'uncertain-commit') {
        product.stock = result.stock_after
        history.unshift(result)
        movementByOperation.set(result.operation_id, result)
      }
      if (postMode === 'uncertain-commit' || postMode === 'uncertain-abort') {
        throw new Error('network details are private')
      }
      return json(result)
    }
    if (path.startsWith('/api/backoffice/products/')) {
      if (path.endsWith('/stock-movements')) throw new Error('route order should match above')
      return json(product.sku === decodeURIComponent(path.split('/').at(-1)!)
        ? product : { detail: 'private' }, product.sku === decodeURIComponent(path.split('/').at(-1)!) ? 200 : 404)
    }
    throw new Error(`Unexpected admin request ${path}`)
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function open(path = '/admin/products/SKU-1/stock') {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/admin/login', component: AdminLoginPage },
      { path: '/admin/products/:sku/stock', component: AdminProductStockPage, meta: { admin: true } },
      { path: '/admin/products', component: { template: '<p>Products</p>' }, meta: { admin: true } },
    ],
  })
  protectAdminRoutes(router)
  await router.push(path)
  wrapper = mount(RouterView, { global: { plugins: [router] } })
  await flushPromises()
  return router
}

function button(label: string) {
  return wrapper!.findAll('button').find(value => value.text() === label)!
}

async function preview(delta: string, reason = '  Inventory recount  ') {
  await wrapper!.get('#stock-delta').setValue(delta)
  await wrapper!.get('#stock-reason').setValue(reason)
  await wrapper!.get('form').trigger('submit')
  await flushPromises()
}

async function confirm() {
  await wrapper!.get('input[type="checkbox"]').setValue(true)
  await button('Confirmer l’ajustement').trigger('click')
  await flushPromises()
}

function posts() {
  return fetchMock.mock.calls.filter(([, options]) => options?.method === 'POST')
}

it('previews and confirms a positive adjustment once, then trusts server stock and history', async () => {
  await open()
  expect(wrapper!.text()).toContain('stock serveur : 5')
  await preview('4')
  expect(wrapper!.text()).toContain('Stock estimé : 5 → 9')
  expect(wrapper!.text()).toContain('Motif : Inventory recount')
  expect(posts()).toHaveLength(0)
  await button('Confirmer l’ajustement').trigger('click')
  await flushPromises()
  expect(posts()).toHaveLength(0)
  await confirm()
  expect(posts()).toHaveLength(1)
  const [url, options] = posts()[0]!
  expect(url).toBe('/api/backoffice/products/SKU-1/stock-adjustments')
  expect(options?.credentials).toBe('include')
  expect(new Headers(options?.headers).get('X-Admin-CSRF')).toBe('csrf')
  expect(new Headers(options?.headers).has('Authorization')).toBe(false)
  expect(JSON.parse(String(options?.body))).toEqual({
    operation_id: operationId, delta: 4, reason: 'Inventory recount',
  })
  expect(wrapper!.text()).toContain('Ajustement enregistré : stock 5 → 9.')
  expect(wrapper!.text()).toContain('stock serveur : 9')
  expect(wrapper!.text()).toContain('Historique des mouvements')
  expect(wrapper!.text()).toContain('Inventory recount')
})

it('previews a negative adjustment and validates reason, integer and nonnegative estimate', async () => {
  await open()
  await wrapper!.get('#stock-delta').setValue('-4')
  await wrapper!.get('#stock-reason').setValue('  ')
  await wrapper!.get('form').trigger('submit')
  await flushPromises()
  expect(wrapper!.text()).toContain('stock final estimé positif ou nul requis')
  expect(posts()).toHaveLength(0)
  await preview('-4', 'damage')
  expect(wrapper!.text()).toContain('Stock estimé : 5 → 1')
  await confirm()
  expect(posts()).toHaveLength(1)
  expect(JSON.parse(String(posts()[0]![1]?.body))).toMatchObject({ delta: -4, reason: 'damage' })
  expect(wrapper!.text()).toContain('stock serveur : 1')
})

it('a definitive insufficient-stock rejection reconciles without automatic retry', async () => {
  postMode = 'reject-409'
  await open()
  await preview('-3', 'count')
  await confirm()
  expect(posts()).toHaveLength(1)
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/stock-adjustments/'))).toHaveLength(1)
  expect(wrapper!.text()).toContain('stock serveur : 5')
  expect(wrapper!.text()).toContain('Ajustement refusé')
  expect(wrapper!.find('#stock-delta').attributes('disabled')).toBeUndefined()
})

it('keeps an ambiguous absent operation blocked and reuses the same ID for manual reconciliation', async () => {
  postMode = 'uncertain-abort'
  await open()
  await preview('2', 'count')
  await confirm()
  expect(posts()).toHaveLength(1)
  expect(wrapper!.text()).toContain('Résultat toujours inconnu')
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
  const lookups = fetchMock.mock.calls.filter(([url]) => String(url).includes('/stock-adjustments/')).length
  await button('Vérifier le résultat').trigger('click')
  await flushPromises()
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/stock-adjustments/'))).toHaveLength(lookups + 1)
  expect(posts()).toHaveLength(1)
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
  const lookupPath = fetchMock.mock.calls.filter(([url]) => String(url).includes('/stock-adjustments/')).at(-1)?.[0]
  expect(lookupPath).toBe(`/api/backoffice/stock-adjustments/${operationId}`)
})

it('reconciles an ambiguous committed operation and reads current stock', async () => {
  postMode = 'uncertain-commit'
  await open()
  await preview('3', 'delivery')
  await confirm()
  expect(posts()).toHaveLength(1)
  expect(wrapper!.text()).toContain('Ajustement retrouvé dans l’historique')
  expect(wrapper!.text()).toContain('stock serveur : 8')
  expect(wrapper!.get('#stock-delta').attributes('disabled')).toBeUndefined()
})

it.each([
  [401, 'unauthorized', '/admin/login'],
  [403, 'forbidden', '/admin/products/SKU-1/stock'],
] as const)('handles session/security rejection %i without retry', async (_status, mode, target) => {
  postMode = mode
  await open()
  await preview('1', 'count')
  await confirm()
  expect(router.currentRoute.value.path).toBe(target)
  expect(posts()).toHaveLength(1)
})

it('handles a missing product without exposing response details', async () => {
  product = { ...product, sku: 'OTHER' }
  await open('/admin/products/MISSING/stock')
  expect(wrapper!.text()).toContain('Produit introuvable.')
  expect(wrapper!.text()).not.toContain('private')
  expect(posts()).toHaveLength(0)
})

it('shows stable history pagination based on has_more', async () => {
  history = Array.from({ length: 21 }, (_, index) => ({
    movement_id: `m-${index}`,
    operation_id: `4d22787c-a342-4c9c-b2b6-${String(index).padStart(12, '0')}`,
    product_sku: 'SKU-1',
    delta: 1,
    stock_before: index,
    stock_after: index + 1,
    reason: `reason ${index}`,
    occurred_at: '2026-10-10T10:00:00Z',
    source: 'BACKOFFICE' as const,
  }))
  await open()
  expect(button('Suivant').attributes('disabled')).toBeUndefined()
  await button('Suivant').trigger('click')
  await flushPromises()
  expect(wrapper!.text()).toContain('Page 2')
  expect(wrapper!.text()).toContain('reason 20')
  expect(button('Suivant').attributes('disabled')).toBeDefined()
})

it('ignores a stale product response after changing SKU route', async () => {
  let resolveOld: ((response: Response) => void) | undefined
  fetchMock.mockImplementation(async (input, options) => {
    const path = new URL(String(input), 'http://localhost').pathname
    if (path.endsWith('/session/me')) return json({ username: 'admin', csrf_token: 'csrf' })
    if (path.endsWith('/stock-movements')) {
      return json({ items: [], limit: 20, offset: 0, count: 0, has_more: false })
    }
    if (path === '/api/backoffice/products/SKU-1') {
      return new Promise<Response>(resolve => { resolveOld = resolve })
    }
    if (path === '/api/backoffice/products/SKU-2') {
      return json({ ...product, sku: 'SKU-2', product_name: 'Second drill', stock: 12 })
    }
    if (options?.method === 'POST') throw new Error('unexpected write')
    return json({ items: [], limit: 20, offset: 0, count: 0, has_more: false })
  })
  await open()
  await router.push('/admin/products/SKU-2/stock')
  await flushPromises()
  expect(wrapper!.text()).toContain('Second drill')
  resolveOld?.(json(product))
  await flushPromises()
  expect(wrapper!.text()).toContain('Second drill')
  expect(wrapper!.text()).not.toContain('stock serveur : 5')
})

it.each(['0', '1.5', '1e3', '1000001', '-6', 'abc', ''])('refuses invalid delta %s without a POST', async delta => {
  await open()
  await preview(delta, 'count')
  expect(posts()).toHaveLength(0)
  expect(wrapper!.find('input[type="checkbox"]').exists()).toBe(false)
})

it.each(['', '  ', 'x'.repeat(501)])('refuses an invalid reason without a POST', async reason => {
  await open()
  await preview('1', reason)
  expect(posts()).toHaveLength(0)
  expect(wrapper!.find('input[type="checkbox"]').exists()).toBe(false)
})

it('requires confirmation again after editing a preview', async () => {
  await open()
  await preview('1', 'count')
  await wrapper!.get('input[type="checkbox"]').setValue(true)
  await button('Modifier').trigger('click')
  await preview('2', 'new reason')
  expect(button('Confirmer l’ajustement').attributes('disabled')).toBeDefined()
  expect(posts()).toHaveLength(0)
})

it('guards double clicks and preserves one operation identifier while awaiting a POST', async () => {
  const original = fetchMock.getMockImplementation()!
  let release: ((response: Response) => void) | undefined
  fetchMock.mockImplementation((input, options) => options?.method === 'POST'
    ? new Promise(resolve => { release = resolve })
    : original(input, options))
  await open()
  await preview('2', 'count')
  await wrapper!.get('input[type="checkbox"]').setValue(true)
  const submit = button('Confirmer l’ajustement')
  await submit.trigger('click')
  await submit.trigger('click')
  expect(posts()).toHaveLength(1)
  expect(readPendingStockAdjustment()).toEqual({ sku: 'SKU-1', delta: 2, reason: 'count', operationId })
  const result = movement(operationId, 2, 'count')
  product.stock = 7
  release?.(json(result))
  await flushPromises()
  expect(readPendingStockAdjustment()).toBeNull()
})

it('retains uncertain operation across remount and blocks a different SKU', async () => {
  postMode = 'uncertain-abort'
  await open()
  await preview('2', 'count')
  await confirm()
  wrapper!.unmount()
  await open()
  expect(wrapper!.text()).toContain(operationId)
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
  product = { ...product, sku: 'SKU-2' }
  await router.push('/admin/products/SKU-2/stock')
  await flushPromises()
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
  expect(wrapper!.text()).toContain('Revenir au produit dont l’opération est incertaine')
  expect(posts()).toHaveLength(1)
})

it('restores a pending committed operation without another POST', async () => {
  savePendingStockAdjustment({ sku: 'SKU-1', operationId, delta: 2, reason: 'count' })
  const result = movement(operationId, 2, 'count')
  product.stock = 7
  history = [result]
  movementByOperation.set(operationId, result)
  await open()
  expect(wrapper!.text()).toContain('Ajustement retrouvé dans l’historique')
  expect(wrapper!.text()).toContain('stock serveur : 7')
  expect(readPendingStockAdjustment()).toBeNull()
  expect(posts()).toHaveLength(0)
})

it('never unlocks corrupt pending storage, including after a manual refresh', async () => {
  sessionStorage.setItem('stockpilot.pending-stock-adjustment.v1', '{invalid')
  await open()
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
  expect(wrapper!.text()).toContain('Rapprochement manuel requis')
  await button('Actualiser').trigger('click')
  await flushPromises()
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
  expect(posts()).toHaveLength(0)
})

it('does not send when operation ID persistence fails', async () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('storage unavailable') })
  await open()
  await preview('2', 'count')
  await confirm()
  expect(posts()).toHaveLength(0)
  expect(wrapper!.text()).toContain('Aucun ajustement n’a été envoyé')
})

it('rejects a reconciliation movement with a different payload and retains the pending operation', async () => {
  postMode = 'uncertain-abort'
  await open()
  await preview('2', 'count')
  await confirm()
  movementByOperation.set(operationId, movement(operationId, 3, 'other'))
  await button('Vérifier le résultat').trigger('click')
  await flushPromises()
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
  expect(readPendingStockAdjustment()?.operationId).toBe(operationId)
  expect(posts()).toHaveLength(1)
})

it('requires a current product read even when the operation is found', async () => {
  postMode = 'uncertain-abort'
  await open()
  await preview('2', 'count')
  await confirm()
  movementByOperation.set(operationId, movement(operationId, 2, 'count'))
  const original = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation((input, options) => String(input) === '/api/backoffice/products/SKU-1'
    ? Promise.resolve(json({}, 500)) : original(input, options))
  await button('Vérifier le résultat').trigger('click')
  await flushPromises()
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
  expect(posts()).toHaveLength(1)
  fetchMock.mockImplementation(original)
  await button('Actualiser').trigger('click')
  await flushPromises()
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeUndefined()
})

it('a 20-second timeout reconciles without a second POST and remains blocked if absent', async () => {
  const original = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation((input, options) => {
    if (options?.method !== 'POST') return original(input, options)
    return new Promise((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    })
  })
  await open()
  vi.useFakeTimers()
  await preview('2', 'count')
  await wrapper!.get('input[type="checkbox"]').setValue(true)
  await button('Confirmer l’ajustement').trigger('click')
  await vi.advanceTimersByTimeAsync(20_000)
  await flushPromises()
  expect(wrapper!.text()).toContain('Résultat toujours inconnu')
  expect(button('Vérifier le résultat').attributes('disabled')).toBeUndefined()
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
  expect(posts()).toHaveLength(1)
  expect(readPendingStockAdjustment()?.operationId).toBe(operationId)
})
