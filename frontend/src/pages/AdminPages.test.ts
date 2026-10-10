import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import AdminLoginPage from './AdminLoginPage.vue'
import AdminOrdersPage from './AdminOrdersPage.vue'
import AdminOrderDetailPage from './AdminOrderDetailPage.vue'
import { protectAdminRoutes } from '../router/adminGuard'
import { adminMe } from '../api/admin'

const fetchMock = vi.fn<typeof fetch>()
let wrapper: VueWrapper | undefined
const order = {
  order_id: 'admin-order', customer_id: 'other-customer', product_name: 'Bosch X100',
  quantity: 2, status: 'PROCESSING', created_at: '2026-10-10T10:00:00Z',
  events: [
    { status: 'CREATED', occurred_at: '2026-10-10T10:00:00Z', source: 'SYSTEM' },
    { status: 'PROCESSING', occurred_at: '2026-10-10T10:05:00Z', source: 'BACKOFFICE' },
  ],
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const page = (items: unknown[] = [order], offset = 0, count = items.length) => ({ items, offset, count, limit: 20 })
beforeEach(() => {
  fetchMock.mockReset()
  fetchMock.mockImplementation(async (input) => {
    const url = String(input)
    if (url.includes('/session/')) return json({ username: 'admin', csrf_token: 'csrf' })
    if (url.includes('/orders?')) return json(page())
    return json(order)
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.unstubAllGlobals() })
async function open(path: string, guarded = true) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/admin/login', component: AdminLoginPage },
      { path: '/admin/orders', component: AdminOrdersPage, meta: { admin: true } },
      { path: '/admin/orders/:orderId', component: AdminOrderDetailPage, meta: { admin: true } },
      { path: '/orders', component: { template: '<div>Customer</div>' } },
    ],
  })
  if (guarded) protectAdminRoutes(router)
  await router.push(path)
  wrapper = mount(RouterView, { global: { plugins: [router] } })
  await flushPromises()
  return router
}
function button(text: string) {
  return wrapper!.findAll('button').find((element) => element.text() === text)!
}

it.each(['/admin/orders', '/admin/orders/admin-order'])('verifies session before protected route %s', async (path) => {
  fetchMock.mockResolvedValue(json({}, 401))
  const router = await open(path)
  expect(router.currentRoute.value.path).toBe('/admin/login')
  expect(wrapper!.text()).toContain('session absente ou expirée')
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(fetchMock.mock.calls[0]![0]).toBe('/api/backoffice/session/me')
})

it('fails closed when session verification is unavailable but never guards customer routes', async () => {
  fetchMock.mockResolvedValue(json({}, 503))
  const router = await open('/admin/orders')
  expect(wrapper!.text()).toContain('Vérification de session indisponible')
  await router.push('/orders')
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it('login clears password immediately, submits once and navigates after server verification', async () => {
  const router = await open('/admin/login')
  await wrapper!.get('#admin-username').setValue('admin')
  await wrapper!.get('#admin-password').setValue('test-only-password-long')
  await wrapper!.get('form').trigger('submit')
  expect((wrapper!.get('#admin-password').element as HTMLInputElement).value).toBe('')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/admin/orders')
  expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
    '/api/backoffice/session/login', '/api/backoffice/session/me', '/api/backoffice/orders?limit=20&offset=0',
  ])
})

it.each([401, 429, 503])('login errors %i keep credentials out of UI', async (status) => {
  fetchMock.mockResolvedValue(json({ detail: 'private-password' }, status))
  await open('/admin/login')
  await wrapper!.get('#admin-username').setValue('admin')
  await wrapper!.get('#admin-password').setValue('test-only-password-long')
  await wrapper!.get('form').trigger('submit')
  await flushPromises()
  expect(wrapper!.find('[role="alert"]').exists()).toBe(true)
  expect(wrapper!.html()).not.toContain('private-password')
  expect((wrapper!.get('#admin-password').element as HTMLInputElement).value).toBe('')
})

