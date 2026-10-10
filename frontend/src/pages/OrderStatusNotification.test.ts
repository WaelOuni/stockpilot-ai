import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import OrderDetailPage from './OrderDetailPage.vue'
import type { OrderStatus } from '../types/api'
import { setCustomerToken } from '../utils/customerSession'
import { getOrderStatusMessage } from '../utils/orderStatusMessage'

const fetchMock = vi.fn<typeof fetch>()
let wrapper: VueWrapper | undefined
const response = (status: OrderStatus) => new Response(JSON.stringify({
  order_id: 'order-1', product_name: 'Bosch X100', quantity: 1, status,
  created_at: '2026-10-10T10:00:00Z', events: [],
}))
beforeEach(() => {
  vi.useFakeTimers()
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  setCustomerToken('test-notification-token')
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  setCustomerToken('')
  vi.useRealTimers()
  vi.unstubAllGlobals()
})
async function open() {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/orders', component: { template: '<div />' } },
    { path: '/orders/:orderId', component: OrderDetailPage },
  ] })
  await router.push('/orders/order-1')
  wrapper = mount(OrderDetailPage, { global: { plugins: [router] } })
  await flushPromises()
  return router
}
const notification = () => wrapper!.findComponent({ name: 'OrderStatusNotification' })
async function poll() {
  await vi.advanceTimersByTimeAsync(15_000)
  await flushPromises()
}

it.each(['CREATED', 'PROCESSING', 'SHIPPED', 'DELIVERED', 'CANCELLED'] as OrderStatus[])('does not notify on initial %s', async (status) => {
  fetchMock.mockImplementation(async () => response(status))
  await open()
  expect(notification().exists()).toBe(false)
})

it.each([
  ['CREATED', 'PROCESSING'], ['PROCESSING', 'SHIPPED'],
  ['SHIPPED', 'DELIVERED'], ['PROCESSING', 'CANCELLED'],
] as [OrderStatus, OrderStatus][])('notifies on %s → %s and respects terminal polling', async (initial, next) => {
  fetchMock.mockResolvedValueOnce(response(initial)).mockImplementation(async () => response(next))
  await open()
  await poll()
  expect(notification().get('[role="status"]').text()).toBe(getOrderStatusMessage(next))
  expect(wrapper!.get('.status-badge').text()).toContain(next)
  expect(wrapper!.findAll('time')).toHaveLength(0)
  await poll()
  expect(notification().exists()).toBe(false)
  expect(fetchMock).toHaveBeenCalledTimes(['DELIVERED', 'CANCELLED'].includes(next) ? 2 : 3)
})

it('does not duplicate notifications on repeated equal reads or recreate one after dismissal', async () => {
  fetchMock.mockResolvedValueOnce(response('CREATED')).mockImplementation(async () => response('SHIPPED'))
  await open()
  await poll()
  expect(notification().exists()).toBe(true)
  await wrapper!.get('[aria-label="Fermer la notification de statut"]').trigger('click')
  expect(notification().exists()).toBe(false)
  await poll()
  expect(notification().exists()).toBe(false)
})

it('never notifies for repeated unchanged status', async () => {
  fetchMock.mockImplementation(async () => response('CREATED'))
  await open()
  await poll()
  await poll()
  expect(notification().exists()).toBe(false)
})

it('expires after eight seconds, not before', async () => {
  fetchMock.mockResolvedValueOnce(response('CREATED')).mockImplementation(async () => response('PROCESSING'))
  await open()
  await poll()
  await vi.advanceTimersByTimeAsync(7_999)
  expect(notification().exists()).toBe(true)
  await vi.advanceTimersByTimeAsync(1)
  expect(notification().exists()).toBe(false)
})

it('replaces a pending notification on a new change but does not extend it on equal reads', async () => {
  fetchMock.mockResolvedValueOnce(response('CREATED'))
    .mockResolvedValueOnce(response('PROCESSING'))
    .mockImplementation(async () => response('SHIPPED'))
  await open()
  await poll()
  await vi.advanceTimersByTimeAsync(4_000)
  await wrapper!.get('button').trigger('click')
  await flushPromises()
  expect(notification().text()).toContain(getOrderStatusMessage('SHIPPED'))
  expect(wrapper!.findAllComponents({ name: 'OrderStatusNotification' })).toHaveLength(1)
  await vi.advanceTimersByTimeAsync(4_000)
  await wrapper!.get('button').trigger('click')
  await flushPromises()
  expect(notification().exists()).toBe(true)
  await vi.advanceTimersByTimeAsync(4_000)
  expect(notification().exists()).toBe(false)
})

it.each(['jwt', 'route', 'unmount'])('cleans notification and timers on %s', async (action) => {
  fetchMock.mockResolvedValueOnce(response('CREATED')).mockImplementation(async () => response('PROCESSING'))
  const router = await open()
  await poll()
  expect(notification().exists()).toBe(true)
  if (action === 'jwt') setCustomerToken('test-another-customer')
  else if (action === 'route') await router.push('/orders/order-2')
  else { wrapper!.unmount(); wrapper = undefined }
  await flushPromises()
  if (wrapper) expect(notification().exists()).toBe(false)
  expect(vi.getTimerCount()).toBe(action === 'unmount' ? 0 : 1)
})

it('does not notify for event-only changes and updates events from the response', async () => {
  fetchMock.mockResolvedValueOnce(response('CREATED')).mockResolvedValueOnce(new Response(JSON.stringify({
    order_id: 'order-1', product_name: 'Bosch X100', quantity: 1, status: 'CREATED',
    created_at: '2026-10-10T10:00:00Z',
    events: [{ status: 'CREATED', occurred_at: '2026-10-10T10:00:00Z', source: 'SYSTEM' }],
  })))
  await open()
  await poll()
  expect(notification().exists()).toBe(false)
  expect(wrapper!.get('time').attributes('datetime')).toBe('2026-10-10T10:00:00Z')
})

it('retains the last successfully read status across a temporary read failure', async () => {
  fetchMock.mockResolvedValueOnce(response('CREATED'))
    .mockRejectedValueOnce(new TypeError('offline')).mockResolvedValueOnce(response('PROCESSING'))
  await open()
  await poll()
  expect(notification().exists()).toBe(false)
  await wrapper!.get('button').trigger('click')
  await flushPromises()
  expect(notification().text()).toContain(getOrderStatusMessage('PROCESSING'))
})

it.each([
  ['CREATED', 'Votre commande a été créée.'],
  ['PROCESSING', 'Votre commande est en préparation.'],
  ['SHIPPED', 'Votre commande a été expédiée.'],
  ['DELIVERED', 'Votre commande a été livrée.'],
  ['CANCELLED', 'Votre commande a été annulée.'],
] as [OrderStatus, string][])('maps %s deterministically', (status, message) => {
  expect(getOrderStatusMessage(status)).toBe(message)
})
