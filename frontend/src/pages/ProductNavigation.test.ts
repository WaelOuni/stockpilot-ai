import { setCustomerToken } from '../utils/customerSession'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, RouterView, type LocationQuery } from 'vue-router'
import ProductsPage from './ProductsPage.vue'
import ProductDetailPage from './ProductDetailPage.vue'
import { catalogueQuery } from '../utils/products'

const product = {
  sku: 'BOSCH-X100', product_name: 'Bosch X100', category: 'DRILL', price: 99, stock: 10,
}
const fetchMock = vi.fn<typeof fetch>()
let wrapper: VueWrapper | undefined

beforeEach(() => {
  setCustomerToken('test-navigation-customer')
  fetchMock.mockReset()
  fetchMock.mockImplementation(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    return new Response(JSON.stringify(url.pathname === '/api/products' ? {
      items: [product], count: 1, limit: 20, offset: Number(url.searchParams.get('offset') || 0), has_more: false,
    } : product))
  })
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => { setCustomerToken(''); wrapper?.unmount(); wrapper = undefined; vi.unstubAllGlobals() })

async function open(path: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/products', component: ProductsPage },
      { path: '/products/:sku', component: ProductDetailPage },
      { path: '/assistant', component: { template: '<div>Assistant</div>' } },
    ],
  })
  await router.push(path)
  wrapper = mount(RouterView, { global: { plugins: [router] } })
  await flushPromises()
  return router
}

function link(text: string) {
  const result = wrapper!.findAll('a').find((item) => item.text() === text)
  expect(result, `Link "${text}" should exist`).toBeDefined()
  return result!
}

it.each([
  { q: 'Bosch' },
  { category: 'DRILL', available: 'false' },
  { offset: '20' },
  { q: 'Bosch & béton + / ? #', category: 'DRILL', available: 'true', offset: '40' },
])('restores applied catalogue context after visiting detail: %j', async (query) => {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (typeof value === 'string') params.set(key, value)
  }
  const router = await open('/products?' + params)
  await wrapper!.get('input[type="search"]').setValue('unsubmitted draft')
  expect(fetchMock).toHaveBeenCalledTimes(1)
  await link('Voir le produit Bosch X100').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/products/BOSCH-X100')
  expect(router.currentRoute.value.query).toEqual(query)
  expect(fetchMock).toHaveBeenCalledTimes(2)
  const destination = router.resolve(link('Retour au catalogue').attributes('href')!)
  expect(destination.path).toBe('/products')
  expect(destination.query).toEqual(query)
  await link('Retour au catalogue').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.query).toEqual(query)
  expect((wrapper!.get('input[type="search"]').element as HTMLInputElement).value).toBe(query.q || '')
  expect(fetchMock).toHaveBeenCalledTimes(3)
  const api = new URL(String(fetchMock.mock.calls.at(-1)![0]), 'http://localhost')
  for (const [key, value] of Object.entries(query)) expect(api.searchParams.get(key)).toBe(value)
})

it('restores context when a detail URL is opened in a fresh router (refresh)', async () => {
  const router = await open('/products/BOSCH-X100?q=Bosch%20%26%20b%C3%A9ton&category=DRILL&available=false&offset=20')
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(fetchMock.mock.calls[0]![0]).toBe('/api/products/BOSCH-X100')
  await link('Retour au catalogue').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.query).toEqual({
    q: 'Bosch & béton', category: 'DRILL', available: 'false', offset: '20',
  })
  expect(wrapper!.text()).toContain('Page 2')
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

it('returns directly opened detail to catalogue without query', async () => {
  const router = await open('/products/BOSCH-X100')
  expect(link('Retour au catalogue').attributes('href')).toBe('/products')
  await link('Retour au catalogue').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.fullPath).toBe('/products')
})

it('retains native router back navigation', async () => {
  const router = await open('/products?q=Bosch&category=DRILL&available=true&offset=20')
  const original = router.currentRoute.value.fullPath
  await link('Voir le produit Bosch X100').trigger('click')
  await flushPromises()
  router.back()
  await flushPromises()
  expect(router.currentRoute.value.fullPath).toBe(original)
  expect(wrapper!.text()).toContain('Page 2')
  expect(fetchMock).toHaveBeenCalledTimes(3)
})

it('gives each product link a contextual accessible name', async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({
    items: [product, { ...product, sku: 'MAKITA-D200', product_name: 'Makita D200' }],
    count: 2, limit: 20, offset: 0, has_more: false,
  })))
  await open('/products')
  expect(link('Voir le produit Bosch X100').attributes('href')).toBe('/products/BOSCH-X100')
  expect(link('Voir le produit Makita D200').attributes('href')).toBe('/products/MAKITA-D200')
})

it('encodes product SKU and search independently in the detail URL', async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({
    items: [{ ...product, sku: 'SKU / + ? #' }], count: 1, limit: 20, offset: 0, has_more: false,
  })))
  const router = await open('/products?q=Bosch%20%26%20b%C3%A9ton')
  const target = router.resolve(link('Voir le produit Bosch X100').attributes('href')!)
  expect(target.params.sku).toBe('SKU / + ? #')
  expect(target.query).toEqual({ q: 'Bosch & béton' })
})

it('ignores unknown context but retains validated fields', async () => {
  const router = await open('/products?q=Bosch&category=DRILL&available=true&offset=20&returnUrl=https://example.invalid&unknown=1')
  await link('Voir le produit Bosch X100').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.query).toEqual({
    q: 'Bosch', category: 'DRILL', available: 'true', offset: '20',
  })
})

it.each([
  'q=one&q=two', 'q', 'q=' + 'x'.repeat(101),
  'category=INVALID', 'category=drill', 'category=DRILL&category=SAW',
  'available=1', 'available=true&available=false',
  'offset=-1', 'offset=1.5', 'offset=abc', 'offset=9007199254740992',
  'offset=20&offset=40', 'returnUrl=https://example.invalid&unknown=1',
])('does not propagate invalid/unknown detail context: %s', async (query) => {
  await open('/products/BOSCH-X100?' + query)
  expect(link('Retour au catalogue').attributes('href')).toBe('/products')
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it('drops invalid fields individually without discarding valid criteria', () => {
  expect(catalogueQuery({
    q: 'Bosch', category: 'INVALID', available: 'false', offset: ['20', '40'], extra: 'ignored',
  })).toEqual({ q: 'Bosch', available: 'false' })
})

it('does not reload detail on context-only changes', async () => {
  const router = await open('/products/BOSCH-X100?q=Bosch')
  await router.push('/products/BOSCH-X100?q=Makita&offset=20')
  await flushPromises()
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(router.resolve(link('Retour au catalogue').attributes('href')!).query).toEqual({
    q: 'Makita', offset: '20',
  })
})

it.each(['Commander', 'Demander à StockPilot'])('keeps %s destination independent of catalogue context', async (action) => {
  const router = await open('/products/BOSCH-X100?q=Bosch&category=DRILL&available=true&offset=20')
  const query: LocationQuery = {
    message: action === 'Commander' ? 'Commande-moi 1 Bosch X100'
      : 'Peux-tu me conseiller sur perceuse Bosch X100 ?',
  }
  expect(router.resolve(link(action).attributes('href')!).query).toEqual(query)
  await link(action).trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/assistant')
  expect(router.currentRoute.value.query).toEqual(query)
  expect(fetchMock).toHaveBeenCalledTimes(1)
})
