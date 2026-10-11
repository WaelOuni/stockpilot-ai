import { validatePagination } from './pagination'
import type { ProductFilters } from './client'
import { ApiError } from './http'
import type { CustomerOrdersResponse, OrderResponse, OrderStatus } from '../types/api'
import type {
  BackofficeDashboard, ProductResponse as Product, ProductCategory, ProductsResponse, StockMovement,
  StockMovementsResponse,
} from '../types/api'
import { productCategoryLabels } from '../utils/products'

export interface AdminSession { username: string; csrf_token: string }
export interface AdminOrder extends OrderResponse { customer_id: string | null; events: NonNullable<OrderResponse['events']> }
export interface AdminOrders extends Omit<CustomerOrdersResponse, 'items'> { items: AdminOrder[] }
const orderStatuses: OrderStatus[] = ['CREATED', 'PROCESSING', 'SHIPPED', 'DELIVERED', 'CANCELLED']
let csrfToken = ''
let sessionGeneration = 0

async function adminRequest<T>(
  path: string,
  options: { method?: 'POST' | 'PATCH'; body?: unknown; csrf?: string; signal?: AbortSignal;
    raw?: Blob; headers?: Record<string, string> } = {},
): Promise<T> {
  const generation = sessionGeneration
  const headers = new Headers({ Accept: 'application/json' })
  if (options.body !== undefined) headers.set('Content-Type', 'application/json')
  if (options.csrf) headers.set('X-Admin-CSRF', options.csrf)
  if (options.raw) headers.set('Content-Type', 'text/csv; charset=utf-8')
  for (const [key, value] of Object.entries(options.headers || {})) headers.set(key, value)
  let response: Response
  try {
    // Fixed same-origin proxy: admin cookies must never follow a VITE URL.
    response = await fetch(`/api/backoffice${path}`, {
      method: options.method || 'GET', headers,
      body: options.raw || (options.body === undefined ? undefined : JSON.stringify(options.body)),
      credentials: 'include', cache: 'no-store', redirect: 'error', signal: options.signal,
    })
  } catch (cause) {
    if (options.signal?.aborted) throw cause
    throw new ApiError('Connexion impossible au back-office.', 0)
  }
  if (generation !== sessionGeneration) throw new ApiError('Session administrateur modifiée.', 401)
  if (!response.ok) {
    if (response.status === 401) { csrfToken = ''; sessionGeneration++ }
    const messages: Record<number, string> = {
      401: 'Identifiants invalides ou session administrateur expirée.',
      403: 'Accès refusé : origine ou protection CSRF invalide.',
      404: 'Commande introuvable.',
      409: 'La commande a changé ou cette opération est interdite. Vérification du statut nécessaire.',
      422: 'Informations de connexion invalides.',
      429: 'Trop de tentatives. Attendez une minute.',
      503: 'Back-office indisponible. Vérifiez sa configuration serveur.',
    }
    const productMessages: Record<number, string> = {
      404: 'Produit introuvable.',
      409: 'Ce SKU existe déjà. Vérifiez le produit avant toute nouvelle opération.',
      422: 'Informations produit invalides.',
      503: 'Gestion produits indisponible : vérifiez la configuration et la préparation de l’index SKU.',
    }
    const stockMessages: Record<number, string> = {
      404: path.includes('/stock-adjustments/')
        ? 'Aucun mouvement visible pour cette opération.'
        : 'Produit ou mouvement introuvable.',
      409: 'Ajustement refusé : vérifiez le stock actuel et le résultat de l’opération.',
      422: 'Ajustement de stock invalide.',
      503: 'Ajustement indisponible ou résultat incertain. Vérifiez l’opération avant toute nouvelle action.',
    }
    const importMessages: Record<number, string> = {
      404: 'Aucun résultat de lot visible ; cela ne prouve pas un échec.',
      409: 'Import CREATE_ONLY refusé : conflit de SKU ou d’opération. Prévisualisez à nouveau.',
      413: 'Le CSV dépasse la limite serveur.',
      422: 'CSV, confirmation ou aperçu invalide/expiré.',
      500: 'Import impossible ou résultat incertain. Vérifiez le résultat du lot.',
      503: 'Import indisponible ou résultat inconnu. Vérifiez les indexes SKU/import et le résultat du lot.',
    }
    throw new ApiError((path.startsWith('/products/import') && importMessages[response.status])
      || (path.includes('/stock-') || path.startsWith('/stock-adjustments')
      ? stockMessages[response.status]
      : undefined)
      || (path.startsWith('/products') && productMessages[response.status])
      || messages[response.status] || 'Erreur du back-office.', response.status)
  }
  if (response.status === 204) return undefined as T
  let value: T
  try { value = await response.json() as T }
  catch { throw new ApiError('Réponse du back-office invalide.', 0) }
  if (generation !== sessionGeneration) throw new ApiError('Session administrateur modifiée.', 401)
  return value
}

