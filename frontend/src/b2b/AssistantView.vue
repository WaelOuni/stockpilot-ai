<script setup lang="ts">
import { t, dateLabel } from './preferences'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useB2B } from './session'
import { assistantHistory, assistantTurn, conversationList, type AssistantTurn } from './assistant'
import { buyerDecoder, object, page, text } from './contracts'
import { BffError, StaleResponse } from './client'
import { formatMoney } from './money'
import { useTask } from './tasks'
import { mutate } from './workflows'
import ComparisonPanel from './ComparisonPanel.vue'

const props = defineProps<{ embedded?: boolean }>()
const comparison = ref<string[]>([])

const statusLabels = { prepared: 'Préparée — aucun stock réservé', reserved: 'Réservée', cancelled: 'Annulée' }
const kindLabels = { message: 'Message', confirm: 'Confirmation', cancel: 'Annulation' }
const session = useB2B()
const route = useRoute()
const router = useRouter()
const { busy, error, run } = useTask()
const conversations = ref<ReturnType<typeof conversationList>['items']>([])
const turns = ref<AssistantTurn[]>([])
const buyers = ref<ReturnType<typeof buyerDecoder>[]>([])
const buyer = ref('')
const message = ref('')
const confirmation = ref(false)
const cancelConfirmation = ref(false)
const unknown = ref(false)
const historyOffset = ref(0)
const hasMore = ref(false)
let epoch = 0
let disposed = false
const id = computed(() => !props.embedded && typeof route.params.conversationId === 'string' && route.params.conversationId
  ? route.params.conversationId : session.continuity.active.value)
