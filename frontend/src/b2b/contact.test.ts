import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import SupportContactForm from './SupportContactForm.vue'
import OrganizationContact from './OrganizationContact.vue'
import { supportAddress, supportMailto, supportText, supportTypes } from './contact'
import { createB2BSession, sessionKey, type B2BSession } from './session'
import { json, sessionFixture } from './fixtures'
import { language, theme } from './preferences'

const ownPermissions = ['contact:create', 'contact:read_own']
const adminPermissions = [...ownPermissions, 'contact:read_organization', 'contact:reply_organization', 'contact:manage_organization']
const request = {
  request_id: 'opaque-request', subject: 'Question locale', category: 'access', description: '<img src=x onerror=alert(1)>',
  created_by: 'user-a', created_at: '2026-10-21T10:00:00Z', updated_at: '2026-10-21T10:00:00Z', status: 'open', revision: 1,
}
function counted(items: unknown[], total = items.length, offset = 0) { return { items, total, offset, limit: 10, has_more: offset + 10 < total } }
const mounted: VueWrapper[] = [], sessions: B2BSession[] = []
beforeEach(() => { sessionStorage.clear(); localStorage.clear(); language.value = 'fr'; theme.value = 'light' })
afterEach(() => {
  mounted.splice(0).forEach(w => w.unmount()); sessions.splice(0).forEach(s => s.dispose())
  vi.restoreAllMocks(); vi.unstubAllGlobals(); language.value = 'fr'; theme.value = 'light'
})
function button(wrapper: VueWrapper, label: string) { return wrapper.findAll('button').find(b => b.text() === label)! }
async function setup(respond: (path: string, init?: RequestInit) => Response | Promise<Response>, admin = false) {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => String(input) === '/b2b/auth/session'
    ? json({ ...sessionFixture, role: admin ? 'ORGANIZATION_ADMIN' : 'READ_ONLY', permissions: admin ? adminPermissions : ownPermissions })
    : respond(String(input), init))
  const session = createB2BSession(fetcher); sessions.push(session); await session.restore()
  const wrapper = mount(OrganizationContact, { global: { provide: { [sessionKey as symbol]: session } } }); mounted.push(wrapper)
  await flushPromises()
  return { wrapper, session, fetcher }
}

it('encodes a local mailto with only the user content, a fixed destination and no automatic private context', () => {
  localStorage.setItem('synthetic-private', 'do-not-copy')
  const body = 'Étage 2 & question ?\nAucune donnée jointe.'
  const url = new URL(supportMailto(supportTypes[0], 'Écran & stock #1 ?', body))
  expect(url.protocol).toBe('mailto:')
  expect(url.pathname).toBe(supportAddress)
  expect([...url.searchParams.keys()]).toEqual(['subject', 'body'])
  expect(url.searchParams.get('subject')).toBe('[StockPilot AI] Problème technique — Écran & stock #1 ?')
  expect(url.searchParams.get('body')).toBe(body)
  expect(url.href).not.toContain('do-not-copy')
})
it.each([
  ['', 'Description'], ['x'.repeat(101), 'Description'], ['Objet', ''], ['Objet', 'x'.repeat(801)],
  ['Objet\r\nbcc:fake@example.invalid', 'Body'], ['Objet', '\0body'],
])('rejects invalid mailto fields without making a request (%s)', (subject, body) => {
  expect(() => supportMailto(supportTypes[0], subject, body)).toThrow()
})
it('offers copy for multibyte text that would exceed the encoded URL budget', () => {
  expect(() => supportMailto(supportTypes[0], 'Objet', 'é'.repeat(800))).toThrow('Copier')
  expect(supportText(supportTypes[0], 'Objet', 'é'.repeat(800)).body).toHaveLength(800)
})
it('opens the local client without claiming delivery and copies the exact text with a selectable fallback', async () => {
  const clicked: string[] = []
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { clicked.push(this.href) })
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher)
  const writeText = vi.fn().mockResolvedValue(undefined)
  vi.stubGlobal('navigator', { clipboard: { writeText } })
  const wrapper = mount(SupportContactForm); mounted.push(wrapper)
  await wrapper.get('input').setValue('Question')
  await wrapper.get('textarea').setValue('Texte choisi')
  await wrapper.get('form').trigger('submit')
  expect(clicked).toHaveLength(1)
  expect(wrapper.get('[role=status]').text()).toBe("Votre application de messagerie va s'ouvrir. Vérifiez puis envoyez votre email.")
  expect(wrapper.text()).not.toMatch(/Message envoyé|Email envoyé/)
  await button(wrapper, 'Copier le message').trigger('click'); await flushPromises()
  expect(writeText).toHaveBeenCalledWith('waelounie@gmail.com\n[StockPilot AI] Problème technique — Question\n\nTexte choisi')
  writeText.mockRejectedValue(new Error('clipboard denied'))
  await button(wrapper, 'Copier le message').trigger('click'); await flushPromises()
  expect(wrapper.get<HTMLTextAreaElement>('textarea[readonly]').element.value).toContain('Texte choisi')
  expect(wrapper.get('[role=status]').text()).toContain('Copie automatique indisponible')
  expect(fetcher).not.toHaveBeenCalled()
})

