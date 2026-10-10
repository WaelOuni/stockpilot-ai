import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import OrderDetailPage from './OrderDetailPage.vue'
import OrdersPage from './OrdersPage.vue'
import OrderTimeline from '../components/OrderTimeline.vue'
import type { OrderStatus } from '../types/api'
import { setCustomerToken } from '../utils/customerSession'

const fetchMock = vi.fn<typeof fetch>()
let wrapper: VueWrapper | undefined
const order = (status: OrderStatus = 'CREATED') => ({
  order_id: 'order-1', product_name: 'Bosch X100', quantity: 2, status, created_at: '2026-10-10T12:00:00Z',
})
const response = (status: OrderStatus = 'CREATED') => new Response(JSON.stringify(order(status)))
beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', fetchMock)
  setCustomerToken('test-detail-token')
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  setCustomerToken('')
  vi.useRealTimers()
  vi.unstubAllGlobals()
})
async function mountPage(path = '/orders/order-1') {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/orders/:orderId', component: OrderDetailPage },
    { path: '/orders', component: OrdersPage },
  ] })
  await router.push(path)
  wrapper = mount(path === '/orders' ? OrdersPage : OrderDetailPage, { global: { plugins: [router] } })
  await flushPromises()
  return router
}

describe('Order detail', () => {
  it.each(['CREATED', 'PROCESSING', 'SHIPPED', 'DELIVERED'] as OrderStatus[])('renders %s timeline accurately', (status) => {
    wrapper = mount(OrderTimeline, { props: { status } })
    const steps = ['CREATED', 'PROCESSING', 'SHIPPED', 'DELIVERED']
    expect(wrapper.findAll('li.reached')).toHaveLength(steps.indexOf(status) + 1)
    expect(wrapper.get('[aria-current="step"]').text()).toContain(status)
    expect(wrapper.findAll('li')).toHaveLength(4)
  })

  it('shows cancellation without marking future stages complete', () => {
    wrapper = mount(OrderTimeline, { props: { status: 'CANCELLED' } })
    expect(wrapper.text()).toContain('Commande annulée')
    expect(wrapper.findAll('li')).toHaveLength(0)
  })

  it('renders owned details and refreshes current status', async () => {
    fetchMock.mockResolvedValueOnce(response()).mockResolvedValueOnce(response('PROCESSING'))
    await mountPage()
    expect(wrapper!.text()).toContain('Bosch X100')
    expect(wrapper!.text()).toContain('order-1')
    expect(wrapper!.text()).toContain('2026')
    expect(wrapper!.get('dl').text()).toContain('2')
    expect(fetchMock.mock.calls[0]![0]).toBe('/api/orders/order-1')
    await wrapper!.get('button').trigger('click')
    await flushPromises()
    expect(wrapper!.get('.status-badge').text()).toContain('PROCESSING')
  })

  it('announces loading and prevents concurrent refreshes', async () => {
    let done!: (value: Response) => void
    fetchMock.mockReturnValue(new Promise((resolve) => { done = resolve }))
    await mountPage()
    expect(wrapper!.get('[role="status"]').text()).toContain('Actualisation')
    expect(wrapper!.get('button').attributes('disabled')).toBeDefined()
    await vi.advanceTimersByTimeAsync(30_000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    done(response())
    await flushPromises()
  })

  it('shows generic errors and stops automatic refresh until manually retried', async () => {
    fetchMock.mockResolvedValueOnce(new Response('error', { status: 500 })).mockResolvedValueOnce(response())
    await mountPage()
    expect(wrapper!.get('[role="alert"]').text()).toContain('serveur')
    await vi.advanceTimersByTimeAsync(30_000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await wrapper!.get('button').trigger('click')
    await flushPromises()
    expect(wrapper!.find('[role="alert"]').exists()).toBe(false)
  })

  it('shows identical not-found state without explaining foreign ownership', async () => {
    fetchMock.mockResolvedValue(new Response('{"detail":"NOT_FOUND"}', { status: 404 }))
    await mountPage()
    expect(wrapper!.text()).toContain('Commande introuvable')
    expect(wrapper!.text()).not.toContain('autre customer')
    expect(wrapper!.find('.detail-content').exists()).toBe(false)
  })

  it('polls every 15 seconds and stops after unmount', async () => {
    fetchMock.mockImplementation(async () => response())
    await mountPage()
    await vi.advanceTimersByTimeAsync(14_999)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    await flushPromises()
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/api/orders/order-1', '/api/orders/order-1'])
    wrapper!.unmount()
    wrapper = undefined
    expect(vi.getTimerCount()).toBe(0)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    await vi.advanceTimersByTimeAsync(30_000)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it.each(['DELIVERED', 'CANCELLED'] as OrderStatus[])('does not poll terminal %s', async (status) => {
    fetchMock.mockResolvedValue(response(status))
    await mountPage()
    await vi.advanceTimersByTimeAsync(30_000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(wrapper!.text()).toContain('Statut terminal')
  })

  it('stops polling after a terminal update', async () => {
    fetchMock.mockResolvedValueOnce(response()).mockResolvedValueOnce(response('DELIVERED'))
    await mountPage()
    await vi.advanceTimersByTimeAsync(15_000)
    await flushPromises()
    await vi.advanceTimersByTimeAsync(30_000)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('clears detail on token change and aborts stale requests', async () => {
    let done!: (value: Response) => void
    fetchMock.mockReturnValueOnce(new Promise((resolve) => { done = resolve }))
      .mockResolvedValueOnce(new Response('error', { status: 404 }))
    await mountPage()
    const signal = fetchMock.mock.calls[0]![1]?.signal
    setCustomerToken('test-other-customer')
    done(response())
    await flushPromises()
    expect(signal?.aborted).toBe(true)
    expect(wrapper!.find('.detail-content').exists()).toBe(false)
  })

  it('does not fetch without JWT and clears polling on disconnect', async () => {
    setCustomerToken('')
    await mountPage()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(wrapper!.text()).toContain('Connectez un JWT')
    fetchMock.mockResolvedValue(response())
    setCustomerToken('test-customer')
    await flushPromises()
    setCustomerToken('')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(30_000)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(wrapper!.find('.detail-content').exists()).toBe(false)
  })

  it('opens detail from My Orders through the correct link', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ items: [order()], count: 1, limit: 20, offset: 0 })))
    const router = await mountPage('/orders')
    await wrapper!.get('a.context-link').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/orders/order-1')
  })
})
