import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  adminMe, adminLogout, previewAdminProductCsv, executeAdminProductCsv, getAdminProductCsvBatch,
} from './admin'
import type { CsvPreview, CsvImportResult } from './admin'

const fetchMock = vi.fn<typeof fetch>()
const operationId = '4d22787c-a342-4c9c-b2b6-4fcd03027df4'
const fingerprint = 'a'.repeat(64)
const preview: CsvPreview = {
  fingerprint, analysed: 1, valid: 1, invalid: 0, existing_conflicts: 0,
  products: [{ sku: 'CSV-A', name: 'Drill', category: 'DRILL', price: 1, stock: 0 }],
  product_lines: [2], issues: [], issues_truncated: false, delimiter: ',', binding: 'signed',
}
const result: CsvImportResult = { operation_id: operationId, fingerprint, created: 1, skus: ['CSV-A'] }
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })
beforeEach(async () => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  fetchMock.mockResolvedValueOnce(json({ username: 'admin', csrf_token: 'csrf-only' }))
  await adminMe()
  fetchMock.mockClear()
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('sends raw bounded CSV with cookies and existing CSRF, never JWT/technical credentials', async () => {
  fetchMock.mockResolvedValueOnce(json(preview))
  const file = new Blob(['sku,name,category,price\nCSV-A,Drill,DRILL,1'])
  expect(await previewAdminProductCsv(file)).toEqual(preview)
  const [path, options] = fetchMock.mock.calls[0]!
  expect(path).toBe('/api/backoffice/products/import/preview')
  expect(options).toMatchObject({ method: 'POST', credentials: 'include', cache: 'no-store', body: file, redirect: 'error' })
  const headers = options!.headers as Headers
  expect(headers.get('Content-Type')).toBe('text/csv; charset=utf-8')
  expect(headers.get('X-Admin-CSRF')).toBe('csrf-only')
  expect(headers.has('Authorization')).toBe(false)
})

it('execute preserves operation/binding and explicit confirmation; GET uses no mutation headers', async () => {
  fetchMock.mockImplementation(async () => json(result))
  const file = new Blob(['csv'])
  expect(await executeAdminProductCsv(file, operationId, 'signed')).toEqual(result)
  const headers = fetchMock.mock.calls[0]![1]!.headers as Headers
  expect(headers.get('X-Import-Operation-Id')).toBe(operationId)
  expect(headers.get('X-Import-Preview')).toBe('signed')
  expect(headers.get('X-Import-Confirm')).toBe('CREATE_ONLY')
  expect(await getAdminProductCsvBatch(operationId)).toEqual(result)
  expect(fetchMock.mock.calls[1]![0]).toBe(`/api/backoffice/products/import/batches/${operationId}`)
  expect(fetchMock.mock.calls[1]![1]!.method).toBe('GET')
  expect((fetchMock.mock.calls[1]![1]!.headers as Headers).has('X-Import-Confirm')).toBe(false)
})

it.each([401, 403, 404, 409, 422, 500, 503])('sanitizes HTTP %s and never retries import', async (status) => {
  fetchMock.mockResolvedValue(json({ detail: 'private-server-diagnostic' }, status))
  await expect(executeAdminProductCsv(new Blob(['csv']), operationId, 'signed')).rejects.toMatchObject({ status })
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it.each([
  { ...preview, fingerprint: 'bad' },
  { ...preview, valid: 2 },
  { ...preview, product_lines: [] },
  { ...preview, issues: Array.from({ length: 101 }, () => ({ line: 2, code: 'error', message: 'bounded' })) },
  { ...preview, binding: 'signed', invalid: 1 },
  { ...preview, products: [{ ...preview.products[0], stock: 10 }] },
])('rejects malformed preview rather than enabling confirmation', async (value) => {
  fetchMock.mockResolvedValueOnce(json(value))
  await expect(previewAdminProductCsv(new Blob(['csv']))).rejects.toMatchObject({ status: 0 })
})

it.each([
  { ...result, operation_id: 'different' }, { ...result, fingerprint: 'bad' },
  { ...result, created: 2 }, { ...result, created: 0, skus: [] },
  { ...result, _id: 'internal-must-not-expose' },
])('rejects malformed receipt and retains uncertainty for caller', async (value) => {
  fetchMock.mockResolvedValueOnce(json(value))
  await expect(getAdminProductCsvBatch(operationId)).rejects.toMatchObject({ status: 0 })
})

it('does not resend on network failure or invalid JSON', async () => {
  fetchMock.mockRejectedValueOnce(new Error('private network failure'))
  await expect(executeAdminProductCsv(new Blob(['csv']), operationId, 'signed')).rejects.toMatchObject({ status: 0 })
  fetchMock.mockResolvedValueOnce(new Response('invalid JSON'))
  await expect(getAdminProductCsvBatch(operationId)).rejects.toMatchObject({ status: 0 })
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

it('rejects delayed import JSON after session logout epoch changed', async () => {
  let resolve!: (value: unknown) => void
  const response = json(result)
  vi.spyOn(response, 'json').mockImplementation(() => new Promise(r => { resolve = r }))
  fetchMock.mockResolvedValueOnce(response)
  const pending = executeAdminProductCsv(new Blob(['csv']), operationId, 'signed')
  await Promise.resolve(); await Promise.resolve()
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
  await adminLogout()
  resolve(result)
  await expect(pending).rejects.toMatchObject({ status: 401 })
})
