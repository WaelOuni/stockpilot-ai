import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import OrdersView from './OrdersView.vue'
import StockView from './StockView.vue'
import { createB2BSession, sessionKey } from './session'
import { json, sessionFixture, orderFixture, previewFixture, buyerFixture, list } from './fixtures'

beforeEach(() => {
  sessionStorage.clear()
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value(this: HTMLDialogElement) { this.setAttribute('open', '') } })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value(this: HTMLDialogElement) { this.removeAttribute('open') } })
})
async function setup(kind: 'orders' | 'stock', respond: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const fetcher = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => String(url) === '/b2b/auth/session' ? json(sessionFixture) : respond(String(url), init))
  const session = createB2BSession(fetcher); await session.restore()
  const component = kind === 'orders' ? OrdersView : StockView
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/b2b-app/' + kind, component }] })
  await router.push('/b2b-app/' + kind)
  const wrapper = mount(component, { global: { plugins: [router], provide: { [sessionKey as symbol]: session } } })
  await flushPromises()
  return { wrapper, session, fetcher }
}
it('uses exact totals, last/first pages and resets on status selection', async () => {
  const { wrapper, session, fetcher } = await setup('orders', url => {
    if (url.includes('/buyers')) return json(list([buyerFixture]))
    const params = new URL(url, 'https://example.invalid').searchParams, offset = Number(params.get('offset'))
    return json({ items: [orderFixture], total: 26, has_more: offset < 20, limit: 10, offset })
  })
  await wrapper.findAll('button').find(b => b.text() === 'Dernière')!.trigger('click'); await flushPromises()
  expect(String(fetcher.mock.lastCall![0])).toContain('offset=20')
  expect(wrapper.get('[aria-current=page]').text()).toBe('3')
  expect(wrapper.text()).toContain('Affichage de 21 à 26 sur 26')
  await wrapper.findAll('select').find(s => s.find('option[value=cancelled]').exists())!.setValue('cancelled'); await flushPromises()
  expect(String(fetcher.mock.lastCall![0])).toContain('offset=0')
  expect(String(fetcher.mock.lastCall![0])).toContain('status=cancelled')
  wrapper.unmount(); session.dispose()
})
it('requires archive confirmation and retries an uncertain outcome with the same key and CSRF', async () => {
  const calls: { operation_id: string; confirmed: boolean }[] = []
  const { wrapper, session } = await setup('orders', (url, init) => {
    if (url.includes('/buyers')) return json(list([]))
    if (url.endsWith('/archive')) {
      expect(init?.headers).toMatchObject({ 'X-BFF-CSRF': sessionFixture.csrf_token })
      const body = JSON.parse(String(init?.body)); calls.push(body)
      return calls.length === 1 ? json({ code: 'OUTCOME_UNKNOWN' }, 503) : json({ ...body, order_id: orderFixture.order_id, archived: true })
    }
    return json(list([{ ...orderFixture, status: 'cancelled' }]))
  })
  await wrapper.findAll('button').find(b => b.text() === 'Archiver pour moi')!.trigger('click')
  expect(calls).toHaveLength(0)
  await wrapper.findAll('button').find(b => b.text() === 'Confirmer l’archivage personnel')!.trigger('click'); await flushPromises()
  expect(calls).toHaveLength(1)
  await wrapper.findAll('button').find(b => b.text() === 'Réessayer la même préférence')!.trigger('click'); await flushPromises()
  expect(calls[1]).toEqual(calls[0]); expect(calls[0]?.confirmed).toBe(true)
  expect(wrapper.text()).toContain('La commande et les stocks sont conservés')
  wrapper.unmount(); session.dispose()
})
it('clamps a page that becomes empty after a reload', async () => {
  let total = 11
  const { wrapper, session, fetcher } = await setup('orders', url => {
    if (url.includes('/buyers')) return json(list([]))
    const offset = Number(new URL(url, 'https://example.invalid').searchParams.get('offset'))
    return json({ items: offset < total ? [orderFixture] : [], total, has_more: offset + 10 < total, limit: 10, offset })
  })
  await wrapper.findAll('button').find(b => b.text() === 'Dernière')!.trigger('click'); await flushPromises()
  total = 10
  await wrapper.findAll('button').find(b => b.text() === 'Actualiser les commandes')!.trigger('click'); await flushPromises()
  expect(String(fetcher.mock.lastCall![0])).toContain('offset=0')
  expect(wrapper.get('[aria-current=page]').text()).toBe('1')
  wrapper.unmount(); session.dispose()
})
it('keeps movement and import offsets independent and exposes no false last page', async () => {
  const { wrapper, session, fetcher } = await setup('stock', url => {
    const offset = Number(new URL(url, 'https://example.invalid').searchParams.get('offset'))
    if (url.includes('/movements')) return json({ items: [], total: 23, has_more: offset < 20, limit: 10, offset })
    return json({ items: [previewFixture], total: 12, has_more: offset === 0, limit: 10, offset })
  })
  const movements = wrapper.get('nav[aria-label="Pages des mouvements"]')
  const imports = wrapper.get('nav[aria-label="Pages des imports"]')
  await movements.findAll('button').find(b => b.text() === 'Dernière')!.trigger('click'); await flushPromises()
  expect(String(fetcher.mock.lastCall![0])).toContain('/movements?limit=10&offset=20')
  expect(imports.get('[aria-current=page]').text()).toBe('1')
  await imports.findAll('button').find(b => b.text() === 'Suivant')!.trigger('click'); await flushPromises()
  expect(String(fetcher.mock.lastCall![0])).toContain('/imports?limit=10&offset=10')
  expect(movements.get('[aria-current=page]').text()).toBe('3')
  wrapper.unmount(); session.dispose()
})
