// Matches schemas/chat.py, schemas/order.py and application/workflows/state.py.
export type OrderStatus = 'CREATED' | 'PROCESSING' | 'SHIPPED' | 'DELIVERED' | 'CANCELLED'
export type PreparedOrderStatus =
  | 'PENDING_CONFIRMATION' | 'CONFIRMED' | 'CANCELLED'
  | 'RESERVATION_FAILED' | 'ORDER_CREATION_FAILED' | 'COMPENSATION_FAILED'
  | 'ORDER_OUTCOME_UNKNOWN' | 'PRODUCT_NOT_FOUND' | 'OUT_OF_STOCK' | 'INSUFFICIENT_STOCK'
export type Intent = 'PRODUCT_ADVICE' | 'STOCK_CHECK' | 'ORDER' | 'ORDER_STATUS' | 'UNKNOWN'
export type ProductCategory = 'DRILL' | 'SAW' | 'SANDER' | 'SCREWDRIVER' | 'OTHER'

export interface AssistantRequest {
  message: string
  thread_id: string
}

// /assistant returns a partial workflow state, not a dedicated response schema.
export interface AssistantResponse {
  response?: string
  message?: string
  customer_id?: string | null
  intent?: Intent
  raw_product?: string | null
  product_name?: string | null
  category?: ProductCategory | null
  budget?: number | null
  usage?: string | null
  quantity?: number | null
  pending_order?: Record<string, string | number | null> | null
  order_status?: string
  confirmation_handled?: boolean
  created_order_id?: string | null
  last_created_order_id?: string
  order_id?: string | null
  queried_order_id?: string | null
  queried_order_status?: string | null
  order_status_result?: string
  last_relevant_intent?: Intent | null
  last_product_name?: string | null
  last_quantity?: number | null
  last_category?: ProductCategory | null
  last_budget?: number | null
  last_usage?: string | null
  last_advice_query?: string | null
  context_customer_id?: string | null
  advice_query?: string | null
}

export interface OrderResponse {
  order_id: string
  product_name: string
  quantity: number
  status: OrderStatus
  created_at: string
}

export interface CustomerOrdersResponse {
  items: OrderResponse[]
  limit: number
  offset: number
  count: number
}

export interface ProductResponse {
  sku: string
  product_name: string
  category: ProductCategory
  price: number
  stock: number
}

export interface ProductsResponse {
  items: ProductResponse[]
  limit: number
  offset: number
  count: number
}
