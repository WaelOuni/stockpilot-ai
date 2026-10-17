import { setCustomerToken } from '../utils/customerSession'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import ProductsPage from './ProductsPage.vue'

const fetchMock = vi.fn<typeof fetch>()
let wrapper: VueWrapper | undefined
function response(offset = 0, count = 1, has_more = false) {
  return new Response(JSON.stringify({
    items: Array.from({ length: count }, (_, index) => ({
      sku: `SKU-${index}`, product_name: 'Bosch X100', category: 'DRILL', stock: 10, price: 99,
    })), limit: 20, offset, count, has_more,
  }))
}
beforeEach(() => { setCustomerToken('test-filter-customer'); fetchMock.mockReset(); vi.stubGlobal('fetch', fetchMock) })
afterEach(() => { setCustomerToken(''); wrapper?.unmount(); wrapper = undefined; vi.unstubAllGlobals() })
async function open(path = '/products') {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/products', component: ProductsPage },
    { path: '/products/:sku', component: { template: '<div />' } },
  ] })
  await router.push(path)
  wrapper = mount(ProductsPage, { global: { plugins: [router] } })
  await flushPromises()
  return router
}
const submit = async () => { await wrapper!.get('form').trigger('submit'); await flushPromises() }
const button = (text: string) => wrapper!.findAll('button').find((item) => item.text() === text)!

it('applies combined drafts only on submit and synchronizes URL', async () => {
  fetchMock.mockImplementation(async () => response())
  const router = await open()
  await wrapper!.get('input').setValue(' Bosch ')
  await wrapper!.findAll('select')[0]!.setValue('DRILL')
  await wrapper!.findAll('select')[1]!.setValue('true')
  expect(fetchMock).toHaveBeenCalledTimes(1)
  await submit()
  expect(router.currentRoute.value.query).toEqual({ q: 'Bosch', category: 'DRILL', available: 'true' })
  expect(fetchMock.mock.calls[1]![0]).toBe('/api/products?limit=20&offset=0&include_total=true&q=Bosch&category=DRILL&available=true')
})

it('restores query fields and pagination on initial router load', async () => {
  fetchMock.mockResolvedValue(response(20, 20))
  await open('/products?q=bosch&category=SAW&available=false&offset=20')
  expect((wrapper!.get('input').element as HTMLInputElement).value).toBe('bosch')
  expect((wrapper!.findAll('select')[0]!.element as HTMLSelectElement).value).toBe('SAW')
  expect((wrapper!.findAll('select')[1]!.element as HTMLSelectElement).value).toBe('false')
  expect(wrapper!.text()).toContain('Page 2')
  expect(fetchMock.mock.calls[0]![0]).toBe('/api/products?limit=20&offset=20&include_total=true&q=bosch&category=SAW&available=false')
})

it('resets pagination on changed filters and keeps filters while paginating', async () => {
  fetchMock.mockResolvedValueOnce(response(20, 20, true)).mockResolvedValueOnce(response(0, 20, true))
    .mockResolvedValueOnce(response(20, 1))
  const router = await open('/products?q=bosch&offset=20')
  await wrapper!.findAll('select')[0]!.setValue('DRILL')
  await submit()
  expect(router.currentRoute.value.query).toEqual({ q: 'bosch', category: 'DRILL' })
  await button('Suivant').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.query).toEqual({ q: 'bosch', category: 'DRILL', offset: '20' })
  expect(fetchMock.mock.calls[2]![0]).toBe('/api/products?limit=20&offset=20&include_total=true&q=bosch&category=DRILL')
  expect(button('Suivant').attributes('disabled')).toBeDefined()
  expect(button('Précédent').attributes('disabled')).toBeUndefined()
})

it('resets every filter and returns to the first page', async () => {
  fetchMock.mockResolvedValueOnce(response(20, 0)).mockResolvedValueOnce(response())
  const router = await open('/products?q=bosch&category=DRILL&available=true&offset=20')
  expect(wrapper!.text()).toContain('Aucun produit ne correspond')
  await button('Réinitialiser les filtres').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.fullPath).toBe('/products')
  expect((wrapper!.get('input').element as HTMLInputElement).value).toBe('')
  expect(wrapper!.findAll('select').map((item) => (item.element as HTMLSelectElement).value)).toEqual(['', ''])
  expect(fetchMock.mock.calls[1]![0]).toBe('/api/products?limit=20&offset=0&include_total=true')
})

