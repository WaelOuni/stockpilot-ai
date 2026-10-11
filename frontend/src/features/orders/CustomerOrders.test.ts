import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import OrdersPage from '../../pages/OrdersPage.vue'
import { setCustomerToken } from '../../utils/customerSession'

const fetchMock = vi.fn<typeof fetch>()
let wrapper: VueWrapper | undefined
function page(count = 1, offset = 0) {
  return new Response(JSON.stringify({
    items: Array.from({ length: count }, (_, index) => ({
      order_id: `order-${offset + index}`,
      product_name: 'Bosch X100',
      quantity: 2,
      status: 'PROCESSING',
      created_at: '2026-10-10T12:00:00+00:00',
    })),
    count, offset, limit: 20,
  }))
}
beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  setCustomerToken('test-client-a')
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  setCustomerToken('')
  vi.unstubAllGlobals()
})

describe('My Orders page', () => {
  it('announces loading and disables refresh and pagination during a request', async () => {
    let complete!: (value: Response) => void
    fetchMock.mockReturnValue(new Promise<Response>((resolve) => { complete = resolve }))
    wrapper = mount(OrdersPage, { global: { stubs: { RouterLink: true } } })
    expect(wrapper.get('[role="status"]').text()).toContain('Chargement')
    expect(wrapper.findAll('button').every((button) => button.attributes('disabled') !== undefined)).toBe(true)
    complete(page())
    await flushPromises()
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    expect(wrapper.get('.conversation-toolbar button').attributes('disabled')).toBeUndefined()
  })

  it('does not load without a JWT', async () => {
    setCustomerToken('')
    wrapper = mount(OrdersPage, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe('Mes commandes')
    expect(wrapper.text()).toContain('Connectez un JWT')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('renders product, quantity, status, date and order identifier', async () => {
    fetchMock.mockResolvedValue(page())
    wrapper = mount(OrdersPage, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('Bosch X100')
    expect(wrapper.get('.status-badge').text()).toContain('En preparation')
    expect(wrapper.get('.order-id').text()).toBe('order-0')
    expect(wrapper.get('dl').text()).toContain('2')
    expect(wrapper.get('dl').text()).toContain('2026')
    expect(wrapper.get('.pagination button:last-child').attributes('disabled')).toBeDefined()
  })

  it('supports next and previous pages without inventing a total count', async () => {
    fetchMock.mockResolvedValueOnce(page(20)).mockResolvedValueOnce(page(1, 20)).mockResolvedValueOnce(page(20))
    wrapper = mount(OrdersPage, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    expect(wrapper.get('.pagination button:first-child').attributes('disabled')).toBeDefined()
    await wrapper.get('.pagination button:last-child').trigger('click')
    await flushPromises()
    expect(fetchMock.mock.calls[1]![0]).toBe('/api/orders?limit=20&offset=20&include_total=true')
    expect(wrapper.text()).toContain('Page 2')
    await wrapper.get('.pagination button:first-child').trigger('click')
    await flushPromises()
    expect(fetchMock.mock.calls[2]![0]).toBe('/api/orders?limit=20&offset=0&include_total=true')
    expect(wrapper.text()).toContain('Page 1')
  })

  it('renders empty history and a recoverable HTTP error', async () => {
    fetchMock.mockResolvedValueOnce(new Response('error', { status: 503 })).mockResolvedValueOnce(page(0))
    wrapper = mount(OrdersPage, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('indisponible')
    await wrapper.get('.conversation-toolbar button').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('pas encore de commande')
  })

  it('clears private data on disconnect', async () => {
    fetchMock.mockResolvedValue(page())
    wrapper = mount(OrdersPage, { global: { stubs: { RouterLink: true } } })
    await flushPromises()
    setCustomerToken('')
    await flushPromises()
    expect(wrapper.findAll('.order-card')).toHaveLength(0)
    expect(wrapper.text()).toContain('Connectez un JWT')
  })

  it('ignores responses from the old token after an identity change', async () => {
    let resolve!: (response: Response) => void
    fetchMock.mockReturnValueOnce(new Promise<Response>((done) => { resolve = done })).mockResolvedValueOnce(page(0))
    wrapper = mount(OrdersPage, { global: { stubs: { RouterLink: true } } })
    setCustomerToken('test-client-b')
    resolve(page())
    await flushPromises()
    expect(wrapper.findAll('.order-card')).toHaveLength(0)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(new Headers(fetchMock.mock.calls[1]![1]?.headers).get('Authorization')).toBe('Bearer test-client-b')
  })
})
