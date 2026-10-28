import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ProductEditor from './ProductEditor.vue'
import ComparisonPanel from './ComparisonPanel.vue'
import { createB2BSession, sessionKey } from './session'
import { json, previewFixture, productFixture, resultFixture, sessionFixture, stockFixture } from './fixtures'
import { language, theme, applyPreferences } from './preferences'
import { createMemoryHistory, createRouter } from 'vue-router'

beforeEach(() => {
  sessionStorage.clear(); localStorage.clear(); language.value = 'fr'; theme.value = 'light'
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value(this: HTMLDialogElement) { this.setAttribute('open', '') } })
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value(this: HTMLDialogElement) { this.removeAttribute('open') } })
})
afterEach(() => { language.value = 'fr'; theme.value = 'light' })
async function setup(props = {}, respond: (path: string, init?: RequestInit) => Response = () => json(previewFixture)) {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => String(input) === '/b2b/auth/session' ? json(sessionFixture) : respond(String(input), init))
  const session = createB2BSession(fetcher); await session.restore()
  const wrapper = mount(ProductEditor, { props, global: { provide: { [sessionKey as symbol]: session } } })
  return { wrapper, session, fetcher }
}
it('creates through a checked preview and requires explicit confirmation before applying', async () => {
  const { wrapper, session, fetcher } = await setup({}, path => json(path.endsWith('/apply') ? { ...resultFixture, namespace: 'import', status: 'succeeded' } : previewFixture))
  const inputs = wrapper.findAll('input')
  for (const [index, value] of ['LAMP-01', 'Lampe, bureau', 'Éclairage', '49.90', '12'].entries()) await inputs[index]!.setValue(value)
  await wrapper.get('form').trigger('submit'); await flushPromises()
  const previewCall = fetcher.mock.calls.find(([url]) => String(url).includes('/preview?mode=create_only'))!
  expect(String(previewCall[1]?.body)).toContain('"4990","EUR","12"')
  expect(fetcher.mock.calls.some(([url]) => String(url).endsWith('/apply'))).toBe(false)
  const save = wrapper.findAll('button').find(b => b.text() === 'Enregistrer le produit')!
  expect(save.attributes('disabled')).toBeDefined()
  await wrapper.get('input[type=checkbox]').setValue(true)
  await save.trigger('click'); await flushPromises()
  expect(wrapper.emitted('saved')).toHaveLength(1)
  wrapper.unmount(); session.dispose()
})
it('withdraws only after confirmation and sends the observed revision', async () => {
  const { wrapper, session, fetcher } = await setup({ product: productFixture, remove: true }, () => json({ ...resultFixture, namespace: 'archive', status: 'succeeded' }))
  expect(fetcher).toHaveBeenCalledTimes(1)
  await wrapper.get('input[type=checkbox]').setValue(true)
  await wrapper.findAll('button').find(b => b.text() === 'Retirer du catalogue')!.trigger('click'); await flushPromises()
  const call = fetcher.mock.calls.find(([url]) => String(url).endsWith('/archive'))!
  expect(JSON.parse(String(call[1]?.body))).toMatchObject({ expected_revision: productFixture.revision, confirmed: true })
  expect(wrapper.emitted('saved')).toHaveLength(1)
  wrapper.unmount(); session.dispose()
})
it('switches interface language and stores only display preferences', async () => {
  const { wrapper, session } = await setup({ product: productFixture, remove: true })
  language.value = 'en'; theme.value = 'dark'; applyPreferences(); await flushPromises()
  expect(wrapper.text()).toContain('Withdraw product from catalogue')
  expect(wrapper.text()).toContain(productFixture.name)
  expect(document.documentElement.dataset.theme).toBe('dark')
  expect(JSON.parse(localStorage.getItem('stockpilot.preferences.v1')!)).toEqual({ language: 'en', theme: 'dark' })
  expect(localStorage.length).toBe(1)
  wrapper.unmount(); session.dispose()
})
it('compares current catalogue facts without making a reservation', async () => {
  const fetcher = vi.fn(async (url: RequestInfo | URL) => json(String(url).endsWith('/session') ? sessionFixture : String(url).endsWith('/stock') ? stockFixture : productFixture))
  const session = createB2BSession(fetcher); await session.restore()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }] }); await router.push('/')
  const wrapper = mount(ComparisonPanel, { props: { ids: ['product-a', 'product-a'] }, global: { provide: { [sessionKey as symbol]: session }, plugins: [router] } })
  await flushPromises()
  expect(wrapper.findAll('thead th')).toHaveLength(2)
  expect(wrapper.get('[role=region]').attributes('tabindex')).toBe('0')
  const hintId = wrapper.get('[role=region]').attributes('aria-describedby')
  expect(wrapper.get(`[id="${hintId}"]`).text()).toContain('défiler horizontalement')
  expect(wrapper.findAll('tbody th').every(th => th.attributes('scope') === 'row')).toBe(true)
  language.value = 'en'; await flushPromises()
  expect(wrapper.get('[role=region]').attributes('aria-label')).toBe('Product comparison')
  expect(wrapper.get(`[id="${hintId}"]`).text()).toContain('left and right arrow keys')
  expect(wrapper.text()).toContain(productFixture.description)
  expect(fetcher.mock.calls).toHaveLength(3)
  wrapper.unmount(); session.dispose()
})
