import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import AssistantResponseCard from './AssistantResponseCard.vue'
import { setCustomerToken } from '../../utils/customerSession'
import { sendAssistantMessage } from '../../api/client'
import type { AssistantResponse } from '../../types/api'

const recommendation = { sku: 'A/B', product_name: 'Outil réel', category: 'DRILL' as const, price: 99.5, stock: 2, explanation: 'Prix catalogue vérifié.' }
const result: AssistantResponse = { intent: 'PRODUCT_ADVICE', advice_status: 'RESULTS', recommendations: [recommendation], response: 'Correspond aux critères.' }
afterEach(() => { vi.unstubAllGlobals(); setCustomerToken('') })

describe('Catalogue-grounded chat cards', () => {
  it('renders product facts and a correctly encoded catalogue link', () => {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/products/:sku', component: { template: '<div />' } }] })
    const wrapper = mount(AssistantResponseCard, { props: { result, actionable: false }, global: { plugins: [router] } })
    expect(wrapper.text()).toContain('Outil réel')
    expect(wrapper.text()).toContain('A/B')
    expect(wrapper.text()).toContain('99,5')
    expect(wrapper.text()).toContain('Disponible')
    expect(wrapper.text()).toContain(recommendation.explanation)
    expect(wrapper.get('a').attributes('href')).toBe('/products/A%2FB')
    wrapper.unmount()
  })
  it.each(['ORDER', 'STOCK_CHECK', 'UNKNOWN'] as const)('never renders stale advice for %s', (intent) => {
    const wrapper = mount(AssistantResponseCard, { props: { result: { ...result, intent }, actionable: false } })
    expect(wrapper.find('[aria-label="Produit recommandé"]').exists()).toBe(false)
    wrapper.unmount()
  })
  it('escapes catalogue and explanation HTML', () => {
    const wrapper = mount(AssistantResponseCard, { props: { result: { ...result, recommendations: [{ ...recommendation, explanation: '<img src=x onerror=alert(1)>' }] }, actionable: false }, global: { stubs: { RouterLink: true } } })
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toContain('<img')
    wrapper.unmount()
  })
  it.each([
    { ...recommendation, price: -1 }, { ...recommendation, stock: -1 },
    { ...recommendation, explanation: undefined }, { ...recommendation, category: 'INVENTED' },
  ])('rejects malformed structured advice before rendering', async (product) => {
    setCustomerToken('test-only-token')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...result, recommendations: [product] }))))
    await expect(sendAssistantMessage({ message: 'Perceuse', thread_id: 'thread' })).rejects.toThrow('recommandations')
  })
})
