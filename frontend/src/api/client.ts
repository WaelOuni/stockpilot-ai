import type { AssistantRequest, AssistantResponse, CustomerOrdersResponse, OrderResponse, OrderEvent, ProductResponse, ProductsResponse, ProductCategory } from '../types/api'
import { ApiError, requestJson } from './http'

export async function sendAssistantMessage(body: AssistantRequest, signal?: AbortSignal): Promise<AssistantResponse & { response: string }> {
  const result = await requestJson<AssistantResponse>('/assistant', { method: 'POST', body, signal })
  if (typeof result?.response !== 'string') {
    throw new ApiError('La reponse assistant est incomplete.', 200)
  }
  return { ...result, response: result.response }
}

export async function listCustomerOrders(limit = 20, offset = 0, signal?: AbortSignal): Promise<CustomerOrdersResponse> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  const result = await requestJson<CustomerOrdersResponse>(`/orders?${params}`, { signal })
  if (!result || !Array.isArray(result.items) ||
      !result.items.every(isOrder) ||
      !Number.isInteger(result.count) || result.count !== result.items.length ||
      !Number.isInteger(result.limit) || result.limit !== limit ||
      !Number.isInteger(result.offset) || result.offset !== offset) {
    throw new ApiError('La reponse des commandes est incomplete.', 200)
  }
  return result
}

function isOrderEvent(value: unknown): value is OrderEvent {
  return typeof value === 'object' && value !== null &&
    'status' in value && typeof value.status === 'string' &&
    ['CREATED', 'PROCESSING', 'SHIPPED', 'DELIVERED', 'CANCELLED'].includes(value.status) &&
    'occurred_at' in value && typeof value.occurred_at === 'string' && Number.isFinite(Date.parse(value.occurred_at)) &&
    'source' in value && typeof value.source === 'string' && ['SYSTEM', 'BACKOFFICE', 'CUSTOMER'].includes(value.source)
}

function isOrder(value: unknown): value is OrderResponse {
  return typeof value === 'object' && value !== null &&
    'order_id' in value && typeof value.order_id === 'string' &&
    'product_name' in value && typeof value.product_name === 'string' &&
    'quantity' in value && typeof value.quantity === 'number' && Number.isInteger(value.quantity) &&
    'created_at' in value && typeof value.created_at === 'string' &&
    'status' in value && typeof value.status === 'string' &&
    ['CREATED', 'PROCESSING', 'SHIPPED', 'DELIVERED', 'CANCELLED'].includes(value.status) &&
    (!('events' in value) || (Array.isArray(value.events) && value.events.every(isOrderEvent)))
}

export async function getCustomerOrder(orderId: string, signal?: AbortSignal): Promise<OrderResponse> {
  const result = await requestJson<unknown>(`/orders/${encodeURIComponent(orderId)}`, { signal })
  if (!isOrder(result)) throw new ApiError('Le detail de commande est incomplet.', 200)
  return result
}

export async function cancelOrder(orderId: string, signal?: AbortSignal): Promise<OrderResponse> {
  const result = await requestJson<unknown>(`/customer/orders/${encodeURIComponent(orderId)}/cancel`, { method: 'POST', signal })
  if (!isOrder(result) || result.order_id !== orderId || result.status !== 'CANCELLED') {
    throw new ApiError("La reponse d'annulation est incomplete. Actualisez la commande.", 200)
  }
  return result
}

function isProduct(value: unknown): value is ProductResponse {
  return typeof value === 'object' && value !== null &&
    'sku' in value && typeof value.sku === 'string' &&
    'product_name' in value && typeof value.product_name === 'string' &&
    'category' in value && typeof value.category === 'string' &&
    ['DRILL', 'SAW', 'SANDER', 'SCREWDRIVER', 'OTHER'].includes(value.category) &&
    'price' in value && typeof value.price === 'number' && Number.isFinite(value.price) &&
    'stock' in value && typeof value.stock === 'number' && Number.isInteger(value.stock)
}

export interface ProductFilters {
  q?: string
  category?: ProductCategory
  available?: boolean
}

export async function listProducts(limit = 20, offset = 0, signal?: AbortSignal, filters: ProductFilters = {}): Promise<ProductsResponse> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (filters.q) params.set('q', filters.q)
  if (filters.category) params.set('category', filters.category)
  if (filters.available !== undefined) params.set('available', String(filters.available))
  const result = await requestJson<ProductsResponse>(`/products?${params}`, { public: true, signal })
  if (!result || !Array.isArray(result.items) || !result.items.every(isProduct) ||
      result.count !== result.items.length || result.limit !== limit || result.offset !== offset ||
      typeof result.has_more !== 'boolean') {
    throw new ApiError('Le catalogue est incomplet.', 200)
  }
  return result
}

export async function getProduct(sku: string, signal?: AbortSignal): Promise<ProductResponse> {
  const result = await requestJson<unknown>(`/products/${encodeURIComponent(sku)}`, { public: true, signal })
  if (!isProduct(result)) throw new ApiError('La fiche produit est incomplete.', 200)
  return result
}
