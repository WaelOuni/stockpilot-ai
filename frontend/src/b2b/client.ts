import type { Decoder } from './contracts'
import { ContractError, object, text } from './contracts'

export class BffError extends Error {
  constructor(public readonly status: number, public readonly code: string, public readonly uncertain = false) {
    super(messages[status] ?? 'Le service ne répond pas. Réessayez une lecture.')
  }
}
const messages: Record<number, string> = {
  0: 'Connexion interrompue. Une mutation peut avoir été enregistrée : vérifiez son résultat.',
  401: 'Votre session a expiré. Reconnectez-vous.',
  403: 'Accès refusé. Vos droits ou votre organisation ont pu changer.',
  404: 'Ressource introuvable ou inaccessible.',
  409: 'Conflit métier. Vérifiez le résultat avant toute nouvelle opération.',
  413: 'Fichier trop volumineux (256 Kio maximum).',
  422: 'Les informations envoyées sont invalides.',
  429: 'Le service est occupé. Attendez avant de relancer une lecture.',
  503: 'Service indisponible. Le résultat de la mutation doit être vérifié.',
}
export class StaleResponse extends Error {}
type Hooks = { generation: () => number; invalid: (status: number) => void; csrf: () => string | undefined }
export class BffClient {
  private controllers = new Set<AbortController>()
  constructor(private hooks: Hooks, private fetcher: typeof fetch = (...args) => fetch(...args), private timeout = 15000) {}
  abort() { for (const c of this.controllers) c.abort(); this.controllers.clear() }
  async request<T>(path: string, decode: Decoder<T>, options: { body?: unknown; csv?: string; login?: boolean; csrf?: string; method?: 'POST'; signal?: AbortSignal } = {}): Promise<T> {
    // The historical API and absolute/protocol-relative URLs cannot enter this transport.
    if (!/^\/b2b\/(auth|api)\/[a-z]/.test(path) || /[\r\n#\\]/.test(path)) throw new ContractError()
    const normalized = new URL(path, location.origin)
    if (normalized.origin !== location.origin || normalized.pathname !== path.split('?')[0]) throw new ContractError()
    const controller = new AbortController()
    const generation = this.hooks.generation()
    this.controllers.add(controller)
    const abort = () => controller.abort()
    options.signal?.addEventListener('abort', abort, { once: true })
    if (options.signal?.aborted) abort()
    const timer = setTimeout(abort, this.timeout)
    const mutation = options.method === 'POST'
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (!mutation && path.startsWith('/b2b/api/')) {
      const csrf = this.hooks.csrf()
      if (!csrf) { clearTimeout(timer); this.controllers.delete(controller); throw new BffError(401, 'SESSION_REQUIRED') }
      headers['X-BFF-CSRF'] = csrf
    }
    if (mutation) {
      const csrf = options.login ? 'login' : options.csrf ?? this.hooks.csrf()
      if (!csrf) { clearTimeout(timer); this.controllers.delete(controller); options.signal?.removeEventListener('abort', abort); throw new BffError(401, 'SESSION_REQUIRED') }
      headers['X-BFF-CSRF'] = csrf
      headers['Content-Type'] = options.csv !== undefined ? 'text/csv; charset=utf-8' : 'application/json'
    }
    try {
      const response = await this.fetcher(path, {
        method: options.method ?? 'GET', credentials: 'include', mode: 'same-origin', redirect: 'error',
        cache: 'no-store', referrerPolicy: 'no-referrer', headers, signal: controller.signal,
        body: options.csv ?? (options.body === undefined ? undefined : JSON.stringify(options.body)),
      })
      if (generation !== this.hooks.generation()) throw new StaleResponse()
      if (response.status === 401 || response.status === 403) {
        this.hooks.invalid(response.status)
        throw new BffError(response.status, 'SESSION_OR_ACCESS_REFUSED')
      }
      // Responses are bounded by the BFF. No raw server error is displayed or logged.
      const data: unknown = await response.json()
      if (generation !== this.hooks.generation()) throw new StaleResponse()
      if (!response.ok) {
        if (response.status === 409) {
          // B3 rejected is a durable typed result, unlike a generic 409 conflict.
          try { return decode(data) } catch { /* normalize the conflict below */ }
        }
        let code = 'HTTP_ERROR'
        if (typeof data === 'object' && data !== null && 'code' in data && typeof data.code === 'string') code = data.code
        throw new BffError(response.status, code, mutation && (response.status >= 500 || response.status === 202))
      }
      return decode(data)
    } catch (error) {
      if (generation !== this.hooks.generation()) throw new StaleResponse()
      if (error instanceof BffError || error instanceof ContractError || error instanceof StaleResponse) throw error
      throw new BffError(0, 'NETWORK', mutation)
    } finally {
      clearTimeout(timer); this.controllers.delete(controller)
      options.signal?.removeEventListener('abort', abort)
    }
  }
}
export function providerNavigation(value: string): string {
  const url = new URL(value)
  const loopback = ['127.0.0.1', 'localhost', '[::1]']
  if (url.username || url.password || url.hash ||
      (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback.includes(url.hostname) && loopback.includes(location.hostname)))) {
    throw new ContractError()
  }
  for (const forbidden of ['client_secret', 'access_token', 'id_token', 'refresh_token', 'code_verifier']) {
    if (url.searchParams.has(forbidden)) throw new ContractError()
  }
  return url.href
}
export const loginDecoder = object({ authorization_url: text })
export const logoutDecoder = object({ provider_logout_url: text })
