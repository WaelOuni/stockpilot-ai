import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import OrderDetailPage from './OrderDetailPage.vue'
import OrdersPage from './OrdersPage.vue'
import OrderTimeline from '../components/OrderTimeline.vue'
import type { OrderStatus } from '../types/api'
import { formatDate } from '../utils/format'
import { setCustomerToken } from '../utils/customerSession'
import { cancelOrder } from '../api/client'

const fetchMock = vi.fn<typeof fetch>()
let wrapper: VueWrapper | undefined
const order = (status: OrderStatus = 'CREATED') => ({
  order_id: 'order-1', product_name: 'Bosch X100', quantity: 2, status, created_at: '2026-10-10T12:00:00Z',
})

function button(label: string) {
  return wrapper!.findAll('button').find((item) => item.text() === label)!
}
async function startCancellation() {
  await button('Annuler la commande').trigger('click')
}

describe('Customer cancellation', () => {
  it.each(['CREATED', 'PROCESSING', 'SHIPPED', 'DELIVERED', 'CANCELLED'] as OrderStatus[])('offers cancellation only when allowed: %s', async (status) => {
    fetchMock.mockResolvedValue(response(status))
    await mountPage()
    expect(Boolean(button('Annuler la commande'))).toBe(['CREATED', 'PROCESSING'].includes(status))
  })

  it('requires confirmation and supports returning without mutation', async () => {
    fetchMock.mockImplementation(async () => response())
    await mountPage()
    await startCancellation()
    expect(wrapper!.text()).toContain('Voulez-vous vraiment annuler cette commande ?')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
    await button('Retour').trigger('click')
    await flushPromises()
    expect(wrapper!.find('[aria-label="Confirmation d\'annulation"]').exists()).toBe(false)
    expect(fetchMock.mock.calls.every(([, options]) => options?.method === 'GET')).toBe(true)
    expect(vi.getTimerCount()).toBe(1)
  })

  it('cancels once, updates the timeline and stops polling', async () => {
    let done!: (value: Response) => void
    fetchMock.mockResolvedValueOnce(response()).mockReturnValueOnce(new Promise((resolve) => { done = resolve }))
    await mountPage()
    await startCancellation()
    const confirm = button('Confirmer l’annulation')
    await confirm.trigger('click')
    await confirm.trigger('click')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[1]![0]).toBe('/api/customer/orders/order-1/cancel')
    expect(fetchMock.mock.calls[1]![1]?.method).toBe('POST')
    expect(new Headers(fetchMock.mock.calls[1]![1]?.headers).get('Authorization')).toBe('Bearer test-detail-token')
    expect(confirm.attributes('disabled')).toBeDefined()
    expect(button('Retour').attributes('disabled')).toBeDefined()
    expect(button('Actualiser').attributes('disabled')).toBeDefined()
    expect(wrapper!.text()).toContain('Annulation en cours')
    done(response('CANCELLED'))
    await flushPromises()
    expect(wrapper!.get('.status-badge').text()).toContain('CANCELLED')
    expect(wrapper!.text()).toContain('Commande annulée')
    expect(button('Annuler la commande')).toBeUndefined()
    expect(vi.getTimerCount()).toBe(0)
    await vi.advanceTimersByTimeAsync(30_000)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('handles a 409 by reading the actual status without retrying the mutation', async () => {
    fetchMock.mockResolvedValueOnce(response())
      .mockResolvedValueOnce(new Response('{}', { status: 409 }))
      .mockResolvedValueOnce(response('SHIPPED'))
    await mountPage()
    await startCancellation()
    await button('Confirmer l’annulation').trigger('click')
    await flushPromises()
    expect(wrapper!.get('.status-badge').text()).toContain('SHIPPED')
    expect(wrapper!.get('[role="alert"]').text()).toContain('ne peut plus être annulée')
    expect(wrapper!.text()).toContain('Statut vérifié : SHIPPED')
    expect(button('Annuler la commande')).toBeUndefined()
    expect(fetchMock.mock.calls.map(([, options]) => options?.method)).toEqual(['GET', 'POST', 'GET'])
  })

  it('reconciles an ambiguous network failure with a committed cancellation', async () => {
    fetchMock.mockResolvedValueOnce(response()).mockRejectedValueOnce(new TypeError('connection lost'))
      .mockResolvedValueOnce(response('CANCELLED'))
    await mountPage()
    await startCancellation()
    await button('Confirmer l’annulation').trigger('click')
    await flushPromises()
    expect(wrapper!.text()).toContain('Statut vérifié : commande annulée')
    expect(wrapper!.find('[role="alert"]').exists()).toBe(true)
    expect(wrapper!.text()).toContain('Votre commande a été annulée.')
    await vi.advanceTimersByTimeAsync(8_000)
    expect(vi.getTimerCount()).toBe(0)
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('requires a successful detail refresh after ambiguous failure before another cancellation', async () => {
    fetchMock.mockResolvedValueOnce(response())
      .mockResolvedValueOnce(new Response('{}', { status: 500 }))
      .mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce(response())
    await mountPage()
    await startCancellation()
    await button('Confirmer l’annulation').trigger('click')
    await flushPromises()
    expect(wrapper!.text()).toContain("Le résultat de l'annulation est incertain")
    expect(button('Annuler la commande')).toBeUndefined()
    await vi.advanceTimersByTimeAsync(30_000)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    await button('Actualiser').trigger('click')
    await flushPromises()
    expect(button('Annuler la commande')).toBeDefined()
    expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
  })

  it.each(['token-change', 'unmount'])('aborts cancellation and ignores a stale success on %s', async (action) => {
    let done!: (value: Response) => void
    fetchMock.mockResolvedValueOnce(response())
      .mockReturnValueOnce(new Promise((resolve) => { done = resolve }))
      .mockResolvedValueOnce(new Response('{}', { status: 404 }))
    await mountPage()
    await startCancellation()
    await button('Confirmer l’annulation').trigger('click')
    const signal = fetchMock.mock.calls[1]![1]?.signal
    if (action === 'token-change') setCustomerToken('test-other-customer')
    else { wrapper!.unmount(); wrapper = undefined }
    done(response('CANCELLED'))
    await flushPromises()
    expect(signal?.aborted).toBe(true)
    expect(vi.getTimerCount()).toBe(0)
    if (wrapper) {
      expect(wrapper.find('.detail-content').exists()).toBe(false)
      expect(wrapper.text()).not.toContain('Commande annulée.')
    }
  })

  it('treats a malformed success as ambiguous instead of assuming cancellation', async () => {
    fetchMock.mockResolvedValueOnce(response('CREATED'))
    await expect(cancelOrder('order-1')).rejects.toThrow('incomplete')
    expect(fetchMock.mock.calls[0]![1]?.method).toBe('POST')
  })
})
const response = (status: OrderStatus = 'CREATED') => new Response(JSON.stringify(order(status)))
beforeEach(() => {
  fetchMock.mockReset()
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
  it.each(['CREATED', 'PROCESSING', 'SHIPPED', 'DELIVERED'] as OrderStatus[])('shows only recorded %s events with their timestamps', (status) => {
    const steps: OrderStatus[] = ['CREATED', 'PROCESSING', 'SHIPPED', 'DELIVERED']
    const events = steps.slice(0, steps.indexOf(status) + 1).map((step, index) => ({
      status: step, occurred_at: `2026-10-10T${String(10 + index).padStart(2, '0')}:00:00Z`,
      source: index === 0 ? 'SYSTEM' as const : 'BACKOFFICE' as const,
    }))
    wrapper = mount(OrderTimeline, { props: { status, events } })
    expect(wrapper.findAll('li.reached')).toHaveLength(steps.indexOf(status) + 1)
    expect(wrapper.get('[aria-current="step"]').text()).toContain(status)
    expect(wrapper.findAll('li')).toHaveLength(events.length)
    const times = wrapper.findAll('time')
    expect(times.map((time) => time.attributes('datetime'))).toEqual(events.map((event) => event.occurred_at))
    expect(times.map((time) => time.text())).toEqual(events.map((event) => formatDate(event.occurred_at)))
    expect(wrapper.text()).toContain('Source : SYSTEM')
    if (events.length > 1) expect(wrapper.text()).toContain('Source : BACKOFFICE')
  })

  it('shows cancellation without marking future stages complete', () => {
    wrapper = mount(OrderTimeline, { props: { status: 'CANCELLED', events: [
      { status: 'CREATED', occurred_at: '2026-10-10T10:02:00Z', source: 'SYSTEM' },
      { status: 'CANCELLED', occurred_at: '2026-10-10T10:08:00Z', source: 'CUSTOMER' },
    ] } })
    expect(wrapper.text()).toContain('Commande annulée')
    expect(wrapper.findAll('li')).toHaveLength(2)
    expect(wrapper.text()).toContain('Source : CUSTOMER')
    expect(wrapper.text()).not.toContain('PROCESSING')
    expect(wrapper.text()).not.toContain('SHIPPED')
  })

  it.each([undefined, []])('does not invent event dates for legacy events=%s', (events) => {
    wrapper = mount(OrderTimeline, { props: { status: 'PROCESSING', events } })
    expect(wrapper.text()).toContain('PROCESSING')
    expect(wrapper.text()).toContain('historique détaillé n’est pas disponible')
    expect(wrapper.findAll('time')).toHaveLength(0)
    expect(wrapper.findAll('li')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('CREATED')
  })

  it('wires real API events into the detail timeline', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({
      ...order('PROCESSING'), events: [
        { status: 'CREATED', occurred_at: '2026-10-10T10:02:00Z', source: 'SYSTEM' },
        { status: 'PROCESSING', occurred_at: '2026-10-10T10:15:00Z', source: 'BACKOFFICE' },
      ],
    })))
    await mountPage()
    expect(wrapper!.findAll('time').map((item) => item.attributes('datetime'))).toEqual([
      '2026-10-10T10:02:00Z', '2026-10-10T10:15:00Z',
    ])
    expect(wrapper!.text()).toContain('Source : BACKOFFICE')
  })

  it('formats a recorded timestamp as a French local date and time', () => {
    const occurredAt = new Date(2026, 9, 10, 10, 2).toISOString()
    wrapper = mount(OrderTimeline, { props: { status: 'CREATED', events: [
      { status: 'CREATED', occurred_at: occurredAt, source: 'SYSTEM' },
    ] } })
    expect(wrapper.get('time').text()).toBe('10 oct. 2026, 10:02')
    expect(wrapper.get('time').attributes('datetime')).toBe(occurredAt)
  })

  it.each([
    [{ status: 'CREATED', occurred_at: 'invalid', source: 'SYSTEM' }],
    [{ status: 'CREATED', occurred_at: '2026-10-10T10:02:00Z', source: 'UNKNOWN' }],
    null,
  ])('rejects malformed event data instead of rendering invented history: %s', async (events) => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ ...order(), events })))
    await mountPage()
    expect(wrapper!.find('[role="alert"]').exists()).toBe(true)
    expect(wrapper!.findAll('time')).toHaveLength(0)
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
    fetchMock.mockImplementation(async () => response())
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
