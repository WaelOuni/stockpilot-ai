import { customerToken } from '../utils/customerSession'

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly requestId: string | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export async function requestJson<T>(
  path: string,
  options: { method?: 'GET' | 'POST'; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const token = customerToken.value
  if (!token) throw new ApiError('Ajoutez un JWT client pour vous connecter.', 401)
  const headers = new Headers({ Accept: 'application/json', Authorization: `Bearer ${token}` })
  if (options.body !== undefined) headers.set('Content-Type', 'application/json')
  const baseUrl = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/+$/, '')
  let response: Response
  try {
    response = await fetch(`${baseUrl}${path}`, {
      method: options.method || 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
      credentials: 'omit',
      cache: 'no-store',
      redirect: 'error',
    })
  } catch (error) {
    if (options.signal?.aborted) throw error
    throw new ApiError('Connexion impossible. Verifiez que le backend est disponible.', 0)
  }
  const requestId = response.headers.get('x-request-id')
  if (!response.ok) {
    // Do not surface arbitrary error bodies that might contain submitted credentials.
    const messages: Record<number, string> = {
      401: 'JWT absent, invalide ou expire. Remplacez le token client.',
      403: 'Acces refuse.',
      422: 'La requete est invalide.',
      503: 'Service indisponible. Verifiez la configuration du backend.',
    }
    throw new ApiError(messages[response.status] || 'Le serveur ne peut pas traiter la requete.', response.status, requestId)
  }
  try {
    return await response.json() as T
  } catch {
    throw new ApiError('Le serveur a renvoye une reponse JSON invalide.', response.status, requestId)
  }
}
