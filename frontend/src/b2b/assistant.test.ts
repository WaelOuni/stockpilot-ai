import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import AssistantView from './AssistantView.vue'
import B2BApp from './B2BApp.vue'
import { assistantTurn } from './assistant'
import { createB2BSession, sessionKey } from './session'
import { json, sessionFixture, orderFixture, buyerFixture, list } from './fixtures'

beforeEach(() => sessionStorage.clear())

const turn = {
  turn_id: 'turn', kind: 'message', message: 'question', status: 'completed', created_at: '2026-10-21T10:00:00Z',
  operation: null, reply: { intent: 'ORDER_PREPARE', text: 'Aucun stock réservé.', products: [], order: orderFixture, can_confirm: true },
}
async function setup(respond: (path: string, init?: RequestInit) => Response | Promise<Response>) {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => String(input) === '/b2b/auth/session'
    ? json({ ...sessionFixture, assistant_available: true }) : respond(String(input), init))
  const session = createB2BSession(fetcher)
  await session.restore()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/b2b-app/assistant/:conversationId?', component: AssistantView }] })
  await router.push('/b2b-app/assistant/conv')
  const wrapper = mount(AssistantView, { global: { plugins: [router], provide: { [sessionKey as symbol]: session } } })
  await flushPromises()
  return { wrapper, session, fetcher, router }
}
function respond(path: string) {
  if (path.includes('/buyers?')) return json(list([buyerFixture]))
  if (path.includes('/conversations?')) return json(list([{ conversation_id: 'conv', created_at: turn.created_at }]))
  return json({ conversation_id: 'conv', items: [turn], has_more: false, limit: 20, offset: 0 })
}
it('rejects malformed amounts in assistant cards', () => {
  expect(() => assistantTurn({ ...turn, reply: { ...turn.reply, products: [{ price_minor: 4.9 }] } })).toThrow()
})
it('restores snapshots and requires distinct confirmation with CSRF', async () => {
  const { wrapper, session, fetcher } = await setup((path, init) => {
    if (path.endsWith('/confirm')) {
      expect(init?.headers).toMatchObject({ 'X-BFF-CSRF': sessionFixture.csrf_token })
      expect(JSON.parse(String(init?.body)).confirmed).toBe(true)
      return json({ ...turn, kind: 'confirm', reply: { ...turn.reply, order: { ...orderFixture, status: 'reserved' }, can_confirm: false } })
    }
    return respond(path)
  })
  const button = wrapper.findAll('button').find(b => b.text() === 'Confirmer et réserver')!
  expect(button.attributes('disabled')).toBeDefined()
  expect(fetcher.mock.calls.filter(([p]) => String(p).endsWith('/confirm'))).toHaveLength(0)
  await wrapper.get('input[type=checkbox]').setValue(true)
  await button.trigger('click'); await flushPromises()
  expect(fetcher.mock.calls.filter(([p]) => String(p).endsWith('/confirm'))).toHaveLength(1)
  wrapper.unmount(); session.dispose()
})
it('blocks new messages after uncertain response until durable history is checked', async () => {
  const { wrapper, session, fetcher } = await setup(path => path.endsWith('/messages') ? json({ code: 'OUTCOME_UNKNOWN' }, 503) : respond(path))
  await wrapper.get('textarea').setValue('Question documentaire')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.text()).toContain('Une réponse reste à vérifier')
  await wrapper.get('textarea').setValue('Brouillon suivant')
  expect(wrapper.findAll('button').find(b => b.text() === 'Envoyer le message')!.attributes('disabled')).toBeDefined()
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.get('textarea').attributes('disabled')).toBeUndefined()
  expect(fetcher.mock.calls.filter(([p]) => String(p).endsWith('/messages'))).toHaveLength(1)
  wrapper.unmount(); session.dispose()
})
it('late message response after unmount cannot repopulate tenant state', async () => {
  let deliver!: (value: Response) => void
  const { wrapper, session } = await setup(path => path.endsWith('/messages') ? new Promise<Response>(resolve => { deliver = resolve }) : respond(path))
  await wrapper.get('textarea').setValue('question')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
  wrapper.unmount()
  await session.logout()
  deliver(json(turn)); await flushPromises()
  expect(session.current.value).toBeNull()
  session.dispose()
})