it('list renders customer, date and badge with read-only navigation', async () => {
  const router = await open('/admin/orders')
  expect(wrapper!.text()).toContain('other-customer')
  expect(wrapper!.text()).toContain('Quantité : 2')
  expect(wrapper!.get('time').attributes('datetime')).toBe(order.created_at)
  expect(wrapper!.text()).toContain('PROCESSING')
  expect(button('Suivant').attributes('disabled')).toBeDefined()
  expect(button('Précédent').attributes('disabled')).toBeDefined()
  await wrapper!.get('article a').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/admin/orders/admin-order')
  expect(wrapper!.text()).toContain('BACKOFFICE')
  expect(wrapper!.findAll('button').map((item) => item.text())).toEqual([
    'Se déconnecter', 'Actualiser', 'Passer à SHIPPED', 'Annuler la commande',
  ])
})

it('detail preserves real timeline and legacy null owner/empty events', async () => {
  await open('/admin/orders/admin-order')
  expect(wrapper!.findAll('.order-timeline li')).toHaveLength(2)
  expect(wrapper!.text()).toContain('SYSTEM')
  fetchMock.mockResolvedValue(json({ ...order, customer_id: null, events: [] }))
  await button('Actualiser').trigger('click')
  await flushPromises()
  expect(wrapper!.text()).toContain('Non renseigné (legacy)')
  expect(wrapper!.text()).toContain('historique détaillé n’est pas disponible')
})

it('pagination preserves offsets, complete page permits next, empty page permits previous', async () => {
  fetchMock.mockImplementation(async (input) => String(input).includes('/session/')
    ? json({ username: 'admin', csrf_token: 'csrf' })
    : json(String(input).includes('offset=20') ? page([], 20) : page([order], 0, 20)))
  const router = await open('/admin/orders')
  await button('Suivant').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.query.offset).toBe('20')
  expect(wrapper!.text()).toContain('Aucune commande sur cette page')
  expect(button('Suivant').attributes('disabled')).toBeDefined()
  await button('Précédent').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.query.offset).toBeUndefined()
})

