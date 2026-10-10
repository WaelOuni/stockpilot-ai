import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { adminMe, adjustAdminProductStock, getAdminStockAdjustment, listAdminStockMovements } from './admin'
import type { StockMovement } from '../types/api'

const operationId = '4d22787c-a342-4c9c-b2b6-4fcd03027df4'
const original: StockMovement = {
  movement_id: 'movement-1', operation_id: operationId, product_sku: 'SKU-1',
  delta: 2, stock_before: 5, stock_after: 7, reason: 'count',
  occurred_at: '2026-10-10T10:00:00Z', source: 'BACKOFFICE',
}
const json = (body: unknown) => new Response(JSON.stringify(body))
const fetchMock = vi.fn<typeof fetch>()
beforeEach(async () => {
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockReset()
  fetchMock.mockResolvedValueOnce(json({ username: 'admin', csrf_token: 'csrf' }))
  await adminMe()
})
afterEach(() => vi.unstubAllGlobals())

it('sends only the normalized adjustment through same-origin cookie CSRF transport', async () => {
  fetchMock.mockResolvedValueOnce(json(original))
  expect(await adjustAdminProductStock('SKU-1', operationId, 2, '  count  ')).toEqual(original)
  const [url, options] = fetchMock.mock.calls.at(-1)!
  expect(url).toBe('/api/backoffice/products/SKU-1/stock-adjustments')
  expect(options).toMatchObject({ method: 'POST', credentials: 'include', cache: 'no-store', redirect: 'error' })
  expect(new Headers(options?.headers).get('X-Admin-CSRF')).toBe('csrf')
  expect(new Headers(options?.headers).has('Authorization')).toBe(false)
  expect(JSON.parse(String(options?.body))).toEqual({ operation_id: operationId, delta: 2, reason: 'count' })
})

it.each([
  { operation_id: 'invalid' }, { product_sku: 'SKU-2' }, { delta: 0 },
  { delta: 1.5 }, { delta: 1_000_001 }, { stock_before: -1 }, { stock_after: -1 },
  { stock_after: 8 }, { stock_after: Number.MAX_SAFE_INTEGER + 1 },
  { reason: '' }, { reason: ' count ' }, { reason: 'x'.repeat(501) },
  { source: 'SYSTEM' }, { occurred_at: 'invalid' },
])('rejects malformed or mismatched committed results %j as uncertain, never retrying', async change => {
  fetchMock.mockResolvedValueOnce(json({ ...original, ...change }))
  await expect(adjustAdminProductStock('SKU-1', operationId, 2, 'count')).rejects.toMatchObject({ status: 0 })
  expect(fetchMock.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(1)
})

it.each([0, 1.5, 1_000_001, Number.NaN])('rejects local invalid delta %s before fetch', async delta => {
  await expect(adjustAdminProductStock('SKU-1', operationId, delta, 'count')).rejects.toMatchObject({ status: 422 })
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it('reconciliation uses GET and rejects another operation identifier', async () => {
  fetchMock.mockResolvedValueOnce(json({ ...original, operation_id: '4d22787c-a342-4c9c-b2b6-4fcd03027df5' }))
  await expect(getAdminStockAdjustment(operationId)).rejects.toMatchObject({ status: 0 })
  expect(fetchMock.mock.calls.at(-1)?.[0]).toBe(`/api/backoffice/stock-adjustments/${operationId}`)
  expect(fetchMock.mock.calls.at(-1)?.[1]?.method).toBe('GET')
})

it.each([
  { has_more: 'true' }, { offset: 0 }, { count: 9 },
  { items: [{ ...original, product_sku: 'SKU-2' }] },
])('rejects inconsistent stock history pages %j', async change => {
  fetchMock.mockResolvedValueOnce(json({
    items: [original], count: 1, limit: 20, offset: 20, has_more: false, ...change,
  }))
  await expect(listAdminStockMovements('SKU-1', 20)).rejects.toMatchObject({ status: 0 })
})