it('global pending operation blocks conversational mutations until reconciliation', async () => {
  const { wrapper, session, fetcher } = await setup(respond)
  session.remember('orders', 'confirm')
  await flushPromises()
  expect(wrapper.get('textarea').attributes('disabled')).toBeUndefined()
  const button = wrapper.findAll('button').find(b => b.text() === 'Confirmer et réserver')!
  expect(button.attributes('disabled')).toBeDefined()
  expect(fetcher.mock.calls.filter(([p]) => String(p).endsWith('/confirm') || String(p).endsWith('/messages'))).toHaveLength(0)
  await wrapper.get('textarea').setValue('Question documentaire')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  const call = fetcher.mock.calls.find(([p]) => String(p).endsWith('/messages'))!
  expect(JSON.parse(String(call[1]?.body))).toMatchObject({ read_only: true, message: 'Question documentaire' })
  expect(session.pending.value).toHaveLength(1)
  wrapper.unmount(); session.dispose()
})

it('creating from an existing conversation reloads privately; late response after navigation stays discarded', async () => {
  let deliver!: (value: Response) => void
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input)
    if (path === '/b2b/auth/session') return json({ ...sessionFixture, assistant_available: true })
    if (path.includes('/buyers?')) return json(list([buyerFixture]))
    if (path.endsWith('/messages')) return new Promise<Response>(resolve => { deliver = resolve })
    if (path.endsWith('/conversations')) return json({ conversation_id: 'new' })
    if (path.includes('/conversations?')) return json(list([{ conversation_id: 'old', created_at: turn.created_at }, { conversation_id: 'new', created_at: turn.created_at }]))
    return json({ conversation_id: path.includes('/new?') ? 'new' : 'old',
      items: path.includes('/new?') ? [] : [{ ...turn, message: 'Ancienne conversation privée' }], has_more: false, limit: 20, offset: 0 })
  }))
  const router = createRouter({ history: createMemoryHistory(), routes: [{
    path: '/b2b-app/', component: B2BApp, children: [{ path: 'assistant/:conversationId?', component: AssistantView }],
  }] })
  await router.push('/b2b-app/assistant/old')
  const wrapper = mount({ template: '<RouterView />' }, { global: { plugins: [router] } })
  await flushPromises()
  expect(wrapper.text()).toContain('Ancienne conversation privée')
  await wrapper.findAll('button').find(b => b.text() === 'Nouvelle conversation')!.trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/b2b-app/assistant/new')
  expect(wrapper.text()).not.toContain('Ancienne conversation privée')
  await wrapper.get('textarea').setValue('Nouvelle question')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  await router.push('/b2b-app/assistant/old'); await flushPromises()
  deliver(json({ ...turn, message: 'Réponse tardive nouvelle conversation' })); await flushPromises()
  expect(wrapper.text()).toContain('Ancienne conversation privée')
  expect(wrapper.text()).not.toContain('Réponse tardive nouvelle conversation')
  wrapper.unmount(); vi.unstubAllGlobals()
})

it('separates user, assistant and preparation messages while preserving history', async () => {
  const { wrapper, session } = await setup(respond)
  expect(wrapper.get('.assistant-user').text()).toContain('question')
  expect(wrapper.get('.assistant-answer').text()).toContain('StockPilot')
  expect(wrapper.get('.assistant-order-summary').text()).toContain('DRILL-001')
  expect(wrapper.find('.assistant-order').exists()).toBe(true)
  expect(wrapper.get('option[value="conv"]').text()).not.toContain('conv')
  wrapper.unmount(); session.dispose()
})

it('still lets the user clear an explicitly selected buyer without preparing an order', async () => {
  const { wrapper, session, fetcher } = await setup(respond)
  const buyer = wrapper.findAll('select').find(s => s.find('option[value="buyer-a"]').exists())!
  await buyer.setValue('')
  expect((buyer.element as HTMLSelectElement).value).toBe('')
  expect(fetcher.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
  wrapper.unmount(); session.dispose()
})

it('keeps new typing when an earlier submitted message replies late', async () => {
  let deliver!: (value: Response) => void
  const { wrapper, session } = await setup(path => path.endsWith('/messages') ? new Promise<Response>(resolve => { deliver = resolve }) : respond(path))
  await wrapper.get('textarea').setValue('First question')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  await wrapper.get('textarea').setValue('Next unsent question')
  deliver(json(turn)); await flushPromises()
  expect(wrapper.get('textarea').element.value).toBe('Next unsent question')
  wrapper.unmount(); session.dispose()
})
