import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import AdminProductsPage from './AdminProductsPage.vue'
import AdminProductFormPage from './AdminProductFormPage.vue'
import AdminLoginPage from './AdminLoginPage.vue'
import { protectAdminRoutes } from '../router/adminGuard'
import { adminMe, createAdminProduct, updateAdminProduct, listAdminProducts } from '../api/admin'
import App from '../App.vue'

const fetchMock = vi.fn<typeof fetch>()
let wrapper: VueWrapper | undefined
const product = { sku: 'SKU-1', product_name: 'Drill', category: 'DRILL', price: 12.5, stock: 7 }
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
function products(has_more = false, offset = 0, items: unknown[] = [product]) {
  return { items, count: items.length, limit: 20, offset, has_more }
}
beforeEach(() => {
  fetchMock.mockReset()
  fetchMock.mockImplementation(async (input, options) => {
    const url = String(input)
    if (url.includes('/session/')) return json({ username: 'admin', csrf_token: 'bound-csrf' })
    if (url.includes('/products?')) return json(products())
    if (options?.method === 'POST') return json({ ...product, stock: 0 }, 201)
    return json(product)
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.unstubAllGlobals(); vi.useRealTimers() })
async function open(path: string, app = false) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/admin/login', component: AdminLoginPage },
      { path: '/admin/orders', component: { template: '<p>Admin orders</p>' }, meta: { admin: true } },
      { path: '/admin/products', component: AdminProductsPage, meta: { admin: true } },
      { path: '/admin/products/new', component: AdminProductFormPage, meta: { admin: true } },
      { path: '/admin/products/:sku/edit', component: AdminProductFormPage, meta: { admin: true } },
      { path: '/admin/products/:sku/stock', component: { template: '<p>Stock adjustment</p>' }, meta: { admin: true } },
      { path: '/assistant', component: { template: '<p>Assistant</p>' } },
      { path: '/products', component: { template: '<p>Catalogue</p>' } },
      { path: '/orders', component: { template: '<p>Orders</p>' } },
    ],
  })
  protectAdminRoutes(router)
  await router.push(path)
  wrapper = mount(app ? App : RouterView, { global: { plugins: [router] } })
  await flushPromises()
  return router
}
function button(text: string) {
  return wrapper!.findAll('button').find(element => element.text() === text)!
}
async function fill() {
  await wrapper!.get('#product-sku').setValue('SKU-1')
  await wrapper!.get('#product-name').setValue('Drill')
  await wrapper!.get('#product-category').setValue('DRILL')
  await wrapper!.get('#product-price').setValue('12.5')
}
function writes() { return fetchMock.mock.calls.filter(([, options]) => ['POST', 'PATCH'].includes(options?.method ?? '')) }

it.each(['/admin/products', '/admin/products/new', '/admin/products/SKU-1/edit'])('protects route %s before reads', async path => {
  fetchMock.mockResolvedValue(json({}, 401))
  const router = await open(path)
  expect(router.currentRoute.value.path).toBe('/admin/login')
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it('lists metadata with stock read-only, edit links and authoritative has_more', async () => {
  await open('/admin/products')
  expect(wrapper!.text()).toContain('Stock : 7 (lecture seule)')
  expect(wrapper!.find('a[href="/admin/products/SKU-1/edit"]').text()).toContain('Drill')
  expect(wrapper!.find('a[href="/admin/products/SKU-1/stock"]').text()).toContain('Ajuster le stock de Drill')
  expect(button('Suivant').attributes('disabled')).toBeDefined()
  expect(wrapper!.findAll('input')).toHaveLength(0)
  expect(writes()).toHaveLength(0)
})

it('paginates only when has_more, reloads and handles empty pages', async () => {
  fetchMock.mockImplementation(async input => String(input).includes('/session/')
    ? json({ username: 'admin', csrf_token: 'csrf' })
    : json(String(input).includes('offset=20') ? products(false, 20, []) : products(true)))
  const router = await open('/admin/products')
  await button('Suivant').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.query.offset).toBe('20')
  expect(wrapper!.text()).toContain('Aucun produit')
  expect(button('Suivant').attributes('disabled')).toBeDefined()
  await button('Actualiser').trigger('click')
  await flushPromises()
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('offset=20')).length).toBe(2)
})

it('invalid pagination never reads catalogue', async () => {
  await open('/admin/products?offset=-1')
  expect(wrapper!.text()).toContain('Pagination invalide')
  expect(fetchMock.mock.calls).toHaveLength(1)
})

