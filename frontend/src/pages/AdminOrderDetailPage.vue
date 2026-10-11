<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { cancelAdminOrder, getAdminOrder, updateAdminOrderStatus, type AdminOrder } from '../api/admin'
import type { OrderStatus } from '../types/api'
import { ApiError } from '../api/http'
import AdminLogout from '../components/AdminLogout.vue'
import ApiErrorNotice from '../components/ApiErrorNotice.vue'
import OrderStatusBadge from '../components/OrderStatusBadge.vue'
import OrderTimeline from '../components/OrderTimeline.vue'
import { formatDate } from '../utils/format'
const route = useRoute()
const router = useRouter()
const orderId = computed(() => String(route.params.orderId || ''))
const order = ref<AdminOrder | null>(null)
const loading = ref(false)
const error = ref<Error | null>(null)
const mutating = ref(false)
const blocked = ref(true)
const loggingOut = ref(false)
const confirming = ref(false)
const feedback = ref('')
const nextStatus = computed<OrderStatus | undefined>(() => ({
  CREATED: 'PROCESSING', PROCESSING: 'SHIPPED', SHIPPED: 'DELIVERED',
} as Partial<Record<OrderStatus, OrderStatus>>)[order.value?.status ?? 'CANCELLED'])
const cancellable = computed(() => order.value?.status === 'CREATED' || order.value?.status === 'PROCESSING')
const disabled = computed(() => loading.value || mutating.value || blocked.value || loggingOut.value)
let controller: AbortController | undefined
let timeout: ReturnType<typeof setTimeout> | undefined
let active = true
function abortPending() {
  controller?.abort()
  clearTimeout(timeout)
}
async function load(reconcile = false) {
  abortPending()
  const request = new AbortController()
  controller = request
  loading.value = true
  blocked.value = true
  confirming.value = false
  if (!reconcile) { order.value = null; error.value = null; feedback.value = '' }
  const id = orderId.value
  const timer = setTimeout(() => request.abort(), 20_000)
  timeout = timer
  try {
    const result = await getAdminOrder(id, request.signal)
    if (active && controller === request && !request.signal.aborted && !loggingOut.value && result.order_id === id) {
      order.value = result
      blocked.value = false
      if (reconcile) feedback.value = 'Statut vérifié auprès du serveur. Aucune opération n’a été relancée.'
    } else if (controller === request && result.order_id !== id) {
      error.value = new Error('Réponse de commande incohérente. Actualisez avant toute opération.')
    }
  }
  catch (cause) {
    if (active && controller === request && !loggingOut.value) {
      if (cause instanceof ApiError && cause.status === 401) {
        order.value = null
        await router.replace('/admin/login?reason=session')
      } else {
        if (cause instanceof ApiError && cause.status === 404) order.value = null
        error.value = cause instanceof Error ? cause : new Error('Chargement impossible.')
      }
    }
  } finally { clearTimeout(timer); if (controller === request) loading.value = false }
}
async function mutate(cancel = false) {
  if (disabled.value || !order.value || (cancel ? !cancellable.value || !confirming.value : !nextStatus.value)) return
  const id = orderId.value
  const status = nextStatus.value
  abortPending()
  const request = new AbortController()
  controller = request
  mutating.value = true
  blocked.value = true
  confirming.value = false
  error.value = null
  feedback.value = ''
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; request.abort() }, 20_000)
  timeout = timer
  try {
    const result = cancel ? await cancelAdminOrder(id, request.signal)
      : await updateAdminOrderStatus(id, status!, request.signal)
    if (!active || controller !== request || loggingOut.value || orderId.value !== id) return
    if (timedOut || result.order_id !== id) throw new ApiError('Résultat incertain. Vérification nécessaire.', 0)
    order.value = result
    blocked.value = false
    feedback.value = cancel ? 'Commande annulée.' : 'Statut mis à jour.'
  } catch (cause) {
    if (!active || controller !== request || loggingOut.value || orderId.value !== id) return
    error.value = cause instanceof Error ? cause : new Error('Opération impossible.')
    if (cause instanceof ApiError && cause.status === 401) {
      order.value = null
      await router.replace('/admin/login?reason=session')
    } else if (timedOut || !(cause instanceof ApiError) || cause.status === 0 || cause.status === 409 || cause.status >= 500) {
      clearTimeout(timer)
      mutating.value = false
      await load(true)
    } else if (cause.status === 404) {
      order.value = null
    }
  } finally { clearTimeout(timer); if (active && controller === request) mutating.value = false }
}
function logoutPending(value: boolean) {
  loggingOut.value = value
  if (value) {
    abortPending()
    controller = undefined
    blocked.value = true
    loading.value = false
    mutating.value = false
    confirming.value = false
  }
}
watch(orderId, () => { mutating.value = false; void load() }, { immediate: true })
onBeforeUnmount(() => { active = false; abortPending() })
</script>
<template>
  <header class="page-heading page-heading-actions">
    <div><span class="eyebrow">Back-office · Opérations</span><h1>Détail administratif</h1></div>
    <div class="header-actions">
      <AdminLogout @pending="logoutPending" />
      <button
        class="secondary"
        :disabled="loading || mutating || loggingOut"
        @click="load()"
      >
        Actualiser
      </button>
    </div>
  </header>
  <nav
    class="page-actions"
    aria-label="Actions de navigation"
  >
    <RouterLink to="/admin/orders">
      Retour aux commandes administrateur
    </RouterLink>
  </nav>
  <p
    v-if="loading"
    role="status"
  >
    Chargement de la commande…
  </p>
  <ApiErrorNotice
    v-if="error"
    :error="error"
  />
  <p
    v-if="feedback && !loading"
    :class="error ? 'info-notice' : 'success-notice'"
    role="status"
  >
    {{ feedback }}
  </p>
  <p
    v-if="blocked && order"
    role="status"
  >
    Actions bloquées jusqu’à une actualisation fiable du serveur.
  </p>
  <article
    v-if="order"
    class="context-card"
  >
    <h2>Informations de la commande</h2>
    <dl class="detail-facts">
      <div>
        <dt>Identifiant</dt><dd class="identifier-value">
          {{ order.order_id }}
        </dd>
      </div>
      <div>
        <dt>Client</dt><dd class="identifier-value">
          {{ order.customer_id ?? 'Non renseigné (legacy)' }}
        </dd>
      </div>
      <div><dt>Produit</dt><dd>{{ order.product_name }}</dd></div>
      <div><dt>Quantité</dt><dd>{{ order.quantity }}</dd></div>
      <div><dt>Date de création</dt><dd><time :datetime="order.created_at">{{ formatDate(order.created_at) }}</time></dd></div>
      <div><dt>Statut actuel</dt><dd><OrderStatusBadge :status="order.status" /></dd></div>
    </dl>
    <section
      class="order-action-section"
      aria-labelledby="order-actions-title"
    >
      <h2 id="order-actions-title">
        Actions sur la commande
      </h2>
      <div class="order-actions">
        <button
          v-if="nextStatus"
          :disabled="disabled"
          @click="mutate()"
        >
          Passer à {{ nextStatus }}
        </button>
        <button
          v-if="cancellable && !confirming"
          class="danger"
          :disabled="disabled"
          @click="confirming = true"
        >
          Annuler la commande
        </button>
      </div>
      <section
        v-if="confirming"
        class="confirmation-panel"
        role="group"
        aria-label="Confirmation d’annulation"
      >
        <p>Confirmer l’annulation de cette commande ? Le stock réservé sera restitué.</p>
        <div class="order-actions">
          <button
            class="danger"
            :disabled="disabled"
            @click="mutate(true)"
          >
            Confirmer l’annulation
          </button>
          <button
            :disabled="disabled"
            class="secondary"
            @click="confirming = false"
          >
            Conserver la commande
          </button>
        </div>
      </section>
      <p
        v-if="mutating"
        role="status"
      >
        Opération en cours…
      </p>
    </section>
    <section
      class="order-history"
      aria-labelledby="order-history-title"
    >
      <h2 id="order-history-title">
        Historique des événements
      </h2>
      <OrderTimeline
        :status="order.status"
        :events="order.events"
      />
    </section>
  </article>
</template>
