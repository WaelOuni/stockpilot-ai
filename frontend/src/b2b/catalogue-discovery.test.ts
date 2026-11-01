import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import CatalogueView from './CatalogueView.vue'
import CataloguePagination from './CataloguePagination.vue'
import CatalogueCombobox from './CatalogueCombobox.vue'
import B2BHeader from './B2BHeader.vue'
import { createB2BSession, sessionKey } from './session'
import { json, productFixture, sessionFixture, stockFixture } from './fixtures'
import { language } from './preferences'
beforeEach(() => { sessionStorage.clear(); language.value = 'fr' })
afterEach(() => { vi.useRealTimers() })

it.each([0, 1, 12, 13, 225, 1125])('uses the server total for %s results and bounded accessible page links', async total => {
  const wrapper = mount(CataloguePagination, { props: { total, offset: 0, limit: 12, busy: false } })
  const pages = Math.ceil(total / 12)
  expect(wrapper.text()).toContain(`${total} produits • Page ${total ? 1 : 0} sur ${pages}`)
  expect(wrapper.get('button').attributes('disabled')).toBeDefined()
  expect(wrapper.findAll('button')).toHaveLength(4 + Math.min(pages, pages > 3 ? 4 : pages))
  if (total) expect(wrapper.get('[aria-current=page]').text()).toBe('1')
  if (pages > 1) {
    await wrapper.findAll('button').find(b => b.text() === 'Dernière')!.trigger('click')
    expect(wrapper.emitted('page')?.[0]).toEqual([pages])
    await wrapper.setProps({ offset: (pages - 1) * 12 })
    expect(wrapper.text()).toContain(`Affichage de ${(pages - 1) * 12 + 1} à ${total} sur ${total}`)
    expect(wrapper.findAll('button').find(b => b.text() === 'Suivant')!.attributes('disabled')).toBeDefined()
  }
  wrapper.unmount()
})
it('does not navigate past the server offset bound', () => {
  const wrapper = mount(CataloguePagination, { props: { total: 20000, offset: 9996, limit: 12, busy: false } })
  expect(wrapper.findAll('button').find(b => b.text() === 'Dernière')!.attributes('disabled')).toBeDefined()
  expect(wrapper.findAll('button').find(b => b.text() === 'Suivant')!.attributes('disabled')).toBeDefined()
  expect(wrapper.text()).toContain('Affinez les filtres')
  wrapper.unmount()
})
it('combobox supports keyboard choice, Escape and free typing', async () => {
  const wrapper = mount(CatalogueCombobox, { props: { id: 'test', label: 'Catégorie', modelValue: '', options: [{ value: '', label: 'Toutes' }, { value: 'Éclairage', label: 'Éclairage' }] } })
  const input = wrapper.get('input')
  await input.trigger('focus'); await input.trigger('keydown', { key: 'ArrowDown' }); await input.trigger('keydown', { key: 'ArrowDown' })
  expect(input.attributes('aria-activedescendant')).toBe('test-option-1')
  await input.trigger('keydown', { key: 'Enter' })
  expect(wrapper.emitted('select')?.[0]).toEqual(['Éclairage'])
  await input.setValue('Inconnu'); expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['Inconnu'])
  await input.trigger('keydown', { key: 'Escape' }); expect(input.attributes('aria-expanded')).toBe('false')
  wrapper.unmount()
})
async function setup(respond: (path: string) => Response | Promise<Response>, header = false) {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input)
    if (path === '/b2b/auth/session') return json(sessionFixture)
    if (path === '/b2b/api/products/categories') return json({ items: ['perceuses', 'Éclairage'] })
    if (path.endsWith('/stock')) return json(stockFixture)
    return respond(path)
  })
  const session = createB2BSession(fetcher); await session.restore()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }] }); await router.push('/b2b-app/')
  const wrapper = mount(header ? B2BHeader : CatalogueView, { props: header ? { busy: false } : {}, attachTo: document.body, global: { provide: { [sessionKey as symbol]: session }, plugins: [router] } })
  await flushPromises()
  return { wrapper, session, fetcher, dispose: () => { wrapper.unmount(); session.dispose() } }
}
const page = (total = 13, items = [productFixture]) => json({ items, total, has_more: total > 12, limit: 12, offset: 0 })
it('changes category on page 1, combines availability and preserves canonical accents', async () => {
  const { wrapper, fetcher, dispose } = await setup(() => page())
  await wrapper.findAll('button').find(b => b.text() === 'Suivant')!.trigger('click'); await flushPromises()
  await wrapper.get('#catalogue-category').setValue('eclairage')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(fetcher.mock.calls.some(([p]) => String(p).includes('category=%C3%89clairage') && String(p).includes('offset=0'))).toBe(true)
  await wrapper.get('.availability-filter input').setValue(true); await flushPromises()
  expect(fetcher.mock.calls.some(([p]) => String(p).includes('available=true') && String(p).includes('category=%C3%89clairage'))).toBe(true)
  dispose()
})
it('discards stale products and stocks when a filter changes during loading', async () => {
  let resolve!: (r: Response) => void
  let calls = 0
  const { wrapper, dispose } = await setup(() => ++calls === 1 ? new Promise<Response>(r => { resolve = r }) : page(0, []))
  await wrapper.get('#catalogue-category').setValue('unknown')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  resolve(page()); await flushPromises()
  expect(wrapper.findAll('article')).toHaveLength(0)
  expect(wrapper.text()).toContain('Aucun produit trouvé')
  dispose()
})
it('debounces SKU suggestions and selects a literal SKU with explicit filter priority', async () => {
  vi.useFakeTimers()
  const { wrapper, fetcher, dispose } = await setup(path => path.includes('/suggestions?') ? json({ items: [productFixture] }) : path.includes('/by-sku?') ? json(productFixture) : page())
  await wrapper.get('#catalogue-category').setValue('perceuses')
  await wrapper.get('#catalogue-sku').setValue('D'); await vi.advanceTimersByTimeAsync(300)
  expect(fetcher.mock.calls.some(([p]) => String(p).includes('/suggestions?'))).toBe(false)
  await wrapper.get('#catalogue-sku').setValue('DR'); await vi.advanceTimersByTimeAsync(249)
  expect(fetcher.mock.calls.some(([p]) => String(p).includes('/suggestions?'))).toBe(false)
  await vi.advanceTimersByTimeAsync(1); await flushPromises()
  expect(wrapper.get('#catalogue-sku-options').text()).toContain(productFixture.name)
  await wrapper.get('#catalogue-sku').trigger('keydown', { key: 'ArrowDown' }); await wrapper.get('#catalogue-sku').trigger('keydown', { key: 'Enter' }); await flushPromises()
  expect(fetcher.mock.calls.some(([p]) => String(p).includes('/by-sku?sku=') && !String(p).includes('category='))).toBe(true)
  expect(wrapper.get('#catalogue-category').attributes('disabled')).toBeDefined()
  dispose()
})
it('menu shows an SVG chevron, account identity and closes with Escape and outside click', async () => {
  const { wrapper, dispose } = await setup(() => page(), true)
  const details = wrapper.get('details').element as HTMLDetailsElement
  expect(wrapper.find('svg.account-chevron').exists()).toBe(true)
  expect(wrapper.text()).toContain(sessionFixture.organization_name)
  details.open = true
  await wrapper.get('details').trigger('keydown', { key: 'Escape' })
  expect(details.open).toBe(false); expect(document.activeElement).toBe(wrapper.get('summary').element)
  details.open = true; document.body.click(); expect(details.open).toBe(false)
  details.open = true
  await wrapper.findAll('button').find(b => b.text() === 'Changer de compte')!.trigger('click')
  expect(wrapper.emitted('switchAccount')).toHaveLength(1)
  expect(details.open).toBe(false)
  dispose()
})

it('ignores a late SKU suggestion response after the prefix changes', async () => {
  vi.useFakeTimers()
  let release!: (response: Response) => void
  const { wrapper, dispose } = await setup(path => path.includes('prefix=DR')
    ? new Promise<Response>(resolve => { release = resolve })
    : path.includes('/suggestions?') ? json({ items: [{ ...productFixture, sku: 'SAW-NEW', name: 'Scie actuelle' }] }) : page())
  await wrapper.get('#catalogue-sku').setValue('DR'); await vi.advanceTimersByTimeAsync(250)
  await wrapper.get('#catalogue-sku').setValue('SA'); await vi.advanceTimersByTimeAsync(250); await flushPromises()
  expect(wrapper.get('#catalogue-sku-options').text()).toContain('SAW-NEW')
  release(json({ items: [productFixture] })); await flushPromises()
  expect(wrapper.get('#catalogue-sku-options').text()).toContain('SAW-NEW')
  expect(wrapper.get('#catalogue-sku-options').text()).not.toContain(productFixture.sku)
  dispose()
})
