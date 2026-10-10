import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import AdminLoginPage from './AdminLoginPage.vue'
import AdminOrdersPage from './AdminOrdersPage.vue'
import AdminOrderDetailPage from './AdminOrderDetailPage.vue'
import { protectAdminRoutes } from '../router/adminGuard'

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
  expect(wrapper!.findAll('button').map((item) => item.text())).toEqual(['Se déconnecter', 'Actualiser'])
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
