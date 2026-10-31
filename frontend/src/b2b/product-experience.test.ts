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
  expect(wrapper.get('[role=region]').attributes('aria-describedby')).toBeUndefined()
  const region = wrapper.get('[role=region]').element as HTMLElement
  Object.defineProperty(region, 'scrollWidth', { configurable: true, value: 1600 })
  Object.defineProperty(region, 'clientWidth', { configurable: true, value: 640 })
  window.dispatchEvent(new Event('resize')); await flushPromises()
  const hintId = wrapper.get('[role=region]').attributes('aria-describedby')
  expect(wrapper.get(`[id="${hintId}"]`).text()).toContain('défiler horizontalement')
  expect(wrapper.findAll('tbody th').every(th => th.attributes('scope') === 'row')).toBe(true)
  language.value = 'en'; await flushPromises()
  expect(wrapper.get('[role=region]').attributes('aria-label')).toBe('Product comparison')
  expect(wrapper.get(`[id="${hintId}"]`).text()).toContain('left and right arrow keys')
  expect(wrapper.text()).toContain(productFixture.description)
  await wrapper.get('[role=region]').trigger('keydown', { key: 'ArrowRight' })
  expect(region.scrollLeft).toBe(320)
  await wrapper.get('[role=region]').trigger('keydown', { key: 'ArrowLeft' })
  expect(region.scrollLeft).toBe(0)
  Object.defineProperty(region, 'clientWidth', { configurable: true, value: 1600 })
  window.dispatchEvent(new Event('resize')); await flushPromises()
  expect(wrapper.get('[role=region]').attributes('aria-describedby')).toBeUndefined()
  expect(fetcher.mock.calls).toHaveLength(3)
  wrapper.unmount(); session.dispose()
})

it.each([2, 4, 10])('loads %s distinct comparison products sequentially and caps at ten', async count => {
  let inFlight = 0; let peak = 0
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    inFlight++; peak = Math.max(peak, inFlight)
    await Promise.resolve(); inFlight--
    const path = String(input)
    const id = path.split('/')[4]!
    return json(path.endsWith('/session') ? sessionFixture : path.endsWith('/stock') ? { ...stockFixture, product_id: id } : { ...productFixture, product_id: id })
  })
  const session = createB2BSession(fetcher); await session.restore()
  const wrapper = mount(ComparisonPanel, { props: { ids: Array.from({ length: count === 10 ? 11 : count }, (_, i) => 'p' + i) }, global: { provide: { [sessionKey as symbol]: session }, stubs: { RouterLink: true } } })
  await flushPromises()
  expect(wrapper.findAll('thead th')).toHaveLength(count + 1)
  expect(fetcher).toHaveBeenCalledTimes(1 + count * 2)
  expect(peak).toBe(1)
  wrapper.unmount(); session.dispose()
})
it('includes edited attributes in the immutable preview and invalidates approval on change', async () => {
  const { wrapper, session, fetcher } = await setup({ product: productFixture })
  const textarea = wrapper.findAll('textarea').at(-1)!
  await textarea.setValue('Marque : Northline\nPuissance : 800 W')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  const call = fetcher.mock.calls.find(([url]) => String(url).includes('/preview'))!
  expect(String(call[1]?.body)).toContain('attributes')
  expect(String(call[1]?.body)).toContain('Puissance : 800 W')
  await wrapper.get('input[type=checkbox]').setValue(true)
  await textarea.setValue('Puissance : 900 W')
  expect(wrapper.findAll('button').find(b => b.text() === 'Enregistrer le produit')).toBeUndefined()
  wrapper.unmount(); session.dispose()
})
