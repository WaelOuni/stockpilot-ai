import { ref } from 'vue'

type Owner = { user_id: string; organization_id: string }
type Reference = { user: string; organization: string; value: string }
const conversationKey = 'stockpilot.b2b.conversation.v1'
const returnKey = 'stockpilot.b2b.return.v1'
const identifier = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value)
const same = (ref: Reference, owner: Owner) => ref.user === owner.user_id && ref.organization === owner.organization_id
// References only, scoped to this tab and a server-validated identity. Never content,
// drafts, tokens, buyer selections or transaction/confirmation payloads.
function read(key: string): Reference | null {
  try {
    const raw: unknown = JSON.parse(sessionStorage.getItem(key) ?? 'null')
    if (typeof raw !== 'object' || raw === null) return null
    const r = raw as Partial<Reference>
    if (typeof r.user !== 'string' || !r.user || r.user.length > 128 || typeof r.organization !== 'string' || !r.organization || r.organization.length > 128 || typeof r.value !== 'string' || r.value.length > 256) return null
    return { user: r.user, organization: r.organization, value: r.value }
  } catch { return null }
}
function write(key: string, value: Reference | null) {
  try {
    if (value) sessionStorage.setItem(key, JSON.stringify(value))
    else sessionStorage.removeItem(key)
  } catch { /* In-memory navigation remains usable without browser storage. */ }
}
function safePath(path: string) {
  return /^\/b2b-app\/(?:orders|stock|assistant(?:\/[A-Za-z0-9_-]{1,128})?)?$/.test(path)
}
export function createContinuity() {
  const active = ref('')
  const drafts = new Map<string, string>()
  let suspended = true
  function draft(key: string) { return !suspended && owner ? drafts.get(key) ?? '' : '' }
  function saveDraft(key: string, value: string) {
    if (suspended || !owner) return
    if (!value) drafts.delete(key)
    else { if (drafts.size >= 20 && !drafts.has(key)) drafts.delete(drafts.keys().next().value!); drafts.set(key, value.slice(0, 10000)) }
  }
  let owner: Owner | null = null
  let cached: Reference | null = null
  function purge() { drafts.clear(); suspended = true; active.value = ''; owner = null; cached = null; write(conversationKey, null); write(returnKey, null) }
  function restore(identity: Owner) {
    const previous = cached ?? read(conversationKey)
    if (owner && (owner.user_id !== identity.user_id || owner.organization_id !== identity.organization_id)) purge()
    suspended = false
    owner = { user_id: identity.user_id, organization_id: identity.organization_id }
    active.value = previous && same(previous, identity) && identifier(previous.value) ? previous.value : ''
    cached = active.value ? previous : null
    if (!active.value) write(conversationKey, null)
  }
  function select(id: string) {
    if (!owner || !identifier(id)) return
    active.value = id
    cached = { user: owner.user_id, organization: owner.organization_id, value: id }
    write(conversationKey, cached)
  }
  function forget(id: string) { if (active.value === id) { active.value = ''; cached = null; write(conversationKey, null) } }
  function suspend() { suspended = true; active.value = '' } // Keep identity-bound references and volatile drafts quarantined.
  function changedIdentity(user: unknown, organization: unknown) {
    const previous = owner ?? read(conversationKey) ?? read(returnKey)
    if (!previous) return
    const u = 'user_id' in previous ? previous.user_id : previous.user
    const o = 'organization_id' in previous ? previous.organization_id : previous.organization
    if (u !== user || o !== organization) purge()
  }
  function rememberReturn(path: string) {
    // A reload may encounter an expired cookie before any owner is validated.
    // The old reference is only a hint: takeReturn still requires the newly
    // authenticated owner to match, and history is authorized again by the BFF.
    const previous = read(conversationKey)
    const candidate = owner ?? (previous && identifier(previous.value)
      ? { user_id: previous.user, organization_id: previous.organization } : null)
    if (candidate && safePath(path)) write(returnKey, { user: candidate.user_id, organization: candidate.organization_id, value: path })
  }
  function takeReturn() {
    const saved = read(returnKey); write(returnKey, null)
    return owner && saved && same(saved, owner) && safePath(saved.value) ? saved.value : null
  }
  return { draft, saveDraft, active, restore, select, forget, suspend, purge, changedIdentity, rememberReturn, takeReturn }
}
