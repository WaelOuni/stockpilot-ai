<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { t, dateLabel } from './preferences'
import { useB2B } from './session'
import { BffError, StaleResponse } from './client'
import { contactCategories, contactDecoder, contactPage, contactReceipt, replyPage, type Contact } from './contact'
import CataloguePagination from './CataloguePagination.vue'
const session = useB2B()
const scope = ref('own'), status = ref(''), offset = ref(0), replyOffset = ref(0)
const subject = ref(''), category = ref<keyof typeof contactCategories>('other'), description = ref(''), answer = ref('')
const rows = ref<Contact[]>([]), total = ref(0), selected = ref<Contact | null>(null)
const replies = ref<ReturnType<typeof replyPage>['items']>([]), replyTotal = ref(0)
const busy = ref(false), error = ref(''), notice = ref('')
const pending = ref<{ path: string; body: Record<string, string>; kind: 'create' | 'reply' | 'status' } | null>(null)
let epoch = 0, disposed = false
const identity = computed(() => [session.current.value?.user_id, session.current.value?.organization_id, session.current.value?.csrf_token].join(':'))
function clear() {
  epoch++; rows.value = []; selected.value = null; replies.value = []; total.value = 0; replyTotal.value = 0
  subject.value = ''; description.value = ''; answer.value = ''; pending.value = null; busy.value = false; notice.value = ''; error.value = ''
}
watch(identity, clear, { flush: 'sync' })
onUnmounted(() => { disposed = true; clear() })
function current(version: number) { if (disposed || version !== epoch) throw new StaleResponse() }
async function run(action: (version: number) => Promise<void>) {
  if (busy.value) return
  const version = epoch; busy.value = true; error.value = ''
  try { await action(version) }
  catch (e) {
    if (version === epoch && !(e instanceof StaleResponse)) error.value = e instanceof BffError && e.code === 'CONTACT_RATE_LIMITED'
      ? 'Trop de demandes. Attendez dix minutes avant de réessayer.' : e instanceof Error ? e.message : 'Action impossible.'
  } finally { if (version === epoch) busy.value = false }
}
async function load(version: number) {
  if (!session.has('contact:read_own')) return
  const params = new URLSearchParams({ limit: '10', offset: String(offset.value), scope: scope.value })
  if (status.value) params.set('status', status.value)
  const result = await session.client.request('/b2b/api/contact/requests?' + params, contactPage)
  current(version); rows.value = result.items; total.value = result.total
  if (offset.value && offset.value >= result.total) {
    offset.value = Math.max(0, Math.floor((result.total - 1) / 10) * 10)
    await load(version)
  }
}
async function detail(version: number, id: string) {
  const value = await session.client.request('/b2b/api/contact/requests/' + encodeURIComponent(id), contactDecoder)
  current(version); selected.value = value
  const page = await session.client.request('/b2b/api/contact/requests/' + encodeURIComponent(id) + '/replies?limit=10&offset=' + replyOffset.value, replyPage)
  current(version); replies.value = page.items; replyTotal.value = page.total
}
async function open(id: string) { replyOffset.value = 0; answer.value = ''; await run(version => detail(version, id)) }
async function submit(kind: 'create' | 'reply' | 'status', nextStatus?: string) {
  if (busy.value || pending.value) return
  let path = '/b2b/api/contact/requests'
  let body: Record<string, string> = { operation_id: crypto.randomUUID() }
  if (kind === 'create') body = { ...body, subject: subject.value, category: category.value, description: description.value }
  else {
    if (!selected.value) return
    path += '/' + encodeURIComponent(selected.value.request_id) + (kind === 'reply' ? '/replies' : '/status')
    body = { ...body, ...(kind === 'reply' ? { body: answer.value } : { status: nextStatus! }) }
  }
  pending.value = { path, body, kind }
  await retry()
}
async function retry() {
  const attempt = pending.value
  if (!attempt) return
  await run(async version => {
    let result
    try {
      result = await session.client.request(attempt.path, contactReceipt, { method: 'POST', body: attempt.body })
    } catch (e) {
      if (version === epoch && e instanceof BffError && e.status >= 400 && e.status < 500 && e.status !== 429) pending.value = null
      throw e
    }
    current(version); pending.value = null
    if (attempt.kind === 'create') { subject.value = ''; description.value = ''; offset.value = 0; scope.value = 'own'; status.value = '' }
    if (attempt.kind === 'reply') answer.value = ''
    notice.value = 'Demande ou mise à jour enregistrée dans votre organisation. Aucun email envoyé.'
    replyOffset.value = 0
    await load(version); await detail(version, result.request_id)
  })
}
async function filter() { offset.value = 0; selected.value = null; replies.value = []; await run(load) }
async function page(value: number) { offset.value = (value - 1) * 10; await run(load) }
async function replyPageTo(value: number) { replyOffset.value = (value - 1) * 10; if (selected.value) await run(version => detail(version, selected.value!.request_id)) }
onMounted(() => { void run(load) })
</script>
<template>
  <section class="b2b-card organization-contact">
    <h2>{{ t('Contacter mon administrateur') }}</h2>
    <p>{{ t('Ces demandes restent dans votre organisation. Ce canal ne transmet aucun email externe.') }}</p>
    <p
      v-if="!session.has('contact:create')"
      role="status"
    >
      {{ t('La messagerie interne n’est pas disponible pour cette session.') }}
    </p>
    <form
      v-else
      @submit.prevent="submit('create')"
    >
      <label>{{ t('Objet obligatoire') }}<input
        v-model="subject"
        required
        maxlength="120"
        :disabled="busy || !!pending"
      /></label>
      <label>{{ t('Catégorie') }}<select
        v-model="category"
        :disabled="busy || !!pending"
      ><option
        v-for="(label, key) in contactCategories"
        :key="key"
        :value="key"
      >{{ t(label) }}</option></select></label>
      <label>{{ t('Description obligatoire') }}<textarea
        v-model="description"
        required
        maxlength="4000"
        rows="5"
        :disabled="busy || !!pending"
      ></textarea></label>
      <button :disabled="busy || !!pending">
        {{ t('Enregistrer ma demande') }}
      </button>
    </form>
    <p
      v-if="busy"
      role="status"
    >
      {{ t('Chargement…') }}
    </p>
    <p
      v-if="error"
      role="alert"
    >
      {{ t(error) }}
    </p>
    <p
      v-if="notice"
      role="status"
    >
      {{ t(notice) }}
    </p>
    <aside
      v-if="pending"
      role="status"
      class="b2b-notice"
    >
      <p>{{ t('Résultat à vérifier. Gardez cette page ouverte : le nouvel essai utilisera la même référence.') }}</p>
      <button
        :disabled="busy"
        @click="retry"
      >
        {{ t('Réessayer la même demande') }}
      </button>
    </aside>
    <template v-if="session.has('contact:read_own')">
      <h3>{{ scope === 'organization' ? t('Demandes de mon organisation') : t('Mes demandes') }}</h3>
      <div class="b2b-actions contact-filters">
        <label v-if="session.has('contact:read_organization')">{{ t('Périmètre des demandes') }}
          <select
            v-model="scope"
            :disabled="busy || !!pending"
            @change="filter"
          >
            <option value="own">{{ t('Mes demandes') }}</option><option value="organization">{{ t('Demandes de mon organisation') }}</option>
          </select>
        </label>
        <label>{{ t('État') }}<select
          v-model="status"
          :disabled="busy || !!pending"
          @change="filter"
        >
          <option value="">{{ t('Tous les états') }}</option><option value="open">{{ t('Ouverte') }}</option><option value="resolved">{{ t('Résolue') }}</option>
        </select></label>
        <button
          class="secondary"
          :disabled="busy"
          @click="run(load)"
        >
          {{ t('Actualiser les demandes') }}
        </button>
      </div>
      <p v-if="!busy && !rows.length">
        {{ t('Aucune demande à afficher.') }}
      </p>
      <ul class="contact-list">
        <li
          v-for="item in rows"
          :key="item.request_id"
        >
          <button
            class="secondary request-subject"
            :disabled="busy || !!pending"
            @click="open(item.request_id)"
          >
            {{ item.subject }}
          </button>
          <p class="hint">
            {{ dateLabel(item.created_at) }} · {{ t(contactCategories[item.category]) }} · {{ item.status === 'open' ? t('Ouverte') : t('Résolue') }}
          </p>
        </li>
      </ul>
      <CataloguePagination
        :total="total"
        :offset="offset"
        :limit="10"
        :busy="busy || !!pending"
        unit="demandes • Page"
        label="Pages des demandes"
        @page="page"
      />
      <section
        v-if="selected"
        class="contact-detail"
        :aria-label="t('Détail de la demande')"
      >
        <h3>{{ selected.subject }}</h3>
        <p>{{ selected.status === 'open' ? t('Ouverte') : t('Résolue') }} · {{ dateLabel(selected.updated_at) }}</p>
        <p class="plain-text">
          {{ selected.description }}
        </p>
        <h4>{{ t('Réponses de l’administrateur') }}</h4>
        <p v-if="!replies.length">
          {{ t('Aucune réponse pour le moment.') }}
        </p>
        <article
          v-for="reply in replies"
          :key="reply.reply_id"
          class="reply"
        >
          <p class="hint">
            {{ dateLabel(reply.created_at) }}
          </p><p class="plain-text">
            {{ reply.body }}
          </p>
        </article>
        <CataloguePagination
          :total="replyTotal"
          :offset="replyOffset"
          :limit="10"
          :busy="busy || !!pending"
          unit="réponses • Page"
          label="Pages des réponses"
          @page="replyPageTo"
        />
        <form
          v-if="session.has('contact:reply_organization')"
          @submit.prevent="submit('reply')"
        >
          <label>{{ t('Réponse obligatoire') }}<textarea
            v-model="answer"
            required
            maxlength="4000"
            rows="4"
            :disabled="busy || !!pending"
          ></textarea></label>
          <button :disabled="busy || !!pending">
            {{ t('Enregistrer la réponse') }}
          </button>
        </form>
        <button
          v-if="session.has('contact:manage_organization')"
          class="secondary"
          :disabled="busy || !!pending"
          @click="submit('status', selected.status === 'open' ? 'resolved' : 'open')"
        >
          {{ selected.status === 'open' ? t('Marquer comme résolue') : t('Rouvrir la demande') }}
        </button>
      </section>
    </template>
  </section>
</template>
<style scoped>
.organization-contact { min-width:0; }
form { display:grid; gap:14px; margin-block:16px; }
textarea { resize:vertical; }
.contact-list { list-style:none; padding:0; }
.contact-list li { border-bottom:1px solid var(--line); padding-block:12px; }
.request-subject { text-align:left; max-width:100%; overflow-wrap:anywhere; }
.contact-detail { border-top:2px solid var(--line); margin-top:20px; padding-top:16px; }
.plain-text { white-space:pre-wrap; overflow-wrap:anywhere; }
.reply { border-left:3px solid var(--brand); padding-left:12px; }
.contact-filters { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); align-items:end; }
@media(max-width:600px) { .contact-filters { grid-template-columns:minmax(0,1fr); } }
h3 { overflow-wrap:anywhere; }
</style>
