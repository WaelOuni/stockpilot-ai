import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createB2BSession } from './session'
import { json, sessionFixture } from './fixtures'

beforeEach(() => { sessionStorage.clear(); localStorage.clear() })
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
const key = 'stockpilot.b2b.conversation.v1'
const expiry = 'Votre session a expiré'
async function open(fetcher = vi.fn().mockImplementation(async () => json(sessionFixture))) {
  const s = createB2BSession(fetcher); await s.restore(); return s
}
it('remembers only an identity-bound reference across reload, never content or CSRF', async () => {
  const s = await open(); s.continuity.select('conv-a'); s.dispose()
  const second = await open()
  expect(second.continuity.active.value).toBe('conv-a')
  expect(JSON.parse(sessionStorage.getItem(key)!)).toEqual({ user: 'user-a', organization: 'org-a', value: 'conv-a' })
  expect(localStorage.length).toBe(0)
  second.dispose()
})
it.each([{ user_id: 'other', organization_id: 'org-a' }, { user_id: 'user-a', organization_id: 'org-b' }])('drops another principal’s reference: %s', async identity => {
  const s = await open(); s.continuity.select('conv-a'); s.dispose()
  const other = await open(vi.fn().mockResolvedValue(json({ ...sessionFixture, ...identity })))
  expect(other.continuity.active.value).toBe(''); expect(sessionStorage.getItem(key)).toBeNull(); other.dispose()
})
it.each(['{', JSON.stringify({ user: 'user-a', organization: 'org-a', value: '../foreign' }), JSON.stringify({ user: 'user-a', organization: 'org-a', value: 'x'.repeat(129) })])('ignores malformed browser references', async value => {
  sessionStorage.setItem(key, value); const s = await open()
  expect(s.continuity.active.value).toBe(''); s.dispose()
})
it('preserves only the reference at expiry and never starts authentication from focus', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2099-01-01T00:09:30Z'))
  const fetcher = vi.fn().mockResolvedValue(json(sessionFixture)), s = await open(fetcher)
  s.continuity.select('conv-a'); vi.setSystemTime(new Date('2099-01-01T01:00:00Z')); s.tick()
  expect(s.current.value).toBeNull(); expect(s.continuity.active.value).toBe('')
  expect(s.notice.value).toContain(expiry)
  await s.revalidateOnReturn(); expect(fetcher).toHaveBeenCalledTimes(1)
  expect(sessionStorage.getItem(key)).toContain('conv-a'); s.dispose()
})
it('checks a stale idle deadline once on return and accepts only renewed server dates', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2099-01-01T00:09:30Z'))
  let resolve!: (value: Response) => void
  const fetcher = vi.fn().mockResolvedValueOnce(json(sessionFixture))
    .mockImplementationOnce(() => new Promise<Response>(r => { resolve = r }))
  const s = await open(fetcher); s.continuity.select('conv-a')
  vi.setSystemTime(new Date('2099-01-01T00:11:00Z')); s.tick()
  expect(s.current.value).toBeNull(); expect(fetcher).toHaveBeenCalledTimes(1)
  const waking = s.revalidateOnReturn(); await s.revalidateOnReturn()
  expect(s.phase.value).toBe('loading'); expect(s.current.value).toBeNull()
  resolve(json({ ...sessionFixture, idle_expires_at: '2099-01-01T00:20:00Z' })); await waking
  expect(s.continuity.active.value).toBe('conv-a'); expect(s.phase.value).toBe('active')
  expect(fetcher).toHaveBeenCalledTimes(2)
  expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true)
  s.dispose()
})
it.each([401, 403])('a refused return (%s) never resurrects a revoked session', async status => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2099-01-01T00:09:30Z'))
  const fetcher = vi.fn().mockResolvedValueOnce(json(sessionFixture)).mockResolvedValue(json({}, status))
  const s = await open(fetcher); vi.setSystemTime(new Date('2099-01-01T00:11:00Z')); s.tick()
  await s.revalidateOnReturn(); await s.revalidateOnReturn()
  expect(s.current.value).toBeNull(); expect(s.phase.value).toBe('guest'); expect(fetcher).toHaveBeenCalledTimes(2); s.dispose()
})
it('suspends business views during network loss, with no transaction replay', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2099-01-01T00:09:30Z'))
  const fetcher = vi.fn().mockResolvedValueOnce(json(sessionFixture)).mockRejectedValue(new TypeError('offline'))
  const s = await open(fetcher); const operation = s.remember('orders', 'confirm')
  vi.setSystemTime(new Date('2099-01-01T00:11:00Z')); s.tick(); await s.revalidateOnReturn()
  expect(s.current.value).toBeNull(); expect(s.phase.value).toBe('unavailable'); expect(s.pending.value).toEqual([operation])
  expect(fetcher.mock.calls.every(([, init]) => init.method === 'GET')).toBe(true); s.dispose()
})
it('logout during a pending return purges references and rejects the late identity', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2099-01-01T00:09:30Z'))
  let resolve!: (value: Response) => void
  const fetcher = vi.fn().mockResolvedValueOnce(json(sessionFixture))
    .mockImplementationOnce(() => new Promise<Response>(r => { resolve = r }))
  const s = await open(fetcher); s.continuity.select('conv-a')
  vi.setSystemTime(new Date('2099-01-01T00:11:00Z')); s.tick()
  const waking = s.revalidateOnReturn(); await s.logout()
  resolve(json({ ...sessionFixture, idle_expires_at: '2099-01-01T00:20:00Z' })); await waking
  expect(s.current.value).toBeNull(); expect(sessionStorage.getItem(key)).toBeNull(); s.dispose()
})
it('a cross-tab logout purges a reference even after local expiry', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2099-01-01T00:09:30Z'))
  let receive!: (event: { data: unknown }) => void
  vi.stubGlobal('BroadcastChannel', class { postMessage() {} close() {} set onmessage(value: typeof receive) { receive = value } })
  const s = createB2BSession(vi.fn().mockResolvedValue(json(sessionFixture))); await s.start(); s.continuity.select('conv-a')
  vi.setSystemTime(new Date('2099-01-01T00:11:00Z')); s.tick(); receive({ data: 'logout' })
  expect(sessionStorage.getItem(key)).toBeNull(); expect(s.continuity.active.value).toBe(''); s.dispose()
})
it('a cross-tab identity change purges quarantined references within the same organization', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2099-01-01T00:09:30Z'))
  let receive!: (event: { data: unknown }) => void
  vi.stubGlobal('BroadcastChannel', class { postMessage() {} close() {} set onmessage(value: typeof receive) { receive = value } })
  const s = createB2BSession(vi.fn().mockResolvedValue(json(sessionFixture))); await s.start(); s.continuity.select('conv-a')
  vi.setSystemTime(new Date('2099-01-01T00:11:00Z')); s.tick(); receive({ data: { type: 'identity', user: 'other', organization: 'org-a' } })
  expect(sessionStorage.getItem(key)).toBeNull(); s.dispose()
})
it('restores only allowlisted navigation for the same authenticated identity', async () => {
  const s = await open(); s.continuity.rememberReturn('/b2b-app/orders'); s.dispose()
  const same = await open(); expect(same.continuity.takeReturn()).toBe('/b2b-app/orders')
  same.continuity.rememberReturn('https://attacker.test/'); expect(same.continuity.takeReturn()).toBeNull()
  same.continuity.rememberReturn('/b2b-app/orders'); same.dispose()
  const other = await open(vi.fn().mockResolvedValue(json({ ...sessionFixture, user_id: 'other' })))
  expect(other.continuity.takeReturn()).toBeNull(); other.dispose()
})
it('continues to navigate in memory when tab storage is unavailable', async () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
  const s = await open(); s.continuity.select('conv-a'); expect(s.continuity.active.value).toBe('conv-a')
  await s.restore(); expect(s.continuity.active.value).toBe('conv-a'); s.dispose()
})
it('discards a late session response when another tab changes account during revalidation', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2099-01-01T00:09:30Z'))
  let receive!: (event: { data: unknown }) => void
  const postMessage = vi.fn()
  vi.stubGlobal('BroadcastChannel', class { postMessage = postMessage; close() {} set onmessage(value: typeof receive) { receive = value } })
  let resolve!: (value: Response) => void
  const fetcher = vi.fn().mockResolvedValueOnce(json(sessionFixture))
    .mockImplementationOnce(() => new Promise<Response>(r => { resolve = r }))
  const s = createB2BSession(fetcher); await s.start(); s.continuity.select('conv-a'); s.remember('orders', 'confirm')
  vi.setSystemTime(new Date('2099-01-01T00:11:00Z')); s.tick()
  const waking = s.revalidateOnReturn()
  receive({ data: { type: 'identity', user: 'other', organization: 'org-a' } })
  resolve(json({ ...sessionFixture, idle_expires_at: '2099-01-01T00:20:00Z' })); await waking
  expect(s.current.value).toBeNull(); expect(s.pending.value).toEqual([])
  expect(s.continuity.active.value).toBe(''); expect(sessionStorage.getItem(key)).toBeNull()
  expect(postMessage).toHaveBeenCalledTimes(1); s.dispose()
})
it.each(['user-a', 'other'])('handles concurrent initial tab checks without a false account switch: %s', async user => {
  let receive!: (event: { data: unknown }) => void
  const postMessage = vi.fn()
  vi.stubGlobal('BroadcastChannel', class { postMessage = postMessage; close() {} set onmessage(value: typeof receive) { receive = value } })
  let resolve!: (value: Response) => void
  const s = createB2BSession(vi.fn().mockImplementationOnce(() => new Promise<Response>(r => { resolve = r })))
  const starting = s.start(); receive({ data: { type: 'identity', user, organization: 'org-a' } })
  expect(s.current.value).toBeNull()
  resolve(json(sessionFixture)); await starting
  expect(s.phase.value).toBe(user === 'user-a' ? 'active' : 'guest')
  expect(postMessage).toHaveBeenCalledTimes(user === 'user-a' ? 1 : 0)
  if (user === 'user-a') expect(s.notice.value).toBe('')
  s.dispose()
})
it('keeps a still-valid mounted session on focus, without losing in-memory form state', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2099-01-01T00:08:00Z'))
  const fetcher = vi.fn().mockResolvedValueOnce(json(sessionFixture))
    .mockResolvedValueOnce(json({ ...sessionFixture, idle_expires_at: '2099-01-01T00:19:00Z' }))
  const s = await open(fetcher), mounted = s.current.value
  s.continuity.select('conv-a')
  vi.setSystemTime(new Date('2099-01-01T00:09:01Z')); await s.revalidateOnReturn()
  expect(s.current.value).toBe(mounted); expect(s.phase.value).toBe('active')
  expect(s.current.value?.idle_expires_at).toBe('2099-01-01T00:19:00Z')
  expect(s.continuity.active.value).toBe('conv-a'); expect(fetcher).toHaveBeenCalledTimes(2); s.dispose()
})