it.each([500, 503])('list errors are sanitized and can be refreshed (%i)', async status => {
  fetchMock.mockImplementation(async input => String(input).includes('/session/')
    ? json({ username: 'admin', csrf_token: 'csrf' }) : json({ detail: 'private' }, status))
  await open('/admin/products')
  expect(wrapper!.find('[role="alert"]').exists()).toBe(true)
  expect(wrapper!.text()).not.toContain('private')
  fetchMock.mockResolvedValue(json(products()))
  await button('Actualiser').trigger('click')
  await flushPromises()
  expect(wrapper!.text()).toContain('Drill')
})

it('creates once with cookie CSRF and whitelist, then prevents repeated creation', async () => {
  await open('/admin/products/new')
  await fill()
  await wrapper!.get('form').trigger('submit')
  await wrapper!.get('form').trigger('submit')
  await flushPromises()
  expect(writes()).toHaveLength(1)
  const [url, options] = writes()[0]!
  expect(url).toBe('/api/backoffice/products')
  expect(options?.credentials).toBe('include')
  expect(new Headers(options?.headers).get('X-Admin-CSRF')).toBe('bound-csrf')
  expect(new Headers(options?.headers).has('Authorization')).toBe(false)
  expect(JSON.parse(String(options?.body))).toEqual({ sku: 'SKU-1', name: 'Drill', category: 'DRILL', price: 12.5 })
  expect(wrapper!.text()).toContain('Produit créé')
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
  expect(wrapper!.find('input[name="stock"]').exists()).toBe(false)
})

it.each([
  ['#product-sku', '   '], ['#product-name', '   '], ['#product-price', ''], ['#product-price', '-1'],
])('client validation blocks invalid %s=%s', async (field, value) => {
  await open('/admin/products/new')
  await fill()
  await wrapper!.get(field).setValue(value)
  await wrapper!.get('form').trigger('submit')
  await flushPromises()
  expect(writes()).toHaveLength(0)
  expect(wrapper!.text()).toContain('prix fini positif ou nul requis')
})

it('edits only metadata, displays immutable SKU and rename warning, trusts persisted response', async () => {
  await open('/admin/products/SKU-1/edit')
  expect(wrapper!.get('#product-sku').attributes('readonly')).toBeDefined()
  expect(wrapper!.text()).toContain('confirmation déjà préparée')
  await wrapper!.get('#product-name').setValue('Draft name')
  fetchMock.mockResolvedValue(json({ ...product, product_name: 'Persisted name', price: 11, stock: 6 }))
  await wrapper!.get('form').trigger('submit')
  await flushPromises()
  expect(writes()).toHaveLength(1)
  const [, options] = writes()[0]!
  expect(options?.method).toBe('PATCH')
  expect(JSON.parse(String(options?.body))).toEqual({ name: 'Draft name', category: 'DRILL', price: 12.5 })
  expect((wrapper!.get('#product-name').element as HTMLInputElement).value).toBe('Persisted name')
  expect(wrapper!.text()).toContain('Stock : 6')
  expect(wrapper!.text()).toContain('Métadonnées enregistrées')
})

it.each([0, 403, 409, 422, 500, 503])('ambiguous create/error %i never retries and existing SKU remains blocked', async status => {
  await open('/admin/products/new')
  await fill()
  fetchMock.mockImplementation(async (_input, options) => {
    if (options?.method === 'POST') {
      if (status === 0) throw new Error('network')
      return json({ detail: 'private' }, status)
    }
    return json(product)
  })

  await wrapper!.get('form').trigger('submit')
  await flushPromises()
  expect(writes()).toHaveLength(1)
  expect(wrapper!.text()).toContain('Ce SKU est présent')
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
  await wrapper!.get('form').trigger('submit')
  expect(writes()).toHaveLength(1)
  expect(wrapper!.text()).not.toContain('private')
})

it('pending create disables controls and timeout only reconciles, never re-posts', async () => {
  await open('/admin/products/new')
  await fill()
  vi.useFakeTimers()
  fetchMock.mockImplementation(async (_input, options) => {
    if (options?.method === 'POST') return new Promise<Response>((_resolve, reject) => {
      options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    })
    return json(product)
  })
  await wrapper!.get('form').trigger('submit')
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
  await wrapper!.get('form').trigger('submit')
  await vi.advanceTimersByTimeAsync(20_000)
  await flushPromises()
  expect(writes()).toHaveLength(1)
  expect(wrapper!.text()).toContain('Ce SKU est présent')
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
})

