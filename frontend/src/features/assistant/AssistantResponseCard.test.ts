import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import AssistantResponseCard from './AssistantResponseCard.vue'
import AssistantConversation from './AssistantConversation.vue'
import OrderStatusBadge from '../../components/OrderStatusBadge.vue'
import type { AssistantResponse, OrderStatus } from '../../types/api'
import { setCustomerToken } from '../../utils/customerSession'
import { useAssistant } from './useAssistant'

const pending: AssistantResponse = {
  response: 'Confirmez-vous la commande ?',
  intent: 'ORDER', order_status: 'PENDING_CONFIRMATION',
  pending_order: { product_name: 'Bosch X100', quantity: 1, operation_id: 'op-1', status: 'PENDING_CONFIRMATION' },
}
const confirmed: AssistantResponse = {
  response: 'Commande creee.', intent: 'ORDER', order_status: 'CONFIRMED',
  confirmation_handled: true, created_order_id: 'order-1',
  pending_order: { ...pending.pending_order, status: 'CONFIRMED' },
}
let wrapper: VueWrapper | undefined
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  setCustomerToken('')
  vi.unstubAllGlobals()
})
const response = (body: AssistantResponse, status = 200) => new Response(JSON.stringify(body), { status })
async function conversation(fetchMock: ReturnType<typeof vi.fn<typeof fetch>>) {
  setCustomerToken('test-only-token')
  useAssistant().reset()
  vi.stubGlobal('fetch', fetchMock)
  wrapper = mount(AssistantConversation, { global: { stubs: { RouterLink: true } } })
  await wrapper.get('textarea').setValue('Commander Bosch X100')
  await wrapper.get('form.composer').trigger('submit')
  await flushPromises()
}

