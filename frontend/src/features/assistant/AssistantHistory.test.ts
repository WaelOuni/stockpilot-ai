import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import AssistantConversation from './AssistantConversation.vue'
import AssistantResponseCard from './AssistantResponseCard.vue'
import { useAssistant } from './useAssistant'
import { setCustomerToken } from '../../utils/customerSession'
import type { AssistantResponse, ProductRecommendation } from '../../types/api'

const products: ProductRecommendation[] = ['A', 'B', 'C'].map((sku, index) => ({
  sku, product_name: `Perceuse ${sku}`, category: 'DRILL', price: 49.9 + index, stock: index + 1,
  explanation: 'Cette perceuse correspond aux critères.',
}))
const advice = (items = products): AssistantResponse => ({
  intent: 'PRODUCT_ADVICE', advice_status: 'RESULTS', recommendations: items, response: 'Voici les perceuses trouvées.',
})
const pending = (id: string): AssistantResponse => ({
  intent: 'ORDER', order_status: 'PENDING_CONFIRMATION', response: 'Confirmez-vous cette commande ?',
  pending_order: { operation_id: id, status: 'PENDING_CONFIRMATION', product_name: 'Perceuse A', quantity: 1 },
})
const reply = (data: AssistantResponse, status = 200) => new Response(JSON.stringify(data), { status })
let wrapper: VueWrapper
const fetchMock = vi.fn<typeof fetch>()
function render() {
  wrapper = mount(AssistantConversation, { global: { stubs: { RouterLink: true } } })
}
async function send(message: string) {
  await wrapper.get('textarea').setValue(message)
  await wrapper.get('form.composer').trigger('submit')
  await flushPromises()
}
beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  setCustomerToken('synthetic-client-a')
  useAssistant().reset()
})
afterEach(() => { wrapper?.unmount(); setCustomerToken(''); vi.unstubAllGlobals() })

describe('Message-owned Advice snapshots', () => {
  it('keeps three cards during the next request, after STOCK_CHECK and next to a new targeted snapshot', async () => {
    let complete!: (value: Response) => void
    fetchMock.mockResolvedValueOnce(reply(advice()))
      .mockReturnValueOnce(new Promise(resolve => { complete = resolve }))
      .mockResolvedValueOnce(reply(advice([{ ...products[0]!, price: 70, stock: 0 }])))
    render()
    await send('Trois perceuses')
    expect(wrapper.findAll('.message.assistant article.recommendation')).toHaveLength(3)
    await send('Le stock ?')
    expect(wrapper.findAll('article.recommendation')).toHaveLength(3)
    expect(wrapper.get('[role="status"]').text()).toContain('prépare')
    complete(reply({ intent: 'STOCK_CHECK', response: 'Stock courant vérifié.' }))
    await flushPromises()
    expect(wrapper.findAll('article.recommendation')).toHaveLength(3)
    await send('La fiche A')
    const messages = wrapper.findAll('.message.assistant')
    expect(messages.map(message => message.findAll('article.recommendation').length)).toEqual([3, 0, 1])
    expect(messages[0]!.text()).toContain('49,90')
    expect(messages[0]!.text()).toContain('1 unité')
    expect(messages[2]!.text()).toContain('70,00')
    expect(messages[2]!.text()).toContain('Indisponible')
    expect(wrapper.findAll('.snapshot-notice')).toHaveLength(2)
    expect(wrapper.findAll('.catalogue-convention')).toHaveLength(1)
    expect(useAssistant().state.result?.recommendations).toHaveLength(1)
  })

  it.each(['NO_RESULTS', 'CLARIFICATION_REQUIRED', 'CATALOGUE_UNAVAILABLE'] as const)(
    'retains history but attaches no cards to a %s answer', async advice_status => {
      fetchMock.mockResolvedValueOnce(reply(advice())).mockResolvedValueOnce(reply({
        intent: 'PRODUCT_ADVICE', advice_status, recommendations: [], response: 'Modifiez vos critères ou réessayez plus tard.',
      }))
      render(); await send('Trois produits'); await send('Autre question')
      expect(wrapper.findAll('article.recommendation')).toHaveLength(3)
      expect(wrapper.findAll('.message.assistant')[1]!.findAll('article.recommendation')).toHaveLength(0)
    },
  )

  it('keeps snapshots on a network error with helpful text and no automatic retry', async () => {
    fetchMock.mockResolvedValueOnce(reply(advice())).mockRejectedValueOnce(new TypeError('Failed to fetch'))
    render(); await send('Trois produits'); await send('Autre question')
    expect(wrapper.findAll('article.recommendation')).toHaveLength(3)
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Votre historique est conservé')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it.each(['new thread', 'new customer'] as const)('clears all private snapshots on %s', async mode => {
    fetchMock.mockResolvedValueOnce(reply(advice()))
    render(); await send('Trois produits')
    const oldThread = useAssistant().state.threadId
    if (mode === 'new thread') await wrapper.get('.conversation-toolbar button').trigger('click')
    else setCustomerToken('synthetic-client-b')
    await flushPromises()
    expect(wrapper.findAll('.message')).toHaveLength(0)
    expect(wrapper.findAll('article.recommendation')).toHaveLength(0)
    expect(wrapper.find('.catalogue-convention').exists()).toBe(false)
    expect(useAssistant().state.threadId).not.toBe(oldThread)
    setCustomerToken('synthetic-client-a'); await flushPromises()
    expect(wrapper.findAll('.message')).toHaveLength(0)
  })

  it('cannot reveal late Advice from another customer after abort', async () => {
    let complete!: (value: Response) => void
    fetchMock.mockResolvedValueOnce(reply(advice())).mockReturnValueOnce(new Promise(resolve => { complete = resolve }))
    render(); await send('Trois produits'); await send('Une autre fiche')
    setCustomerToken('synthetic-client-b')
    complete(reply(advice())); await flushPromises()
    expect(wrapper.findAll('.message')).toHaveLength(0)
    expect(wrapper.text()).not.toContain('Perceuse A')
    expect(fetchMock.mock.calls[1]![1]!.signal?.aborted).toBe(true)
  })
})

