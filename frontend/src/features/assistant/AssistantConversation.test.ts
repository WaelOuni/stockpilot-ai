import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import AssistantPage from '../../pages/AssistantPage.vue'
import { setCustomerToken } from '../../utils/customerSession'
import { useAssistant } from './useAssistant'

const fetchMock = vi.fn<typeof fetch>()
let wrapper: VueWrapper | undefined
function response(text: string, orderStatus?: string) {
  return new Response(JSON.stringify({ response: text, order_status: orderStatus }), { status: 200 })
}
async function send(text: string) {
  await wrapper!.get('textarea').setValue(text)
  await wrapper!.get('form.composer').trigger('submit')
  await flushPromises()
}
beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  setCustomerToken('test-client-a')
  useAssistant().reset()
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  setCustomerToken('')
  vi.unstubAllGlobals()
})

describe('Assistant page', () => {
  it('renders an accessible empty conversation and blocks sending without JWT', () => {
    setCustomerToken('')
    wrapper = mount(AssistantPage)
    expect(wrapper.get('h1').text()).toBe('Assistant')
    expect(wrapper.get('[role="log"]').text()).toContain('prochain projet')
    expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('supports order preparation then confirmation using the same thread', async () => {
    fetchMock.mockResolvedValueOnce(response('Confirmez votre commande.', 'PENDING_CONFIRMATION'))
    fetchMock.mockResolvedValueOnce(response('Commande creee.', 'CONFIRMED'))
    wrapper = mount(AssistantPage)
    await send('Je veux commander 2 Bosch X100')
    expect(wrapper.get('.order-prompt').text()).toContain('décision')
    await send('oui')
    expect(wrapper.find('.order-prompt').exists()).toBe(false)
    expect(wrapper.findAll('.message')).toHaveLength(4)
    const first = JSON.parse(String(fetchMock.mock.calls[0]![1]?.body))
    const second = JSON.parse(String(fetchMock.mock.calls[1]![1]?.body))
    expect(first.thread_id).toBe(second.thread_id)
    expect(second.message).toBe('oui')
    expect(wrapper.text()).toContain('Commande creee.')
  })

  it('renders backend text as text, not HTML', async () => {
    fetchMock.mockResolvedValue(response('<img src=x onerror=alert(1)>'))
    wrapper = mount(AssistantPage)
    await send('Bonjour')
    expect(wrapper.find('.message.assistant img').exists()).toBe(false)
    expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>')
  })

  it('keeps the conversation when navigating away and starts a new thread on reset', async () => {
    fetchMock.mockResolvedValue(response('Bonjour'))
    wrapper = mount(AssistantPage)
    await send('Bonjour')
    const previousThread = useAssistant().state.threadId
    wrapper.unmount()
    wrapper = mount(AssistantPage)
    expect(wrapper.text()).toContain('Bonjour')
    await wrapper.get('.conversation-toolbar button').trigger('click')
    expect(useAssistant().state.threadId).not.toBe(previousThread)
    expect(wrapper.findAll('.message')).toHaveLength(0)
  })

  it('keeps previous advice as snapshots after a stock turn, attached to its original message', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({
      intent: 'PRODUCT_ADVICE', advice_status: 'RESULTS', response: 'Conseil vérifié',
      recommendations: [{ sku: 'A', product_name: 'Perceuse A', category: 'DRILL', price: 60, stock: 2, explanation: 'Stock vérifié' }],
    })))
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ intent: 'STOCK_CHECK', response: 'Stock courant' })))
    wrapper = mount(AssistantPage, { global: { stubs: { RouterLink: true } } })
    await send('Je cherche une perceuse')
    expect(wrapper.findAll('[aria-label="Produit recommandé"]')).toHaveLength(1)
    await send('Le stock du SKU A ?')
    expect(wrapper.findAll('[aria-label="Produit recommandé"]')).toHaveLength(1)
    expect(wrapper.findAll('.message.assistant')[0]!.text()).toContain('Instantané')
    expect(wrapper.findAll('.message.assistant')[1]!.find('article.recommendation').exists()).toBe(false)
    expect(wrapper.text()).toContain('Conseil vérifié')
    expect(wrapper.text()).toContain('Stock courant')
  })

  it('shows HTTP errors without retrying confirmation automatically', async () => {
    fetchMock.mockResolvedValue(new Response('{"detail":"Unauthorized"}', { status: 401 }))
    wrapper = mount(AssistantPage)
    await send('oui')
    expect(wrapper.get('[role="alert"]').text()).toContain('JWT')
    expect(wrapper.text()).toContain('Aucune relance automatique')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('discards an old in-flight response and rotates thread when token changes', async () => {
    let resolve!: (response: Response) => void
    fetchMock.mockReturnValue(new Promise<Response>((done) => { resolve = done }))
    wrapper = mount(AssistantPage)
    await wrapper.get('textarea').setValue('Commande privee')
    await wrapper.get('form.composer').trigger('submit')
    const oldThread = useAssistant().state.threadId
    const signal = fetchMock.mock.calls[0]![1]?.signal
    setCustomerToken('test-client-b')
    resolve(response('Ancienne reponse privee'))
    await flushPromises()
    expect(signal?.aborted).toBe(true)
    expect(useAssistant().state.threadId).not.toBe(oldThread)
    expect(wrapper.text()).not.toContain('Ancienne reponse privee')
    expect(wrapper.findAll('.message')).toHaveLength(0)
  })
})
