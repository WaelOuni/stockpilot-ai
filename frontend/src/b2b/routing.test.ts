import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import App from '../App.vue'
import { router } from '../router'
import { json } from './fixtures'
afterEach(() => vi.unstubAllGlobals())
it.each(['/b2b-app', '/B2B-APP/', '/B2B-APP/ORDERS'])('keeps the B2B shell separate for %s', async path => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({}, 401)))
  await router.push(path)
  const wrapper = mount(App, { global: { plugins: [router] } })
  await flushPromises()
  expect(wrapper.find('.b2b-shell').exists()).toBe(true)
  expect(wrapper.find('.token-panel').exists()).toBe(false)
  expect(wrapper.find('.site-header').exists()).toBe(false)
  expect(wrapper.text()).not.toContain('Connecter un JWT client')
  wrapper.unmount()
})
it('keeps unknown B2B URLs inside the B2B area', async () => {
  await router.push('/b2b-app/not-a-view')
  expect(router.currentRoute.value.path).toBe('/b2b-app/')
  expect(router.currentRoute.value.matched.some(r => r.meta.b2b === true)).toBe(true)
})

it('matches the history page before the dynamic conversation identifier', () => {
  const route = router.resolve('/b2b-app/assistant/history')
  expect(route.params.conversationId).toBeUndefined()
  expect(route.matched.at(-1)?.path).toBe('/b2b-app/assistant/history')
})
