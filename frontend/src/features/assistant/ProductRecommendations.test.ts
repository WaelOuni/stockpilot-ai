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
    expect(wrapper.text()).toContain('99,50')
    expect(wrapper.text()).toContain('Disponible')
    expect(wrapper.text()).toContain('Perceuse')
    expect(wrapper.text()).not.toContain('DRILL')
    expect(wrapper.text()).not.toContain('€')
    expect(wrapper.text()).toContain('prix et stock non actualisés')
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
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

it('escapes long product names, SKU and legacy explanation text without enabling actions', () => {
  const malicious = '<script>window.untrusted = true</script>'
  const wrapper = mount(AssistantResponseCard, { props: {
    result: { ...result, recommendations: [{ ...recommendation,
      product_name: malicious + 'Outil très long '.repeat(40), sku: '<img src=x>',
      explanation: 'Catégorie catalogue : DRILL. Prix catalogue : 99.5.', stock: 0,
    }] }, actionable: false,
  }, global: { stubs: { RouterLink: true } } })
  expect(wrapper.find('script').exists()).toBe(false)
  expect(wrapper.find('img').exists()).toBe(false)
  expect(wrapper.get('h2').text()).toContain(malicious)
  expect(wrapper.get('details').text()).toContain('Catégorie catalogue')
  expect(wrapper.text()).toContain('Indisponible')
  expect(wrapper.find('button').exists()).toBe(false)
  wrapper.unmount()
})

it('renders several cards with explicit comparison roles', () => {
  const reference = { ...recommendation, role: 'reference' as const, matches_criteria: false, price_difference: 0 }
  const alternative = { ...recommendation, sku: 'B', product_name: 'Alternative réelle', role: 'alternative' as const, matches_criteria: true, price_difference: -20, price: 79.5 }
  const wrapper = mount(AssistantResponseCard, { props: { result: { ...result, recommendations: [reference, alternative] }, actionable: false }, global: { stubs: { RouterLink: true } } })
  expect(wrapper.findAll('article')).toHaveLength(2)
  expect(wrapper.get('[aria-label="Produit de référence"]').text()).toContain('Hors critères')
  expect(wrapper.get('[aria-label="Alternative"]').text()).toContain('Alternative réelle')
  wrapper.unmount()
})

it.each(['NO_RESULTS', 'CLARIFICATION_REQUIRED', 'CATALOGUE_UNAVAILABLE'] as const)('renders no old cards for %s', (advice_status) => {
  const wrapper = mount(AssistantResponseCard, { props: { result: { ...result, advice_status, recommendations: [] }, actionable: false } })
  expect(wrapper.findAll('article')).toHaveLength(0)
  wrapper.unmount()
})

it('accepts backward compatible cards and valid comparison metadata', async () => {
  setCustomerToken('test-only-token')
  const products = [
    { ...recommendation, role: 'reference', matches_criteria: false, price_difference: 0 },
    { ...recommendation, sku: 'B', price: 79.5, role: 'alternative', matches_criteria: true, price_difference: -20 },
  ]
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...result, recommendations: products }))))
  expect((await sendAssistantMessage({ message: 'Compare', thread_id: 'thread' })).recommendations).toHaveLength(2)
})

it.each([
  { role: 'alternative', matches_criteria: true, price_difference: 0 },
  { role: 'reference', matches_criteria: true, price_difference: 10 },
  { role: 'reference', matches_criteria: 'yes', price_difference: 0 },
  { role: 'reference', price_difference: 0 },
])('refuses inconsistent comparison metadata', async (metadata) => {
  setCustomerToken('test-only-token')
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...result, recommendations: [{ ...recommendation, ...metadata }] }))))
  await expect(sendAssistantMessage({ message: 'Compare', thread_id: 'thread' })).rejects.toThrow('comparaison')
})
