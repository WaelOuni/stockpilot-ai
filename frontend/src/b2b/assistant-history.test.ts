import { beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import AssistantHistoryView from './AssistantHistoryView.vue'
import { createB2BSession, sessionKey } from './session'
import { json, sessionFixture } from './fixtures'
import { language } from './preferences'

beforeEach(() => { sessionStorage.clear(); language.value = 'fr' })
it('pages twenty scoped conversations with explicit empty state and links', async () => {
  const items = Array.from({ length: 20 }, (_, i) => ({ conversation_id: 'c' + i, created_at: '2026-10-31T10:00:00Z' }))
  const fetcher = vi.fn(async (url: RequestInfo | URL) => json(String(url).endsWith('/session') ? sessionFixture : { items: String(url).includes('offset=20') ? [] : items, limit: 20, offset: String(url).includes('offset=20') ? 20 : 0 }))
  const session = createB2BSession(fetcher); await session.restore()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }] }); await router.push('/')
  const wrapper = mount(AssistantHistoryView, { global: { plugins: [router], provide: { [sessionKey as symbol]: session } } }); await flushPromises()
  expect(wrapper.findAll('li')).toHaveLength(20)
  expect(wrapper.findAll('li a')[0]!.attributes('href')).toBe('/b2b-app/assistant/c0')
  await wrapper.findAll('button').find(b => b.text() === 'Suivant')!.trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain('Aucune conversation sur cette page.')
  expect(fetcher.mock.calls.some(([url]) => String(url).includes('limit=20&offset=20'))).toBe(true)
  language.value = 'en'; await flushPromises()
  expect(wrapper.text()).toContain('Conversation history')
  wrapper.unmount(); session.dispose(); language.value = 'fr'
})