it('malformed creation success is reconciled without claiming success or retry', async () => {
  await open('/admin/products/new')
  await fill()
  fetchMock.mockImplementation(async (_input, options) => options?.method === 'POST'
    ? json({ ...product, sku: 'WRONG' }, 201) : json({}, 500))
  await wrapper!.get('form').trigger('submit')
  await flushPromises()
  expect(writes()).toHaveLength(1)
  expect(wrapper!.text()).not.toContain('Produit créé')
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
})

it('failed reconciliation stays blocked until manual reliable missing-SKU reload', async () => {
  await open('/admin/products/new')
  await fill()
  fetchMock.mockResolvedValue(json({}, 500))
  await wrapper!.get('form').trigger('submit')
  await flushPromises()
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
  fetchMock.mockResolvedValue(json({}, 404))
  await button('Actualiser le produit').trigger('click')
  await flushPromises()
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeUndefined()
  expect(wrapper!.text()).toContain('SKU absent')
  expect(writes()).toHaveLength(1)
})

it('failed edit reconciles current metadata before enabling another action', async () => {
  await open('/admin/products/SKU-1/edit')
  fetchMock.mockImplementation(async (_input, options) => options?.method === 'PATCH'
    ? json({}, 500) : json({ ...product, product_name: 'Current', stock: 5 }))
  await wrapper!.get('form').trigger('submit')
  await flushPromises()
  expect(writes()).toHaveLength(1)
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeUndefined()
  expect((wrapper!.get('#product-name').element as HTMLInputElement).value).toBe('Current')
})

it.each(['/admin/products', '/admin/products/SKU-1/edit'])('read expiry redirects from %s', async path => {
  fetchMock.mockImplementation(async input => String(input).includes('/session/')
    ? json({ username: 'admin', csrf_token: 'csrf' }) : json({}, 401))
  const router = await open(path)
  expect(router.currentRoute.value.path).toBe('/admin/login')
})

it('mutation expiry redirects without retry or reconciliation', async () => {
  const router = await open('/admin/products/new')
  await fill()
  fetchMock.mockResolvedValue(json({}, 401))
  await wrapper!.get('form').trigger('submit')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/admin/login')
  expect(writes()).toHaveLength(1)
})

it('missing edit remains blocked and can be refreshed', async () => {
  fetchMock.mockImplementation(async input => String(input).includes('/session/')
    ? json({ username: 'admin', csrf_token: 'csrf' }) : json({}, 404))
  await open('/admin/products/SKU-1/edit')
  expect(wrapper!.text()).toContain('Produit introuvable')
  expect(wrapper!.get('fieldset').attributes('disabled')).toBeDefined()
})

it('admin navigation coherently exposes products and orders without customer token panel', async () => {
  await open('/admin/products', true)
  const navigation = wrapper!.get('nav[aria-label="Navigation back-office"]')
  expect(navigation.find('a[href="/admin/products"]').exists()).toBe(true)
  expect(navigation.find('a[href="/admin/orders"]').exists()).toBe(true)
  expect(wrapper!.text()).not.toContain('JWT client')
})

it('product transport whitelist ignores injected stock and identifiers', async () => {
  await adminMe()
  const values = { sku: 'SKU-1', name: 'Drill', category: 'DRILL' as const, price: 1, stock: 999, _id: 'bad' }
  await createAdminProduct(values)
  await updateAdminProduct('SKU-1', values)
  expect(writes()).toHaveLength(2)
  expect(JSON.parse(String(writes()[0]![1]?.body))).toEqual({ sku: 'SKU-1', name: 'Drill', category: 'DRILL', price: 1 })
  expect(JSON.parse(String(writes()[1]![1]?.body))).toEqual({ name: 'Drill', category: 'DRILL', price: 1 })
})

it.each([undefined, 'false', 0])('rejects missing/malformed has_more (%s)', async has_more => {
  fetchMock.mockResolvedValue(json({ ...products(), has_more }))
  await expect(listAdminProducts(0)).rejects.toThrow('Réponse catalogue invalide')
})

it('late response after navigation cannot populate another product or allow a duplicate submit', async () => {
  const router = await open('/admin/products/new')
  await fill()
  let resolve: (response: Response) => void = () => {}
  fetchMock.mockImplementation(async (_input, options) => options?.method === 'POST'
    ? new Promise<Response>(done => { resolve = done })
    : json({ username: 'admin', csrf_token: 'csrf' }))
  await wrapper!.get('form').trigger('submit')
  await router.push('/admin/orders')
  resolve(json({ ...product, stock: 0 }, 201))
  await flushPromises()
  expect(wrapper!.text()).toContain('Admin orders')
  expect(writes()).toHaveLength(1)
})
