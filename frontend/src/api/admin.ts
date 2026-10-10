import { ApiError } from './http'
import type { CustomerOrdersResponse, OrderResponse } from '../types/api'

export interface AdminSession { username: string; csrf_token: string }
export interface AdminOrder extends OrderResponse { customer_id: string | null }
export interface AdminOrders extends Omit<CustomerOrdersResponse, 'items'> { items: AdminOrder[] }
let csrfToken = ''

async function adminRequest<T>(
  path: string,
  options: { method?: 'POST'; body?: unknown; csrf?: string; signal?: AbortSignal } = {},
): Promise<T> {
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
  if (!response.ok) {
    if (response.status === 401) csrfToken = ''
    const messages: Record<number, string> = {
      401: 'Identifiants invalides ou session administrateur expirée.',
      403: 'Accès refusé : origine ou protection CSRF invalide.',
      404: 'Commande introuvable.',
      422: 'Informations de connexion invalides.',
      429: 'Trop de tentatives. Attendez une minute.',
      503: 'Back-office indisponible. Vérifiez sa configuration serveur.',
    }
    throw new ApiError(messages[response.status] || 'Erreur du back-office.', response.status)
  }
  if (response.status === 204) return undefined as T
  try { return await response.json() as T }
  catch { throw new ApiError('Réponse du back-office invalide.', response.status) }
}

export async function adminMe(): Promise<AdminSession> {
  const session = await adminRequest<AdminSession>('/session/me')
  csrfToken = session.csrf_token
  return session
}
export async function adminLogin(username: string, password: string): Promise<void> {
  const session = await adminRequest<AdminSession>('/session/login', {
    method: 'POST', body: { username, password }, csrf: 'login',
  })
  csrfToken = session.csrf_token
}
export async function adminLogout(): Promise<void> {
  if (!csrfToken) await adminMe()
  await adminRequest<void>('/session/logout', { method: 'POST', csrf: csrfToken })
  csrfToken = ''
}
export function listAdminOrders(offset: number, signal?: AbortSignal): Promise<AdminOrders> {
  return adminRequest(`/orders?limit=20&offset=${offset}`, { signal })
}
export function getAdminOrder(orderId: string, signal?: AbortSignal): Promise<AdminOrder> {
  return adminRequest(`/orders/${encodeURIComponent(orderId)}`, { signal })
}
