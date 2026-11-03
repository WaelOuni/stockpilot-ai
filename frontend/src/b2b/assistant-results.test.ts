import { expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AssistantCatalogueResults from './AssistantCatalogueResults.vue'
import { createB2BSession, sessionKey } from './session'
import { json, sessionFixture, productFixture, stockFixture } from './fixtures'

const card = { ...productFixture, ...stockFixture, observed_at: '2026-11-04T10:00:00Z' }
const response = {
  items: [card], total: 126, has_more: true, limit: 20, offset: 0, observed_at: card.observed_at,
  filters: { minimum_available: 5, maximum_available: 5, exclude_sold_out: false, sort: 'available' },
}
async function setup(reply: (url: string) => Response | Promise<Response> = () => json(response)) {
  const fetcher = vi.fn(async (url: RequestInfo | URL) => String(url) === '/b2b/auth/session' ? json(sessionFixture) : reply(String(url)))
  const session = createB2BSession(fetcher); await session.restore()
  const wrapper = mount(AssistantCatalogueResults, {
    props: { conversationId: 'conversation', turnId: 'turn' },
    global: { provide: { [sessionKey as symbol]: session } },
  })
  return { wrapper, session, fetcher }
}
it('opens bounded current data, pages server-side and resets offset after explicit filters', async () => {
  const { wrapper, session, fetcher } = await setup()
  expect(fetcher).toHaveBeenCalledTimes(1)
  await wrapper.get('button').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('Les cartes du message initial restent inchangées')
  expect(wrapper.text()).toContain('126')
  await wrapper.findAll('button').find(b => b.text() === 'Suivant')!.trigger('click'); await flushPromises()
  expect(String(fetcher.mock.lastCall![0])).toContain('offset=20')
  await wrapper.get('input[type=checkbox]').setValue(true)
  await wrapper.findAll('input')[0]!.setValue('1')
  await wrapper.findAll('input')[1]!.setValue('14')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  const query = new URL(String(fetcher.mock.lastCall![0]), 'https://synthetic.invalid').searchParams
  expect(Object.fromEntries(query)).toMatchObject({ offset: '0', minimum_available: '1', maximum_available: '14', exclude_sold_out: 'true' })
  expect(wrapper.get('[aria-current=page]').text()).toBe('1')
  wrapper.unmount(); session.dispose()
})
it.each(['-1', '1.5', '9007199254740992'])('rejects invalid stock input %s without a request', async input => {
  const { wrapper, session, fetcher } = await setup()
  await wrapper.get('button').trigger('click'); await flushPromises()
  const before = fetcher.mock.calls.length
  await wrapper.findAll('input')[0]!.setValue(input)
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.get('[role=alert]').text()).toContain('entiers positifs ou nuls')
  expect(fetcher.mock.calls).toHaveLength(before)
  wrapper.unmount(); session.dispose()
})
it('drops a late page and clears data when the account changes', async () => {
  let deliver!: (response: Response) => void
  const { wrapper, session } = await setup(() => new Promise(resolve => { deliver = resolve }))
  await wrapper.get('button').trigger('click'); await flushPromises()
  session.current.value = { ...session.current.value!, user_id: 'different-user' }
  await flushPromises(); deliver(json(response)); await flushPromises()
  expect(wrapper.text()).not.toContain('Perceuse A')
  expect(wrapper.text()).toContain('Voir plus de produits')
  wrapper.unmount(); session.dispose()
})
it('reports empty results and network errors without automatic retry', async () => {
  const { wrapper, session, fetcher } = await setup(() => json({ ...response, items: [], total: 0, has_more: false }))
  await wrapper.get('button').trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('Aucun produit')
  fetcher.mockRejectedValueOnce(new Error('network'))
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(wrapper.find('[role=alert]').exists()).toBe(true)
  expect(fetcher).toHaveBeenCalledTimes(3)
  wrapper.unmount(); session.dispose()
})