async function selectConversation(value: string) {
  if (props.embedded) session.continuity.select(value)
  else await router.push('/b2b-app/assistant/' + encodeURIComponent(value))
}
const base = computed(() => '/b2b/api/assistant/conversations/' + encodeURIComponent(id.value))
const globallyPending = computed(() => session.pending.value.length > 0)
const pending = computed(() => turns.value.find(t => t.status === 'pending'))
const latest = computed(() => turns.value.at(-1))
const order = computed(() => latest.value?.reply?.order)
const canConfirm = computed(() => historyOffset.value === 0 && latest.value?.reply?.can_confirm && order.value?.status === 'prepared')
const enabled = computed(() => session.current.value?.assistant_available === true)
function current(version: number) { if (disposed || epoch !== version) throw new StaleResponse() }
async function history(resume = false) {
  const version = epoch
  const url = base.value + (resume ? '/resume' : '?limit=20&offset=' + historyOffset.value)
  const selected = id.value
  try {
    const value = await session.client.request(url, assistantHistory, resume ? { method: 'POST', body: {} } : {})
    current(version)
    if (id.value !== selected || value.conversation_id !== selected) throw new StaleResponse()
    session.continuity.select(selected)
    turns.value = value.items; hasMore.value = value.has_more; unknown.value = false
  } catch (error) {
    current(version)
    if (error instanceof BffError && error.status === 404) session.continuity.forget(selected)
    throw error
  }
  confirmation.value = false; cancelConfirmation.value = false
}
async function load() {
  const version = ++epoch
  turns.value = []; unknown.value = false; confirmation.value = false; cancelConfirmation.value = false; historyOffset.value = 0
  if (!enabled.value) return
  const result = await session.client.request('/b2b/api/assistant/conversations?limit=50&offset=0', conversationList)
  current(version); conversations.value = result.items
  if (!props.embedded && !route.params.conversationId && id.value) {
    await router.replace('/b2b-app/assistant/' + encodeURIComponent(id.value))
    return
  }
  if (id.value) await history()
}
async function create() {
  await run(async () => {
    const version = epoch
    const result = await session.client.request('/b2b/api/assistant/conversations', object({ conversation_id: text }), {
      method: 'POST', body: { thread_id: crypto.randomUUID() },
    })
    current(version); session.continuity.select(result.conversation_id); await selectConversation(result.conversation_id)
  })
}
async function send() {
  if (!id.value || !message.value.trim() || pending.value || unknown.value || globallyPending.value) return
  const input = message.value.trim()
  await run(async () => {
    const version = epoch
    const turnId = crypto.randomUUID()
    unknown.value = true // Until a response or a durable history has been read.
    const result = await session.client.request(base.value + '/messages', assistantTurn, {
      method: 'POST', body: { message_id: turnId, message: input, customer_id: buyer.value || null }, timeoutMs: 90000,
    })
    current(version); turns.value = [...turns.value, result].slice(-20); message.value = ''; unknown.value = false; confirmation.value = false
  })
}
async function retry() {
  const turn = pending.value
  if (!turn) return
  await run(async () => {
    const version = epoch
    await session.client.request(base.value + '/turns/' + encodeURIComponent(turn.turn_id) + '/retry', assistantTurn, {
      method: 'POST', body: {}, timeoutMs: 90000,
    })
    current(version); await history()
  })
}
async function action(namespace: 'confirm' | 'cancel') {
  if (globallyPending.value) { error.value = 'Vérifiez les opérations en attente avant toute nouvelle mutation.'; return }
  const selected = order.value
  if (!selected || (namespace === 'confirm' ? !confirmation.value : !cancelConfirmation.value)) return
  await run(async () => {
    const version = epoch
    unknown.value = true; confirmation.value = false; cancelConfirmation.value = false
    await mutate(session, 'orders', namespace, base.value + '/' + namespace,
      operationId => ({ operation_id: operationId, order_id: selected.order_id, confirmed: true,
        ...(namespace === 'confirm' ? { preparation_digest: selected.preparation_digest } : {}) }), assistantTurn)
    current(version); await history()
  })
}
async function older(delta: number) { historyOffset.value += delta * 20; await run(() => history()) }
watch(id, () => { void run(load) })
onMounted(() => { void run(async () => {
  await load()
  if (session.has('order:create')) {
    const version = epoch
    const result = await session.client.request('/b2b/api/buyers?limit=100&offset=0', page(buyerDecoder))
    current(version); buyers.value = result.items
    if (buyers.value.length === 1) buyer.value = buyers.value[0]!.customer_id
  }
}) })
onUnmounted(() => { disposed = true; epoch++; turns.value = []; conversations.value = []; message.value = '' })
</script>
<template>
  <section>
    <p class="eyebrow">
      {{ t("Conseil et commandes de votre entreprise") }}
    </p>
    <h1>{{ t("Assistant StockPilot") }}</h1>
    <p>{{ t("Les recherches utilisent votre catalogue et vos documents. Une conversation ne confirme jamais une réservation.") }}</p>
    <p
      v-if="!enabled"
      role="status"
    >
      {{ t("L’assistant n’est pas activé dans cet environnement.") }}
    </p>
    <template v-else>
      <p
        v-if="globallyPending"
        class="b2b-notice"
        role="status"
      >
        {{ t("Une opération métier reste à vérifier dans le panneau de réconciliation. Aucune nouvelle préparation ou confirmation ne sera envoyée.") }}
      </p>
      <p
        v-if="error"
        role="alert"
      >
        {{ t(error) }}
      </p>
      <div class="b2b-actions">
        <button
          :disabled="busy"
          @click="create"
        >
          {{ t("Nouvelle conversation") }}
        </button>
        <label>{{ t("Mes conversations") }}
          <select
            :value="id"
            :disabled="busy"
            @change="selectConversation(($event.target as HTMLSelectElement).value)"
          >
            <option
              value=""
              disabled
            >{{ t("Choisir une conversation") }}</option>
            <option
              v-for="conversation in conversations"
              :key="conversation.conversation_id"
              :value="conversation.conversation_id"
            >
              {{ t("Conversation du") }} {{ dateLabel(conversation.created_at) }}
            </option>
          </select>
        </label>
        <button
          v-if="id"
          class="secondary"
          :disabled="busy"
          @click="run(() => history(true))"
        >
          {{ t("Reprendre et vérifier") }}
        </button>
      </div>
      <template v-if="id">
        <p
          v-if="busy"
          role="status"
        >
          {{ t("Traitement en cours… Aucun nouvel essai automatique.") }}
        </p>
        <aside
          v-if="unknown || pending"
          class="b2b-notice"
          role="status"
        >
          <p>{{ t("Une réponse reste à vérifier. Le délai réseau ne prouve ni un échec ni une annulation.") }}</p>
          <button
            :disabled="busy"
            @click="run(() => history(true))"
          >
            {{ t("Vérifier le résultat durable") }}
          </button>
          <button
            v-if="pending"
            class="secondary"
            :disabled="busy"
            @click="retry"
          >
            {{ t("Rejouer explicitement la même demande") }}
          </button>
        </aside>
        <div
          :aria-label="t('Historique de conversation')"
          aria-live="polite"
        >
          <article
            v-for="turn in turns"
            :key="turn.turn_id"
            class="b2b-card assistant-turn"
            :class="{ 'assistant-order': !!turn.reply?.order, 'assistant-error': turn.reply?.intent === 'CLARIFICATION_REQUIRED' || turn.reply?.intent === 'ORDER_REFUSED' }"
          >
            <p class="hint">
              {{ dateLabel(turn.created_at) }} · {{ t(kindLabels[turn.kind]) }}
            </p>
            <p
              v-if="turn.message"
              class="assistant-user"
            >
              <strong>{{ t("Vous :") }}</strong> {{ turn.message }}
            </p>
            <template v-if="turn.reply">
              <p class="assistant-answer">
                <strong>StockPilot</strong><br />
                {{ turn.reply.text }}
              </p>
              <div
                class="b2b-grid assistant-results"
                :aria-label="t('Produits vérifiés')"
              >
                <article
                  v-for="product in turn.reply.products"
                  :key="product.product_id"
                  class="assistant-product"
                >
                  <h2>{{ product.name }}</h2>
                  <p>{{ product.sku }} · {{ formatMoney(product.price_minor, product.currency) }}</p>
                  <p>{{ t("Physique") }} {{ product.quantity }} {{ t("· Réservé") }} {{ product.reserved }} {{ t("· Disponible") }} {{ product.available }}</p>
                  <p class="hint">
                    {{ t("Observation du") }} {{ dateLabel(product.observed_at) }}{{ t(". Ce message conserve ses données d’origine.") }}
                  </p>
                </article>
              </div>
              <button
                v-if="turn.reply.products.length > 1"
                class="secondary"
                @click="comparison = turn.reply.products.slice(0, 4).map(product => product.product_id)"
              >
                {{ t("Comparer ces produits") }}
              </button>
              <div
                v-if="turn.reply.order"
                class="assistant-order-summary"
              >
                <p>{{ t("Commande") }} {{ turn.reply.order.order_id }} {{ t("· État observé :") }} {{ t(statusLabels[turn.reply.order.status]) }}</p>
                <ul>
                  <li
                    v-for="line in turn.reply.order.lines"
                    :key="line.product_id"
                  >
                    {{ line.sku }} {{ t("· quantité") }} {{ line.quantity }} · {{ formatMoney(line.price_minor, line.currency) }}
                  </li>
                </ul>
              </div>
            </template>
            <p v-else>
              {{ t("Demande enregistrée, résultat à vérifier.") }}
            </p>
          </article>
          <p v-if="!turns.length && !busy">
            {{ t("Posez une question ou recherchez un produit par son SKU.") }}
          </p>
        </div>
        <nav
          class="b2b-actions"
          :aria-label="t('Pages de conversation')"
        >
          <button
            class="secondary"
            :disabled="busy || !hasMore"
            @click="older(1)"
          >
            {{ t("Messages plus anciens") }}
          </button>
          <button
            class="secondary"
            :disabled="busy || historyOffset === 0"
            @click="older(-1)"
          >
            {{ t("Messages plus récents") }}
          </button>
        </nav>
        <section
          v-if="order && historyOffset === 0"
          class="b2b-card"
          :aria-label="t('Action sur la commande')"
        >
          <h2>{{ canConfirm ? t("Confirmer la préparation") : t("Suivi de la commande") }}</h2>
          <p class="hint">
            {{ t("Référence :") }} {{ order.order_id }}
          </p>
          <template v-if="canConfirm && session.has('order:create')">
            <label><span><input
              v-model="confirmation"
              :disabled="busy || !!pending || unknown || globallyPending"
              type="checkbox"
            /> {{ t("Je confirme les SKU, quantités et prix du dernier récapitulatif.") }}</span></label>
            <button
              :disabled="busy || !!pending || unknown || globallyPending || !confirmation"
              @click="action('confirm')"
            >
              {{ t("Confirmer et réserver") }}
            </button>
          </template>
          <template v-if="order.status === 'reserved' && (session.has('order:cancel_own') || session.has('order:cancel_organization'))">
            <label><span><input
              v-model="cancelConfirmation"
              :disabled="busy || !!pending || unknown || globallyPending"
              type="checkbox"
            /> {{ t("Je demande l’annulation de cette commande.") }}</span></label>
            <button
              :disabled="busy || !!pending || unknown || globallyPending || !cancelConfirmation"
              @click="action('cancel')"
            >
              {{ t("Confirmer l’annulation") }}
            </button>
          </template>
          <p v-if="order.status === 'prepared' && !canConfirm">
            {{ t("Cette préparation ne peut plus être confirmée ici. Vérifiez son état et préparez à nouveau si nécessaire.") }}
          </p>
        </section>
        <form
          v-if="historyOffset === 0"
          class="b2b-card"
          @submit.prevent="send"
        >
          <label v-if="session.has('order:create')">{{ t("Acheteur pour une préparation") }}
            <select
              v-model="buyer"
              :disabled="busy"
            >
              <option value="">{{ t("Aucun acheteur sélectionné") }}</option>
              <option
                v-for="association in buyers"
                :key="association.association_id"
                :value="association.customer_id"
              >{{ association.customer_id }}</option>
            </select>
          </label>
          <label>{{ t("Votre message") }}
            <textarea
              v-model="message"
              required
              maxlength="4000"
              rows="3"
              :disabled="busy || !!pending || unknown || globallyPending"
              :placeholder="t('Ex. Des perceuses disponibles à moins de 50 €')"
            ></textarea>
          </label>
          <button :disabled="busy || !!pending || unknown || globallyPending || !message.trim()">
            {{ t("Envoyer le message") }}
          </button>
        </form>
      </template>
    </template>
    <ComparisonPanel
      v-if="comparison.length > 1"
      :ids="comparison"
      @close="comparison = []"
    />
  </section>
</template>
<style scoped>
.assistant-turn { border-left:4px solid #94a3b8; }
.assistant-turn > .hint { margin-top:0; font-size:.8rem; }
.assistant-user { margin:8px 0 12px auto; padding:10px 14px; max-width:85%; border-radius:10px 10px 0 10px; background:#eef2ff; white-space:pre-wrap; }
.assistant-answer { white-space:pre-wrap; line-height:1.5; margin:10px 0; }
.assistant-order { border-left-color:#4338ca; }
.assistant-error { border-left-color:#9f1239; }
.assistant-order-summary { padding:12px; background:#f5f3ff; border:1px solid #c4b5fd; border-radius:8px; }
.assistant-results { margin:12px 0; }
.assistant-product h2 { margin:0 0 8px; font-size:1.05rem; }
.assistant-product p { margin:8px 0; }
@media(max-width:600px) { .assistant-user { max-width:100%; } }
.assistant-product { padding:16px; border:1px solid #e1e6ef; border-radius:8px; }
textarea { width:100%; box-sizing:border-box; resize:vertical; }
</style>