export async function adminMe(): Promise<AdminSession> {
  const session = await adminRequest<AdminSession>('/session/me')
  csrfToken = session.csrf_token
  return session
}
export async function adminLogin(username: string, password: string): Promise<void> {
  sessionGeneration++
  csrfToken = ''
  const session = await adminRequest<AdminSession>('/session/login', {
    method: 'POST', body: { username, password }, csrf: 'login',
  })
  csrfToken = session.csrf_token
}
export async function adminLogout(): Promise<void> {
  sessionGeneration++
  if (!csrfToken) await adminMe()
  await adminRequest<void>('/session/logout', { method: 'POST', csrf: csrfToken })
  csrfToken = ''
}
export function listAdminOrders(offset: number, signal?: AbortSignal, q = ''): Promise<AdminOrders> {
  const params = new URLSearchParams({ limit: '20', offset: String(offset), include_total: 'true' })
  if (q.trim()) params.set('q', q.trim())
  return adminRequest<AdminOrders>(`/orders?${params}`, { signal }).then(page => { validatePagination(page); return page })
}

function exactKeys(value: object, expected: string[]): boolean {
  const keys = Object.keys(value).sort()
  return keys.length === expected.length && keys.every((key, index) => key === [...expected].sort()[index])
}

function nonnegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0
}

function validDate(value: unknown): value is string {
  return typeof value === 'string' && Number.isFinite(Date.parse(value))
}

export async function getAdminDashboard(signal?: AbortSignal): Promise<BackofficeDashboard> {
  const value = await adminRequest<BackofficeDashboard>('/dashboard', { signal })
  const validOrderStats = value && exactKeys(value, [
    'order_stats', 'product_stats', 'recent_orders', 'recent_stock_movements',
  ]) && exactKeys(value.order_stats, ['total_orders', 'by_status'])
    && nonnegativeInteger(value.order_stats.total_orders)
    && exactKeys(value.order_stats.by_status, orderStatuses)
    && orderStatuses.every(status => nonnegativeInteger(value.order_stats.by_status[status]))
    && Object.values(value.order_stats.by_status).reduce((sum, count) => sum + count, 0)
      <= value.order_stats.total_orders
  const validProductStats = value && exactKeys(value.product_stats, [
    'total_products', 'available_products', 'unavailable_products', 'total_stock_units',
  ]) && nonnegativeInteger(value.product_stats.total_products)
    && nonnegativeInteger(value.product_stats.available_products)
    && nonnegativeInteger(value.product_stats.unavailable_products)
    && Number.isSafeInteger(value.product_stats.total_stock_units)
    && value.product_stats.available_products + value.product_stats.unavailable_products
      <= value.product_stats.total_products
  const validRecentOrders = value && Array.isArray(value.recent_orders)
    && value.recent_orders.length <= 5
    && value.recent_orders.every(order => order && exactKeys(order, [
      'order_id', 'product_name', 'quantity', 'status', 'created_at',
    ]) && typeof order.order_id === 'string' && Boolean(order.order_id)
      && typeof order.product_name === 'string' && Number.isSafeInteger(order.quantity)
      && order.quantity > 0 && orderStatuses.includes(order.status) && validDate(order.created_at))
  const validRecentMovements = value && Array.isArray(value.recent_stock_movements)
    && value.recent_stock_movements.length <= 5
    && value.recent_stock_movements.every(movement => movement && exactKeys(movement, [
      'product_sku', 'delta', 'occurred_at', 'reason',
    ]) && typeof movement.product_sku === 'string' && Boolean(movement.product_sku)
      && Number.isSafeInteger(movement.delta) && movement.delta !== 0
      && validDate(movement.occurred_at) && typeof movement.reason === 'string')
  if (!validOrderStats || !validProductStats || !validRecentOrders || !validRecentMovements) {
    throw new ApiError('Réponse du tableau de bord invalide.', 0)
  }
  return value
}