it('clears unsubmitted drafts even when the URL already has no filters', async () => {
  fetchMock.mockResolvedValue(response())
  await open()
  await wrapper!.get('input').setValue('unsent')
  await wrapper!.findAll('select')[0]!.setValue('DRILL')
  await button('Réinitialiser les filtres').trigger('click')
  expect((wrapper!.get('input').element as HTMLInputElement).value).toBe('')
  expect((wrapper!.findAll('select')[0]!.element as HTMLSelectElement).value).toBe('')
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it('restores filters when router history moves back', async () => {
  fetchMock.mockImplementation(async () => response())
  const router = await open('/products?q=bosch')
  await wrapper!.get('input').setValue('makita')
  await submit()
  router.back()
  await flushPromises()
  expect((wrapper!.get('input').element as HTMLInputElement).value).toBe('bosch')
  expect(fetchMock.mock.calls.at(-1)![0]).toBe('/api/products?limit=20&offset=0&include_total=true&q=bosch')
})

it('shows loading and recoverable error for a filtered request', async () => {
  let done!: (response: Response) => void
  fetchMock.mockReturnValueOnce(new Promise((resolve) => { done = resolve }))
    .mockResolvedValueOnce(response(0, 0))
  await open('/products?q=missing')
  expect(wrapper!.get('[role="status"]').text()).toContain('Chargement')
  expect(button('Rechercher').attributes('disabled')).toBeDefined()
  expect(button('Suivant').attributes('disabled')).toBeDefined()
  done(new Response('{}', { status: 500 }))
  await flushPromises()
  expect(wrapper!.get('[role="alert"]').text()).toContain('serveur')
  expect(button('Suivant').attributes('disabled')).toBeDefined()
  await button('Actualiser').trigger('click')
  await flushPromises()
  expect(wrapper!.text()).toContain('Aucun produit ne correspond à vos critères')
  expect(button('Suivant').attributes('disabled')).toBeDefined()
})

it.each(['offset=-1', 'offset=abc', 'category=INVALID', 'available=invalid', 'q=bosch&q=makita'])('rejects invalid URL instead of silently changing criteria: %s', async (query) => {
  await open('/products?' + query)
  expect(wrapper!.find('[role="alert"]').exists()).toBe(true)
  expect(fetchMock).not.toHaveBeenCalled()
})

it('aborts stale reads on URL changes and renders only the latest result', async () => {
  let done!: (response: Response) => void
  fetchMock.mockReturnValueOnce(new Promise((resolve) => { done = resolve })).mockResolvedValueOnce(response(0, 0))
  const router = await open()
  const signal = fetchMock.mock.calls[0]![1]?.signal
  await router.push('/products?q=missing')
  await flushPromises()
  done(response())
  await flushPromises()
  expect(signal?.aborted).toBe(true)
  expect(wrapper!.text()).toContain('Aucun produit ne correspond')
  expect(wrapper!.findAll('article')).toHaveLength(0)
})

it('renders exact totals, jumps to last page and restores URL with browser back', async () => {
  fetchMock.mockImplementation(async input => {
    const offset = Number(new URL(String(input), 'http://local').searchParams.get('offset'))
    const product = { sku: 'SKU-last', product_name: 'Outil', category: 'DRILL', stock: 1, price: 99 }
    return new Response(JSON.stringify({ items: offset === 40 ? [product] : Array.from({ length: 20 }, (_, index) => ({ ...product, sku: `SKU-${index}` })), limit: 20, offset, count: offset === 40 ? 1 : 20, total: 41, has_more: offset < 40 }))
  })
  const router = await open()
  expect(wrapper!.text()).toContain('Page 1 sur 3')
  await button('Dernière page').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.query.offset).toBe('40')
  expect(wrapper!.text()).toContain('Affichage de 41 à 41 sur 41 produits')
  router.back()
  await flushPromises()
  expect(router.currentRoute.value.query.offset).toBeUndefined()
  expect(wrapper!.text()).toContain('Page 1 sur 3')
})
