import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import AdminProductImportPage from './AdminProductImportPage.vue'
import { ApiError } from '../api/http'
import type { CsvPreview, CsvImportResult } from '../api/admin'

const mocks = vi.hoisted(() => ({ preview: vi.fn(), execute: vi.fn(), result: vi.fn(), logout: vi.fn() }))
vi.mock('../api/admin', () => ({
  previewAdminProductCsv: mocks.preview, executeAdminProductCsv: mocks.execute,
  getAdminProductCsvBatch: mocks.result, adminLogout: mocks.logout,
}))
const operationId = '4d22787c-a342-4c9c-b2b6-4fcd03027df4'
const fingerprint = 'a'.repeat(64)
const approved: CsvPreview = {
  fingerprint, analysed: 2, valid: 2, invalid: 0, existing_conflicts: 0,
  issues: [], binding: 'signed-preview', delimiter: ',', issues_truncated: false, product_lines: [2, 3],
  products: [
    { sku: 'NEW-A', name: 'Drill', category: 'DRILL', price: 1, stock: 0 },
    { sku: 'NEW-B', name: 'Saw', category: 'SAW', price: 2, stock: 0 },
  ],
}
const created: CsvImportResult = { operation_id: operationId, fingerprint, created: 2, skus: ['NEW-A', 'NEW-B'] }
let wrapper: VueWrapper | undefined
let router: ReturnType<typeof createRouter>
beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  vi.stubGlobal('crypto', { randomUUID: () => operationId })
  mocks.preview.mockResolvedValue(approved)
  mocks.execute.mockResolvedValue(created)
  mocks.result.mockResolvedValue(created)
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
async function open() {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/admin/products/import', component: AdminProductImportPage },
      { path: '/admin/products', component: { template: '<p>Catalogue réel</p>' } },
      { path: '/admin/login', component: { template: '<p>Login</p>' } },
    ],
  })
  await router.push('/admin/products/import')
  wrapper = mount(AdminProductImportPage, { global: { plugins: [router] } })
  await flushPromises()
}
function button(text: string) { return wrapper!.findAll('button').find(b => b.text() === text)! }
async function select(file = new File(['sku,name,category,price\nNEW-A,Drill,DRILL,1'], 'products.csv'), force = false) {
  const input = wrapper!.get('input[type=file]')
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  if (force) { input.element.dispatchEvent(new Event('change')); await flushPromises() }
  else await input.trigger('change')
}
async function ready() {
  await select()
  await button('Prévisualiser').trigger('click')
  await flushPromises()
  await wrapper!.get('input[type=checkbox]').setValue(true)
}

it('requires selection, approved preview and explicit confirmation then shows only real result', async () => {
  await open()
  expect(button('Prévisualiser').attributes('disabled')).toBeDefined()
  await select()
  await button('Prévisualiser').trigger('click'); await flushPromises()
  expect(button('Confirmer l’import').attributes('disabled')).toBeDefined()
  await wrapper!.get('input[type=checkbox]').setValue(true)
  await button('Confirmer l’import').trigger('click'); await flushPromises()
  expect(mocks.execute).toHaveBeenCalledTimes(1)
  expect(mocks.execute.mock.calls[0]![1]).toBe(operationId)
  expect(wrapper!.text()).toContain('Import confirmé par le serveur')
  expect(wrapper!.text()).toContain('2 produits créés')
  expect(sessionStorage.length).toBe(0)
  expect(wrapper!.findAll('a').some(a => a.attributes('href') === '/admin/products')).toBe(true)
})

it('rejects empty and oversized uploads locally', async () => {
  await open()
  await select(new File([], 'empty.csv'))
  expect(wrapper!.text()).toContain('non vide')
  await select(new File([new Uint8Array(2 * 1024 * 1024 + 1)], 'big.csv'))
  expect(button('Prévisualiser').attributes('disabled')).toBeDefined()
  expect(mocks.preview).not.toHaveBeenCalled()
})

it('replaces preview errors and invalidates approval when changing files', async () => {
  await open()
  mocks.preview.mockResolvedValueOnce({
    ...approved, valid: 0, invalid: 2, products: [], product_lines: [], binding: null,
    issues: [{ line: 2, code: 'duplicate', message: 'SKU repeated' }],
  })
  await select(); await button('Prévisualiser').trigger('click'); await flushPromises()
  expect(wrapper!.text()).toContain('Ligne 2')
  expect(button('Confirmer l’import').attributes('disabled')).toBeDefined()
  await ready()
  expect(wrapper!.text()).not.toContain('SKU repeated')
  await select(new File(['replacement'], 'other.csv'))
  expect(wrapper!.find('input[type=checkbox]').exists()).toBe(false)
})

it('shows existing SKU conflicts and prevents confirmation', async () => {
  mocks.preview.mockResolvedValue({ ...approved, binding: null, existing_conflicts: 1,
    valid: 1, products: approved.products.slice(0, 1), product_lines: [2] })
  await open(); await select(); await button('Prévisualiser').trigger('click'); await flushPromises()
  expect(wrapper!.text()).toContain('SKU existants : 1')
  expect(wrapper!.get('input[type=checkbox]').attributes('disabled')).toBeDefined()
})