export function getAdminOrder(orderId: string, signal?: AbortSignal): Promise<AdminOrder> {
  return adminRequest<AdminOrder>(`/orders/${encodeURIComponent(orderId)}`, { signal }).then(validateOrder)
}

function validateOrder(order: AdminOrder): AdminOrder {
  if (!order || typeof order.order_id !== 'string' || !orderStatuses.includes(order.status)
    || !(order.customer_id === null || typeof order.customer_id === 'string')
    || typeof order.product_name !== 'string' || !Number.isInteger(order.quantity)
    || !Number.isFinite(Date.parse(order.created_at)) || !Array.isArray(order.events)
    || order.events.some(event => !event || !orderStatuses.includes(event.status)
      || !Number.isFinite(Date.parse(event.occurred_at))
      || !['SYSTEM', 'BACKOFFICE', 'CUSTOMER'].includes(event.source))) {
    throw new ApiError('Réponse de commande invalide. Actualisez avant toute opération.', 0)
  }
  return order
}

export async function updateAdminOrderStatus(orderId: string, status: OrderStatus, signal?: AbortSignal): Promise<AdminOrder> {
  if (!csrfToken) throw new ApiError('Session administrateur absente.', 401)
  return validateOrder(await adminRequest<AdminOrder>(`/orders/${encodeURIComponent(orderId)}/status`, {
    method: 'PATCH', body: { status }, csrf: csrfToken, signal,
  }))
}
export async function cancelAdminOrder(orderId: string, signal?: AbortSignal): Promise<AdminOrder> {
  if (!csrfToken) throw new ApiError('Session administrateur absente.', 401)
  return validateOrder(await adminRequest<AdminOrder>(`/orders/${encodeURIComponent(orderId)}/cancel`, {
    method: 'POST', csrf: csrfToken, signal,
  }))
}

export interface ProductMetadata { name: string; category: ProductCategory; price: number }
export interface NewProduct extends ProductMetadata { sku: string }

function validateProduct(product: Product): Product {
  if (!product || typeof product.sku !== 'string' || !product.sku.trim()
    || typeof product.product_name !== 'string' || !product.product_name.trim()
    || !Object.hasOwn(productCategoryLabels, product.category)
    || typeof product.price !== 'number' || !Number.isFinite(product.price) || product.price < 0
    || !Number.isSafeInteger(product.stock) || product.stock < 0) {
    throw new ApiError('Réponse produit invalide. Actualisez avant toute opération.', 0)
  }
  return product
}

export async function listAdminProducts(offset: number, signal?: AbortSignal, filters: ProductFilters = {}): Promise<ProductsResponse> {
  const params = new URLSearchParams({ limit: '20', offset: String(offset), include_total: 'true' })
  if (filters.q) params.set('q', filters.q)
  if (filters.category) params.set('category', filters.category)
  if (filters.available !== undefined) params.set('available', String(filters.available))
  const page = await adminRequest<ProductsResponse>(`/products?${params}`, { signal })
  if (!page || !Array.isArray(page.items) || typeof page.has_more !== 'boolean'
    || page.limit !== 20 || page.offset !== offset || page.count !== page.items.length) {
    throw new ApiError('Réponse catalogue invalide.', 0)
  }
  validatePagination(page)
  page.items.forEach(validateProduct)
  return page
}

export async function getAdminProduct(sku: string, signal?: AbortSignal): Promise<Product> {
  const product = validateProduct(await adminRequest<Product>(`/products/${encodeURIComponent(sku)}`, { signal }))
  if (product.sku !== sku) throw new ApiError('Réponse produit incohérente.', 0)
  return product
}

