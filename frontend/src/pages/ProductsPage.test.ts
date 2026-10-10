import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import ProductsPage from './ProductsPage.vue'
import ProductDetailPage from './ProductDetailPage.vue'
import AssistantPage from './AssistantPage.vue'
import { setCustomerToken } from '../utils/customerSession'
import { useAssistant } from '../features/assistant/useAssistant'
import { getProduct, listProducts } from '../api/client'

const product = { sku: 'BOSCH-X100', product_name: 'Bosch X100', category: 'DRILL', price: 99, stock: 10 }
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status })
const list = (count = 1, offset = 0, has_more = false) => json({
  items: Array.from({ length: count }, (_, i) => ({ ...product, sku: `sku-${i}` })),
  count, limit: 20, offset, has_more,
})
const fetchMock = vi.fn<typeof fetch>()
let wrapper: VueWrapper | undefined
beforeEach(() => {
  setCustomerToken('')
  useAssistant().reset()
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  setCustomerToken('')
  vi.unstubAllGlobals()
})
async function open(path = '/products') {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/products', component: ProductsPage },
    { path: '/products/:sku', component: ProductDetailPage },
    { path: '/assistant', component: AssistantPage, props: (route) => ({ initialMessage: route.query.message }) },
  ] })
  await router.push(path)
  wrapper = mount(RouterView, { global: { plugins: [router] } })
  await flushPromises()
  return router
}

it('lists public products with only real fields and no JWT header', async () => {
  fetchMock.mockResolvedValue(list())
  await open()
  expect(wrapper!.text()).toContain('Bosch X100')
  expect(wrapper!.text()).toContain('Perceuse')
  expect(wrapper!.text()).toContain('99')
  expect(wrapper!.text()).toContain('10')
  expect(new Headers(fetchMock.mock.calls[0]![1]?.headers).has('Authorization')).toBe(false)
  expect(wrapper!.get('a').attributes('href')).toBe('/products/sku-0')
})

it('shows loading, empty and recoverable error states', async () => {
  let done!: (value: Response) => void
  fetchMock.mockReturnValueOnce(new Promise((resolve) => { done = resolve }))
    .mockResolvedValueOnce(json({}, 500)).mockResolvedValueOnce(list(0))
  await open()
  expect(wrapper!.get('[role="status"]').text()).toContain('Chargement')
  done(list(0))
  await flushPromises()
  expect(wrapper!.text()).toContain('Aucun produit')
  await wrapper!.get('.conversation-toolbar button').trigger('click')
  await flushPromises()
  expect(wrapper!.get('[role="alert"]').text()).toContain('serveur')
  await wrapper!.get('.conversation-toolbar button').trigger('click')
  await flushPromises()
  expect(wrapper!.find('[role="alert"]').exists()).toBe(false)
})

it('paginates and opens product detail', async () => {
  fetchMock.mockResolvedValueOnce(list(20, 0, true)).mockResolvedValueOnce(list(1, 20)).mockResolvedValueOnce(json(product))
  const router = await open()
  await wrapper!.findAll('button').find((button) => button.text() === 'Suivant')!.trigger('click')
  await flushPromises()
  expect(fetchMock.mock.calls[1]![0]).toBe('/api/products?limit=20&offset=20')
  await wrapper!.get('a').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/products/sku-0')
  expect(wrapper!.text()).toContain('Fiche produit')
  expect(wrapper!.text()).toContain('Disponible')
})

it.each([
  [0, false], [3, false], [20, false], [20, true],
])('uses has_more for next-page availability (count=%s, has_more=%s)', async (count, hasMore) => {
  fetchMock.mockResolvedValue(list(count, 0, hasMore))
  const router = await open()
  const next = wrapper!.findAll('button').find((button) => button.text() === 'Suivant')!
  expect(next.attributes('disabled') !== undefined).toBe(!hasMore)
  if (!hasMore) {
    await next.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.fullPath).toBe('/products')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  }
})

it.each(['Commander', 'Demander à StockPilot'])('prefills Assistant from %s without auto-sending', async (action) => {
  fetchMock.mockResolvedValue(json(product))
  const router = await open('/products/BOSCH-X100')
  const link = wrapper!.findAll('a').find((a) => a.text() === action)!
  await link.trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/assistant')
  const expected = action === 'Commander' ? 'Commande-moi 1 Bosch X100' : 'Peux-tu me conseiller sur perceuse Bosch X100 ?'
  expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe(expected)
  expect(fetchMock).toHaveBeenCalledTimes(1)
  setCustomerToken('test-catalogue-customer')
  await flushPromises()
  expect((wrapper!.get('textarea').element as HTMLTextAreaElement).value).toBe(expected)
  expect(wrapper!.get('textarea').attributes('disabled')).toBeUndefined()
})

it('retains confirmation flow after submitting the catalogue draft', async () => {
  setCustomerToken('test-customer')
  fetchMock.mockResolvedValueOnce(json(product)).mockResolvedValueOnce(json({
    response: 'Confirmez-vous ?', intent: 'ORDER', order_status: 'PENDING_CONFIRMATION',
    pending_order: { status: 'PENDING_CONFIRMATION', operation_id: 'op', product_name: 'Bosch X100', quantity: 1 },
  }))
  await open('/products/BOSCH-X100')
  await wrapper!.findAll('a').find((a) => a.text() === 'Commander')!.trigger('click')
  await flushPromises()
  await wrapper!.get('form.composer').trigger('submit')
  await flushPromises()
  expect(wrapper!.get('.confirmation-card').text()).toContain('Bosch X100')
  expect(JSON.parse(String(fetchMock.mock.calls[1]![1]?.body)).message).toBe('Commande-moi 1 Bosch X100')
})

it('handles detail loading, 404 and network failure', async () => {
  let done!: (value: Response) => void
  fetchMock.mockReturnValueOnce(new Promise((resolve) => { done = resolve })).mockRejectedValueOnce(new TypeError('network'))
  await open('/products/missing')
  expect(wrapper!.get('[role="status"]').text()).toContain('Chargement')
  done(json({}, 404))
  await flushPromises()
  expect(wrapper!.text()).toContain('Produit introuvable')
  await wrapper!.get('button').trigger('click')
  await flushPromises()
  expect(wrapper!.get('[role="alert"]').text()).toContain('Connexion')
})

it('does not send a JWT for public endpoints even when connected and validates responses', async () => {
  setCustomerToken('private-test-token')
  fetchMock.mockResolvedValueOnce(json(product)).mockResolvedValueOnce(json({ items: [], limit: 20, offset: 0, count: 1 }))
  await getProduct('BOSCH-X100')
  expect(new Headers(fetchMock.mock.calls[0]![1]?.headers).has('Authorization')).toBe(false)
  await expect(listProducts()).rejects.toThrow('incomplet')
})
