import { computed, inject, ref, type InjectionKey } from 'vue'
import { createContinuity } from './continuity'
import { BffClient, BffError, loginDecoder, logoutDecoder, providerNavigation, StaleResponse } from './client'
import { sessionDecoder, type Session } from './contracts'

export type OperationRef = {
  user: string; organization: string; family: 'orders' | 'stock'
  namespace: 'prepare' | 'confirm' | 'cancel' | 'adjust' | 'import' | 'archive'; id: string
}
export const sessionWarningMs = 60000
const sessionCheckIntervalMs = 60000
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
          || (v.family === 'stock' && ['adjust', 'import', 'archive'].includes(v.namespace)))
    }).map(v => ({ user: v.user, organization: v.organization, family: v.family, namespace: v.namespace, id: v.id }))
  } catch { return [] }
}
export function createB2BSession(fetcher?: typeof fetch, timeout?: number) {
  const continuity = createContinuity()
  const current = ref<Session | null>(null)
  let idleCandidate: Session | null = null
  let waking = false
  let announcedIdentity: { user: string; organization: string } | null = null
  const phase = ref<'loading' | 'guest' | 'active' | 'unavailable'>('loading')
  const notice = ref('')
  const now = ref(Date.now())
  const pending = ref<OperationRef[]>([])
  const businessRevision = ref(0)
  const ssoLogout = ref<string | null>(null)
  let sessionCheckAt = 0
  let checkingDates = false
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
    const wasAuthenticated = current.value !== null || idleCandidate !== null
    idleCandidate = null; continuity.suspend()
    generation++; client.abort(); current.value = null; phase.value = 'guest'
    notice.value = status === 403 ? 'Accès refusé. Vérifiez votre appartenance et vos droits auprès de votre administrateur.' : wasAuthenticated ? 'Votre session a expiré. Reconnectez-vous ; aucune mutation ne sera relancée automatiquement.' : ''
    // Pending references remain quarantined. Business views unmount immediately.
  }
  const client = new BffClient({ generation: () => generation, csrf: () => current.value?.csrf_token, invalid: invalidate, activity: () => { void refreshDates() } }, fetcher, timeout)
  const has = (permission: string) => current.value?.permissions.includes(permission) === true
  async function restore() {
    idleCandidate = current.value ?? idleCandidate
    announcedIdentity = null
    const version = ++generation; client.abort(); current.value = null; phase.value = 'loading'
    continuity.suspend()
    ssoLogout.value = null
    try {
      const session = await client.request('/b2b/auth/session', sessionDecoder)
      if (version !== generation) return
      // Another tab may finish its first identity check while this one is loading.
      // Accept matching identities, but never activate a response older than a switch.
      const announced = announcedIdentity as { user: string; organization: string } | null
      if (announced && (announced.user !== session.user_id || announced.organization !== session.organization_id)) {
        invalidate(); continuity.purge(); pending.value = []; persist()
        notice.value = 'Le compte a changé dans un autre onglet. Reconnectez-vous avec le compte souhaité.'
        return
      }
      if (Date.parse(session.expires_at) <= Date.now() || Date.parse(session.idle_expires_at) <= Date.now()) { invalidate(); return }
      now.value = Date.now(); sessionCheckAt = now.value
      current.value = session; phase.value = 'active'; notice.value = ''; idleCandidate = null
      continuity.restore(session)
      channel?.postMessage({ type: 'identity', user: session.user_id, organization: session.organization_id })
      pending.value = readJournal().filter(p => p.user === session.user_id && p.organization === session.organization_id)
      persist()
    } catch (error) {
      if (error instanceof StaleResponse || version !== generation) return
      if (!(error instanceof BffError && [401, 403].includes(error.status))) {
        phase.value = 'unavailable'; notice.value = 'Session impossible à vérifier. Aucune donnée métier ne sera chargée.'
      }
    }
  }
  // Revalidate after business activity or an explicit return to the page; no polling.
  // Never infer a renewed idle deadline from the browser clock.
  async function refreshDates(force = false) {
    const s = current.value
    if (!s || checkingDates || (!force && Date.now() - sessionCheckAt < sessionCheckIntervalMs)) return
    const version = generation
    checkingDates = true; sessionCheckAt = Date.now()
    try {
      const fresh = await client.request('/b2b/auth/session', sessionDecoder)
      if (version !== generation || current.value !== s) return
      if (fresh.user_id !== s.user_id || fresh.organization_id !== s.organization_id || fresh.csrf_token !== s.csrf_token) { invalidate(); return }
      now.value = Date.now(); sessionCheckAt = now.value
      if (Math.min(Date.parse(fresh.expires_at), Date.parse(fresh.idle_expires_at)) <= now.value) { invalidate(); return }
      s.expires_at = fresh.expires_at; s.idle_expires_at = fresh.idle_expires_at
    } catch { /* Keep conservative dates. 401/403 already invalidates the session. */ }
    finally { checkingDates = false }
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
    idleCandidate = null; continuity.purge()
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
    if (s && Math.min(Date.parse(s.expires_at), Date.parse(s.idle_expires_at)) <= now.value) {
      invalidate()
      // Another tab may have advanced idle expiry. Suspend all business views;
      // only an explicit return to the page may check the existing cookie.
      if (Date.parse(s.expires_at) > now.value) idleCandidate = s
    }
  }
  async function revalidateOnReturn() {
    if (document.visibilityState === 'hidden' || waking) return
    const candidate = current.value ?? idleCandidate
    if (!candidate) return // Never undo logout, a server refusal or an absolute expiry.
    if (Date.parse(candidate.expires_at) <= Date.now()) { invalidate(); return }
    if (current.value && Date.now() - sessionCheckAt < sessionCheckIntervalMs) return
    waking = true
    try {
      // A still-valid session can keep its mounted view and unsent draft.
      // A stale idle snapshot must remain quarantined until the server replies.
      if (current.value && Date.parse(candidate.idle_expires_at) > Date.now()) await refreshDates(true)
      else await restore()
    } finally { waking = false }
  }
  function start() {
    document.addEventListener('input', revalidateOnReturn)
    document.addEventListener('pointerdown', revalidateOnReturn)
    window.addEventListener('focus', revalidateOnReturn)
    window.addEventListener('online', revalidateOnReturn)
    document.addEventListener('visibilitychange', revalidateOnReturn)
    if (typeof BroadcastChannel !== 'undefined') {
      channel = new BroadcastChannel('stockpilot.b2b.session')
      channel.onmessage = event => {
        const data: unknown = event.data
        if (typeof data === 'object' && data !== null && 'type' in data && data.type === 'identity') {
          if ('user' in data && typeof data.user === 'string' && 'organization' in data && typeof data.organization === 'string') {
            const known = current.value ?? idleCandidate
            continuity.changedIdentity(data.user, data.organization)
            if (known && (known.user_id !== data.user || known.organization_id !== data.organization)) {
              // Abort a pending identity read too: its old response must never
              // repopulate this tab or broadcast the previous account again.
              invalidate(); continuity.purge(); pending.value = []; persist()
              notice.value = 'Le compte a changé dans un autre onglet. Reconnectez-vous avec le compte souhaité.'
            } else if (phase.value === 'loading') {
              announcedIdentity = { user: data.user, organization: data.organization }
            }
          }
          return
        }
        if (event.data !== 'logout') return
        invalidate(); continuity.purge(); pending.value = []; persist()
        notice.value = 'La session a été fermée dans un autre onglet.'
      }
    }
    timer = setInterval(tick, 1000); return restore()
  }
  function dispose() {
    document.removeEventListener('input', revalidateOnReturn)
    document.removeEventListener('pointerdown', revalidateOnReturn)
    window.removeEventListener('focus', revalidateOnReturn)
    window.removeEventListener('online', revalidateOnReturn)
    document.removeEventListener('visibilitychange', revalidateOnReturn)
    channel?.close(); clearInterval(timer); generation++; client.abort(); current.value = null; idleCandidate = null; continuity.suspend()
  }
  const expiresSoon = computed(() => current.value && Math.min(Date.parse(current.value.expires_at), Date.parse(current.value.idle_expires_at)) - now.value <= sessionWarningMs)
  return { continuity, revalidateOnReturn, businessRevision, current, phase, notice, ssoLogout, pending, client, has, restore, login, logout, remember, resolve, start, dispose, expiresSoon, tick }
}
export type B2BSession = ReturnType<typeof createB2BSession>
export const sessionKey: InjectionKey<B2BSession> = Symbol('isolated B2B session')
export function useB2B() {
  const session = inject(sessionKey)
  if (!session) throw new Error('B2B session provider required.')
  return session
}
