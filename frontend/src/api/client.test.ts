import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getCustomerOrder, listCustomerOrders, sendAssistantMessage } from './client'
import { ApiError, requestJson } from './http'
import { setCustomerToken } from '../utils/customerSession'

const fetchMock = vi.fn<typeof fetch>()
function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json', 'x-request-id': 'request-123' },
  })
}

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock)
  setCustomerToken('demo-test-token')
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  setCustomerToken('')
})

describe('HTTP client', () => {
  it('encodes an order identifier and validates detail response', async () => {
    fetchMock.mockResolvedValueOnce(json({ order_id: 'a/b', product_name: 'Bosch X100', quantity: 1, status: 'CREATED', created_at: '2026-10-10' }))
    expect((await getCustomerOrder('a/b')).order_id).toBe('a/b')
    expect(fetchMock.mock.calls[0]![0]).toBe('/api/orders/a%2Fb')
    fetchMock.mockResolvedValueOnce(json({ status: 'PAID' }))
    await expect(getCustomerOrder('id')).rejects.toThrow('incomplet')
  })
  it('sends the real assistant contract and centralized Bearer header', async () => {
    fetchMock.mockResolvedValue(json({ response: 'Bonjour', intent: 'UNKNOWN' }))
    const body = { message: 'Bonjour', thread_id: 'thread-1' }
    expect(await sendAssistantMessage(body)).toMatchObject({ response: 'Bonjour' })
    const [url, options] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/assistant')
    expect(options?.method).toBe('POST')
    expect(options?.body).toBe(JSON.stringify(body))
    const headers = new Headers(options?.headers)
    expect(headers.get('Authorization')).toBe('Bearer demo-test-token')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(headers.has('X-Customer-Id')).toBe(false)
    expect(options?.redirect).toBe('error')
    expect(options?.credentials).toBe('omit')
  })

  it('uses a configurable base URL and the current token for each call', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://example.test/api/')
    setCustomerToken('replacement-token')
    fetchMock.mockResolvedValue(json({ items: [], limit: 20, offset: 40, count: 0 }))
    await listCustomerOrders(20, 40)
    const [url, options] = fetchMock.mock.calls[0]!
    expect(url).toBe('https://example.test/api/orders?limit=20&offset=40')
    expect(new Headers(options?.headers).get('Authorization')).toBe('Bearer replacement-token')
  })

  it('does not call the backend without a token', async () => {
    setCustomerToken('')
    await expect(listCustomerOrders()).rejects.toMatchObject({ status: 401 })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([401, 403, 422, 500, 503])('normalizes HTTP %s without exposing an arbitrary body', async (status) => {
    fetchMock.mockResolvedValue(json({ detail: 'demo-test-token SECRET' }, status))
    try {
      await listCustomerOrders()
      expect.unreachable('Expected HTTP failure')
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      expect(error).toMatchObject({ status, requestId: 'request-123' })
      expect(String(error)).not.toContain('demo-test-token')
      expect(String(error)).not.toContain('SECRET')
    }
  })

  it('normalizes network failures', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(listCustomerOrders()).rejects.toMatchObject({ status: 0 })
  })

  it('reports invalid JSON and incomplete endpoint responses', async () => {
    fetchMock.mockResolvedValueOnce(new Response('not JSON'))
    await expect(requestJson('/orders')).rejects.toThrow('JSON invalide')
    fetchMock.mockResolvedValueOnce(json({ intent: 'ORDER' }))
    await expect(sendAssistantMessage({ message: 'test', thread_id: 't' })).rejects.toThrow('incomplete')
    fetchMock.mockResolvedValueOnce(json({ items: [] }))
    await expect(listCustomerOrders()).rejects.toThrow('incomplete')
  })

  it('rejects malformed order items and inconsistent page metadata', async () => {
    fetchMock.mockResolvedValueOnce(json({ items: [null], count: 1, limit: 20, offset: 0 }))
    await expect(listCustomerOrders()).rejects.toThrow('incomplete')
    fetchMock.mockResolvedValueOnce(json({ items: [], count: 1, limit: 20, offset: 0 }))
    await expect(listCustomerOrders()).rejects.toThrow('incomplete')
  })

  it('forwards cancellation without converting it into a network error', async () => {
    const controller = new AbortController()
    controller.abort()
    const failure = new DOMException('Aborted', 'AbortError')
    fetchMock.mockRejectedValue(failure)
    await expect(listCustomerOrders(20, 0, controller.signal)).rejects.toBe(failure)
  })
})
