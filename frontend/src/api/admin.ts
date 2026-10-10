import { ApiError } from './http'
import type { CustomerOrdersResponse, OrderResponse, OrderStatus } from '../types/api'
import type { ProductResponse as Product, ProductCategory, ProductsResponse } from '../types/api'
import { productCategoryLabels } from '../utils/products'

export interface AdminSession { username: string; csrf_token: string }
export interface AdminOrder extends OrderResponse { customer_id: string | null; events: NonNullable<OrderResponse['events']> }
export interface AdminOrders extends Omit<CustomerOrdersResponse, 'items'> { items: AdminOrder[] }
let csrfToken = ''
let sessionGeneration = 0

async function adminRequest<T>(
  path: string,
  options: { method?: 'POST' | 'PATCH'; body?: unknown; csrf?: string; signal?: AbortSignal } = {},
): Promise<T> {
  const generation = sessionGeneration
  const headers = new Headers({ Accept: 'application/json' })
  if (options.body !== undefined) headers.set('Content-Type', 'application/json')
  if (options.csrf) headers.set('X-Admin-CSRF', options.csrf)
  let response: Response
  try {
    // Fixed same-origin proxy: admin cookies must never follow a VITE URL.
    response = await fetch(`/api/backoffice${path}`, {
      method: options.method || 'GET', headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
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
    throw new ApiError((path.startsWith('/products') && productMessages[response.status])
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
export function listAdminOrders(offset: number, signal?: AbortSignal): Promise<AdminOrders> {
  return adminRequest(`/orders?limit=20&offset=${offset}`, { signal })
}
export function getAdminOrder(orderId: string, signal?: AbortSignal): Promise<AdminOrder> {
  return adminRequest<AdminOrder>(`/orders/${encodeURIComponent(orderId)}`, { signal }).then(validateOrder)
}

function validateOrder(order: AdminOrder): AdminOrder {
  const statuses = ['CREATED', 'PROCESSING', 'SHIPPED', 'DELIVERED', 'CANCELLED']
  if (!order || typeof order.order_id !== 'string' || !statuses.includes(order.status)
    || !(order.customer_id === null || typeof order.customer_id === 'string')
    || typeof order.product_name !== 'string' || !Number.isInteger(order.quantity)
    || !Number.isFinite(Date.parse(order.created_at)) || !Array.isArray(order.events)
    || order.events.some(event => !event || !statuses.includes(event.status)
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
    || !Number.isInteger(product.stock) || product.stock < 0) {
    throw new ApiError('Réponse produit invalide. Actualisez avant toute opération.', 0)
  }
  return product
}

export async function listAdminProducts(offset: number, signal?: AbortSignal): Promise<ProductsResponse> {
  const page = await adminRequest<ProductsResponse>(`/products?limit=20&offset=${offset}`, { signal })
  if (!page || !Array.isArray(page.items) || typeof page.has_more !== 'boolean'
    || page.limit !== 20 || page.offset !== offset || page.count !== page.items.length) {
    throw new ApiError('Réponse catalogue invalide.', 0)
  }
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