it.each(['-1', 'abc', '9007199254740992'])('rejects invalid pagination %s without read', async (offset) => {
  await open(`/admin/orders?offset=${offset}`)
  expect(wrapper!.text()).toContain('Pagination invalide')
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it.each(['/admin/orders', '/admin/orders/admin-order'])('expired read redirects %s to login', async (path) => {
  fetchMock.mockImplementation(async (input) => String(input).includes('/session/')
    ? json({ username: 'admin', csrf_token: 'csrf' }) : json({}, 401))
  const router = await open(path)
  expect(router.currentRoute.value.path).toBe('/admin/login')
  expect(wrapper!.find('article').exists()).toBe(false)
})

it.each([404, 503])('detail handles read error %i without fake order data', async (status) => {
  fetchMock.mockImplementation(async (input) => String(input).includes('/session/')
    ? json({ username: 'admin', csrf_token: 'csrf' }) : json({}, status))
  await open('/admin/orders/missing')
  expect(wrapper!.find('[role="alert"]').exists()).toBe(true)
  expect(wrapper!.find('article').exists()).toBe(false)
})

it('logout really calls endpoint with CSRF and redirects only after success', async () => {
  const router = await open('/admin/orders')
  fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
  await button('Se déconnecter').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/admin/login')
  const [path, options] = fetchMock.mock.calls.at(-1)!
  expect(path).toBe('/api/backoffice/session/logout')
  expect(new Headers(options!.headers).get('X-Admin-CSRF')).toBe('csrf')
})

it('failed logout preserves page and shows an error', async () => {
  const router = await open('/admin/orders')
  fetchMock.mockResolvedValue(json({}, 403))
  await button('Se déconnecter').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/admin/orders')
  expect(wrapper!.find('[role="alert"]').exists()).toBe(true)
})

it('loading state blocks duplicate submission and clears on error', async () => {
  let resolve!: (response: Response) => void
  fetchMock.mockReturnValue(new Promise<Response>((done) => { resolve = done }))
  await open('/admin/login')
  await wrapper!.get('#admin-username').setValue('admin')
  await wrapper!.get('#admin-password').setValue('test-only-password-long')
  await wrapper!.get('form').trigger('submit')
  await wrapper!.get('form').trigger('submit')
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(button('Connexion…').attributes('disabled')).toBeDefined()
  resolve(json({}, 401))
  await flushPromises()
  expect(button('Se connecter').attributes('disabled')).toBeUndefined()
})

it('detail aborts outdated requests on route change and unmount', async () => {
  await open('/admin/orders/admin-order')
  fetchMock.mockReturnValue(new Promise<Response>(() => {}))
  await button('Actualiser').trigger('click')
  const signal = fetchMock.mock.calls.at(-1)![1]!.signal!
  expect(wrapper!.text()).toContain('Chargement de la commande')
  wrapper!.unmount()
  wrapper = undefined
  expect(signal.aborted).toBe(true)
})

it('list errors discard stale data and manual refresh can recover', async () => {
  await open('/admin/orders')
  fetchMock.mockResolvedValue(json({}, 503))
  await button('Actualiser').trigger('click')
  await flushPromises()
  expect(wrapper!.find('article').exists()).toBe(false)
  expect(wrapper!.find('[role="alert"]').exists()).toBe(true)
  fetchMock.mockResolvedValue(json(page()))
  await button('Actualiser').trigger('click')
  await flushPromises()
  expect(wrapper!.find('article').exists()).toBe(true)
  expect(wrapper!.find('[role="alert"]').exists()).toBe(false)
})

it.each([
  ['CREATED', 'PROCESSING', true], ['PROCESSING', 'SHIPPED', true],
  ['SHIPPED', 'DELIVERED', false], ['DELIVERED', null, false], ['CANCELLED', null, false],
])('detail exposes only valid actions for %s', async (status, next, cancel) => {
  fetchMock.mockImplementation(async input => String(input).includes('/session/')
    ? json({ username: 'admin', csrf_token: 'csrf' }) : json({ ...order, status }))
  await open('/admin/orders/admin-order')
  const actions = wrapper!.findAll('button').map(item => item.text())
  expect(actions.filter(text => text.startsWith('Passer à'))).toEqual(next ? [`Passer à ${next}`] : [])
  expect(actions.includes('Annuler la commande')).toBe(cancel)
})

it('cancel requires explicit confirmation, decline sends nothing, success uses server timeline', async () => {
  await open('/admin/orders/admin-order')
  const before = fetchMock.mock.calls.length
  await button('Annuler la commande').trigger('click')
  expect(wrapper!.text()).toContain('Le stock réservé sera restitué')
  await button('Conserver la commande').trigger('click')
  expect(fetchMock).toHaveBeenCalledTimes(before)
  await button('Annuler la commande').trigger('click')
  const events = [...order.events, { status: 'CANCELLED', source: 'BACKOFFICE', occurred_at: '2026-10-10T10:10:00Z' }]
  fetchMock.mockResolvedValue(json({ ...order, status: 'CANCELLED', events }))
  await button('Confirmer l’annulation').trigger('click')
  await flushPromises()
  expect(fetchMock.mock.calls.at(-1)![0]).toBe('/api/backoffice/orders/admin-order/cancel')
  expect(fetchMock.mock.calls.at(-1)![1]!.method).toBe('POST')
  expect(wrapper!.findAll('.order-timeline li')).toHaveLength(3)
  expect(wrapper!.text()).toContain('Commande annulée.')
  expect(wrapper!.findAll('button').map(item => item.text())).toEqual(['Se déconnecter', 'Actualiser'])
})

it('blocks double and cross mutation plus refresh while pending; no optimistic updates', async () => {
  await open('/admin/orders/admin-order')
  let resolve!: (response: Response) => void
  fetchMock.mockReturnValue(new Promise<Response>(done => { resolve = done }))
  await button('Passer à SHIPPED').trigger('click')
  await button('Passer à SHIPPED').trigger('click')
  await button('Annuler la commande').trigger('click')
  expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(1)
  expect(button('Actualiser').attributes('disabled')).toBeDefined()
  expect(wrapper!.text()).toContain('(PROCESSING)')
  resolve(json({ ...order, status: 'SHIPPED', events: [] }))
  await flushPromises()
  expect(wrapper!.text()).toContain('Statut mis à jour.')
  expect(button('Passer à DELIVERED').attributes('disabled')).toBeUndefined()
})

it.each([0, 409, 500])('ambiguous failure %i reconciles via GET without mutation replay', async status => {
  await open('/admin/orders/admin-order')
  let resolve!: (response: Response) => void
  if (status === 0) fetchMock.mockRejectedValueOnce(new TypeError('network-private'))
  else fetchMock.mockResolvedValueOnce(json({}, status))
  fetchMock.mockReturnValueOnce(new Promise<Response>(done => { resolve = done }))
  await button('Passer à SHIPPED').trigger('click')
  await flushPromises()
  expect(button('Passer à SHIPPED').attributes('disabled')).toBeDefined()
  expect(wrapper!.text()).toContain('Actions bloquées')
  expect(fetchMock.mock.calls.at(-1)![1]!.method).toBe('GET')
  resolve(json({ ...order, status: 'SHIPPED', events: [] }))
  await flushPromises()
  expect(button('Passer à DELIVERED').attributes('disabled')).toBeUndefined()
  expect(wrapper!.text()).toContain('Statut vérifié auprès du serveur')
  expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(1)
})

it('failed reconciliation stays blocked until a reliable manual read', async () => {
  await open('/admin/orders/admin-order')
  fetchMock.mockResolvedValueOnce(json({}, 409)).mockResolvedValueOnce(json({}, 503))
  await button('Passer à SHIPPED').trigger('click')
  await flushPromises()
  expect(button('Passer à SHIPPED').attributes('disabled')).toBeDefined()
  fetchMock.mockResolvedValueOnce(json({ ...order, status: 'CREATED' }))
  await button('Actualiser').trigger('click')
  await flushPromises()
  expect(button('Passer à PROCESSING').attributes('disabled')).toBeUndefined()
})

it.each([401, 403, 404])('mutation error %i never invents success or retries', async status => {
  const router = await open('/admin/orders/admin-order')
  const before = fetchMock.mock.calls.length
  fetchMock.mockResolvedValueOnce(json({}, status))
  await button('Passer à SHIPPED').trigger('click')
  await flushPromises()
  expect(fetchMock.mock.calls.slice(before).filter(([, options]) => options?.method === 'PATCH')).toHaveLength(1)
  if (status === 401) expect(router.currentRoute.value.path).toBe('/admin/login')
  else {
    expect(wrapper!.find('[role="alert"]').exists()).toBe(true)
    if (status === 403) expect(button('Passer à SHIPPED').attributes('disabled')).toBeDefined()
    else expect(wrapper!.find('article').exists()).toBe(false)
  }
  expect(wrapper!.text()).not.toContain('Statut mis à jour.')
})

it('timeout aborts mutation and reconciles without replay', async () => {
  await open('/admin/orders/admin-order')
  vi.useFakeTimers()
  try {
    fetchMock.mockImplementationOnce((_input, options) => new Promise<Response>((_resolve, reject) => {
      options!.signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }))
    fetchMock.mockResolvedValueOnce(json({ ...order, status: 'SHIPPED' }))
    await button('Passer à SHIPPED').trigger('click')
    await vi.advanceTimersByTimeAsync(20_000)
    await flushPromises()
    expect(button('Passer à DELIVERED').attributes('disabled')).toBeUndefined()
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'PATCH')).toHaveLength(1)
    expect(fetchMock.mock.calls.find(([, options]) => options?.method === 'PATCH')![1]!.signal!.aborted).toBe(true)
  } finally { vi.useRealTimers() }
})