export async function createAdminProduct(values: NewProduct, signal?: AbortSignal): Promise<Product> {
  if (!csrfToken) throw new ApiError('Session administrateur absente.', 401)
  const product = validateProduct(await adminRequest<Product>('/products', {
    method: 'POST', body: { sku: values.sku, name: values.name, category: values.category, price: values.price },
    csrf: csrfToken, signal,
  }))
  if (product.sku !== values.sku) throw new ApiError('Réponse produit incohérente.', 0)
  return product
}

export async function updateAdminProduct(sku: string, values: ProductMetadata, signal?: AbortSignal): Promise<Product> {
  if (!csrfToken) throw new ApiError('Session administrateur absente.', 401)
  const product = validateProduct(await adminRequest<Product>(`/products/${encodeURIComponent(sku)}`, {
    method: 'PATCH', body: { name: values.name, category: values.category, price: values.price },
    csrf: csrfToken, signal,
  }))
  if (product.sku !== sku) throw new ApiError('Réponse produit incohérente.', 0)
  return product
}

const operationUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

// Reference/demo history uses deterministic UUID v5. New adjustments and
// reconciliation keep the UUID v4 contract; only history accepts both versions.
const historicalOperationUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[45][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

function validateStockMovement(
  movement: StockMovement,
  expected?: { sku: string; operationId: string; delta?: number; reason?: string },
  history = false,
): StockMovement {
  if (!movement || typeof movement.movement_id !== 'string' || !movement.movement_id
    || typeof movement.operation_id !== 'string' || !(history ? historicalOperationUuid : operationUuid).test(movement.operation_id)
    || typeof movement.product_sku !== 'string' || !movement.product_sku.trim()
    || !Number.isSafeInteger(movement.delta) || movement.delta === 0 || Math.abs(movement.delta) > 1_000_000
    || !Number.isSafeInteger(movement.stock_before) || movement.stock_before < 0
    || !Number.isSafeInteger(movement.stock_after) || movement.stock_after < 0
    || movement.stock_after !== movement.stock_before + movement.delta
    || typeof movement.reason !== 'string' || !movement.reason.trim()
    || movement.reason !== movement.reason.trim() || movement.reason.length > 500
    || !Number.isFinite(Date.parse(movement.occurred_at))
    || movement.source !== 'BACKOFFICE'
    || (expected && (movement.product_sku !== expected.sku
      || movement.operation_id !== expected.operationId
      || (expected.delta !== undefined && movement.delta !== expected.delta)
      || (expected.reason !== undefined && movement.reason !== expected.reason)))) {
    throw new ApiError('Réponse de mouvement invalide. Vérifiez le stock avant toute action.', 0)
  }
  return movement
}

export async function adjustAdminProductStock(
  sku: string,
  operationId: string,
  delta: number,
  reason: string,
  signal?: AbortSignal,
): Promise<StockMovement> {
  if (!csrfToken) throw new ApiError('Session administrateur absente.', 401)
  if (!Number.isSafeInteger(delta) || delta === 0 || Math.abs(delta) > 1_000_000
    || !operationUuid.test(operationId) || !reason.trim() || reason.trim().length > 500) {
    throw new ApiError('Ajustement de stock invalide.', 422)
  }
  const movement = await adminRequest<StockMovement>(
    `/products/${encodeURIComponent(sku)}/stock-adjustments`,
    { method: 'POST', body: { operation_id: operationId, delta, reason: reason.trim() }, csrf: csrfToken, signal },
  )
  return validateStockMovement(movement, { sku, operationId, delta, reason: reason.trim() })
}

export async function getAdminStockAdjustment(
  operationId: string,
  signal?: AbortSignal,
): Promise<StockMovement> {
  const movement = await adminRequest<StockMovement>(
    `/stock-adjustments/${encodeURIComponent(operationId)}`, { signal },
  )
  const valid = validateStockMovement(movement)
  if (valid.operation_id !== operationId) {
    throw new ApiError('Réponse de rapprochement incohérente.', 0)
  }
  return valid
}

export async function listAdminStockMovements(
  sku: string,
  offset: number,
  signal?: AbortSignal,
): Promise<StockMovementsResponse> {
  const page = await adminRequest<StockMovementsResponse>(
    `/products/${encodeURIComponent(sku)}/stock-movements?limit=20&offset=${offset}&include_total=true`, { signal },
  )
  if (!page || !Array.isArray(page.items) || page.items.length > 20 || typeof page.has_more !== 'boolean'
    || page.limit !== 20 || page.offset !== offset || page.count !== page.items.length) {
    throw new ApiError('Réponse de mouvements invalide.', 0)
  }
  validatePagination(page)
  page.items.forEach(movement => validateStockMovement(movement, { sku, operationId: movement.operation_id }, true))
  return page
}

export interface CsvPreview {
  fingerprint: string; analysed: number; valid: number; invalid: number; existing_conflicts: number
  issues: { line: number; code: string; message: string }[]
  products: { sku: string; name: string; category: ProductCategory; price: number; stock: number }[]
  product_lines: number[]
  binding: string | null; delimiter: string | null; issues_truncated: boolean
}
export interface CsvImportResult { operation_id: string; fingerprint: string; created: number; skus: string[] }

function validateImportResult(value: CsvImportResult, operationId: string): CsvImportResult {
  if (!value || !exactKeys(value, ['operation_id', 'fingerprint', 'created', 'skus'])
    || value.operation_id !== operationId || !/^[0-9a-f]{64}$/.test(value.fingerprint)
    || !Number.isSafeInteger(value.created) || value.created < 1 || value.created > 500
    || !Array.isArray(value.skus) || value.skus.length !== value.created
    || value.skus.some(sku => typeof sku !== 'string' || !sku.trim())
    || new Set(value.skus).size !== value.created) throw new ApiError('Résultat d’import invalide.', 0)
  return value
}

export async function previewAdminProductCsv(file: Blob, signal?: AbortSignal): Promise<CsvPreview> {
  if (!csrfToken) throw new ApiError('Session administrateur absente.', 401)
  const value = await adminRequest<CsvPreview>('/products/import/preview', {
    method: 'POST', raw: file, csrf: csrfToken, signal,
  })
  if (!value || !/^[0-9a-f]{64}$/.test(value.fingerprint)
    || ![value.analysed, value.valid, value.invalid, value.existing_conflicts].every(nonnegativeInteger)
    || value.analysed > 501 || value.valid > 500 || value.valid > value.analysed
    || !Array.isArray(value.issues) || value.issues.length > 100
    || value.issues.some(issue => !issue || !nonnegativeInteger(issue.line)
      || typeof issue.code !== 'string' || typeof issue.message !== 'string')
    || !Array.isArray(value.products) || value.products.length !== value.valid
    || !Array.isArray(value.product_lines) || value.product_lines.length !== value.valid
    || !value.product_lines.every(line => Number.isSafeInteger(line) && line > 1)
    || value.products.some(p => !p || typeof p.sku !== 'string' || !p.sku.trim()
      || typeof p.name !== 'string' || !p.name.trim() || !Object.hasOwn(productCategoryLabels, p.category)
      || !Number.isFinite(p.price) || p.price < 0 || p.stock !== 0)
    || !(value.binding === null || typeof value.binding === 'string')
    || !(value.delimiter === null || [',', ';'].includes(value.delimiter))
    || typeof value.issues_truncated !== 'boolean'
    || (value.binding !== null && (value.invalid > 0 || value.existing_conflicts > 0))) {
    throw new ApiError('Aperçu CSV invalide.', 0)
  }
  return value
}

export async function executeAdminProductCsv(
  file: Blob, operationId: string, binding: string, signal?: AbortSignal,
): Promise<CsvImportResult> {
  if (!csrfToken) throw new ApiError('Session administrateur absente.', 401)
  return validateImportResult(await adminRequest<CsvImportResult>('/products/import/execute', {
    method: 'POST', raw: file, csrf: csrfToken, signal,
    headers: { 'X-Import-Operation-Id': operationId, 'X-Import-Preview': binding, 'X-Import-Confirm': 'CREATE_ONLY' },
  }), operationId)
}

export async function getAdminProductCsvBatch(operationId: string, signal?: AbortSignal): Promise<CsvImportResult> {
  return validateImportResult(await adminRequest<CsvImportResult>(
    `/products/import/batches/${encodeURIComponent(operationId)}`, { signal },
  ), operationId)
}