it('READ_ONLY can see own messages as literal text but no organization or admin controls', async () => {
  const { wrapper, fetcher } = await setup(path => json(path.includes('/replies') ? counted([{ reply_id: 'answer', request_id: request.request_id, body: '<script>not executed</script>', created_by: 'admin', created_at: request.created_at }])
    : path.includes('?') ? counted([request]) : request))
  expect(wrapper.text()).toContain('Enregistrer ma demande')
  expect(wrapper.text()).not.toContain('Périmètre des demandes')
  await button(wrapper, request.subject).trigger('click'); await flushPromises()
  expect(wrapper.text()).toContain(request.description)
  expect(wrapper.find('img').exists()).toBe(false); expect(wrapper.find('script').exists()).toBe(false)
  expect(wrapper.text()).toContain('<script>not executed</script>')
  expect(wrapper.text()).not.toContain('Enregistrer la réponse')
  expect(wrapper.text()).not.toContain('Marquer comme résolue')
  expect(fetcher.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true)
})
it('keeps one operation key and frozen content after a lost response; double click never creates a second operation', async () => {
  let reject!: (reason: Error) => void, postCount = 0
  const { wrapper, fetcher } = await setup((path, init) => {
    if (init?.method === 'POST') {
      postCount++
      if (postCount === 1) return new Promise<Response>((_, fail) => { reject = fail })
      return json({ operation_id: JSON.parse(String(init.body)).operation_id, request_id: request.request_id, reply_id: null })
    }
    return json(path.includes('/replies') ? counted([]) : path.includes('?') ? counted([request]) : request)
  })
  await wrapper.get('input').setValue('Question')
  await wrapper.get('textarea').setValue('Texte privé choisi')
  await wrapper.get('form').trigger('submit'); await wrapper.get('form').trigger('submit'); await nextTick()
  expect(postCount).toBe(1)
  expect(wrapper.get('input').attributes('disabled')).toBeDefined()
  reject(new Error('synthetic lost reply')); await flushPromises()
  expect(wrapper.text()).toContain('Résultat à vérifier')
  await button(wrapper, 'Réessayer la même demande').trigger('click'); await flushPromises()
  const calls = fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')
  expect(calls).toHaveLength(2)
  expect(calls[0]![1]?.body).toBe(calls[1]![1]?.body)
  expect(JSON.parse(String(calls[0]![1]?.body))).toMatchObject({ subject: 'Question', description: 'Texte privé choisi' })
  expect(wrapper.text()).toContain('Aucun email envoyé')
  expect(sessionStorage.length).toBe(0); expect(localStorage.length).toBe(0)
  expect(fetcher.mock.calls.every(([path]) => !String(path).includes('/orders') && !String(path).includes('/assistant') && !String(path).includes('/stock'))).toBe(true)
})
it('admin can paginate their organization, reply, resolve and reopen through separate explicit actions', async () => {
  let state = 'open'
  const { wrapper, fetcher } = await setup((path, init) => {
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body))
      if (path.endsWith('/status')) state = body.status
      return json({ operation_id: body.operation_id, request_id: request.request_id, reply_id: path.endsWith('/replies') ? 'answer' : null })
    }
    if (path.includes('/replies')) return json(counted([]))
    if (path.includes('?')) { const offset = Number(new URL(path, 'https://synthetic.invalid').searchParams.get('offset')); return json(counted([{ ...request, status: state }], 105, offset)) }
    return json({ ...request, status: state })
  }, true)
  await wrapper.findAll('select')[1]!.setValue('organization'); await flushPromises()
  expect(String(fetcher.mock.calls.at(-1)![0])).toContain('scope=organization')
  await button(wrapper, 'Dernière').trigger('click'); await flushPromises()
  expect(String(fetcher.mock.calls.at(-1)![0])).toContain('offset=100')
  expect(wrapper.get('[aria-current=page]').text()).toBe('11')
  await button(wrapper, request.subject).trigger('click'); await flushPromises()
  await wrapper.findAll('textarea')[1]!.setValue('Réponse locale')
  await wrapper.findAll('form')[1]!.trigger('submit'); await flushPromises()
  await button(wrapper, 'Marquer comme résolue').trigger('click'); await flushPromises()
  expect(state).toBe('resolved')
  await button(wrapper, 'Rouvrir la demande').trigger('click'); await flushPromises()
  expect(state).toBe('open')
  const posts = fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')
  expect(posts.map(([p]) => String(p).split('/').at(-1))).toEqual(['replies', 'status', 'status'])
  expect(new Set(posts.map(([, init]) => JSON.parse(String(init?.body)).operation_id)).size).toBe(3)
  expect(posts.every(([, init]) => (init?.headers as Record<string, string>)['X-BFF-CSRF'] === sessionFixture.csrf_token)).toBe(true)
})
it('discards pending private content and late responses when the organization changes', async () => {
  let finish!: (value: Response) => void
  const { wrapper, session } = await setup(() => new Promise<Response>(resolve => { finish = resolve }))
  // The initial request remains pending; the organization switch clears the view synchronously.
  session.current.value = { ...session.current.value!, organization_id: 'org-b', user_id: 'user-b', csrf_token: 'new-csrf' }
  finish(json(counted([request]))); await flushPromises()
  expect(wrapper.text()).not.toContain(request.subject)
  expect(wrapper.findAll('.contact-list li')).toHaveLength(0)
})
it('expiry removes the request and form contents and never retries a mutation automatically', async () => {
  const { wrapper, session, fetcher } = await setup((path, init) => init?.method === 'POST' ? json({ code: 'SESSION_REQUIRED' }, 401) : json(counted([request])))
  await wrapper.get('input').setValue('Objet privé')
  await wrapper.get('textarea').setValue('Texte privé')
  await wrapper.get('form').trigger('submit'); await flushPromises()
  expect(session.current.value).toBeNull()
  expect(wrapper.text()).not.toContain(request.subject)
  expect(wrapper.find('input').exists()).toBe(false)
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})
