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
export interface ProductRecommendation extends ProductResponse {
  explanation: string
}

export interface AssistantResponse {
  recommendations?: readonly ProductRecommendation[]
  advice_status?: 'RESULTS' | 'NO_RESULTS' | 'CLARIFICATION_REQUIRED' | 'CATALOGUE_UNAVAILABLE' | null
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

export interface OrderEvent {
  status: OrderStatus
  occurred_at: string
  source: 'SYSTEM' | 'BACKOFFICE' | 'CUSTOMER'
}

export interface OrderResponse {
  order_id: string
  product_name: string
  quantity: number
  status: OrderStatus
  created_at: string
  events?: OrderEvent[]
}

export interface CustomerOrdersResponse {
  has_more?: boolean
  items: OrderResponse[]
  limit: number
  offset: number
  count: number
  total?: number
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
  total?: number
  has_more: boolean
}

export interface StockMovement {
  movement_id: string
  operation_id: string
  product_sku: string
  delta: number
  stock_before: number
  stock_after: number
  reason: string
  occurred_at: string
  source: 'BACKOFFICE'
}

export interface StockMovementsResponse {
  items: StockMovement[]
  limit: number
  offset: number
  count: number
  total?: number
  has_more: boolean
}

export interface BackofficeDashboard {
  order_stats: {
    total_orders: number
    by_status: Record<OrderStatus, number>
  }
  product_stats: {
    total_products: number
    available_products: number
    unavailable_products: number
    total_stock_units: number
  }
  recent_orders: Array<Pick<OrderResponse, 'order_id' | 'product_name' | 'quantity' | 'status' | 'created_at'>>
  recent_stock_movements: Array<Pick<StockMovement, 'product_sku' | 'delta' | 'occurred_at' | 'reason'>>
}
