import type { AssistantRequest, AssistantResponse, CustomerOrdersResponse, OrderResponse } from '../types/api'
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

function isOrder(value: unknown): value is OrderResponse {
  return typeof value === 'object' && value !== null &&
    'order_id' in value && typeof value.order_id === 'string' &&
    'product_name' in value && typeof value.product_name === 'string' &&
    'quantity' in value && typeof value.quantity === 'number' && Number.isInteger(value.quantity) &&
    'created_at' in value && typeof value.created_at === 'string' &&
    'status' in value && typeof value.status === 'string' &&
    ['CREATED', 'PROCESSING', 'SHIPPED', 'DELIVERED', 'CANCELLED'].includes(value.status)
}