it('route change discards a late mutation even if fetch ignores abort', async () => {
  const router = await open('/admin/orders/admin-order')
  let resolve!: (response: Response) => void
  fetchMock.mockReturnValueOnce(new Promise<Response>(done => { resolve = done }))
  await button('Passer à SHIPPED').trigger('click')
  const signal = fetchMock.mock.calls.at(-1)![1]!.signal!
  fetchMock.mockImplementation(async input => String(input).includes('/session/')
    ? json({ username: 'admin', csrf_token: 'csrf' })
    : json({ ...order, order_id: 'other-order', status: 'CREATED' }))
  await router.push('/admin/orders/other-order')
  await flushPromises()
  expect(signal.aborted).toBe(true)
  resolve(json({ ...order, status: 'SHIPPED' }))
  await flushPromises()
  expect(wrapper!.text()).toContain('other-order')
  expect(button('Passer à PROCESSING').attributes('disabled')).toBeUndefined()
  expect(wrapper!.text()).not.toContain('Statut mis à jour.')
})

it('logout aborts pending mutation and late success cannot restore the detail', async () => {
  const router = await open('/admin/orders/admin-order')
  let resolve!: (response: Response) => void
  fetchMock.mockReturnValueOnce(new Promise<Response>(done => { resolve = done }))
  await button('Passer à SHIPPED').trigger('click')
  const signal = fetchMock.mock.calls.at(-1)![1]!.signal!
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
  await button('Se déconnecter').trigger('click')
  await flushPromises()
  expect(signal.aborted).toBe(true)
  expect(router.currentRoute.value.path).toBe('/admin/login')
  resolve(json({ ...order, status: 'SHIPPED' }))
  await flushPromises()
  expect(wrapper!.find('article').exists()).toBe(false)
})

