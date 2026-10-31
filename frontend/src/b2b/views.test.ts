import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import CatalogueView from './CatalogueView.vue'
import OrdersView from './OrdersView.vue'
import StockView from './StockView.vue'
import OperationPanel from './OperationPanel.vue'
import { createB2BSession, sessionKey } from './session'
import { buyerFixture, json, list, orderFixture, previewFixture, productFixture, sessionFixture, stockFixture } from './fixtures'
import type { Component } from 'vue'

beforeEach(() => {
  sessionStorage.clear()
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value(this: HTMLDialogElement) { this.setAttribute('open', '') } })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value(this: HTMLDialogElement) { this.removeAttribute('open') } })
})
async function setup(component: Component, responder: (path: string, init?: RequestInit) => Response | Promise<Response>, permissions = sessionFixture.permissions) {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    return path === '/b2b/auth/session' ? json({ ...sessionFixture, permissions }) : responder(path, init)
  })
  const session = createB2BSession(fetcher)
  await session.restore()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div></div>' } }] })
  await router.push('/b2b-app/orders')
  const wrapper = mount(component, { global: { provide: { [sessionKey as symbol]: session }, plugins: [router] } })
  await flushPromises()
  return { wrapper, session, fetcher }
}
function response(path: string) {
  if (path.includes('/by-sku?')) return json(productFixture)
  if (path.endsWith('/stock')) return json(stockFixture)
  if (path.startsWith('/b2b/api/orders?')) return json(list([]))
  if (path.startsWith('/b2b/api/buyers?')) return json(list([buyerFixture]))
  if (path.startsWith('/b2b/api/stock/movements?') || path.startsWith('/b2b/api/imports?')) return json(list([]))
  throw new Error('Unexpected test route: ' + path)
}
describe('B2B views use strict wire contracts', () => {
  it('shows exact money, stocks and deterministic pagination from the catalogue', async () => {
    const { wrapper, session, fetcher } = await setup(CatalogueView, path => {
      if (path.includes('/stock')) return json(stockFixture)
      return json({ ...list([productFixture]), total: 13, has_more: true })
    })
    expect(wrapper.text()).toContain('90 071 992 547 409,93')
    expect(wrapper.text()).toMatch(/Disponible\s*10/)
    await wrapper.findAll('button').find(b => b.text() === 'Suivant')!.trigger('click'); await flushPromises()
    expect(fetcher.mock.calls.some(([p]) => String(p).includes('offset=12'))).toBe(true)
    wrapper.unmount(); session.dispose()
  })
  it('prepares without confirmation until a separate explicit user action', async () => {
    let confirms = 0
    const { wrapper, session } = await setup(OrdersView, path => {
      if (path.endsWith('/prepare')) return json(orderFixture)
      if (path.endsWith('/confirm')) { confirms++; return json({ ...orderFixture, status: 'reserved', reserved_at: '2026-10-21T10:01:00Z', revision: 2 }) }
      return response(path)
    })
    await wrapper.findAll('button').find(b => b.text() === 'Nouvelle commande')!.trigger('click')
    await wrapper.get('input').setValue('DRILL-001')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('Aucun stock réservé')
    expect(confirms).toBe(0)
    const button = wrapper.findAll('button').find(b => b.text() === 'Confirmer et réserver')!
    expect(button.attributes('disabled')).toBeDefined()
    await wrapper.get('input[type=checkbox]').setValue(true)
    await button.trigger('click'); await flushPromises()
    expect(confirms).toBe(1)
    expect(wrapper.text()).toContain('Réservée')
    expect(wrapper.find('input[type=checkbox]').exists()).toBe(false)
    wrapper.unmount(); session.dispose()
  })
  it('requires separate cancellation confirmation and waits for server success', async () => {
    let cancellations = 0
    const { wrapper, session } = await setup(OrdersView, path => {
      if (path.startsWith('/b2b/api/orders?')) return json(list([{ ...orderFixture, status: 'reserved' }]))
      if (path.endsWith('/cancel')) { cancellations++; return json({ ...orderFixture, status: 'cancelled' }) }
      return response(path)
    })
    await wrapper.findAll('button').find(b => b.text() === 'Annuler la commande')!.trigger('click')
    expect(cancellations).toBe(0)
    await wrapper.findAll('button').find(b => b.text() === 'Confirmer l’annulation')!.trigger('click'); await flushPromises()
    expect(cancellations).toBe(1)
    expect(wrapper.text()).toContain('État serveur : Annulée')
    wrapper.unmount(); session.dispose()
  })
  it('keeps an unknown confirmation reference and blocks a second new operation', async () => {
    const { wrapper, session, fetcher } = await setup(OrdersView, path => {
      if (path.endsWith('/prepare')) return json(orderFixture)
      if (path.endsWith('/confirm')) return json({ code: 'OUTCOME_UNKNOWN' }, 503)
      return response(path)
    })
    await wrapper.findAll('button').find(b => b.text() === 'Nouvelle commande')!.trigger('click')
    await wrapper.get('input').setValue('DRILL-001'); await wrapper.get('form').trigger('submit'); await flushPromises()
    await wrapper.get('input[type=checkbox]').setValue(true)
    await wrapper.findAll('button').find(b => b.text() === 'Confirmer et réserver')!.trigger('click'); await flushPromises()
    expect(session.pending.value).toHaveLength(1)
    expect(wrapper.findAll('button').find(b => b.text() === 'Confirmer et réserver')!.attributes('disabled')).toBeDefined()
    const posts = fetcher.mock.calls.filter(([p]) => String(p).endsWith('/confirm'))
    expect(posts).toHaveLength(1)
    wrapper.unmount(); session.dispose()
  })
  it('does not offer administration mutation controls to a read-only user', async () => {
    const { wrapper, session } = await setup(StockView, path => {
      if (path.includes('/by-sku?')) return json(productFixture)
      if (path.endsWith('/stock')) return json(stockFixture)
      return response(path)
    }, ['catalogue:read', 'stock:read'])
    await wrapper.get('input').setValue('DRILL-001')
    await wrapper.get('form').trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('consultation uniquement')
    expect(wrapper.text()).not.toContain('Enregistrer l’ajustement')
    expect(wrapper.text()).not.toContain('Importer un CSV')
    wrapper.unmount(); session.dispose()
  })
  it('refuses physical stock below reserved without sending a mutation', async () => {
    const { wrapper, session, fetcher } = await setup(StockView, path => {
      if (path.includes('/by-sku?')) return json(productFixture)
      if (path.endsWith('/stock')) return json({ ...stockFixture, reserved: 6, available: 4 })
      return response(path)
    })
    await wrapper.get('input').setValue('DRILL-001'); await wrapper.get('form').trigger('submit'); await flushPromises()
    await wrapper.findAll('button').find(b => b.text() === 'Ajuster le stock physique')!.trigger('click')
    const forms = wrapper.findAll('form')
    await forms[1]!.findAll('input')[0]!.setValue('5')
    await forms[1]!.findAll('input')[1]!.setValue('Inventaire')
    await forms[1]!.trigger('submit'); await flushPromises()
    expect(wrapper.get('[role=alert]').text()).toContain('inférieur au stock réservé')
    expect(fetcher.mock.calls.some(([p]) => String(p).endsWith('/adjustments'))).toBe(false)
    wrapper.unmount(); session.dispose()
  })
  it('CSV preview never applies until explicit confirmation, and file replacement clears approval', async () => {
    let applies = 0
    const { wrapper, session } = await setup(StockView, path => {
      if (path.includes('/imports/preview?')) return json(previewFixture)
      if (path.endsWith('/imports/apply')) { applies++; throw new Error('Not expected') }
      return response(path)
    })
    await wrapper.findAll('button').find(b => b.text() === 'Importer un CSV')!.trigger('click')
    const file = new File(['sku,quantity\nDRILL-001,9\n'], 'stock.csv', { type: 'text/csv' })
    Object.defineProperty(file, 'arrayBuffer', { value: async () => new TextEncoder().encode('sku,quantity\nDRILL-001,9\n').buffer })
    const input = wrapper.get('input[type=file]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await wrapper.findAll('form').find(f => f.find('input[type=file]').exists())!.trigger('submit'); await flushPromises()
    expect(wrapper.text()).toContain('Aperçu prêt')
    expect(applies).toBe(0)
    expect(wrapper.findAll('button').find(b => b.text() === 'Appliquer l’import')!.attributes('disabled')).toBeDefined()
    await wrapper.get('input[type=checkbox]').setValue(true)
    await input.trigger('change')
    expect(wrapper.find('input[type=checkbox]').exists()).toBe(false)
    wrapper.unmount(); session.dispose()
  })
  it('rejects a preview response if the file selection changed while it was in flight', async () => {
    let release!: (r: Response) => void
    const { wrapper, session } = await setup(StockView, path => {
      if (path.includes('/imports/preview?')) return new Promise<Response>(resolve => { release = resolve })
      return response(path)
    })
    await wrapper.findAll('button').find(b => b.text() === 'Importer un CSV')!.trigger('click')
    const file = new File(['sku,quantity\nDRILL-001,9\n'], 'a.csv', { type: 'text/csv' })
    Object.defineProperty(file, 'arrayBuffer', { value: async () => new TextEncoder().encode('sku,quantity\nDRILL-001,9\n').buffer })
    const input = wrapper.get('input[type=file]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await wrapper.findAll('form').find(f => f.find('input[type=file]').exists())!.trigger('submit'); await flushPromises()
    expect(input.attributes('disabled')).toBeDefined()
    // An event queued despite disabled controls must still fail the epoch guard.
    Object.defineProperty(input.element, 'files', { value: [new File([''], 'b.csv')], configurable: true })
    input.element.dispatchEvent(new Event('change', { bubbles: true }))
    await flushPromises()
    release(json(previewFixture)); await flushPromises()
    expect(wrapper.text()).not.toContain('Aperçu prêt')
    expect(wrapper.find('input[type=checkbox]').exists()).toBe(false)
    wrapper.unmount(); session.dispose()
  })
  it('reconciles a committed operation by GET, clears its reference and invalidates old drafts', async () => {
    const { wrapper, session, fetcher } = await setup(OperationPanel, () => json({ ...orderFixture, status: 'reserved' }))
    const operation = session.remember('orders', 'confirm')
    await flushPromises()
    await wrapper.get('button').trigger('click'); await flushPromises()
    expect(fetcher.mock.calls.some(([p]) => String(p).includes('operation_id=' + operation.id))).toBe(true)
    expect(session.pending.value).toHaveLength(0)
    expect(session.businessRevision.value).toBe(1)
    expect(wrapper.text()).toContain('réservée')
    session.remember('stock', 'adjust'); await flushPromises()
    expect(wrapper.text()).not.toContain('Commande order-a : réservée')
    wrapper.unmount(); session.dispose()
  })
})

it('opens supplementary product details and closes by keyboard without repeating price', async () => {
  const { wrapper, session } = await setup(CatalogueView, path => {
    if (path.endsWith('/stock')) return json(stockFixture)
    if (path.endsWith('/product-a')) return json(productFixture)
    return json({ ...list([productFixture]), total: 1, has_more: false })
  })
  await wrapper.findAll('button').find(b => b.text() === 'Voir le produit')!.trigger('click'); await flushPromises()
  const detail = wrapper.get('dialog[aria-label="Détail produit"]')
  expect(detail.attributes('open')).toBeDefined()
  expect(detail.text()).toContain(productFixture.description)
  expect(detail.text()).not.toContain('90 071 992 547 409,93')
  await detail.trigger('cancel')
  expect(wrapper.find('dialog').exists()).toBe(false)
  wrapper.unmount(); session.dispose()
})
it('opens and closes verified order details separately from the history', async () => {
  const { wrapper, session } = await setup(OrdersView, path => {
    if (path.startsWith('/b2b/api/orders?')) return json(list([orderFixture]))
    if (path.endsWith('/order-a')) return json(orderFixture)
    return response(path)
  })
  await wrapper.findAll('button').find(b => b.text() === 'Voir la commande')!.trigger('click'); await flushPromises()
  expect(wrapper.get('dialog').text()).toContain('DRILL-001')
  await wrapper.get('dialog button').trigger('click')
  expect(wrapper.find('dialog').exists()).toBe(false)
  expect(wrapper.get('table').text()).toContain('order-a')
  wrapper.unmount(); session.dispose()
})

it('keeps the order detail trigger mounted during its asynchronous load', async () => {
  let release!: (r: Response) => void
  const { wrapper, session } = await setup(OrdersView, path => {
    if (path.startsWith('/b2b/api/orders?')) return json(list([orderFixture]))
    if (path.endsWith('/order-a')) return new Promise<Response>(resolve => { release = resolve })
    return response(path)
  })
  const trigger = wrapper.findAll('button').find(b => b.text() === 'Voir la commande')!
  await trigger.trigger('click'); await flushPromises()
  expect(wrapper.get('table button').element).toBe(trigger.element)
  expect(trigger.attributes('disabled')).toBeDefined()
  release(json(orderFixture)); await flushPromises()
  await wrapper.get('dialog').trigger('cancel')
  expect(wrapper.get('table button').element).toBe(trigger.element)
  expect(trigger.attributes('disabled')).toBeUndefined()
  wrapper.unmount(); session.dispose()
})

it.each([9, 13])('rejects %s units using available rather than physical and preserves the draft', async requested => {
  const { wrapper, session, fetcher } = await setup(OrdersView, path => path.endsWith('/stock') ? json({ ...stockFixture, quantity: 12, reserved: 4, available: 8 }) : response(path))
  await wrapper.findAll('button').find(b => b.text() === 'Nouvelle commande')!.trigger('click')
  await wrapper.get('input').setValue('DRILL-001')
  await wrapper.findAll('input')[1]!.setValue(String(requested))
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.get('[role=alert]').text()).toContain('8 unités disponibles, ' + requested + ' demandées.')
  expect(wrapper.findAll('input')[1]!.element.value).toBe(String(requested))
  expect(fetcher.mock.calls.some(([p]) => String(p).endsWith('/prepare'))).toBe(false)
  wrapper.unmount(); session.dispose()
})
it('keeps stock private and unlocks after a definitive server refusal, without retry', async () => {
  const { wrapper, session, fetcher } = await setup(OrdersView, path => path.endsWith('/prepare') ? json({ code: 'STOCK_INSUFFICIENT', stock: { sku: 'DRILL-001', requested: 1, available: null } }, 409) : response(path), ['order:create'])
  await wrapper.findAll('button').find(b => b.text() === 'Nouvelle commande')!.trigger('click')
  await wrapper.get('input').setValue('DRILL-001'); await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.text()).toContain('Stock insuffisant pour la quantité demandée.')
  expect(wrapper.text()).not.toContain('unités disponibles')
  expect(session.pending.value).toHaveLength(0)
  expect(fetcher.mock.calls.filter(([p]) => String(p).endsWith('/prepare'))).toHaveLength(1)
  wrapper.unmount(); session.dispose()
})
