import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { adminLogin, adminLogout, adminMe, cancelAdminOrder, getAdminOrder, listAdminOrders, updateAdminOrderStatus } from './admin'
import { setCustomerToken } from '../utils/customerSession'
import { ApiError } from './http'

const fetchMock = vi.fn<typeof fetch>()
beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  setCustomerToken('customer-jwt-not-admin')
})
afterEach(() => { setCustomerToken(''); vi.unstubAllGlobals(); vi.unstubAllEnvs() })
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })

it('uses only same-origin cookies and never customer Authorization or VITE base', async () => {
  vi.stubEnv('VITE_API_BASE_URL', 'https://untrusted.test')
  fetchMock.mockResolvedValue(json({ items: [], count: 0, limit: 20, offset: 20 }))
  const controller = new AbortController()
  await listAdminOrders(20, controller.signal)
  const [url, options] = fetchMock.mock.calls[0]!
  expect(url).toBe('/api/backoffice/orders?limit=20&offset=20')
  expect(options).toMatchObject({ credentials: 'include', cache: 'no-store', redirect: 'error', signal: controller.signal })
  expect(new Headers(options?.headers).has('Authorization')).toBe(false)
  vi.unstubAllEnvs()
})

it('login custom header and logout session CSRF are separate and never stored', async () => {
  const local = vi.spyOn(Storage.prototype, 'setItem')
  fetchMock.mockResolvedValueOnce(json({ username: 'admin', csrf_token: 'session-csrf' }))
  await adminLogin('admin', 'test-only-long-password')
  let options = fetchMock.mock.calls[0]![1]!
  expect(new Headers(options.headers).get('X-Admin-CSRF')).toBe('login')
  expect(new Headers(options.headers).get('Content-Type')).toBe('application/json')
  expect(new Headers(options.headers).has('Authorization')).toBe(false)
  expect(JSON.parse(String(options.body))).toEqual({ username: 'admin', password: 'test-only-long-password' })
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
  await adminLogout()
  options = fetchMock.mock.calls[1]![1]!
  expect(new Headers(options.headers).get('X-Admin-CSRF')).toBe('session-csrf')
  expect(options.body).toBeUndefined()
  expect(local).not.toHaveBeenCalled()
  local.mockRestore()
})

it('recovers logout CSRF via me after reload or successful logout', async () => {
  fetchMock.mockResolvedValueOnce(json({ username: 'admin', csrf_token: 'csrf' }))
  await adminMe()
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
  await adminLogout()
  fetchMock.mockResolvedValueOnce(json({ username: 'admin', csrf_token: 'fresh' }))
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
  await adminLogout()
  expect(fetchMock.mock.calls.slice(-2).map(([path]) => path)).toEqual(['/api/backoffice/session/me', '/api/backoffice/session/logout'])
})

it.each([401, 403, 404, 409, 422, 429, 500, 503])('sanitizes error bodies for status %i', async (status) => {
  fetchMock.mockResolvedValue(json({ detail: 'sensitive server input' }, status))
  await expect(getAdminOrder('order/1')).rejects.toMatchObject({ status })
  try { await getAdminOrder('order/1') } catch (cause) {
    expect((cause as Error).message).not.toContain('sensitive server input')
  }
  expect(fetchMock.mock.calls[0]![0]).toBe('/api/backoffice/orders/order%2F1')
})

it('mutations reuse session-bound CSRF and cookie-only fixed transport', async () => {
  fetchMock.mockResolvedValueOnce(json({ username: 'admin', csrf_token: 'bound-csrf' }))
  await adminMe()
  const order = { order_id: 'one', customer_id: null, product_name: 'Bosch', quantity: 1,
    created_at: '2026-10-10T10:00:00Z', status: 'PROCESSING', events: [] }
  fetchMock.mockImplementation(async () => json(order))
  const controller = new AbortController()
  await updateAdminOrderStatus('one/2', 'PROCESSING', controller.signal)
  await cancelAdminOrder('one/2', controller.signal)
  const calls = fetchMock.mock.calls.slice(-2)
  expect(calls.map(([path]) => path)).toEqual(['/api/backoffice/orders/one%2F2/status', '/api/backoffice/orders/one%2F2/cancel'])
  expect(calls.map(([, options]) => options!.method)).toEqual(['PATCH', 'POST'])
  for (const [, options] of calls) {
    expect(new Headers(options!.headers).get('X-Admin-CSRF')).toBe('bound-csrf')
    expect(new Headers(options!.headers).has('Authorization')).toBe(false)
    expect(options!.signal).toBe(controller.signal)
    expect(options!.credentials).toBe('include')
  }
  expect(JSON.parse(String(calls[0]![1]!.body))).toEqual({ status: 'PROCESSING' })
  expect(calls[1]![1]!.body).toBeUndefined()
})

it('invalid rereads cannot authorize mutations with fabricated status/events', async () => {
  fetchMock.mockResolvedValue(json({ order_id: 'one', status: 'CREATED', events: 'not-events' }))
  await expect(getAdminOrder('one')).rejects.toMatchObject({ status: 0 })
})

it('logout invalidates responses whose JSON parsing was already pending', async () => {
  fetchMock.mockResolvedValueOnce(json({ username: 'admin', csrf_token: 'csrf' }))
  await adminMe()
  const response = json({})
  let resolve!: (body: unknown) => void
  const parsed = vi.spyOn(response, 'json').mockReturnValue(new Promise(done => { resolve = done }))
  fetchMock.mockResolvedValueOnce(response)
  const pending = getAdminOrder('one')
  const rejected = expect(pending).rejects.toMatchObject({ status: 401 })
  await Promise.resolve()
  expect(parsed).toHaveBeenCalledOnce()
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
  await adminLogout()
  resolve({})
  await rejected
})

it('network and JSON failures are sanitized; abort retains cancellation', async () => {
  fetchMock.mockRejectedValue(new Error('private network detail'))
  await expect(adminMe()).rejects.toMatchObject({ status: 0 })
  const signal = new AbortController()
  signal.abort()
  const aborted = new DOMException('Aborted', 'AbortError')
  fetchMock.mockRejectedValue(aborted)
  await expect(getAdminOrder('order', signal.signal)).rejects.toBe(aborted)
  fetchMock.mockResolvedValue(new Response('not JSON'))
  await expect(adminMe()).rejects.toBeInstanceOf(ApiError)
})