it('expiry discovered during a pending mutation invalidates its late success', async () => {
  const router = await open('/admin/orders/admin-order')
  let resolve!: (response: Response) => void
  fetchMock.mockReturnValueOnce(new Promise<Response>(done => { resolve = done }))
  await button('Passer à SHIPPED').trigger('click')
  fetchMock.mockResolvedValueOnce(json({}, 401))
  await expect(adminMe()).rejects.toMatchObject({ status: 401 })
  resolve(json({ ...order, status: 'SHIPPED' }))
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/admin/login')
  expect(wrapper!.find('article').exists()).toBe(false)
  expect(wrapper!.text()).not.toContain('Statut mis à jour.')
})

it('failed logout blocks mutations until an authenticated detail refresh', async () => {
  await open('/admin/orders/admin-order')
  fetchMock.mockResolvedValueOnce(json({}, 403))
  await button('Se déconnecter').trigger('click')
  await flushPromises()
  expect(button('Passer à SHIPPED').attributes('disabled')).toBeDefined()
  fetchMock.mockResolvedValueOnce(json(order))
  await button('Actualiser').trigger('click')
  await flushPromises()
  expect(button('Passer à SHIPPED').attributes('disabled')).toBeUndefined()
})

it.each([401, 404])('reconciliation error %i clears stale detail', async status => {
  const router = await open('/admin/orders/admin-order')
  fetchMock.mockResolvedValueOnce(json({}, 409)).mockResolvedValueOnce(json({}, status))
  await button('Passer à SHIPPED').trigger('click')
  await flushPromises()
  expect(wrapper!.find('article').exists()).toBe(false)
  if (status === 401) expect(router.currentRoute.value.path).toBe('/admin/login')
})

it('unmount aborts pending mutation and clears its timeout', async () => {
  await open('/admin/orders/admin-order')
  vi.useFakeTimers()
  try {
    fetchMock.mockReturnValueOnce(new Promise<Response>(() => {}))
    await button('Passer à SHIPPED').trigger('click')
    const signal = fetchMock.mock.calls.at(-1)![1]!.signal!
    expect(vi.getTimerCount()).toBe(1)
    wrapper!.unmount()
    wrapper = undefined
    expect(signal.aborted).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
  } finally { vi.useRealTimers() }
})
