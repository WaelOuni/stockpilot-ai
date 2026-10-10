import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import App from './App.vue'
import AssistantPage from './pages/AssistantPage.vue'
import OrdersPage from './pages/OrdersPage.vue'
import { setCustomerToken } from './utils/customerSession'
import { useAssistant } from './features/assistant/useAssistant'

afterEach(() => {
  setCustomerToken('')
  vi.unstubAllGlobals()
})

it('navigates between client pages while preserving the assistant thread and transcript', async () => {
  const fetchMock = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(new Response(JSON.stringify({ response: 'Bonjour client' })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [], limit: 20, offset: 0, count: 0 })))
  vi.stubGlobal('fetch', fetchMock)
  setCustomerToken('test-navigation-token')
  useAssistant().reset()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/assistant', component: AssistantPage },
      { path: '/orders', component: OrdersPage },
      { path: '/products', component: { template: '<div />' } },
      { path: '/admin/orders', component: { template: '<div>Admin read-only</div>' } },
    ],
  })
  await router.push('/assistant')
  const wrapper = mount(App, { global: { plugins: [router] } })
  await wrapper.get('textarea').setValue('Bonjour')
  await wrapper.get('form.composer').trigger('submit')
  await flushPromises()
  const threadId = useAssistant().state.threadId
  await wrapper.get('nav a[href="/orders"]').trigger('click')
  await flushPromises()
  expect(wrapper.get('h1').text()).toBe('My Orders')
  expect(fetchMock.mock.calls[1]![0]).toBe('/api/orders?limit=20&offset=0')
  await wrapper.get('nav a[href="/assistant"]').trigger('click')
  await flushPromises()
  expect(wrapper.get('h1').text()).toBe('Assistant')
  expect(wrapper.text()).toContain('Bonjour client')
  expect(useAssistant().state.threadId).toBe(threadId)
  await wrapper.get('nav a[href="/admin/orders"]').trigger('click')
  await flushPromises()
  expect(wrapper.text()).toContain('Admin read-only')
  expect(wrapper.find('input[type="password"]').exists()).toBe(false)
  expect(wrapper.find('#customer-token').exists()).toBe(false)
  expect(wrapper.text()).not.toContain('JWT client')
  wrapper.unmount()
})