it('guards double execution and persists only operation fingerprint before POST', async () => {
  let resolve!: (value: CsvImportResult) => void
  mocks.execute.mockImplementation(() => new Promise(r => { resolve = r }))
  await open(); await ready()
  await button('Confirmer l’import').trigger('click')
  await button('Confirmer l’import').trigger('click')
  expect(mocks.execute).toHaveBeenCalledTimes(1)
  expect(JSON.parse(sessionStorage.getItem('stockpilot_pending_product_import_v6')!)).toEqual({
    operation_id: operationId, fingerprint,
  })
  resolve(created); await flushPromises()
  expect(wrapper!.text()).toContain('Import confirmé par le serveur')
})

it('fails closed if storage cannot persist the operation before POST', async () => {
  await open(); await ready()
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('unavailable') })
  await button('Confirmer l’import').trigger('click'); await flushPromises()
  expect(mocks.execute).not.toHaveBeenCalled()
  expect(wrapper!.text()).toContain('aucun import envoyé')
  expect(wrapper!.get('input[type=file]').attributes('disabled')).toBeDefined()
})

it('times out once without replacing the operation or retrying POST', async () => {
  await open(); await ready()
  vi.useFakeTimers()
  mocks.execute.mockImplementation((_file, _operation, _binding, signal: AbortSignal) => new Promise((_resolve, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
  }))
  await button('Confirmer l’import').trigger('click')
  await vi.advanceTimersByTimeAsync(20_001); await flushPromises()
  expect(wrapper!.text()).toContain('Résultat incertain')
  expect(mocks.execute).toHaveBeenCalledTimes(1)
  expect(JSON.parse(sessionStorage.getItem('stockpilot_pending_product_import_v6')!).operation_id).toBe(operationId)
})

it('guards duplicate preview and ignores stale approval after file replacement', async () => {
  let resolve!: (value: CsvPreview) => void
  mocks.preview.mockImplementation(() => new Promise(r => { resolve = r }))
  await open(); await select()
  await button('Prévisualiser').trigger('click')
  await button('Prévisualiser').trigger('click')
  expect(mocks.preview).toHaveBeenCalledTimes(1)
  await select(new File(['replacement'], 'replacement.csv'), true)
  resolve(approved); await flushPromises()
  expect(wrapper!.find('input[type=checkbox]').exists()).toBe(false)
})

it('keeps uncertain ID and never automatically retries POST; absent GET remains blocked', async () => {
  mocks.execute.mockRejectedValue(new ApiError('network', 0))
  mocks.result.mockRejectedValue(new ApiError('absent', 404))
  await open(); await ready(); await button('Confirmer l’import').trigger('click'); await flushPromises()
  expect(wrapper!.text()).toContain('Résultat incertain')
  expect(mocks.result).not.toHaveBeenCalled()
  await button('Vérifier le résultat du lot').trigger('click'); await flushPromises()
  expect(wrapper!.get('input[type=file]').attributes('disabled')).toBeDefined()
  expect(mocks.execute).toHaveBeenCalledTimes(1)
  expect(sessionStorage.length).toBe(1)
})

it('restores pending operation on reload and reconciles without storing file', async () => {
  sessionStorage.setItem('stockpilot_pending_product_import_v6', JSON.stringify({ operation_id: operationId, fingerprint }))
  await open()
  expect(wrapper!.text()).toContain('Résultat incertain')
  await button('Vérifier le résultat du lot').trigger('click'); await flushPromises()
  expect(wrapper!.text()).toContain('Import confirmé par le serveur')
  expect(mocks.execute).not.toHaveBeenCalled()
})

it('rejects mismatch reconciliation and corrupt storage without mutation', async () => {
  sessionStorage.setItem('stockpilot_pending_product_import_v6', JSON.stringify({ operation_id: operationId, fingerprint }))
  mocks.result.mockResolvedValue({ ...created, fingerprint: 'b'.repeat(64) })
  await open(); await button('Vérifier le résultat du lot').trigger('click'); await flushPromises()
  expect(wrapper!.text()).toContain('incohérent')
  expect(sessionStorage.length).toBe(1)
  wrapper!.unmount()
  sessionStorage.setItem('stockpilot_pending_product_import_v6', 'bad')
  await open()
  expect(wrapper!.text()).toContain('corrompu')
  expect(wrapper!.get('input[type=file]').attributes('disabled')).toBeDefined()
})

it.each([403, 409, 413, 422])('definitive refusal %s requires a fresh preview', async (status) => {
  mocks.execute.mockRejectedValue(new ApiError('refused', status))
  await open(); await ready(); await button('Confirmer l’import').trigger('click'); await flushPromises()
  expect(wrapper!.text()).not.toContain('Import confirmé par le serveur')
  expect(wrapper!.find('input[type=checkbox]').exists()).toBe(false)
  expect(sessionStorage.length).toBe(0)
})

it('expiry redirects login and ignores late result after unmount', async () => {
  mocks.execute.mockRejectedValue(new ApiError('expired', 401))
  await open(); await ready(); await button('Confirmer l’import').trigger('click'); await flushPromises()
  expect(router.currentRoute.value.path).toBe('/admin/login')
  expect(sessionStorage.length).toBe(1)
  wrapper!.unmount()
  sessionStorage.clear()
  let resolve!: (value: CsvImportResult) => void
  mocks.execute.mockImplementation(() => new Promise(r => { resolve = r }))
  await open(); await ready(); await button('Confirmer l’import').trigger('click')
  wrapper!.unmount()
  resolve(created); await flushPromises()
  expect(sessionStorage.length).toBe(1)
})
