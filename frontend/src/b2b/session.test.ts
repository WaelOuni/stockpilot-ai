import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createB2BSession } from './session'
import { object, text } from './contracts'
import { json, orderFixture, resultFixture, sessionFixture } from './fixtures'
import { mutate } from './workflows'
import { orderDecoder, resultDecoder } from './contracts'

beforeEach(() => sessionStorage.clear())
afterEach(() => vi.useRealTimers())
describe('server session lifecycle and tenant state', () => {
  it('restores the validated organization and permissions', async () => {
    const s = createB2BSession(vi.fn().mockResolvedValue(json(sessionFixture)))
    await s.restore()
    expect(s.current.value?.organization_name).toBe('Entreprise A')
    expect(s.has('stock:adjust')).toBe(true)
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    s.dispose()
  })
  it.each([401, 403, 503])('fails closed for %s', async status => {
    const s = createB2BSession(vi.fn().mockResolvedValue(json({ detail: 'private' }, status)))
    await s.restore()
    expect(s.current.value).toBeNull()
    expect(s.phase.value).toBe(status === 503 ? 'unavailable' : 'guest')
    s.dispose()
  })
  it('clears displayed identity immediately at logout, before its response', async () => {
    let release!: (r: Response) => void
    const fetcher = vi.fn().mockResolvedValueOnce(json(sessionFixture))
      .mockImplementationOnce(() => new Promise<Response>(resolve => { release = resolve }))
    const s = createB2BSession(fetcher)
    await s.restore()
    s.remember('orders', 'confirm')
    const logout = s.logout()
    expect(s.current.value).toBeNull()
    expect(s.pending.value).toEqual([])
    expect(sessionStorage.length).toBe(0)
    release(json({ provider_logout_url: 'https://keycloak.test/logout' }))
    await logout
    expect(s.notice.value).toContain('SSO Keycloak peut rester ouverte')
    s.dispose()
  })
  it('does not restore an old identity from a delayed request after logout', async () => {
    let release!: (r: Response) => void
    const fetcher = vi.fn().mockResolvedValueOnce(json(sessionFixture))
      .mockImplementationOnce(() => new Promise<Response>(resolve => { release = resolve }))
      .mockResolvedValueOnce(json({ provider_logout_url: 'https://keycloak.test/logout' }))
    const s = createB2BSession(fetcher); await s.restore()
    const read = s.client.request('/b2b/api/products', object({ name: text })).catch(() => null)
    await s.logout(); release(json({ name: 'tenant-a' }))
    expect(await read).toBeNull()
    expect(s.current.value).toBeNull()
    s.dispose()
  })
  it('expires conservatively using reliable server dates', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2099-01-01T00:09:30Z'))
    const s = createB2BSession(vi.fn().mockResolvedValue(json(sessionFixture))); await s.restore()
    expect(s.expiresSoon.value).toBeTruthy()
    vi.setSystemTime(new Date('2099-01-01T00:10:01Z')); s.tick()
    expect(s.current.value).toBeNull()
    expect(s.phase.value).toBe('guest')
    s.dispose()
  })
  it('quarantines opaque pending references across reload and drops other identities', async () => {
    const s = createB2BSession(vi.fn().mockResolvedValue(json(sessionFixture))); await s.restore()
    const operation = s.remember('orders', 'confirm')
    const persisted = sessionStorage.getItem('stockpilot.b2b.pending.v1')!
    expect(persisted).not.toContain('csrf')
    expect(persisted).not.toContain('buyer')
    s.dispose()
    const restored = createB2BSession(vi.fn().mockResolvedValue(json(sessionFixture))); await restored.restore()
    expect(restored.pending.value[0]?.id).toBe(operation.id); restored.dispose()
    const other = createB2BSession(vi.fn().mockResolvedValue(json({ ...sessionFixture, user_id: 'user-b', organization_id: 'org-b' })))
    await other.restore()
    expect(other.pending.value).toEqual([])
    other.dispose()
  })
  it.each([200, 401, 403])('ignores an old logout response %s after a new session restoration', async status => {
    let release!: (r: Response) => void
    const fetcher = vi.fn().mockResolvedValueOnce(json(sessionFixture))
      .mockImplementationOnce(() => new Promise<Response>(resolve => { release = resolve }))
      .mockResolvedValueOnce(json({ ...sessionFixture, user_id: 'user-b', organization_id: 'org-b', organization_name: 'B' }))
    const s = createB2BSession(fetcher); await s.restore()
    const logout = s.logout()
    await s.restore()
    release(status === 200 ? json({ provider_logout_url: 'https://old.test/logout' }) : json({}, status))
    await logout
    expect(s.current.value?.user_id).toBe('user-b')
    expect(s.phase.value).toBe('active')
    expect(s.ssoLogout.value).toBeNull()
    expect(s.notice.value).toBe('')
    s.dispose()
  })
  it('projects only opaque journal fields and drops injected extra properties', async () => {
    sessionStorage.setItem('stockpilot.b2b.pending.v1', JSON.stringify([{
      user: 'user-a', organization: 'org-a', id: 'operation-a', family: 'orders', namespace: 'confirm',
      refresh_token: 'untrusted-extra', payload: { customer_id: 'must-not-persist' },
    }]))
    const s = createB2BSession(vi.fn().mockResolvedValue(json(sessionFixture))); await s.restore()
    expect(sessionStorage.getItem('stockpilot.b2b.pending.v1')).not.toContain('refresh_token')
    expect(sessionStorage.getItem('stockpilot.b2b.pending.v1')).not.toContain('payload')
    s.dispose()
  })
  it('retains the same operation after timeout and prevents a compensating new key', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(json(sessionFixture)).mockRejectedValue(new TypeError('network'))
    const s = createB2BSession(fetcher); await s.restore()
    await expect(mutate(s, 'orders', 'confirm', '/b2b/api/orders/x/confirm', id => ({ operation_id: id }), orderDecoder)).rejects.toThrow()
    const first = s.pending.value[0]!
    await expect(mutate(s, 'orders', 'confirm', '/b2b/api/orders/x/confirm', id => ({ operation_id: id }), orderDecoder)).rejects.toThrow('Vérifiez')
    expect(s.pending.value).toEqual([first])
    expect(fetcher).toHaveBeenCalledTimes(2)
    s.dispose()
  })
  it('keeps a pending B3 result but removes a durable terminal result', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(json(sessionFixture))
      .mockResolvedValueOnce(json({ ...resultFixture, status: 'pending' }, 202))
      .mockResolvedValueOnce(json(orderFixture))
    const s = createB2BSession(fetcher); await s.restore()
    await mutate(s, 'stock', 'adjust', '/b2b/api/products/x/adjustments', id => ({ operation_id: id }), resultDecoder)
    expect(s.pending.value).toHaveLength(1)
    s.resolve(s.pending.value[0]!)
    await mutate(s, 'orders', 'prepare', '/b2b/api/orders/prepare', id => ({ operation_id: id }), orderDecoder)
    expect(s.pending.value).toHaveLength(0)
    s.dispose()
  })
})