describe('Historical ORDER actions stay inactive', () => {
  it.each(['confirm', 'cancel'] as const)('never reactivates old actions before or after %s', async decision => {
    fetchMock.mockResolvedValueOnce(reply(advice()))
      .mockResolvedValueOnce(reply(pending('old-op')))
      .mockResolvedValueOnce(reply({ intent: 'STOCK_CHECK', response: 'Stock courant.' }))
      .mockResolvedValueOnce(reply(pending('new-op')))
      .mockResolvedValueOnce(reply({ intent: 'ORDER', response: 'Décision traitée.', confirmation_handled: true,
        order_status: decision === 'confirm' ? 'CONFIRMED' : 'CANCELLED',
        ...(decision === 'confirm' ? { created_order_id: 'synthetic-order' } : {}),
      }))
    render(); await send('Trois produits'); await send('Préparer une commande')
    const original = wrapper.findAllComponents(AssistantResponseCard)[1]!
    await send('Stock courant ?')
    expect(original.get('button').attributes('disabled')).toBeDefined()
    await send('Une nouvelle commande')
    expect(wrapper.findAll('article.recommendation')).toHaveLength(3)
    expect(original.get('button').attributes('disabled')).toBeDefined()
    original.vm.$emit('confirm'); original.vm.$emit('cancel'); await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(4)
    const current = wrapper.findAll('.confirmation-card')[1]!
    const buttons = current.findAll('button')
    await buttons[decision === 'confirm' ? 0 : 1]!.trigger('click'); await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(5)
    expect(JSON.parse(String(fetchMock.mock.calls[4]![1]?.body)).message).toBe(decision === 'confirm' ? 'Oui je confirme' : 'Non annule')
    expect(wrapper.findAll('.confirmation-card button').every(button => button.attributes('disabled') !== undefined)).toBe(true)
    original.vm.$emit('confirm'); await flushPromises()
    expect(fetchMock).toHaveBeenCalledTimes(5)
  })
})

describe('Conversation scrolling', () => {
  it('follows new content at the bottom, preserves an older reading position and offers a working latest-response action', async () => {
    fetchMock.mockImplementation(async () => reply(advice()))
    render(); await send('Trois produits')
    const log = wrapper.get('[role="log"]')
    const element = log.element as HTMLElement
    const scrollTo = vi.fn()
    element.scrollTo = scrollTo
    Object.defineProperties(element, {
      scrollHeight: { configurable: true, value: 2000 },
      clientHeight: { configurable: true, value: 400 },
      scrollTop: { configurable: true, writable: true, value: 1600 },
    })
    await log.trigger('scroll'); await send('Encore')
    expect(scrollTo).toHaveBeenCalledWith({ top: 2000, behavior: 'auto' })
    scrollTo.mockClear(); element.scrollTop = 0; await log.trigger('scroll')
    await send('Autre question')
    expect(scrollTo).not.toHaveBeenCalled()
    expect(element.scrollTop).toBe(0)
    await wrapper.get('button.conversation-latest').trigger('click')
    expect(scrollTo).toHaveBeenCalledWith({ top: 2000, behavior: 'auto' })
    expect(wrapper.find('button.conversation-latest').exists()).toBe(false)
  })
})
