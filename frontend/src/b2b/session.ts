import { computed, inject, ref, type InjectionKey } from 'vue'
import { BffClient, BffError, loginDecoder, logoutDecoder, providerNavigation, StaleResponse } from './client'
import { sessionDecoder, type Session } from './contracts'

export type OperationRef = {
  user: string; organization: string; family: 'orders' | 'stock'
  namespace: 'prepare' | 'confirm' | 'cancel' | 'adjust' | 'import'; id: string
}
const journalKey = 'stockpilot.b2b.pending.v1'
// References only: no tokens, payloads, prices, CSV, buyer or order documents persisted.
function readJournal(): OperationRef[] {
  try {
    const raw: unknown = JSON.parse(sessionStorage.getItem(journalKey) ?? '[]')
    if (!Array.isArray(raw) || raw.length > 20) return []
    return raw.filter((v): v is OperationRef => {
      if (typeof v !== 'object' || v === null) return false
      return ['user', 'organization', 'id'].every(k => typeof v[k] === 'string' && v[k].length > 0 && v[k].length <= 128)
        && ((v.family === 'orders' && ['prepare', 'confirm', 'cancel'].includes(v.namespace))
          || (v.family === 'stock' && ['adjust', 'import'].includes(v.namespace)))
    }).map(v => ({ user: v.user, organization: v.organization, family: v.family, namespace: v.namespace, id: v.id }))
  } catch { return [] }
}
export function createB2BSession(fetcher?: typeof fetch, timeout?: number) {
  const current = ref<Session | null>(null)
  const phase = ref<'loading' | 'guest' | 'active' | 'unavailable'>('loading')
  const notice = ref('')
  const now = ref(Date.now())
  const pending = ref<OperationRef[]>([])
  const businessRevision = ref(0)
  const ssoLogout = ref<string | null>(null)
  let generation = 0
  let channel: BroadcastChannel | undefined
  let timer: ReturnType<typeof setInterval> | undefined
  function persist() {
    try {
      if (pending.value.length) sessionStorage.setItem(journalKey, JSON.stringify(pending.value))
      else sessionStorage.removeItem(journalKey)
    } catch {
      notice.value = 'Références conservées en mémoire uniquement. Ne rechargez pas pendant une vérification.'
    }
  }
  function invalidate(status = 401) {
    generation++; client.abort(); current.value = null; phase.value = 'guest'
    notice.value = status === 403 ? 'Accès refusé. Vérifiez votre appartenance et vos droits auprès de votre administrateur.' : 'Votre session a expiré. Reconnectez-vous ; aucune mutation ne sera relancée automatiquement.'
    // Pending references remain quarantined. Business views unmount immediately.
  }
  const client = new BffClient({ generation: () => generation, csrf: () => current.value?.csrf_token, invalid: invalidate }, fetcher, timeout)
  const has = (permission: string) => current.value?.permissions.includes(permission) === true
  async function restore() {
    const version = ++generation; client.abort(); current.value = null; phase.value = 'loading'
    ssoLogout.value = null
    try {
      const session = await client.request('/b2b/auth/session', sessionDecoder)
      if (version !== generation) return
      if (Date.parse(session.expires_at) <= Date.now() || Date.parse(session.idle_expires_at) <= Date.now()) { invalidate(); return }
      current.value = session; phase.value = 'active'; notice.value = ''
      pending.value = readJournal().filter(p => p.user === session.user_id && p.organization === session.organization_id)
      persist()
    } catch (error) {
      if (error instanceof StaleResponse || version !== generation) return
      if (!(error instanceof BffError && [401, 403].includes(error.status))) {
        phase.value = 'unavailable'; notice.value = 'Session impossible à vérifier. Aucune donnée métier ne sera chargée.'
      }
    }
  }
  async function login(organization: string) {
    notice.value = ''
    const response = await client.request('/b2b/auth/login', loginDecoder, {
      method: 'POST', login: true, body: { organization_id: organization.trim() },
    })
    return providerNavigation(response.authorization_url)
  }
  async function logout() {
    const csrf = current.value?.csrf_token
    // Capture protection, then purge and abort before waiting for the network.
    channel?.postMessage('logout')
    const version = ++generation; client.abort(); current.value = null; phase.value = 'guest'
    pending.value = []; persist(); ssoLogout.value = null
    notice.value = 'Déconnexion locale en cours…'
    try {
      if (!csrf) throw new BffError(401, 'SESSION_REQUIRED')
      const response = await client.request('/b2b/auth/logout', logoutDecoder, { method: 'POST', csrf })
      if (version !== generation) return
      ssoLogout.value = providerNavigation(response.provider_logout_url)
      notice.value = 'Session StockPilot fermée. La session SSO Keycloak peut rester ouverte.'
    } catch {
      if (version !== generation) return
      notice.value = 'Données locales effacées. La révocation serveur n’a pas pu être confirmée ; fermez le navigateur ou vérifiez votre session avant de reprendre.'
    }
  }
  function remember(family: OperationRef['family'], namespace: OperationRef['namespace']): OperationRef {
    const s = current.value
    if (!s) throw new BffError(401, 'SESSION_REQUIRED')
    if (pending.value.length >= 20) throw new Error('Vérifiez les opérations en attente avant de continuer.')
    const operation = { user: s.user_id, organization: s.organization_id, family, namespace, id: crypto.randomUUID() }
    pending.value.push(operation); persist()
    return operation
  }
  function resolve(operation: OperationRef) {
    pending.value = pending.value.filter(p => !(p.id === operation.id && p.namespace === operation.namespace))
    persist()
  }
  function tick() {
    now.value = Date.now()
    const s = current.value
    // Idle expiry is a conservative snapshot. Explicit revalidation extends it safely.
    if (s && Math.min(Date.parse(s.expires_at), Date.parse(s.idle_expires_at)) <= now.value) invalidate()
  }
  function start() {
    if (typeof BroadcastChannel !== 'undefined') {
      channel = new BroadcastChannel('stockpilot.b2b.session')
      channel.onmessage = event => {
        if (event.data !== 'logout') return
        invalidate(); pending.value = []; persist()
        notice.value = 'La session a été fermée dans un autre onglet.'
      }
    }
    timer = setInterval(tick, 1000); return restore()
  }
  function dispose() { channel?.close(); clearInterval(timer); generation++; client.abort(); current.value = null }
  const expiresSoon = computed(() => current.value && Math.min(Date.parse(current.value.expires_at), Date.parse(current.value.idle_expires_at)) - now.value < 120000)
  return { businessRevision, current, phase, notice, ssoLogout, pending, client, has, restore, login, logout, remember, resolve, start, dispose, expiresSoon, tick }
}
export type B2BSession = ReturnType<typeof createB2BSession>
export const sessionKey: InjectionKey<B2BSession> = Symbol('isolated B2B session')
export function useB2B() {
  const session = inject(sessionKey)
  if (!session) throw new Error('B2B session provider required.')
  return session
}