it('warns only within the final documented minute', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2099-01-01T00:08:30Z'))
  const s = createB2BSession(vi.fn().mockResolvedValue(json(sessionFixture))); await s.restore()
  expect(s.expiresSoon.value).toBe(false)
  vi.setSystemTime(new Date('2099-01-01T00:09:00Z')); s.tick()
  expect(s.expiresSoon.value).toBe(true)
  vi.setSystemTime(new Date('2099-01-01T00:10:00Z')); s.tick()
  expect(s.current.value).toBeNull()
  await expect(mutate(s, 'orders', 'prepare', '/b2b/api/orders/prepare', () => ({}), orderDecoder)).rejects.toThrow()
  s.dispose()
})
it('uses refreshed server dates after business activity without keepalive polling', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2099-01-01T00:07:30Z'))
  const fetcher = vi.fn().mockResolvedValueOnce(json(sessionFixture))
    .mockResolvedValueOnce(json({ name: 'read' }))
    .mockResolvedValueOnce(json({ ...sessionFixture, idle_expires_at: '2099-01-01T00:19:00Z' }))
  const s = createB2BSession(fetcher); await s.restore()
  vi.setSystemTime(new Date('2099-01-01T00:09:00Z')); s.tick()
  expect(s.expiresSoon.value).toBe(true)
  await s.client.request('/b2b/api/products', object({ name: text }))
  await flushPromises()
  expect(s.current.value?.idle_expires_at).toBe('2099-01-01T00:19:00Z')
  expect(s.expiresSoon.value).toBe(false)
  expect(fetcher).toHaveBeenCalledTimes(3)
  s.dispose()
})
it('ignores an expiry refresh that arrives after logout', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2099-01-01T00:07:30Z'))
  let release!: (r: Response) => void
  const fetcher = vi.fn().mockResolvedValueOnce(json(sessionFixture))
    .mockResolvedValueOnce(json({ name: 'read' }))
    .mockImplementationOnce(() => new Promise<Response>(resolve => { release = resolve }))
    .mockResolvedValueOnce(json({ provider_logout_url: 'https://keycloak.test/logout' }))
  const s = createB2BSession(fetcher); await s.restore()
  vi.setSystemTime(new Date('2099-01-01T00:09:00Z'))
  await s.client.request('/b2b/api/products', object({ name: text }))
  await s.logout(); release(json(sessionFixture)); await flushPromises()
  expect(s.current.value).toBeNull(); expect(s.phase.value).toBe('guest')
  s.dispose()
})

it('throttles session revalidation attempts even when the server is unavailable', async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date('2099-01-01T00:07:30Z'))
  const fetcher = vi.fn().mockResolvedValueOnce(json(sessionFixture))
    .mockResolvedValueOnce(json({ name: 'read' })).mockResolvedValueOnce(json({}, 503))
    .mockResolvedValueOnce(json({ name: 'next' }))
  const s = createB2BSession(fetcher); await s.restore()
  vi.setSystemTime(new Date('2099-01-01T00:09:00Z'))
  await s.client.request('/b2b/api/products', object({ name: text })); await flushPromises()
  await s.client.request('/b2b/api/products', object({ name: text })); await flushPromises()
  expect(fetcher.mock.calls.filter(([url]) => url === '/b2b/auth/session')).toHaveLength(2)
  expect(s.current.value?.idle_expires_at).toBe(sessionFixture.idle_expires_at)
  s.dispose()
})
