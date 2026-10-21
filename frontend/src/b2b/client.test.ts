import { describe, expect, it, vi } from 'vitest'
import { BffClient, BffError, providerNavigation, StaleResponse } from './client'
import { object, resultDecoder, text } from './contracts'
import { json, resultFixture } from './fixtures'

describe('dedicated same-origin cookie transport', () => {
  const hooks = () => ({ generation: () => 1, csrf: () => 'csrf-a', invalid: vi.fn() })
  it('sends credentials and current CSRF only to BFF paths', async () => {
    const fetcher = vi.fn().mockResolvedValue(json({ ok: 'yes' }))
    const client = new BffClient(hooks(), fetcher)
    await client.request('/b2b/api/orders/prepare', object({ ok: text }), { method: 'POST', body: { operation_id: 'same' } })
    const [path, options] = fetcher.mock.calls[0]!
    expect(path).toBe('/b2b/api/orders/prepare')
    expect(options).toMatchObject({ credentials: 'include', mode: 'same-origin', redirect: 'error', cache: 'no-store', headers: { 'X-BFF-CSRF': 'csrf-a' } })
    expect(options.headers).not.toHaveProperty('Authorization')
    for (const forbidden of ['/api/orders', '//evil/b2b/api/orders', 'https://evil/b2b/api/orders', '/b2b/api/../api/orders#frag']) {
      await expect(client.request(forbidden, text)).rejects.toThrow()
    }
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('binds business reads to the CSRF of the restored session, never only the ambient cookie', async () => {
    const fetcher = vi.fn().mockResolvedValue(json('ok'))
    await new BffClient(hooks(), fetcher).request('/b2b/api/products', text)
    expect(fetcher.mock.calls[0]![1].headers['X-BFF-CSRF']).toBe('csrf-a')
  })
  it('uses the pre-authentication non-simple login header', async () => {
    const fetcher = vi.fn().mockResolvedValue(json('ok'))
    await new BffClient(hooks(), fetcher).request('/b2b/auth/login', text, { method: 'POST', login: true, body: { organization_id: 'org-a' } })
    expect(fetcher.mock.calls[0]![1].headers).toMatchObject({ 'X-BFF-CSRF': 'login', 'Content-Type': 'application/json' })
  })
  it.each([401, 403])('invalidates on %s without displaying raw server content', async status => {
    const h = hooks()
    const client = new BffClient(h, vi.fn().mockResolvedValue(json({ code: 'DENIED', detail: 'private-token' }, status)))
    await expect(client.request('/b2b/auth/session', text)).rejects.toMatchObject({ status })
    expect(h.invalid).toHaveBeenCalledWith(status)
  })
  it.each([401, 403])('invalidates even when %s has an HTML or empty body', async status => {
    const h = hooks()
    const client = new BffClient(h, vi.fn().mockResolvedValue(new Response('<html>unavailable</html>', { status })))
    await expect(client.request('/b2b/auth/session', text)).rejects.toMatchObject({ status })
    expect(h.invalid).toHaveBeenCalledWith(status)
  })
  it.each(['/b2b/api/x/../../../api/orders', '/b2b/api/%2e%2e/%2e%2e/api/orders'])('rejects normalized escape %s before fetch', async path => {
    const fetcher = vi.fn()
    await expect(new BffClient(hooks(), fetcher).request(path, text)).rejects.toThrow()
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('accepts a typed durable B3 rejection on 409 and pending on 202', async () => {
    for (const [status, state] of [[409, 'rejected'], [202, 'pending']] as const) {
      const client = new BffClient(hooks(), vi.fn().mockResolvedValue(json({ ...resultFixture, status: state }, status)))
      expect((await client.request('/b2b/api/stock/operations?namespace=adjust&operation_id=x', resultDecoder)).status).toBe(state)
    }
  })
  it('discards a late body even when fetch ignores abort', async () => {
    let generation = 1
    let release!: (response: Response) => void
    const fetcher = vi.fn(() => new Promise<Response>(resolve => { release = resolve }))
    const client = new BffClient({ ...hooks(), generation: () => generation }, fetcher)
    const read = client.request('/b2b/api/products', text)
    generation++; client.abort(); release(json('tenant-a'))
    await expect(read).rejects.toBeInstanceOf(StaleResponse)
  })
  it('bounds timeouts and never retries a mutation', async () => {
    const fetcher = vi.fn((_path, options) => new Promise<Response>((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    }))
    const client = new BffClient(hooks(), fetcher, 5)
    await expect(client.request('/b2b/api/orders/prepare', text, { method: 'POST', body: {} })).rejects.toMatchObject({ uncertain: true, status: 0 })
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('preserves explicit OUTCOME_UNKNOWN and never treats it as an abort', async () => {
    const client = new BffClient(hooks(), vi.fn().mockResolvedValue(json({ code: 'OUTCOME_UNKNOWN' }, 503)))
    await expect(client.request('/b2b/api/orders/prepare', text, { method: 'POST', body: {} })).rejects.toBeInstanceOf(BffError)
  })
  it.each(['javascript:alert(1)', 'https://user:secret@id.test/auth', 'http://public.test/auth', 'https://id.test/auth?client_secret=bad'])('rejects unsafe navigation %s', value => {
    expect(() => providerNavigation(value)).toThrow()
  })
})
