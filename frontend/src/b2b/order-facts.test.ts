import { afterEach, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import OrderFacts from './OrderFacts.vue'
import { orderDecoder } from './contracts'
import { orderFixture } from './fixtures'
import { language } from './preferences'
import { formatMoneyTotal } from './money'

afterEach(() => { language.value = 'fr' })
it('keeps exact large totals and separates currencies without inventing events or missing facts', () => {
  const order = orderDecoder({ ...orderFixture, lines: [
    { ...orderFixture.lines[0], price_minor: '9223372036854775807', quantity: 2 },
    { ...orderFixture.lines[0], product_id: 'jpy', sku: 'JPY-1', price_minor: '300', currency: 'JPY', quantity: 3 },
  ] })
  const wrapper = mount(OrderFacts, { props: { order, organization: 'Entreprise A' } })
  expect(wrapper.get('.order-totals').text()).toContain('184 467 440 737 095 516,14')
  expect(wrapper.get('.order-totals').text()).toContain('900')
  expect(wrapper.get('.order-totals').text()).toContain('JPY')
  expect(wrapper.findAll('.order-events li')).toHaveLength(1)
  expect(wrapper.text()).toContain('Non disponible')
  expect(wrapper.text()).toContain('Taxes, livraison et remises non renseignées')
  expect(wrapper.text()).not.toContain('Stock réservé pour cette commande')
  wrapper.unmount()
})
it('only displays recorded reservation and cancellation dates and translates their labels', () => {
  language.value = 'en'
  const order = orderDecoder({ ...orderFixture, status: 'cancelled', reserved_at: '2026-10-21T11:00:00Z', cancelled_at: '2026-10-21T12:00:00Z' })
  const wrapper = mount(OrderFacts, { props: { order, organization: 'Company A' } })
  expect(wrapper.findAll('.order-events time').map(item => item.attributes('datetime'))).toEqual([order.created_at, order.reserved_at, order.cancelled_at])
  expect(wrapper.text()).toContain('Reservation released')
  expect(wrapper.text()).toContain('Last updatedUnavailable')
  wrapper.unmount()
})
it('rejects negative calculated totals and never invents precision for unknown currencies', () => {
  expect(() => formatMoneyTotal(-1n, 'EUR')).toThrow()
  expect(formatMoneyTotal(123n, 'ZZZ')).toContain('précision inconnue')
})