describe('Contextual response cards', () => {
  it('renders a structured preparation with explicit actions', () => {
    wrapper = mount(AssistantResponseCard, { props: { result: pending, actionable: true } })
    expect(wrapper.get('h3').text()).toBe('Commande à confirmer')
    expect(wrapper.text()).toContain('Bosch X100')
    expect(wrapper.text()).toContain('Quantité : 1')
    expect(wrapper.text()).toContain('Aucun stock n’est réservé à cette étape.')
    expect(wrapper.text()).toContain('La disponibilité sera revérifiée lors de la confirmation.')
    expect(wrapper.findAll('button')).toHaveLength(2)
  })

  it('shows the reservation explanation only while the response is a preparation', async () => {
    wrapper = mount(AssistantResponseCard, {
      props: { result: pending, actionable: true }, global: { stubs: { RouterLink: true } },
    })
    expect(wrapper.get('.confirmation-card').text()).toContain('Aucun stock n’est réservé à cette étape.')
    await wrapper.get('.confirmation-card button:first-child').trigger('click')
    expect(wrapper.emitted('confirm')).toEqual([[]])
    expect(wrapper.emitted('cancel')).toBeUndefined()
    await wrapper.setProps({ result: confirmed, actionable: false })
    expect(wrapper.text()).toContain('Commande créée')
    expect(wrapper.text()).not.toContain('Aucun stock n’est réservé à cette étape.')
    expect(wrapper.find('.confirmation-card').exists()).toBe(false)
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it.each(['CREATED', 'PROCESSING', 'SHIPPED', 'DELIVERED', 'CANCELLED'] as OrderStatus[])('renders the real %s status badge', (status) => {
    wrapper = mount(OrderStatusBadge, { props: { status } })
    expect(wrapper.text()).toContain(status)
    expect(wrapper.classes()).toContain(status.toLowerCase())
  })

  it('renders insufficient stock without parsing text or inventing availability', () => {
    wrapper = mount(AssistantResponseCard, { props: { actionable: false, result: {
      response: 'There are 999 units', intent: 'ORDER', order_status: 'INSUFFICIENT_STOCK',
      product_name: 'Bosch X100', quantity: 10,
    } } })
    expect(wrapper.text()).toContain('Stock insuffisant')
    expect(wrapper.text()).toContain('Demandé : 10')
    expect(wrapper.text()).not.toContain('999')
    expect(wrapper.text()).not.toContain('Disponible :')
  })

  it('does not show status cards from stale fields or prose', () => {
    wrapper = mount(AssistantResponseCard, { props: { actionable: true, result: {
      ...pending, intent: 'UNKNOWN', response: 'Commande créée CREATED',
      queried_order_id: 'old', queried_order_status: 'CREATED', order_status_result: 'FOUND',
    } } })
    expect(wrapper.find('section').exists()).toBe(false)
  })

  it('shows only the identified order and supported status on FOUND', () => {
    wrapper = mount(AssistantResponseCard, { global: { stubs: { RouterLink: true } }, props: { actionable: false, result: {
      intent: 'ORDER_STATUS', order_status_result: 'FOUND', queried_order_id: 'order-a',
      queried_order_status: 'SHIPPED', product_name: 'Stale product', quantity: 99,
    } } })
    expect(wrapper.text()).toContain('order-a')
    expect(wrapper.text()).toContain('SHIPPED')
    expect(wrapper.text()).not.toContain('Stale product')
    expect(wrapper.text()).not.toContain('99')
  })

  it.each(['NOT_FOUND', 'NO_ORDER_ASSOCIATED'])('does not create an order summary for %s', (result) => {
    wrapper = mount(AssistantResponseCard, { props: { actionable: false, result: {
      intent: 'ORDER_STATUS', order_status_result: result,
      queried_order_id: 'old', queried_order_status: 'CREATED',
    } } })
    expect(wrapper.find('section').exists()).toBe(false)
  })

  it('rejects unknown statuses and malformed pending payloads', () => {
    wrapper = mount(AssistantResponseCard, { props: { actionable: true, result: {
      intent: 'ORDER_STATUS', order_status_result: 'FOUND', queried_order_id: 'a', queried_order_status: 'PAID',
    } } })
    expect(wrapper.find('section').exists()).toBe(false)
    wrapper.unmount()
    wrapper = mount(AssistantResponseCard, { props: { actionable: true, result: {
      ...pending, pending_order: { product_name: 'Bosch X100', quantity: 1 },
    } } })
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('shows created order details and navigates to My Orders without guessing status', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/orders', component: { template: '<div />' } },
      { path: '/orders/:orderId', component: { template: '<div />' } },
    ] })
    await router.push('/')
    wrapper = mount(AssistantResponseCard, { props: { actionable: false, result: confirmed }, global: { plugins: [router] } })
    expect(wrapper.text()).toContain('Commande créée')
    expect(wrapper.text()).toContain('Bosch X100')
    expect(wrapper.text()).toContain('order-1')
    expect(wrapper.find('.status-badge').exists()).toBe(false)
    expect(wrapper.findAll('a')[0]!.attributes('href')).toBe('/orders/order-1')
    await wrapper.findAll('a')[1]!.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/orders')
  })

  it('confirms in the same thread, disables actions while loading and prevents double sends', async () => {
    let complete!: (value: Response) => void
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(response(pending))
      .mockReturnValueOnce(new Promise((resolve) => { complete = resolve }))
    await conversation(fetchMock)
    const confirm = wrapper!.get('.confirmation-card button:first-child')
    await confirm.trigger('click')
    await confirm.trigger('click')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(confirm.attributes('disabled')).toBeDefined()
    expect(wrapper!.get('textarea').attributes('disabled')).toBeDefined()
    expect(wrapper!.get('[role="status"]').text()).toContain('prépare')
    const original = JSON.parse(String(fetchMock.mock.calls[0]![1]?.body))
    const decision = JSON.parse(String(fetchMock.mock.calls[1]![1]?.body))
    expect(decision).toEqual({ thread_id: original.thread_id, message: 'Oui je confirme' })
    complete(response(confirmed))
    await flushPromises()
    expect(wrapper!.text()).toContain('Commande créée')
    expect(confirm.attributes('disabled')).toBeDefined()
  })

  it('abandons preparation conversationally without calling backoffice', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(response(pending))
      .mockResolvedValueOnce(response({ response: 'Commande annulée.', confirmation_handled: true, order_status: 'CANCELLED' }))
    await conversation(fetchMock)
    await wrapper!.get('.confirmation-card button:last-of-type').trigger('click')
    await flushPromises()
    expect(JSON.parse(String(fetchMock.mock.calls[1]![1]?.body)).message).toBe('Non annule')
    expect(fetchMock.mock.calls.every(([url]) => url === '/api/assistant')).toBe(true)
    expect(wrapper!.text()).toContain('Commande annulée.')
    expect(wrapper!.find('.status-badge').exists()).toBe(false)
  })

  it('locks decision actions after a failed confirmation and retains understandable errors', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(response(pending))
      .mockResolvedValueOnce(response({ response: 'Error' }, 500))
    await conversation(fetchMock)
    await wrapper!.get('.confirmation-card button:first-child').trigger('click')
    await flushPromises()
    expect(wrapper!.get('[role="alert"]').text()).toContain('serveur')
    await wrapper!.get('.confirmation-card button:first-child').trigger('click')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('removes private cards when the JWT changes', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(response(pending))
    await conversation(fetchMock)
    expect(wrapper!.find('.confirmation-card').exists()).toBe(true)
    const oldThread = useAssistant().state.threadId
    setCustomerToken('test-another-client')
    await flushPromises()
    expect(wrapper!.find('.confirmation-card').exists()).toBe(false)
    expect(useAssistant().state.threadId).not.toBe(oldThread)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})
